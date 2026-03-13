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

# Import optional chunkers
if HAS_IMAGE_CHUNKER:
    from chunkforge.chunkers import ImageChunker
if HAS_PDF_CHUNKER:
    from chunkforge.chunkers import PDFChunker
if HAS_AUDIO_CHUNKER:
    from chunkforge.chunkers import AudioChunker
if HAS_VIDEO_CHUNKER:
    from chunkforge.chunkers import VideoChunker


class ChunkForge:
    """Smart context cache with semantic chunking and vector search."""

    DEFAULT_CHUNK_SIZE = 256
    DEFAULT_MAX_CHUNK_SIZE = 4096
    DEFAULT_MERGE_THRESHOLD = 0.7
    DEFAULT_CHANGE_THRESHOLD = 0.85

    def __init__(
        self,
        storage_dir: Optional[str] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
        change_threshold: float = DEFAULT_CHANGE_THRESHOLD,
    ):
        self.storage = StorageBackend(storage_dir)
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size
        self.merge_threshold = merge_threshold
        self.change_threshold = change_threshold
        self._init_chunkers()
        self.vector_index = self._load_or_rebuild_index()
        self.session_manager = SessionManager(self.storage, self.vector_index)

    def _init_chunkers(self) -> None:
        """Initialize modality-specific chunkers."""
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

        if HAS_IMAGE_CHUNKER:
            self.chunkers["image"] = ImageChunker()
        if HAS_PDF_CHUNKER:
            self.chunkers["pdf"] = PDFChunker(
                chunk_size=self.chunk_size,
                max_chunk_size=self.max_chunk_size,
            )
        if HAS_AUDIO_CHUNKER:
            self.chunkers["audio"] = AudioChunker()
        if HAS_VIDEO_CHUNKER:
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
                pass
        save_index(index, current_hash, self.storage.index_dir)
        return index

    def _save_index(self) -> None:
        """Persist current index to disk."""
        current_hash = compute_chunk_ids_hash(self.storage)
        save_index(self.vector_index, current_hash, self.storage.index_dir)

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

                # Route through appropriate chunker
                modality = self.detect_modality(str(path))
                chunker = self.chunkers.get(modality, self.chunkers["text"])
                chunks = chunker.chunk(content, str(path))

                # Post-process: merge similar adjacent chunks
                chunks = self._merge_similar_chunks(chunks)

                # Store chunks with content
                for chunk in chunks:
                    chunk_content = (
                        chunk.content if isinstance(chunk.content, str) else None
                    )
                    self.storage.store_chunk(
                        chunk_id=chunk.chunk_id,
                        document_path=str(path),
                        content_hash=chunk.content_hash,
                        semantic_signature=chunk.semantic_signature,
                        start_pos=chunk.start_pos,
                        end_pos=chunk.end_pos,
                        token_count=chunk.token_count,
                        content=chunk_content,
                    )
                    # Add to HNSW index
                    self.vector_index.add_chunk(
                        chunk.chunk_id,
                        sig_to_list(chunk.semantic_signature),
                    )

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

    def _merge_similar_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """Merge adjacent chunks with high similarity."""
        if len(chunks) <= 1:
            return chunks

        merged = list(chunks)
        changed = True

        while changed:
            changed = False
            new_merged: List[Chunk] = []
            i = 0

            while i < len(merged):
                if i == len(merged) - 1:
                    new_merged.append(merged[i])
                    i += 1
                    continue

                current = merged[i]
                next_chunk = merged[i + 1]

                similarity = current.similarity(next_chunk)
                combined_tokens = current.token_count + next_chunk.token_count
                should_merge = (
                    similarity >= self.merge_threshold
                    and combined_tokens <= self.max_chunk_size
                )

                if should_merge:
                    # Merge content based on type
                    if isinstance(current.content, str) and isinstance(
                        next_chunk.content, str
                    ):
                        merged_content = current.content + "\n\n" + next_chunk.content
                    else:
                        merged_content = current.content

                    merged_chunk = Chunk(
                        content=merged_content,
                        modality=current.modality,
                        start_pos=current.start_pos,
                        end_pos=next_chunk.end_pos,
                        document_path=current.document_path,
                        chunk_index=current.chunk_index,
                        metadata={**current.metadata, **next_chunk.metadata},
                    )
                    new_merged.append(merged_chunk)
                    i += 2
                    changed = True
                else:
                    new_merged.append(current)
                    i += 1

            merged = new_merged

        return merged

    def detect_changes_and_update(
        self,
        session_id: str,
        document_paths: Optional[List[str]] = None,
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
                new_chunks = chunker.chunk(content, doc_path)
                new_chunks = self._merge_similar_chunks(new_chunks)

                old_chunks_meta = self.storage.get_document_chunks(doc_path)
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
                            and search_results[0][1] >= self.change_threshold
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

                        if similarity >= self.change_threshold:
                            results["kv_restored"] += 1
                        else:
                            results["kv_reprocessed"] += 1

                    # Persist updated chunk
                    chunk_content = (
                        new_chunk.content
                        if isinstance(new_chunk.content, str)
                        else None
                    )
                    self.storage.store_chunk(
                        chunk_id=new_chunk.chunk_id,
                        document_path=doc_path,
                        content_hash=new_chunk.content_hash,
                        semantic_signature=new_chunk.semantic_signature,
                        start_pos=new_chunk.start_pos,
                        end_pos=new_chunk.end_pos,
                        token_count=new_chunk.token_count,
                        content=chunk_content,
                    )
                    self.vector_index.add_chunk(
                        new_chunk.chunk_id,
                        sig_to_list(new_chunk.semantic_signature),
                    )

                # Update document record
                self.storage.store_document(
                    document_path=doc_path,
                    content_hash=current_hash,
                    chunk_count=len(new_chunks),
                    last_modified=path.stat().st_mtime,
                )

        if results["modified"]:
            self._save_index()

        return results

    def search(
        self,
        query: str,
        top_k: int = 10,
    ) -> List[Dict[str, Any]]:
        """Semantic search across all indexed chunks."""
        query_chunk = Chunk(
            content=query,
            modality="text",
            start_pos=0,
            end_pos=len(query),
            document_path="<query>",
        )

        query_sig = sig_to_list(query_chunk.semantic_signature)
        search_results = self.vector_index.search(query_sig, k=top_k)

        results = []
        for chunk_id, similarity_score in search_results:
            chunk_meta = self.storage.get_chunk(chunk_id)
            if chunk_meta is None:
                continue

            content = self.storage.get_chunk_content(chunk_id)

            results.append(
                {
                    "chunk_id": chunk_id,
                    "content": content,
                    "document_path": chunk_meta["document_path"],
                    "relevance_score": float(similarity_score),
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
            "version": "0.5.3",
            "storage": storage_stats,
            "index": index_stats,
            "config": {
                "chunk_size": self.chunk_size,
                "max_chunk_size": self.max_chunk_size,
                "merge_threshold": self.merge_threshold,
                "change_threshold": self.change_threshold,
            },
        }
