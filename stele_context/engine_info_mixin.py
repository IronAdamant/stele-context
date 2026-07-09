"""
Engine mixin — information and health methods.

Contains the read-only inspection and health-check methods of the `Stele`
facade: `get_map`, `get_history`, `get_chunk_history`, `get_stats`,
`get_project_brief`, `doctor_snapshot`, `list_sessions`, `check_environment`,
`clean_bytecache`.

**Inclusion criterion:** a method belongs here if it returns a read-only view
of storage/index state or runs an environment check. Mutating methods, search
methods, and symbol-graph methods live in their own mixins.

This module is a namespace for methods that operate on a `Stele` instance's
`self`. It relies on these attributes being present (set by `Stele.__init__`):
`_lock`, `storage`, `vector_index`, `_project_root`, `skip_dirs`, `_STAT_KEYS`,
and the private helpers `_normalize_path`. Zero new imports beyond what the
extracted methods already used.
"""

from __future__ import annotations

import sys
from typing import Any

from stele_context import search_engine as _se
from stele_context.agent_guidance import (
    assemble_doctor_guidance,
    build_enrichment_plan,
)
from stele_context.engine_utils import check_environment_impl


class _InfoMixin:
    """Read-only inspection + health methods for `Stele`."""

    # Attributes/methods provided by Stele.__init__ or other mixins.
    # Declared for mypy; kept as Any to avoid import cycles.
    _lock: Any
    storage: Any
    vector_index: Any
    _project_root: Any
    skip_dirs: Any
    _STAT_KEYS: Any
    _normalize_path: Any

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

    def get_project_brief(self, top_n: int = 40) -> dict[str, Any]:
        with self._lock.read_lock():
            data = _se.get_project_brief_unlocked(self.storage, top_n=top_n)
        data["project_root"] = (
            str(self._project_root) if self._project_root is not None else None
        )
        return data

    def doctor_snapshot(self) -> dict[str, Any]:
        """One-screen orientation: version, storage, health, env issues, map preview.

        Also includes agent guidance: next_steps, recommended MCP lite mode,
        token_savings estimate, and enrichment_preview for Tier-2 bootstrap.
        """
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
            search_quality = _se.get_search_quality_snapshot(
                self.storage, self.vector_index
            )
            # Metadata-only path — do not load full content for every chunk.
            light_chunks = self.storage.list_chunk_metadata(
                include_preview=True, preview_chars=120
            )
            enrich_plan = build_enrichment_plan(light_chunks, top_n=15)
        env = self.check_environment()
        stats["project_root"] = (
            str(self._project_root) if self._project_root is not None else None
        )
        m["project_root"] = stats["project_root"]
        storage = stats.get("storage") or {}
        doc_count = storage.get("document_count") or 0
        chunk_count = storage.get("chunk_count") or 0
        total_tokens = storage.get("total_tokens") or 0
        index_health = stats.get("index_health") or {}
        guidance = assemble_doctor_guidance(
            document_count=int(doc_count),
            chunk_count=int(chunk_count),
            symbol_rows=int(index_health.get("symbol_rows") or 0),
            total_indexed_tokens=int(total_tokens),
            tier2_coverage_percent=float(
                search_quality.get("tier2_coverage_percent") or 0.0
            ),
            seconds_since_last_index=index_health.get("seconds_since_last_index"),
            index_alerts=list(index_health.get("alerts") or []),
            enrichment_plan=enrich_plan,
        )
        return {
            "stele_version": stats.get("version"),
            "python": sys.version.split()[0],
            "project_root": stats["project_root"],
            "storage_dir": storage.get("storage_dir"),
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "index_health": index_health,
            "db_health": self.storage.get_db_health_snapshot(),
            "search_quality": search_quality,
            "environment": env,
            "map_preview": m,
            **guidance,
        }

    def enrichment_plan(self, top_n: int = 15, min_tokens: int = 20) -> dict[str, Any]:
        """Return hot chunks missing Tier-2 summaries for agent enrichment.

        Candidates are ranked by token mass among chunks without
        ``agent_signature`` / ``semantic_summary``. Use with
        ``bulk_store_summaries`` after writing short purpose summaries.
        """
        with self._lock.read_lock():
            chunks = self.storage.list_chunk_metadata(
                include_preview=True, preview_chars=120
            )
            return build_enrichment_plan(chunks, top_n=top_n, min_tokens=min_tokens)

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            return self.storage.list_sessions(agent_id=agent_id)

    def check_environment(self) -> dict[str, Any]:
        return check_environment_impl(self._project_root, self.skip_dirs)

    def clean_bytecache(self) -> dict[str, Any]:
        if not self._project_root:
            return {"cleaned": 0}
        from stele_context.env_checks import clean_stale_pycache

        return clean_stale_pycache(self._project_root, self.skip_dirs - {"__pycache__"})
