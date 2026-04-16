"""
Engine mixin — indexing, change detection, annotations, and Tier-2 writes.

Contains the mutating methods of the `Stele` facade that create or modify
chunks, documents, annotations, and agent-supplied semantic signatures:
`index_documents`, `detect_changes_and_update`, `remove_document`,
the annotation CRUD (`annotate`, `get_annotations`, `delete_annotation`,
`update_annotation`, `search_annotations`, `bulk_annotate`, plus the
unified `annotations` dispatcher), `prune_history`, and Tier-2 writes
(`store_semantic_summary`, `store_embedding`, `bulk_store_embeddings`,
`llm_embed`, `bulk_store_summaries`, `store_chunk_agent_notes`,
`bulk_store_chunk_agent_notes`).

**Inclusion criterion:** a method belongs here if it creates or modifies
persisted index state (chunks, documents, annotations, agent signatures).
Read paths live in `_SearchMixin` / `_InfoMixin`; symbol-graph queries
in `_SymbolMixin`; locks in `_LockMixin`.

Relies on `self._lock`, `self.storage`, `self.vector_index`,
`self.bm25_index`, `self._bm25_ready`, `self.chunkers`, `self.skip_dirs`,
`self.symbol_manager`, `self.merge_threshold`, `self.max_chunk_size`,
`self.change_threshold`, `self.MODALITY_THRESHOLDS`, `self._coordination`,
`self._normalize_path`, `self._resolve_path`, `self.detect_modality`,
`self._do_acquire_lock`, `self._do_get_lock_status`,
`self._do_record_conflict`, `self._save_index`, `self._save_bm25`,
`self._project_root` being provided by `Stele.__init__`.
"""

from __future__ import annotations

from typing import Any

from stele_context import change_detection as _cd
from stele_context import indexing as _ix
from stele_context import search_engine as _se
from stele_context.engine_utils import read_and_hash as _read_and_hash


class _IndexMixin:
    """Indexing, change detection, annotations, and Tier-2 writes for `Stele`."""

    # Attributes/methods provided by Stele.__init__ or other mixins.
    # Declared for mypy; kept as Any to avoid import cycles.
    _lock: Any
    storage: Any
    vector_index: Any
    bm25_index: Any
    _bm25_ready: Any
    chunkers: Any
    skip_dirs: Any
    symbol_manager: Any
    merge_threshold: Any
    max_chunk_size: Any
    change_threshold: Any
    MODALITY_THRESHOLDS: Any
    _coordination: Any
    _project_root: Any
    _normalize_path: Any
    _resolve_path: Any
    detect_modality: Any
    _do_acquire_lock: Any
    _do_get_lock_status: Any
    _do_record_conflict: Any
    _save_index: Any
    _save_bm25: Any

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

    def annotations(
        self,
        action: str,
        target: str | None = None,
        target_type: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        annotation_id: int | None = None,
        query: str | None = None,
        items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Unified annotation lifecycle tool.

        Actions:
          - create: create a single annotation (requires target, target_type, content)
          - get: retrieve annotations (optionally filtered by target, target_type, tags)
          - delete: delete by annotation_id
          - update: update by annotation_id (content and/or tags)
          - search: search annotation content by substring query
          - bulk_create: create many annotations at once (requires items)
        """
        action = action.lower()
        if action == "create":
            if not target or not target_type or content is None:
                return {"error": "create requires target, target_type, and content"}
            return self.annotate(target, target_type, content, tags)
        if action == "get":
            return {"annotations": self.get_annotations(target, target_type, tags)}
        if action == "delete":
            if annotation_id is None:
                return {"error": "delete requires annotation_id"}
            return self.delete_annotation(annotation_id)
        if action == "update":
            if annotation_id is None:
                return {"error": "update requires annotation_id"}
            return self.update_annotation(annotation_id, content, tags)
        if action == "search":
            if not query:
                return {"error": "search requires query"}
            return {"annotations": self.search_annotations(query, target_type)}
        if action == "bulk_create":
            if not items:
                return {"error": "bulk_create requires items"}
            return self.bulk_annotate(items)
        return {"error": f"Unknown action: {action}"}

    # -- History / pruning ----------------------------------------------------

    def prune_history(
        self, max_age_seconds: float | None = None, max_entries: int | None = None
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return {"pruned": self.storage.prune_history(max_age_seconds, max_entries)}

    # -- Tier 2 agent-supplied embeddings -------------------------------------

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

    def bulk_store_embeddings(
        self, embeddings: dict[str, list[float]]
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return _se.bulk_store_embeddings_unlocked(
                embeddings, self.storage, self.vector_index, self._save_index
            )

    def llm_embed(
        self,
        text: str,
        chunk_id: str,
        fingerprint_values: list[float] | None = None,
        agent_id: str | None = None,
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

    def store_chunk_agent_notes(
        self, chunk_id: str, notes: str | None
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            ok = self.storage.store_chunk_agent_notes(chunk_id, notes)
            return {"stored": ok, "chunk_id": chunk_id}

    def bulk_store_chunk_agent_notes(self, notes: dict[str, str]) -> dict[str, Any]:
        with self._lock.write_lock():
            return self.storage.bulk_store_chunk_agent_notes(notes)

    # -- Change detection (delegated to stele_context.change_detection) --------

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
