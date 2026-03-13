"""
Session manager for ChunkForge.

Provides high-level session operations wrapping SessionStorage
with HNSW-accelerated chunk retrieval.
"""

from typing import Any, Dict, List, Optional

from chunkforge.chunkers.base import Chunk
from chunkforge.chunkers.numpy_compat import (
    cosine_similarity,
    sig_to_list,
    sig_from_bytes,
)
from chunkforge.index import VectorIndex
from chunkforge.storage import StorageBackend


class SessionManager:
    """
    High-level session manager for ChunkForge.

    Wraps SessionStorage with HNSW-accelerated search and
    convenient methods for session lifecycle management.
    """

    def __init__(
        self,
        storage: StorageBackend,
        vector_index: VectorIndex,
    ):
        self.storage = storage
        self.vector_index = vector_index

    def get_relevant_chunks(
        self,
        session_id: str,
        query: str,
        top_k: int = 10,
    ) -> Dict[str, Any]:
        """
        Get chunks most relevant to a query, returning content.

        Uses HNSW search for fast retrieval, falls back to linear
        scan for session chunks not in the index.
        """
        query_chunk = Chunk(
            content=query,
            modality="text",
            start_pos=0,
            end_pos=len(query),
            document_path="<query>",
        )

        session_chunks = self.storage.get_session_chunks(session_id)
        if not session_chunks:
            return {"query": query, "chunks": [], "total_tokens": 0}

        query_sig = sig_to_list(query_chunk.semantic_signature)
        search_results = self.vector_index.search(query_sig, k=top_k * 2)
        session_chunk_ids = {c["chunk_id"] for c in session_chunks}

        # Filter to session chunks
        scored = []
        for chunk_id, sim in search_results:
            if chunk_id in session_chunk_ids:
                scored.append((chunk_id, sim))
            if len(scored) >= top_k:
                break

        # Fall back to linear scan if needed
        if len(scored) < top_k:
            found_ids = {s[0] for s in scored}
            query_sig_arr = query_chunk.semantic_signature
            for chunk_meta in session_chunks:
                if chunk_meta["chunk_id"] in found_ids:
                    continue
                chunk_sig = sig_from_bytes(chunk_meta["semantic_signature"])
                sim = cosine_similarity(query_sig_arr, chunk_sig)
                scored.append((chunk_meta["chunk_id"], sim))

            scored.sort(key=lambda x: x[1], reverse=True)
            scored = scored[:top_k]

        result_chunks = []
        total_tokens = 0

        for chunk_id, score in scored:
            content = self.storage.get_chunk_content(chunk_id)
            chunk_info = self.storage.get_chunk(chunk_id)
            if chunk_info is None:
                continue

            result_chunks.append(
                {
                    "chunk_id": chunk_id,
                    "content": content,
                    "document_path": chunk_info["document_path"],
                    "relevance_score": float(score),
                    "token_count": chunk_info["token_count"],
                }
            )
            total_tokens += chunk_info["token_count"]

        return {
            "query": query,
            "chunks": result_chunks,
            "total_tokens": total_tokens,
        }

    def save_state(
        self,
        session_id: str,
        kv_data: Dict[str, Any],
        chunk_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Save state for a session. Alias for save_kv_state."""
        self.storage.create_session(session_id)
        session = self.storage.get_session(session_id)

        if session is None:
            return {"error": "Failed to create session"}

        turn_number = session["turn_count"]
        saved_count = 0
        total_tokens = session["total_tokens"]

        for chunk_id, data in kv_data.items():
            if chunk_ids is not None and chunk_id not in chunk_ids:
                continue

            chunk_meta = self.storage.get_chunk(chunk_id)
            if chunk_meta is None:
                continue

            self.storage.store_kv_state(
                session_id=session_id,
                chunk_id=chunk_id,
                turn_number=turn_number,
                kv_data=data,
                relevance_score=1.0,
            )

            saved_count += 1
            total_tokens += chunk_meta["token_count"]

        self.storage.update_session(
            session_id=session_id,
            turn_count=turn_number + 1,
            total_tokens=total_tokens,
        )

        return {
            "session_id": session_id,
            "turn_number": turn_number,
            "chunks_saved": saved_count,
            "total_tokens": total_tokens,
        }

    # Backward compat alias
    save_kv_state = save_state

    def rollback(
        self,
        session_id: str,
        target_turn: int,
    ) -> Dict[str, Any]:
        """Rollback session to a previous turn."""
        session = self.storage.get_session(session_id)

        if session is None:
            return {"error": "Session not found"}

        current_turn = session["turn_count"]
        if target_turn >= current_turn:
            return {
                "error": f"Target turn {target_turn} >= current turn {current_turn}"
            }
        if target_turn < 0:
            return {"error": "Target turn must be non-negative"}

        removed_count = self.storage.rollback_session(session_id, target_turn)

        return {
            "session_id": session_id,
            "previous_turn": current_turn,
            "current_turn": target_turn,
            "chunks_removed": removed_count,
        }

    def prune(
        self,
        session_id: str,
        max_tokens: int,
    ) -> Dict[str, Any]:
        """Prune low-relevance chunks to stay under token limit."""
        session = self.storage.get_session(session_id)

        if session is None:
            return {"error": "Session not found"}

        current_tokens = session["total_tokens"]
        if current_tokens <= max_tokens:
            return {
                "session_id": session_id,
                "current_tokens": current_tokens,
                "max_tokens": max_tokens,
                "chunks_pruned": 0,
                "message": "Already under limit",
            }

        pruned_count = self.storage.prune_chunks(session_id, max_tokens)
        updated_session = self.storage.get_session(session_id)
        new_tokens = updated_session["total_tokens"] if updated_session else 0

        return {
            "session_id": session_id,
            "previous_tokens": current_tokens,
            "current_tokens": new_tokens,
            "max_tokens": max_tokens,
            "chunks_pruned": pruned_count,
            "tokens_saved": current_tokens - new_tokens,
        }
