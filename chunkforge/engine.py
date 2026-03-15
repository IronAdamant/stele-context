"""
ChunkForge engine — smart context cache with semantic chunking and vector search.
"""

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from chunkforge.chunkers.numpy_compat import (
    cosine_similarity,
    sig_to_list,
    sig_from_bytes,
)
from chunkforge.chunkers.base import Chunk
from chunkforge.chunkers import (
    TextChunker,
    CodeChunker,
    HAS_IMAGE_CHUNKER,
    HAS_PDF_CHUNKER,
    HAS_AUDIO_CHUNKER,
    HAS_VIDEO_CHUNKER,
)
from chunkforge.index import VectorIndex
from chunkforge.index_store import (
    compute_chunk_ids_hash,
    load_if_fresh,
    save_index,
)
from chunkforge.session import SessionManager
from chunkforge.storage import StorageBackend


def _get_version() -> str:
    """Get version without circular import."""
    from chunkforge import __version__

    return __version__


class ChunkForge:
    """Smart context cache with semantic chunking and vector search."""

    DEFAULT_CHUNK_SIZE = 256
    DEFAULT_MAX_CHUNK_SIZE = 4096
    DEFAULT_MERGE_THRESHOLD = 0.7
    DEFAULT_CHANGE_THRESHOLD = 0.85
    DEFAULT_SEARCH_ALPHA = 0.7

    MODALITY_THRESHOLDS = {
        "text": {"merge": 0.70, "change": 0.85},
        "code": {"merge": 0.85, "change": 0.80},
        "pdf": {"merge": 0.75, "change": 0.85},
    }

    def __init__(
        self,
        storage_dir: Optional[str] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
        search_alpha: float = DEFAULT_SEARCH_ALPHA,
    ):
        self.storage = StorageBackend(storage_dir)
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size
        self.merge_threshold = merge_threshold
        self.change_threshold = change_threshold
        self.search_alpha = search_alpha
        self._init_chunkers()
        self.vector_index = self._load_or_rebuild_index()
        self.session_manager = SessionManager(self.storage, self.vector_index)
        self.bm25_index = None
        self._bm25_ready = False

    def _init_chunkers(self) -> None:
        """Initialize modality-specific chunkers."""
        from chunkforge.chunkers import (
            ImageChunker,
            PDFChunker,
            AudioChunker,
            VideoChunker,
        )

        self.chunkers: Dict[str, Any] = {
            "text": TextChunker(
                chunk_size=self.chunk_size,
                max_chunk_size=self.max_chunk_size,
            ),
            "code": CodeChunker(
                chunk_size=self.chunk_size,
                max_chunk_size=self.max_chunk_size,
            ),
        }

        if HAS_IMAGE_CHUNKER and ImageChunker is not None:
            self.chunkers["image"] = ImageChunker()
        if HAS_PDF_CHUNKER and PDFChunker is not None:
            self.chunkers["pdf"] = PDFChunker(
                chunk_size=self.chunk_size,
                max_chunk_size=self.max_chunk_size,
            )
        if HAS_AUDIO_CHUNKER and AudioChunker is not None:
            self.chunkers["audio"] = AudioChunker()
        if HAS_VIDEO_CHUNKER and VideoChunker is not None:
            self.chunkers["video"] = VideoChunker()

    def _load_or_rebuild_index(self) -> VectorIndex:
        """Load persisted index if fresh, otherwise rebuild from SQLite."""
        current_hash = compute_chunk_ids_hash(self.storage)
        index = load_if_fresh(self.storage.index_dir, current_hash)
        if index is not None:
            return index

        index = VectorIndex()
        for chunk in self.storage.search_chunks():
            try:
                sig = sig_from_bytes(chunk["semantic_signature"])
                index.add_chunk(chunk["chunk_id"], sig_to_list(sig))
            except Exception:
                continue
        save_index(index, current_hash, self.storage.index_dir)
        return index

    def _save_index(self) -> None:
        """Persist current index to disk."""
        current_hash = compute_chunk_ids_hash(self.storage)
        save_index(self.vector_index, current_hash, self.storage.index_dir)

    def _ensure_bm25(self) -> None:
        """Lazily initialize BM25 index from stored chunk content."""
        if self._bm25_ready:
            return
        from chunkforge.bm25 import BM25Index

        self.bm25_index = BM25Index()
        for chunk in self.storage.search_chunks():
            content = chunk.get("content")
            if content:
                self.bm25_index.add_document(chunk["chunk_id"], content)
        self._bm25_ready = True

    def _persist_chunks(
        self, chunks: List[Chunk], doc_path: str
    ) -> None:
        """Store chunks and add them to the vector and keyword indexes."""
        for chunk in chunks:
            chunk_content = (
                chunk.content if isinstance(chunk.content, str) else None
            )
            self.storage.store_chunk(
                chunk_id=chunk.chunk_id,
                document_path=doc_path,
                content_hash=chunk.content_hash,
                semantic_signature=chunk.semantic_signature,
                start_pos=chunk.start_pos,
                end_pos=chunk.end_pos,
                token_count=chunk.token_count,
                content=chunk_content,
            )
            self.vector_index.add_chunk(
                chunk.chunk_id,
                sig_to_list(chunk.semantic_signature),
            )
            if self._bm25_ready and chunk_content:
                self.bm25_index.add_document(chunk.chunk_id, chunk_content)

    def _remove_stale_chunks(
        self, old_ids: set, new_ids: set
    ) -> None:
        """Remove chunks that no longer exist after re-indexing."""
        stale_ids = old_ids - new_ids
        if stale_ids:
            for cid in stale_ids:
                self.vector_index.remove_chunk(cid)
                if self._bm25_ready:
                    self.bm25_index.remove_document(cid)
            self.storage.delete_chunks(list(stale_ids))

    def detect_modality(self, file_path: str) -> str:
        """Detect file modality."""
        ext = Path(file_path).suffix.lower()
        code_extensions = self.chunkers["code"].supported_extensions()
        if ext in code_extensions:
            return "code"
        for modality, chunker in self.chunkers.items():
            if modality != "text" and ext in chunker.supported_extensions():
                return modality
        if ext in self.chunkers["text"].supported_extensions():
            return "text"
        return "unknown"

    def index_documents(
        self,
        paths: List[str],
        force_reindex: bool = False,
    ) -> Dict[str, Any]:
        """Index documents through modality-specific chunkers."""
        results: Dict[str, Any] = {
            "indexed": [],
            "skipped": [],
            "errors": [],
            "total_chunks": 0,
            "total_tokens": 0,
        }

        for path_str in paths:
            path = Path(path_str)

            if not path.exists():
                results["errors"].append({"path": path_str, "error": "File not found"})
                continue
            if not path.is_file():
                results["errors"].append({"path": path_str, "error": "Not a file"})
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="replace")
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

                existing_doc = self.storage.get_document(str(path))
                if existing_doc and not force_reindex:
                    if existing_doc["content_hash"] == content_hash:
                        results["skipped"].append(
                            {
                                "path": path_str,
                                "reason": "Unchanged",
                                "chunk_count": existing_doc["chunk_count"],
                            }
                        )
                        continue

                # Build signature cache from old chunks (skip recomputation)
                old_chunks_meta = []
                if existing_doc:
                    old_chunks_meta = self.storage.get_document_chunks(
                        str(path)
                    )
                sig_cache = {
                    c["content_hash"]: c["semantic_signature"]
                    for c in old_chunks_meta
                }

                # Route through appropriate chunker
                modality = self.detect_modality(str(path))
                chunker = self.chunkers.get(modality, self.chunkers["text"])
                chunks = chunker.chunk(content, str(path))

                # Inject cached signatures for unchanged chunks
                for chunk in chunks:
                    cached_sig = sig_cache.get(chunk.content_hash)
                    if cached_sig is not None:
                        chunk._semantic_signature = sig_from_bytes(cached_sig)

                # Post-process: merge similar adjacent chunks
                chunks = self._merge_similar_chunks(chunks)

                # Clean up stale chunks from previous indexing
                if old_chunks_meta:
                    old_ids = {c["chunk_id"] for c in old_chunks_meta}
                    new_ids = {c.chunk_id for c in chunks}
                    self._remove_stale_chunks(old_ids, new_ids)

                # Store chunks with content
                self._persist_chunks(chunks, str(path))

                last_modified = path.stat().st_mtime
                self.storage.store_document(
                    document_path=str(path),
                    content_hash=content_hash,
                    chunk_count=len(chunks),
                    last_modified=last_modified,
                )

                total_tokens = sum(c.token_count for c in chunks)
                results["indexed"].append(
                    {
                        "path": path_str,
                        "chunk_count": len(chunks),
                        "total_tokens": total_tokens,
                        "modality": modality,
                    }
                )
                results["total_chunks"] += len(chunks)
                results["total_tokens"] += total_tokens

            except Exception as e:
                results["errors"].append({"path": path_str, "error": str(e)})

        if results["indexed"]:
            self._save_index()

        return results

    def remove_document(self, document_path: str) -> Dict[str, Any]:
        """Remove a document and all its chunks, annotations, and index entries."""
        result = self.storage.remove_document(document_path)
        if result.get("removed"):
            for chunk_id in result.get("chunk_ids", []):
                self.vector_index.remove_chunk(chunk_id)
            self._save_index()
        return result

    def _merge_similar_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """Merge adjacent chunks with high similarity (single-pass).

        Uses modality-specific merge thresholds: code chunks require
        higher similarity to merge (preserving AST boundaries), while
        prose chunks merge more aggressively.
        """
        if len(chunks) <= 1:
            return chunks

        modality = chunks[0].modality if chunks else "text"
        threshold = self.MODALITY_THRESHOLDS.get(modality, {}).get(
            "merge", self.merge_threshold
        )

        merged = [chunks[0]]
        for chunk in chunks[1:]:
            current = merged[-1]
            similarity = current.similarity(chunk)
            combined_tokens = current.token_count + chunk.token_count

            if (
                similarity >= threshold
                and combined_tokens <= self.max_chunk_size
                and isinstance(current.content, str)
                and isinstance(chunk.content, str)
            ):
                merged_content = current.content + "\n\n" + chunk.content
                merged[-1] = Chunk(
                    content=merged_content,
                    modality=current.modality,
                    start_pos=current.start_pos,
                    end_pos=chunk.end_pos,
                    document_path=current.document_path,
                    chunk_index=current.chunk_index,
                    metadata={**current.metadata, **chunk.metadata},
                )
            else:
                merged.append(chunk)

        return merged

    def annotate(
        self,
        target: str,
        target_type: str,
        content: str,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Add an annotation to a document or chunk."""
        if target_type not in ("document", "chunk"):
            return {"error": "target_type must be 'document' or 'chunk'"}

        if target_type == "document":
            doc = self.storage.get_document(target)
            if doc is None:
                return {"error": f"Document not found: {target}"}
        else:
            chunk = self.storage.get_chunk(target)
            if chunk is None:
                return {"error": f"Chunk not found: {target}"}

        annotation_id = self.storage.store_annotation(target, target_type, content, tags)
        return {"id": annotation_id, "target": target, "target_type": target_type}

    def get_annotations(
        self,
        target: Optional[str] = None,
        target_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve filtered annotations."""
        return self.storage.get_annotations(target, target_type, tags)

    def delete_annotation(self, annotation_id: int) -> Dict[str, Any]:
        """Delete an annotation by ID."""
        deleted = self.storage.delete_annotation(annotation_id)
        return {"deleted": deleted, "id": annotation_id}

    def update_annotation(
        self,
        annotation_id: int,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update an annotation by ID."""
        updated = self.storage.update_annotation(annotation_id, content, tags)
        return {"updated": updated, "id": annotation_id}

    def search_annotations(
        self, query: str, target_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search annotation content text."""
        return self.storage.search_annotations(query, target_type)

    def bulk_annotate(self, annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Annotate multiple targets in one call.

        Each entry: {target, target_type, content, tags?}
        """
        results = []
        errors = []
        for entry in annotations:
            result = self.annotate(
                target=entry["target"],
                target_type=entry["target_type"],
                content=entry["content"],
                tags=entry.get("tags"),
            )
            if "error" in result:
                errors.append({**entry, "error": result["error"]})
            else:
                results.append(result)
        return {"created": results, "errors": errors}

    def prune_history(
        self,
        max_age_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Prune old history entries."""
        deleted = self.storage.prune_history(max_age_seconds, max_entries)
        return {"pruned": deleted}

    def get_map(self) -> Dict[str, Any]:
        """Get a project overview: all documents with chunk counts and annotations."""
        documents = self.storage.get_all_documents()
        result = []
        total_tokens = 0

        for doc in documents:
            chunks = self.storage.search_chunks(document_path=doc["document_path"])
            doc_tokens = sum(c["token_count"] for c in chunks)
            total_tokens += doc_tokens

            annotations = self.storage.get_annotations(
                target=doc["document_path"], target_type="document"
            )

            result.append({
                "path": doc["document_path"],
                "chunk_count": doc["chunk_count"],
                "total_tokens": doc_tokens,
                "indexed_at": doc["indexed_at"],
                "annotations": [
                    {"id": a["id"], "content": a["content"], "tags": a["tags"]}
                    for a in annotations
                ],
            })

        return {
            "documents": result,
            "total_documents": len(result),
            "total_tokens": total_tokens,
        }

    def get_history(
        self,
        limit: int = 20,
        document_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get change history entries."""
        return self.storage.get_change_history(limit, document_path)

    def detect_changes_and_update(
        self,
        session_id: str,
        document_paths: Optional[List[str]] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Detect changes in documents and update accordingly."""
        self.storage.create_session(session_id)

        results: Dict[str, Any] = {
            "unchanged": [],
            "modified": [],
            "new": [],
            "removed": [],
            "kv_restored": 0,
            "kv_reprocessed": 0,
        }

        if document_paths is None:
            all_chunks = self.storage.search_chunks()
            document_paths = list({c["document_path"] for c in all_chunks})

        session = self.storage.get_session(session_id)

        for doc_path in document_paths:
            path = Path(doc_path)

            if not path.exists():
                results["removed"].append(doc_path)
                self.remove_document(doc_path)
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                results["modified"].append({"path": doc_path, "reason": "Read error"})
                continue

            current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            stored_doc = self.storage.get_document(doc_path)

            if stored_doc is None:
                results["new"].append({"path": doc_path, "reason": "Not indexed"})
                continue

            if stored_doc["content_hash"] == current_hash:
                results["unchanged"].append(doc_path)
                chunks = self.storage.get_document_chunks(doc_path)
                if session and session["turn_count"] > 0:
                    for chunk_meta in chunks:
                        kv_data = self.storage.load_kv_state(
                            session_id,
                            chunk_meta["chunk_id"],
                            session["turn_count"] - 1,
                        )
                        if kv_data is not None:
                            results["kv_restored"] += 1
            else:
                results["modified"].append(
                    {
                        "path": doc_path,
                        "old_hash": stored_doc["content_hash"][:16],
                        "new_hash": current_hash[:16],
                    }
                )

                # Re-chunk through proper chunker
                modality = self.detect_modality(doc_path)
                chunker = self.chunkers.get(modality, self.chunkers["text"])
                change_thresh = self.MODALITY_THRESHOLDS.get(
                    modality, {}
                ).get("change", self.change_threshold)

                old_chunks_meta = self.storage.get_document_chunks(doc_path)

                # Inject cached signatures for unchanged chunks
                sig_cache = {
                    c["content_hash"]: c["semantic_signature"]
                    for c in old_chunks_meta
                }
                new_chunks = chunker.chunk(content, doc_path)
                for nc in new_chunks:
                    cached = sig_cache.get(nc.content_hash)
                    if cached is not None:
                        nc._semantic_signature = sig_from_bytes(cached)
                new_chunks = self._merge_similar_chunks(new_chunks)

                old_by_pos: Dict = {}
                for meta in old_chunks_meta:
                    old_by_pos[(meta["start_pos"], meta["end_pos"])] = meta

                for new_chunk in new_chunks:
                    old_meta = old_by_pos.get((new_chunk.start_pos, new_chunk.end_pos))

                    if old_meta is None:
                        search_results = self.vector_index.search(
                            sig_to_list(new_chunk.semantic_signature), k=1
                        )
                        if (
                            search_results
                            and search_results[0][1] >= change_thresh
                        ):
                            results["kv_restored"] += 1
                        else:
                            results["new"].append(
                                {
                                    "path": doc_path,
                                    "chunk_id": new_chunk.chunk_id,
                                    "reason": "New chunk",
                                }
                            )
                            results["kv_reprocessed"] += 1
                    elif new_chunk.content_hash == old_meta["content_hash"]:
                        results["kv_restored"] += 1
                    else:
                        old_sig = sig_from_bytes(old_meta["semantic_signature"])
                        similarity = cosine_similarity(
                            old_sig, new_chunk.semantic_signature
                        )

                        if similarity >= change_thresh:
                            results["kv_restored"] += 1
                        else:
                            results["kv_reprocessed"] += 1

                # Persist updated chunks and clean up stale ones
                self._persist_chunks(new_chunks, doc_path)
                old_chunk_ids = {m["chunk_id"] for m in old_chunks_meta}
                new_chunk_ids = {c.chunk_id for c in new_chunks}
                self._remove_stale_chunks(old_chunk_ids, new_chunk_ids)

                # Update document record
                self.storage.store_document(
                    document_path=doc_path,
                    content_hash=current_hash,
                    chunk_count=len(new_chunks),
                    last_modified=path.stat().st_mtime,
                )

        if results["modified"]:
            self._save_index()

        self.storage.record_change(
            summary=results, session_id=session_id, reason=reason
        )

        return results

    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Hybrid semantic + keyword search across all indexed chunks.

        Combines HNSW vector similarity with BM25 keyword scoring.
        The blend is controlled by search_alpha (1.0 = pure vector,
        0.0 = pure keyword).
        """
        query_chunk = Chunk(
            content=query,
            modality="text",
            start_pos=0,
            end_pos=len(query),
            document_path="<query>",
        )

        query_sig = sig_to_list(query_chunk.semantic_signature)

        # Widen HNSW candidate set for re-ranking
        hnsw_results = self.vector_index.search(query_sig, k=top_k * 3)

        if not hnsw_results:
            return []

        # BM25 re-ranking
        self._ensure_bm25()
        candidate_ids = [cid for cid, _ in hnsw_results]
        hnsw_scores = {cid: score for cid, score in hnsw_results}
        bm25_scores = self.bm25_index.score_batch(query, candidate_ids)

        # Normalize BM25 scores to [0, 1]
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 0.0
        if max_bm25 > 0:
            bm25_norm = {k: v / max_bm25 for k, v in bm25_scores.items()}
        else:
            bm25_norm = bm25_scores

        # Blend: alpha * vector + (1 - alpha) * keyword
        alpha = self.search_alpha
        combined = {}
        for cid in candidate_ids:
            vec_score = hnsw_scores.get(cid, 0.0)
            kw_score = bm25_norm.get(cid, 0.0)
            combined[cid] = alpha * vec_score + (1.0 - alpha) * kw_score

        ranked = sorted(
            combined.items(), key=lambda x: x[1], reverse=True
        )[:top_k]

        results = []
        for chunk_id, score in ranked:
            chunk_meta = self.storage.get_chunk(chunk_id)
            if chunk_meta is None:
                continue

            content = self.storage.get_chunk_content(chunk_id)

            results.append(
                {
                    "chunk_id": chunk_id,
                    "content": content,
                    "document_path": chunk_meta["document_path"],
                    "relevance_score": float(score),
                    "token_count": chunk_meta["token_count"],
                    "start_pos": chunk_meta["start_pos"],
                    "end_pos": chunk_meta["end_pos"],
                }
            )

        return results

    def get_context(
        self,
        document_paths: List[str],
    ) -> Dict[str, Any]:
        """Get cached context for documents."""
        result: Dict[str, Any] = {
            "unchanged": [],
            "changed": [],
            "new": [],
        }

        for doc_path in document_paths:
            path = Path(doc_path)

            if not path.exists():
                continue

            stored_doc = self.storage.get_document(doc_path)
            if stored_doc is None:
                result["new"].append({"path": doc_path})
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                result["changed"].append({"path": doc_path, "reason": "Read error"})
                continue

            current_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

            if stored_doc["content_hash"] == current_hash:
                chunks = self.storage.search_chunks(document_path=doc_path)
                chunk_data = []
                for chunk in chunks:
                    chunk_data.append(
                        {
                            "chunk_id": chunk["chunk_id"],
                            "content": chunk.get("content"),
                            "start_pos": chunk["start_pos"],
                            "end_pos": chunk["end_pos"],
                            "token_count": chunk["token_count"],
                        }
                    )
                result["unchanged"].append(
                    {
                        "path": doc_path,
                        "chunks": chunk_data,
                        "total_tokens": sum(c["token_count"] for c in chunk_data),
                    }
                )
            else:
                result["changed"].append(
                    {
                        "path": doc_path,
                        "old_hash": stored_doc["content_hash"][:16],
                        "new_hash": current_hash[:16],
                    }
                )

        return result

    def get_relevant_kv(
        self,
        session_id: str,
        query: str,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """Get cached state for chunks most relevant to a query."""
        return self.session_manager.get_relevant_chunks(session_id, query, top_k)

    def save_kv_state(
        self,
        session_id: str,
        kv_data: Dict[str, Any],
        chunk_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Save KV-cache state for a session."""
        return self.session_manager.save_state(session_id, kv_data, chunk_ids)

    save_state = save_kv_state

    def rollback(self, session_id: str, target_turn: int) -> Dict[str, Any]:
        """Rollback session to a previous turn."""
        return self.session_manager.rollback(session_id, target_turn)

    def prune_chunks(self, session_id: str, max_tokens: int) -> Dict[str, Any]:
        """Prune low-relevance chunks to stay under token limit."""
        return self.session_manager.prune(session_id, max_tokens)

    def get_stats(self) -> Dict[str, Any]:
        """Get ChunkForge statistics."""
        storage_stats = self.storage.get_storage_stats()
        index_stats = self.vector_index.get_stats()

        return {
            "version": _get_version(),
            "storage": storage_stats,
            "index": index_stats,
            "config": {
                "chunk_size": self.chunk_size,
                "max_chunk_size": self.max_chunk_size,
                "merge_threshold": self.merge_threshold,
                "change_threshold": self.change_threshold,
                "search_alpha": self.search_alpha,
            },
        }
