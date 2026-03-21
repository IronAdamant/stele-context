"""
Stele engine -- smart context cache with semantic chunking and vector search.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

from stele.config import load_config, apply_config
from stele.engine_utils import (
    normalize_path as _norm,
    resolve_path as _resolve,
    detect_project_root,
    do_acquire_lock,
    do_get_lock_status,
    do_release_lock,
    do_record_conflict,
    check_environment_impl,
    init_coordination,
)
from stele.rwlock import RWLock
from stele.symbol_graph import SymbolGraphManager
from stele.session import SessionManager
from stele.storage import StorageBackend

from stele import indexing as _ix
from stele import search_engine as _se
from stele import change_detection as _cd


def _read_and_hash(path: Path, modality: str) -> tuple:
    """Read file content and compute SHA-256 hash."""
    if modality in ("image", "audio", "video"):
        raw = path.read_bytes()
        return raw, hashlib.sha256(raw).hexdigest()
    content = path.read_text(encoding="utf-8", errors="replace")
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


class Stele:
    """Smart context cache with semantic chunking and vector search."""

    DEFAULT_CHUNK_SIZE = 256
    DEFAULT_MAX_CHUNK_SIZE = 4096
    DEFAULT_MERGE_THRESHOLD = 0.7
    DEFAULT_CHANGE_THRESHOLD = 0.85
    DEFAULT_SEARCH_ALPHA = 0.7
    DEFAULT_SKIP_DIRS = {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        ".eggs",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
    MODALITY_THRESHOLDS = {
        "text": {"merge": 0.70, "change": 0.85},
        "code": {"merge": 0.85, "change": 0.80},
        "pdf": {"merge": 0.75, "change": 0.85},
    }

    def __init__(
        self,
        storage_dir: str | None = None,
        project_root: str | None = None,
        enable_coordination: bool = True,
        chunk_size: int | None = None,
        max_chunk_size: int | None = None,
        merge_threshold: float | None = None,
        change_threshold: float | None = None,
        search_alpha: float | None = None,
        skip_dirs: set | None = None,
    ):
        self._project_root = detect_project_root(project_root)
        file_cfg = load_config(self._project_root)
        cfg = apply_config(
            file_cfg,
            storage_dir=storage_dir,
            chunk_size=chunk_size,
            max_chunk_size=max_chunk_size,
            merge_threshold=merge_threshold,
            change_threshold=change_threshold,
            search_alpha=search_alpha,
            skip_dirs=skip_dirs,
        )
        resolved_storage = cfg.get("storage_dir", storage_dir)
        if resolved_storage is None:
            resolved_storage = os.environ.get("STELE_STORAGE_DIR")
        if resolved_storage is None and self._project_root is not None:
            resolved_storage = str(self._project_root / ".stele")
        self.storage = StorageBackend(resolved_storage)
        self.chunk_size = cfg.get("chunk_size", self.DEFAULT_CHUNK_SIZE)
        self.max_chunk_size = cfg.get("max_chunk_size", self.DEFAULT_MAX_CHUNK_SIZE)
        self.merge_threshold = cfg.get("merge_threshold", self.DEFAULT_MERGE_THRESHOLD)
        self.change_threshold = cfg.get(
            "change_threshold", self.DEFAULT_CHANGE_THRESHOLD
        )
        self.search_alpha = cfg.get("search_alpha", self.DEFAULT_SEARCH_ALPHA)
        self.skip_dirs = self.DEFAULT_SKIP_DIRS | cfg.get("skip_dirs", set())
        self.chunkers = _se.init_chunkers(self.chunk_size, self.max_chunk_size)
        self.vector_index = _se.load_or_rebuild_index(self.storage)
        self.session_manager = SessionManager(self.storage, self.vector_index)
        self.symbol_manager = SymbolGraphManager(self.storage)
        self.bm25_index: Any | None = None
        self._bm25_ready = False
        self._lock = RWLock()
        self._bm25_init_lock = threading.Lock()
        self._coordination = (
            init_coordination(self._project_root) if enable_coordination else None
        )

    # -- Internal helpers ------------------------------------------------------

    def _do_acquire_lock(
        self, p: str, a: str, ttl: float = 300.0, force: bool = False
    ) -> dict[str, Any]:
        return do_acquire_lock(p, a, self._coordination, self.storage, ttl, force)

    def _do_get_lock_status(self, p: str) -> dict[str, Any]:
        return do_get_lock_status(p, self._coordination, self.storage)

    def _do_release_lock(self, p: str, a: str) -> dict[str, Any]:
        return do_release_lock(p, a, self._coordination, self.storage)

    def _do_record_conflict(self, **kw: Any) -> int | None:
        return do_record_conflict(
            coordination=self._coordination, storage=self.storage, **kw
        )

    def _normalize_path(self, path: str) -> str:
        return _norm(path, self._project_root)

    def _resolve_path(self, normalized: str) -> Path:
        return _resolve(normalized, self._project_root)

    def _load_or_rebuild_index(self) -> Any:
        return _se.load_or_rebuild_index(self.storage)

    def _save_index(self) -> None:
        from stele.index_store import compute_chunk_ids_hash, save_index

        save_index(
            self.vector_index,
            compute_chunk_ids_hash(self.storage),
            self.storage.index_dir,
        )

    def _ensure_bm25(self) -> None:
        _se.ensure_bm25(
            self.storage,
            self._bm25_init_lock,
            lambda: (self._bm25_ready, self.bm25_index),
            self._set_bm25,
        )

    def _set_bm25(self, idx: Any, ready: bool) -> None:
        self.bm25_index = idx
        self._bm25_ready = ready

    def _save_bm25(self) -> None:
        _se.save_bm25(self.bm25_index, self._bm25_ready, self.storage)

    def detect_modality(self, file_path: str) -> str:
        return _ix.detect_modality(file_path, self.chunkers)

    # -- Indexing (delegated to stele.indexing) --------------------------------

    def index_documents(
        self,
        paths: list[str],
        force_reindex: bool = False,
        agent_id: str | None = None,
        expected_versions: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Index documents through modality-specific chunkers."""
        with self._lock.write_lock():
            return _ix.index_documents_unlocked(
                paths,
                force_reindex,
                agent_id,
                expected_versions,
                expand_paths=lambda ps: _ix.expand_paths(
                    ps, self.chunkers, self.skip_dirs, self._normalize_path
                ),
                normalize_path=self._normalize_path,
                resolve_path=self._resolve_path,
                detect_modality=self.detect_modality,
                read_and_hash=_read_and_hash,
                storage=self.storage,
                chunkers=self.chunkers,
                vector_index=self.vector_index,
                bm25_index=self.bm25_index,
                bm25_ready=self._bm25_ready,
                symbol_manager=self.symbol_manager,
                merge_threshold=self.merge_threshold,
                max_chunk_size=self.max_chunk_size,
                modality_thresholds=self.MODALITY_THRESHOLDS,
                do_get_lock_status=self._do_get_lock_status,
                do_acquire_lock=self._do_acquire_lock,
                do_record_conflict=self._do_record_conflict,
                save_index=self._save_index,
                save_bm25=self._save_bm25,
                coordination=self._coordination,
            )

    def remove_document(
        self, document_path: str, agent_id: str | None = None
    ) -> dict[str, Any]:
        """Remove a document and its chunks, annotations, and index entries."""
        with self._lock.write_lock():
            return _ix.remove_document_unlocked(
                document_path,
                agent_id,
                normalize_path=self._normalize_path,
                do_get_lock_status=self._do_get_lock_status,
                do_record_conflict=self._do_record_conflict,
                storage=self.storage,
                vector_index=self.vector_index,
                bm25_index=self.bm25_index,
                bm25_ready=self._bm25_ready,
                save_index=self._save_index,
                save_bm25=self._save_bm25,
                coordination=self._coordination,
            )

    # -- Annotations -----------------------------------------------------------

    def annotate(
        self,
        target: str,
        target_type: str,
        content: str,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return _ix.annotate_unlocked(
                target, target_type, content, tags, self.storage, self._normalize_path
            )

    def get_annotations(
        self,
        target: str | None = None,
        target_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            if target is not None and target_type == "document":
                target = self._normalize_path(target)
            return self.storage.get_annotations(target, target_type, tags)

    def delete_annotation(self, annotation_id: int) -> dict[str, Any]:
        with self._lock.write_lock():
            return {
                "deleted": self.storage.delete_annotation(annotation_id),
                "id": annotation_id,
            }

    def update_annotation(
        self,
        annotation_id: int,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return {
                "updated": self.storage.update_annotation(annotation_id, content, tags),
                "id": annotation_id,
            }

    def search_annotations(
        self, query: str, target_type: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            return self.storage.search_annotations(query, target_type)

    def bulk_annotate(self, annotations: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock.write_lock():
            results: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            for e in annotations:
                r = _ix.annotate_unlocked(
                    e["target"],
                    e["target_type"],
                    e["content"],
                    e.get("tags"),
                    self.storage,
                    self._normalize_path,
                )
                (errors if "error" in r else results).append(
                    {**e, "error": r["error"]} if "error" in r else r
                )
            return {"created": results, "errors": errors}

    # -- History, map, stats ---------------------------------------------------

    def prune_history(
        self, max_age_seconds: float | None = None, max_entries: int | None = None
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return {"pruned": self.storage.prune_history(max_age_seconds, max_entries)}

    def get_map(self) -> dict[str, Any]:
        with self._lock.read_lock():
            return _se.get_map_unlocked(self.storage)

    def get_history(
        self, limit: int = 20, document_path: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            return self.storage.get_change_history(limit, document_path)

    def get_chunk_history(
        self,
        chunk_id: str | None = None,
        document_path: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            return self.storage.get_chunk_history(chunk_id, document_path, limit)

    _STAT_KEYS = (
        "chunk_size",
        "max_chunk_size",
        "merge_threshold",
        "change_threshold",
        "search_alpha",
    )

    def get_stats(self) -> dict[str, Any]:
        with self._lock.read_lock():
            return _se.get_stats_unlocked(
                self.storage,
                self.vector_index,
                {k: getattr(self, k) for k in self._STAT_KEYS},
            )

    def search_text(
        self,
        pattern: str,
        regex: bool = False,
        document_path: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search chunk content by exact substring or regex pattern.

        Perfect recall for literal patterns. Complements semantic search
        for cases where exact text matching is needed (e.g., finding all
        usages of a specific identifier before renaming).
        """
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            matches = self.storage.search_text(
                pattern, regex=regex, document_path=document_path, limit=limit
            )
            return {
                "pattern": pattern,
                "regex": regex,
                "match_count": sum(m["match_count"] for m in matches),
                "chunk_count": len(matches),
                "results": matches,
            }

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            return self.storage.list_sessions(agent_id=agent_id)

    # -- Agent embeddings ------------------------------------------------------

    def store_semantic_summary(self, chunk_id: str, summary: str) -> dict[str, Any]:
        with self._lock.write_lock():
            return _se.store_semantic_summary_unlocked(
                chunk_id, summary, self.storage, self.vector_index, self._save_index
            )

    def store_embedding(self, chunk_id: str, vector: list[float]) -> dict[str, Any]:
        with self._lock.write_lock():
            return _se.store_embedding_unlocked(
                chunk_id, vector, self.storage, self.vector_index, self._save_index
            )

    # -- Change detection (delegated to stele.change_detection) ----------------

    def detect_changes_and_update(
        self,
        session_id: str,
        document_paths: list[str] | None = None,
        reason: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return _cd.detect_changes_unlocked(
                session_id,
                document_paths,
                reason,
                agent_id,
                normalize_path=self._normalize_path,
                resolve_path=self._resolve_path,
                detect_modality=self.detect_modality,
                read_and_hash=_read_and_hash,
                storage=self.storage,
                chunkers=self.chunkers,
                vector_index=self.vector_index,
                bm25_index=self.bm25_index,
                bm25_ready=self._bm25_ready,
                symbol_manager=self.symbol_manager,
                merge_threshold=self.merge_threshold,
                max_chunk_size=self.max_chunk_size,
                change_threshold=self.change_threshold,
                modality_thresholds=self.MODALITY_THRESHOLDS,
                do_get_lock_status=self._do_get_lock_status,
                do_record_conflict=self._do_record_conflict,
                save_index=self._save_index,
                save_bm25=self._save_bm25,
                coordination=self._coordination,
            )

    # -- Search (delegated to stele.search_engine) ----------------------------

    def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            return _se.search_unlocked(
                query,
                top_k,
                vector_index=self.vector_index,
                storage=self.storage,
                get_bm25=lambda: self.bm25_index,
                search_alpha=self.search_alpha,
                symbol_manager=self.symbol_manager,
                do_ensure_bm25=self._ensure_bm25,
            )

    def get_context(self, document_paths: list[str]) -> dict[str, Any]:
        with self._lock.read_lock():
            return _se.get_context_unlocked(
                document_paths,
                normalize_path=self._normalize_path,
                resolve_path=self._resolve_path,
                detect_modality=self.detect_modality,
                read_and_hash=_read_and_hash,
                storage=self.storage,
            )

    # -- Session ops (delegated to SessionManager) ----------------------------

    def get_relevant_kv(
        self, session_id: str, query: str, top_k: int = 10
    ) -> dict[str, Any]:
        with self._lock.read_lock():
            return self.session_manager.get_relevant_chunks(session_id, query, top_k)

    def save_kv_state(
        self,
        session_id: str,
        kv_data: dict[str, Any],
        chunk_ids: list[str] | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            if agent_id is not None:
                self.storage.create_session(session_id, agent_id=agent_id)
            return self.session_manager.save_state(session_id, kv_data, chunk_ids)

    save_state = save_kv_state

    def rollback(self, session_id: str, target_turn: int) -> dict[str, Any]:
        with self._lock.write_lock():
            return self.session_manager.rollback(session_id, target_turn)

    def prune_chunks(self, session_id: str, max_tokens: int) -> dict[str, Any]:
        with self._lock.write_lock():
            return self.session_manager.prune(session_id, max_tokens)

    # -- Symbol graph (delegated to SymbolGraphManager) ----------------------

    def stale_chunks(self, threshold: float = 0.3) -> dict[str, Any]:
        with self._lock.read_lock():
            return self.symbol_manager.stale_chunks(threshold)

    def find_references(self, symbol: str) -> dict[str, Any]:
        with self._lock.read_lock():
            return self.symbol_manager.find_references(symbol)

    def find_definition(self, symbol: str) -> dict[str, Any]:
        with self._lock.read_lock():
            return self.symbol_manager.find_definition(symbol)

    def impact_radius(self, chunk_id: str, depth: int = 2) -> dict[str, Any]:
        with self._lock.read_lock():
            return self.symbol_manager.impact_radius(chunk_id, depth)

    def rebuild_symbol_graph(self) -> dict[str, Any]:
        with self._lock.write_lock():
            return self.symbol_manager.rebuild_graph()

    # -- Document ownership & conflict prevention -----------------------------

    def acquire_document_lock(
        self, document_path: str, agent_id: str, ttl: float = 300.0, force: bool = False
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return self._do_acquire_lock(
                self._normalize_path(document_path), agent_id, ttl, force
            )

    def refresh_document_lock(
        self, document_path: str, agent_id: str, ttl: float | None = None
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            dp = self._normalize_path(document_path)
            if self._coordination:
                return self._coordination.refresh_lock(dp, agent_id, ttl)
            return self.storage.refresh_document_lock(dp, agent_id, ttl)

    def release_document_lock(
        self, document_path: str, agent_id: str
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return self._do_release_lock(self._normalize_path(document_path), agent_id)

    def get_document_lock_status(self, document_path: str) -> dict[str, Any]:
        with self._lock.read_lock():
            return self._do_get_lock_status(self._normalize_path(document_path))

    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        with self._lock.write_lock():
            if self._coordination:
                return self._coordination.release_agent_locks(agent_id)
            return self.storage.release_agent_locks(agent_id)

    def reap_expired_locks(self) -> dict[str, Any]:
        with self._lock.write_lock():
            if self._coordination:
                return self._coordination.reap_expired_locks()
            return self.storage.reap_expired_locks()

    def get_conflicts(
        self,
        document_path: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            if self._coordination:
                return self._coordination.get_conflicts(document_path, agent_id, limit)
            return self.storage.get_conflicts(document_path, agent_id, limit)

    # -- Agent coordination ---------------------------------------------------

    def register_agent(self, agent_id: str) -> dict[str, Any]:
        if not self._coordination:
            return {"registered": False, "reason": "no_coordination"}
        root = str(self._project_root) if self._project_root else ""
        return self._coordination.register_agent(agent_id, root)

    def deregister_agent(self, agent_id: str) -> dict[str, Any]:
        if not self._coordination:
            return {"deregistered": False, "reason": "no_coordination"}
        return self._coordination.deregister_agent(agent_id)

    def heartbeat(self, agent_id: str) -> dict[str, Any]:
        if not self._coordination:
            return {"updated": False}
        return self._coordination.heartbeat(agent_id)

    def list_agents(self, active_only: bool = True) -> list[dict[str, Any]]:
        if not self._coordination:
            return []
        return self._coordination.list_agents(active_only=active_only)

    def get_notifications(
        self,
        since: float | None = None,
        exclude_self: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not self._coordination:
            return {"notifications": [], "count": 0, "latest_timestamp": 0.0}
        return self._coordination.get_notifications(
            since=since, exclude_agent=exclude_self, limit=limit
        )

    # -- Environment checks ---------------------------------------------------

    def check_environment(self) -> dict[str, Any]:
        return check_environment_impl(self._project_root, self.skip_dirs)

    def clean_bytecache(self) -> dict[str, Any]:
        if not self._project_root:
            return {"cleaned": 0}
        from stele.env_checks import clean_stale_pycache

        return clean_stale_pycache(self._project_root, self.skip_dirs - {"__pycache__"})
