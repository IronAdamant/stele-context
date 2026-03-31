"""
Stele engine -- smart context cache with semantic chunking and vector search.
"""

from __future__ import annotations

__all__ = ["Stele"]

import hashlib
import os
import threading
from pathlib import Path
from typing import Any

from stele_context.config import load_config, apply_config
from stele_context.engine_utils import (
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
from stele_context.rwlock import RWLock
from stele_context.symbol_graph import SymbolGraphManager
from stele_context.session import SessionManager
from stele_context.storage import StorageBackend

from stele_context import indexing as _ix
from stele_context import search_engine as _se
from stele_context import change_detection as _cd


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
    DEFAULT_SEARCH_ALPHA = 0.42
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
            resolved_storage = os.environ.get("STELE_CONTEXT_STORAGE_DIR")
        if resolved_storage is None and self._project_root is not None:
            resolved_storage = str(self._project_root / ".stele-context")
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

    def _save_index(self) -> None:
        from stele_context.index_store import compute_chunk_ids_hash, save_index

        save_index(
            self.vector_index,
            compute_chunk_ids_hash(self.storage),
            self.storage.index_dir,
        )

    def _ensure_bm25(self) -> None:
        def _set(idx: Any, ready: bool) -> None:
            self.bm25_index = idx
            self._bm25_ready = ready

        _se.ensure_bm25(
            self.storage,
            self._bm25_init_lock,
            lambda: (self._bm25_ready, self.bm25_index),
            _set,
        )

    def _save_bm25(self) -> None:
        _se.save_bm25(self.bm25_index, self._bm25_ready, self.storage)

    def detect_modality(self, file_path: str) -> str:
        return _ix.detect_modality(file_path, self.chunkers)

    # -- Indexing (delegated to stele_context.indexing) --------------------------------

    def index_documents(
        self,
        paths: list[str],
        force_reindex: bool = False,
        agent_id: str | None = None,
        expected_versions: dict[str, int] | None = None,
        summaries: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Index documents through modality-specific chunkers.

        Args:
            summaries: Optional mapping of file path to semantic summary.
                When provided, all chunks from a file receive the summary
                as an agent-supplied signature (Tier 2), improving search
                relevance. Applied in the same write lock as indexing.
        """
        with self._lock.write_lock():
            result = _ix.index_documents_unlocked(
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
            if summaries:
                if result["indexed"]:
                    self._apply_inline_summaries(summaries, result)
                else:
                    result["summaries_applied"] = 0
            return result

    def _apply_inline_summaries(
        self, summaries: dict[str, str], result: dict[str, Any]
    ) -> None:
        """Apply document-level summaries to all chunks of indexed files.

        Called inside the write lock after index_documents_unlocked.
        """
        from stele_context.chunkers.numpy_compat import sig_to_list

        normalized = {self._normalize_path(k): v for k, v in summaries.items()}
        applied = 0
        for doc_info in result["indexed"]:
            doc_path = doc_info["path"]
            summary_text = normalized.get(doc_path)
            if not summary_text:
                continue
            sig = _se._text_signature(summary_text)
            chunks = self.storage.get_document_chunks(doc_path)
            chunk_ids = [c["chunk_id"] for c in chunks]
            if not chunk_ids:
                continue
            self.storage.bulk_update_summaries(chunk_ids, summary_text, sig)
            for cid in chunk_ids:
                self.vector_index.remove_chunk(cid)
                self.vector_index.add_chunk(cid, sig_to_list(sig))
            applied += len(chunk_ids)
        if applied:
            self._save_index()
        result["summaries_applied"] = applied

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

    def get_map(
        self,
        *,
        compact: bool = False,
        max_documents: int | None = None,
        max_annotation_chars: int = 200,
        path_prefix: str | None = None,
    ) -> dict[str, Any]:
        if path_prefix is not None:
            path_prefix = self._normalize_path(path_prefix)
        with self._lock.read_lock():
            data = _se.get_map_unlocked(
                self.storage,
                compact=compact,
                max_documents=max_documents,
                max_annotation_chars=max_annotation_chars,
                path_prefix=path_prefix,
            )
        data["project_root"] = (
            str(self._project_root) if self._project_root is not None else None
        )
        return data

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

    def get_stats(self, *, compact: bool = False) -> dict[str, Any]:
        with self._lock.read_lock():
            data = _se.get_stats_unlocked(
                self.storage,
                self.vector_index,
                {k: getattr(self, k) for k in self._STAT_KEYS},
                compact=compact,
            )
        data["project_root"] = (
            str(self._project_root) if self._project_root is not None else None
        )
        return data

    def search_text(
        self,
        pattern: str,
        regex: bool = False,
        document_path: str | None = None,
        limit: int = 50,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Search chunk content by exact substring or regex pattern.

        Perfect recall for literal patterns. Complements semantic search
        for cases where exact text matching is needed (e.g., finding all
        usages of a specific identifier before renaming).
        When session_id is provided, records search history and auto-indexes
        files with matches.
        """
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            matches = self.storage.search_text(
                pattern, regex=regex, document_path=document_path, limit=limit
            )
            doc_paths = sorted({m["document_path"] for m in matches})
            result = {
                "pattern": pattern,
                "regex": regex,
                "match_count": sum(m["match_count"] for m in matches),
                "chunk_count": len(matches),
                "results": matches,
                "files_checked": len(doc_paths),
                "files_with_matches": doc_paths,
            }

        # Auto-index and record history outside the read lock
        if session_id:
            self.storage.record_search(
                session_id=session_id,
                pattern=pattern,
                tool="search_text",
                files_checked=[document_path] if document_path else doc_paths,
                files_with_matches=doc_paths,
            )
            if doc_paths:
                self.index_documents(doc_paths, agent_id=session_id)

        return result

    def agent_grep(
        self,
        pattern: str,
        regex: bool = False,
        document_path: str | None = None,
        classify: bool = True,
        include_scope: bool = True,
        group_by: str = "file",
        max_tokens: int = 4000,
        deduplicate: bool = True,
        context_lines: int = 0,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """LLM-optimized search: grep with scope, classification, token budget.

        See :func:`stele_context.agent_grep.agent_grep` for full docs.
        When session_id is provided, records search history and auto-indexes
        files with matches (no separate index call needed).
        """
        from stele_context.agent_grep import agent_grep as _agent_grep

        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            result = _agent_grep(
                self.storage,
                pattern,
                regex=regex,
                document_path=document_path,
                classify=classify,
                include_scope=include_scope,
                group_by=group_by,
                max_tokens=max_tokens,
                deduplicate=deduplicate,
                context_lines=context_lines,
                session_id=session_id,
                auto_index_func=None,  # deferred outside lock to avoid deadlock
            )

        # Auto-index files with matches after releasing the read lock
        if session_id and result.get("files_with_matches"):
            self.index_documents(result["files_with_matches"], agent_id=session_id)

        return result

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

    def llm_embed(
        self,
        text: str,
        chunk_id: str,
        fingerprint_values: list[float] | None = None,
    ) -> dict[str, Any]:
        """Generate and store a semantic embedding via LLM reasoning.

        If ``fingerprint_values`` is provided (32 floats, -1.0 to 1.0), those are
        used directly as the semantic fingerprint. Otherwise the statistical
        fallback ``semantic_fingerprint()`` is used.

        The fingerprint is converted to a 128-dim unit vector. If the chunk_id
        already exists (e.g. an indexed file chunk), the signature is updated.
        Otherwise a new memory chunk is created with the given content and vector.

        Args:
            text: Source text content (first 4000 chars are used).
            chunk_id: Unique identifier for this embedded content.
            fingerprint_values: Optional 32 semantic dimension scores from the
                LLM. Each value should be in [-1.0, 1.0].

        Returns:
            {"stored": True, "chunk_id": str} on success.
        """
        from stele_context.llm_embedding import (
            FINGERPRINT_NAMES,
            fingerprint_to_vector,
            semantic_fingerprint,
        )

        if fingerprint_values is not None:
            if len(fingerprint_values) != len(FINGERPRINT_NAMES):
                return {
                    "stored": False,
                    "error": f"Expected {len(FINGERPRINT_NAMES)} fingerprint values, "
                    f"got {len(fingerprint_values)}",
                }
            fp = dict(zip(FINGERPRINT_NAMES, fingerprint_values))
        else:
            fp = semantic_fingerprint(text[:4000])

        vector = fingerprint_to_vector(fp)
        with self._lock.write_lock():
            # Try existing chunk first (update signature in place)
            result = _se.store_embedding_unlocked(
                chunk_id, vector, self.storage, self.vector_index, self._save_index
            )
            if result.get("stored"):
                return result
            # Chunk doesn't exist — create a memory chunk with the content
            ok = self.storage.create_memory_chunk(
                chunk_id=chunk_id,
                content=text[:4000],
                agent_signature=vector,
            )
            if not ok:
                return {"stored": False, "error": "failed to create memory chunk"}
            # Add to HNSW index
            self.vector_index.remove_chunk(chunk_id)
            self.vector_index.add_chunk(chunk_id, vector)
            self._save_index()
            return {"stored": True, "chunk_id": chunk_id}

    def bulk_store_summaries(self, summaries: dict[str, str]) -> dict[str, Any]:
        """Batch-store per-chunk semantic summaries.

        Args:
            summaries: Mapping of chunk_id to semantic summary text.
                Each chunk gets its own signature computed from its summary.
        """
        with self._lock.write_lock():
            return _se.bulk_store_summaries_unlocked(
                summaries, self.storage, self.vector_index, self._save_index
            )

    # -- Change detection (delegated to stele_context.change_detection) ----------------

    def detect_changes_and_update(
        self,
        session_id: str,
        document_paths: list[str] | None = None,
        reason: str | None = None,
        agent_id: str | None = None,
        *,
        scan_new: bool = True,
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
                scan_new=scan_new,
                project_root=self._project_root,
                skip_dirs=self.skip_dirs,
            )

    # -- Search (delegated to stele_context.search_engine) ----------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        search_mode: str = "hybrid",
        max_result_tokens: int | None = None,
        compact: bool = False,
        return_response_meta: bool = False,
        path_prefix: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if path_prefix is not None:
            path_prefix = self._normalize_path(path_prefix)
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
                search_mode=search_mode,
                max_result_tokens=max_result_tokens,
                compact=compact,
                return_response_meta=return_response_meta,
                path_prefix=path_prefix,
            )

    def get_context(
        self,
        document_paths: list[str],
        *,
        session_id: str | None = None,
        include_trust: bool = True,
        max_chunk_content_tokens: int | None = None,
    ) -> dict[str, Any]:
        with self._lock.read_lock():
            result = _se.get_context_unlocked(
                document_paths,
                normalize_path=self._normalize_path,
                resolve_path=self._resolve_path,
                detect_modality=self.detect_modality,
                read_and_hash=_read_and_hash,
                storage=self.storage,
                include_trust=include_trust,
                max_chunk_content_tokens=max_chunk_content_tokens,
                session_id=session_id,
            )
        # Record file reads in session after releasing lock
        if session_id and result.get("unchanged"):
            for entry in result["unchanged"]:
                chunk_ids = [c["chunk_id"] for c in entry.get("chunks", [])]
                if chunk_ids:
                    self.storage.record_file_read(session_id, entry["path"], chunk_ids)
        return result

    def get_search_history(self, session_id: str) -> dict[str, Any]:
        """Return all searches recorded for this session."""
        searches = self.storage.get_search_history(session_id)
        return {"session_id": session_id, "searches": searches}

    def get_session_read_files(self, session_id: str) -> list[dict[str, Any]]:
        """Return all files fully read in this session."""
        return self.storage.get_session_read_files(session_id)

    def get_project_brief(self, top_n: int = 40) -> dict[str, Any]:
        with self._lock.read_lock():
            data = _se.get_project_brief_unlocked(self.storage, top_n=top_n)
        data["project_root"] = (
            str(self._project_root) if self._project_root is not None else None
        )
        return data

    def doctor_snapshot(self) -> dict[str, Any]:
        """One-screen orientation: version, storage, health, env issues, map preview."""
        import sys

        with self._lock.read_lock():
            stats = _se.get_stats_unlocked(
                self.storage,
                self.vector_index,
                {k: getattr(self, k) for k in self._STAT_KEYS},
                compact=True,
            )
            m = _se.get_map_unlocked(
                self.storage,
                compact=True,
                max_documents=8,
                max_annotation_chars=120,
            )
        env = self.check_environment()
        stats["project_root"] = (
            str(self._project_root) if self._project_root is not None else None
        )
        m["project_root"] = stats["project_root"]
        return {
            "stele_version": stats.get("version"),
            "python": sys.version.split()[0],
            "project_root": stats["project_root"],
            "storage_dir": (stats.get("storage") or {}).get("storage_dir"),
            "document_count": (stats.get("storage") or {}).get("document_count"),
            "chunk_count": (stats.get("storage") or {}).get("chunk_count"),
            "index_health": stats.get("index_health"),
            "environment": env,
            "map_preview": m,
        }

    def store_chunk_agent_notes(
        self, chunk_id: str, notes: str | None
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            ok = self.storage.store_chunk_agent_notes(chunk_id, notes)
            return {"stored": ok, "chunk_id": chunk_id}

    def bulk_store_chunk_agent_notes(self, notes: dict[str, str]) -> dict[str, Any]:
        with self._lock.write_lock():
            return self.storage.bulk_store_chunk_agent_notes(notes)

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

    def impact_radius(
        self,
        chunk_id: str | None = None,
        depth: int = 2,
        document_path: str | None = None,
        *,
        compact: bool = True,
        include_content: bool = True,
        path_filter: str | None = None,
        summary_mode: bool = False,
        top_n_files: int = 25,
    ) -> dict[str, Any]:
        if document_path:
            document_path = self._normalize_path(document_path)
        with self._lock.read_lock():
            return self.symbol_manager.impact_radius(
                chunk_id,
                depth,
                document_path,
                compact=compact,
                include_content=include_content,
                path_filter=path_filter,
                summary_mode=summary_mode,
                top_n_files=top_n_files,
            )

    def coupling(self, document_path: str) -> dict[str, Any]:
        document_path = self._normalize_path(document_path)
        with self._lock.read_lock():
            return self.symbol_manager.coupling(document_path)

    def rebuild_symbol_graph(self) -> dict[str, Any]:
        with self._lock.write_lock():
            return self.symbol_manager.rebuild_graph()

    def register_dynamic_symbols(
        self,
        symbols: list[dict[str, Any]],
        agent_id: str,
    ) -> dict[str, Any]:
        """Register runtime/dynamic symbols that don't correspond to indexed chunks.

        Use this for plugin hook registrations, runtime callbacks, and other
        symbols that only exist at runtime and are invisible to static analysis.

        Dynamic symbols appear in ``find_references``, ``coupling``, and
        ``impact_radius`` just like statically-extracted symbols, enabling the
        symbol graph to model dynamic registration patterns.

        Symbols are namespaced by agent_id in the storage layer
        (``runtime:{agent_id}:{name}``) and can be removed with
        ``remove_dynamic_symbols``.

        Args:
            symbols: List of dicts with keys: name (required), kind
                (default "function"), role (default "definition"),
                document_path (default ""), line_number (optional).
            agent_id: Agent registering these symbols (used for namespacing
                and later removal).

        Example::

            engine.register_dynamic_symbols(
                symbols=[
                    {"name": "on_recipe_validated", "kind": "function",
                     "document_path": "src/plugins/hooks.js"},
                    {"name": "dietary_check_hook", "kind": "function",
                     "role": "reference",
                     "document_path": "src/services/validator.js"},
                ],
                agent_id="my-agent-123",
            )
        """
        with self._lock.write_lock():
            result = self.storage.store_dynamic_symbols(symbols, agent_id)
            if result.get("stored"):
                # Rebuild edges so dynamic symbols are connected into the graph.
                self.symbol_manager.rebuild_edges()
            return result

    def remove_dynamic_symbols(self, agent_id: str) -> dict[str, Any]:
        """Remove all dynamic symbols previously registered by an agent.

        Returns count of removed symbols.
        """
        with self._lock.write_lock():
            result = self.storage.remove_dynamic_symbols(agent_id)
            if result.get("removed"):
                self.symbol_manager.rebuild_edges()
            return result

    def get_dynamic_symbols(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """List all registered dynamic/runtime symbols, optionally filtered."""
        with self._lock.read_lock():
            return self.storage.get_dynamic_symbols(agent_id)

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
        from stele_context.env_checks import clean_stale_pycache

        return clean_stale_pycache(self._project_root, self.skip_dirs - {"__pycache__"})
