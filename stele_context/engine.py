"""
Stele engine -- smart context cache with semantic chunking and vector search.
"""

from __future__ import annotations

__all__ = ["Stele"]

import os
import threading
from pathlib import Path
from typing import Any

from stele_context.config import load_config, apply_config
from stele_context.engine_index_mixin import _IndexMixin
from stele_context.engine_info_mixin import _InfoMixin
from stele_context.engine_lock_mixin import _LockMixin
from stele_context.engine_search_mixin import _SearchMixin
from stele_context.engine_symbol_mixin import _SymbolMixin
from stele_context.engine_utils import (
    normalize_path as _norm,
    resolve_path as _resolve,
    detect_project_root,
    do_acquire_lock,
    do_get_lock_status,
    do_release_lock,
    do_record_conflict,
    init_coordination,
)
from stele_context.rwlock import RWLock
from stele_context.symbol_graph import SymbolGraphManager
from stele_context.session import SessionManager
from stele_context.storage import StorageBackend

from stele_context import indexing as _ix  # used by query() dispatcher
from stele_context import search_engine as _se  # used by query() dispatcher


class Stele(_IndexMixin, _InfoMixin, _SearchMixin, _SymbolMixin, _LockMixin):
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

    # index_documents, _apply_inline_summaries, remove_document, annotation CRUD
    # (annotate, get_annotations, delete_annotation, update_annotation,
    # search_annotations, bulk_annotate, unified annotations dispatcher),
    # prune_history, Tier-2 writes (store_semantic_summary, store_embedding,
    # bulk_store_embeddings, llm_embed, bulk_store_summaries,
    # store_chunk_agent_notes, bulk_store_chunk_agent_notes),
    # and detect_changes_and_update all live in engine_index_mixin._IndexMixin

    _STAT_KEYS = (
        "chunk_size",
        "max_chunk_size",
        "merge_threshold",
        "change_threshold",
        "search_alpha",
    )
    # get_map / get_history / get_chunk_history / get_stats live in engine_info_mixin._InfoMixin

    # _index_working_tree, _git_working_tree_is_dirty, _recent_files_path_prefix,
    # search_text, agent_grep live in engine_search_mixin._SearchMixin

    # list_sessions lives in engine_info_mixin._InfoMixin

    # search, get_context, get_search_history, get_session_read_files live in
    # engine_search_mixin._SearchMixin

    # get_project_brief / doctor_snapshot live in engine_info_mixin._InfoMixin

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

    def stale_chunks(
        self, threshold: float = 0.3, max_age_seconds: float | None = None
    ) -> dict[str, Any]:
        with self._lock.read_lock():
            return self.symbol_manager.stale_chunks(threshold, max_age_seconds)

    # find_references, find_definition, impact_radius, coupling, rebuild_symbol_graph,
    # register_dynamic_symbols, remove_dynamic_symbols, get_dynamic_symbols live in
    # engine_symbol_mixin._SymbolMixin

    # document_lock (unified) lives in engine_lock_mixin._LockMixin

    # annotations (unified dispatcher) lives in engine_index_mixin._IndexMixin

    # -- Composite query ------------------------------------------------------

    def query(
        self,
        query: str,
        top_k: int = 10,
        path_prefix: str | None = None,
        working_tree: bool = False,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Composite retrieval that merges symbol, semantic, and text search.

        Runs in parallel:
          1. Semantic search (`search`)
          2. Symbol lookup (`find_references` / `find_definition` for extracted identifiers)
          3. Text grep (`agent_grep`) for high-signal matches

        Returns deduplicated chunks with source provenance.
        """
        from stele_context.search_engine import extract_query_identifiers

        # Smart default: auto-enable working_tree when session_id is given and tree is dirty
        if not working_tree and session_id:
            working_tree = self._git_working_tree_is_dirty()

        # Smart default: restrict large projects unless query asks for global scope
        if path_prefix is None:
            doc_count = self.storage.get_document_count()
            if doc_count > 500:
                global_keywords = {
                    "everywhere",
                    "all files",
                    "project-wide",
                    "entire project",
                }
                if not any(kw in query.lower() for kw in global_keywords):
                    path_prefix = self._recent_files_path_prefix()

        if working_tree:
            self._index_working_tree(agent_id=session_id)

        results: list[dict[str, Any]] = []
        seen_chunks: set[str] = set()
        errors: list[str] = []

        # 1. Semantic search
        try:
            semantic = self.search(
                query,
                top_k=top_k,
                path_prefix=path_prefix,
                compact=True,
            )
            if isinstance(semantic, dict):
                semantic = semantic.get("results", [])
            for r in semantic:
                cid = r.get("chunk_id")
                if cid and cid not in seen_chunks:
                    seen_chunks.add(cid)
                    results.append({**r, "source": "semantic_search"})
        except Exception as e:
            errors.append(f"semantic_search: {e}")

        # 2. Symbol lookups for identifiers in the query
        idents = extract_query_identifiers(query)
        for ident in idents[:3]:  # Limit to top 3 to avoid explosion
            try:
                refs = self.find_references(ident)
                for ref in refs.get("definitions", []):
                    cid = ref.get("chunk_id")
                    if cid and cid not in seen_chunks:
                        seen_chunks.add(cid)
                        chunk = self.storage.get_chunk(cid)
                        if chunk:
                            results.append(
                                {
                                    "chunk_id": cid,
                                    "document_path": ref.get("document_path", ""),
                                    "content": (chunk.get("content") or "")[:300],
                                    "source": "symbol_graph",
                                    "symbol": ident,
                                }
                            )
                for ref in refs.get("references", []):
                    cid = ref.get("chunk_id")
                    if cid and cid not in seen_chunks:
                        seen_chunks.add(cid)
                        chunk = self.storage.get_chunk(cid)
                        if chunk:
                            results.append(
                                {
                                    "chunk_id": cid,
                                    "document_path": ref.get("document_path", ""),
                                    "content": (chunk.get("content") or "")[:300],
                                    "source": "symbol_graph",
                                    "symbol": ident,
                                }
                            )
            except Exception as e:
                errors.append(f"symbol_graph({ident}): {e}")

        # 3. Text grep for the full query phrase (agent-friendly, token-capped)
        try:
            grep_res = self.agent_grep(
                pattern=query,
                max_tokens=2000,
                session_id=None,
            )
            # agent_grep returns groups, not results; matches have 'file' not 'chunk_id'
            for group in grep_res.get("groups", []):
                for match in group.get("matches", []):
                    file_path = match.get("file", "")
                    if not file_path:
                        continue
                    # Find the chunk for this file/line
                    chunk_meta = self._chunk_for_line(file_path, match.get("line", 0))
                    cid = chunk_meta.get("chunk_id") if chunk_meta else None
                    if cid and cid not in seen_chunks:
                        seen_chunks.add(cid)
                        results.append(
                            {
                                "chunk_id": cid,
                                "document_path": file_path,
                                "content": match.get("excerpt", "")[:300],
                                "source": "text_match",
                            }
                        )
        except Exception as e:
            errors.append(f"text_match: {e}")

        out: dict[str, Any] = {
            "query": query,
            "results": results[:top_k],
            "sources": {
                "semantic_search": sum(
                    1 for r in results if r.get("source") == "semantic_search"
                ),
                "symbol_graph": sum(
                    1 for r in results if r.get("source") == "symbol_graph"
                ),
                "text_match": sum(
                    1 for r in results if r.get("source") == "text_match"
                ),
            },
        }
        if errors:
            out["errors"] = errors
        return out

    def _chunk_for_line(
        self, document_path: str, line_number: int
    ) -> dict[str, Any] | None:
        """Find the chunk that contains the given line in a document."""
        chunks = self.storage.get_document_chunks(document_path)
        if not chunks:
            return None
        # Compute base lines for each chunk
        cumulative = 1
        for chunk in sorted(chunks, key=lambda c: c.get("start_pos", 0)):
            content = chunk.get("content") or ""
            newlines = content.count("\n")
            end_line = cumulative + newlines + (0 if content.endswith("\n") else 1)
            if cumulative <= line_number < end_line:
                return chunk
            cumulative = end_line
        # Fallback: last chunk
        return chunks[-1] if chunks else None

    # -- Batch operations -----------------------------------------------------

    def batch(
        self,
        operations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute a sequence of engine operations in one round-trip.

        Each operation: {"method": "index_documents", "params": {"paths": [...]}}
        Unknown methods or errors are captured and the batch continues.

        Note: individual engine methods manage their own locking, so operations
        are sequential but not wrapped in a single global lock.
        """
        results: list[dict[str, Any]] = []
        for idx, op in enumerate(operations):
            method = op.get("method", "")
            params = op.get("params", {})
            func = getattr(self, method, None)
            if not callable(func):
                results.append(
                    {
                        "index": idx,
                        "method": method,
                        "error": f"Unknown method: {method}",
                    }
                )
                continue
            try:
                result = func(**params)
                results.append(
                    {
                        "index": idx,
                        "method": method,
                        "success": True,
                        "result": result,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "index": idx,
                        "method": method,
                        "error": str(e),
                    }
                )
        return {"operations": results, "total": len(operations)}

    # acquire/refresh/release/get_status document_lock methods + release_agent_locks,
    # reap_expired_locks, get_conflicts, register_agent, deregister_agent, heartbeat,
    # list_agents, get_notifications live in engine_lock_mixin._LockMixin

    # check_environment / clean_bytecache live in engine_info_mixin._InfoMixin
