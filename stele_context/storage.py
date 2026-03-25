"""
Storage backend for Stele.

Handles persistent storage of:
- Chunk metadata (hashes, semantic signatures, positions)
- Chunk text content for retrieval
- Session state and rollback history (delegated to SessionStorage)
- Document indexing information
- Chunk versioning and history

Uses SQLite for metadata and filesystem for KV-cache blobs.
All storage is local-only with zero network dependencies.
"""

from __future__ import annotations

__all__ = ["StorageBackend"]

import re as _re
import sqlite3
import time
from pathlib import Path
from typing import Any

from stele_context.document_lock_storage import DocumentLockStorage
from stele_context.storage_schema import (
    connect,
    init_pool,
    init_database,
    migrate_database,
)
from stele_context.metadata_storage import MetadataStorage
from stele_context.session_storage import SessionStorage
from stele_context.storage_delegates import StorageDelegatesMixin
from stele_context.symbol_storage import SymbolStorage

from stele_context.chunkers.numpy_compat import sig_to_bytes, sig_from_bytes


class StorageBackend(StorageDelegatesMixin):
    """
    Persistent storage backend for Stele.

    Manages SQLite database for metadata and filesystem for KV-cache blobs.
    Delegates session, metadata, symbol, and lock operations via
    StorageDelegatesMixin.
    """

    def __init__(self, base_dir: str | None = None):
        """
        Initialize storage backend.

        Args:
            base_dir: Base directory for storage. Defaults to ~/.stele-context/
        """
        if base_dir is None:
            base_dir = str(Path("~/.stele-context").expanduser())

        self.base_dir = Path(base_dir)
        self.db_path = self.base_dir / "stele_context.db"
        self.kv_dir = self.base_dir / "kv_cache"
        self.index_dir = self.base_dir / "indices"

        # Create directories
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.kv_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database and connection pool
        self._init_database()
        self._migrate_database()
        self._pool = init_pool(self.db_path)

        # Initialize storage delegates
        self._session_storage = SessionStorage(self.db_path, self.kv_dir)
        self._metadata_storage = MetadataStorage(self.db_path)
        self._symbol_storage = SymbolStorage(self.db_path)
        self._document_lock_storage = DocumentLockStorage(self.db_path)

    def _init_database(self) -> None:
        """Initialize SQLite database with required tables."""
        init_database(self.db_path)

    def _migrate_database(self) -> None:
        """Run database migrations for schema changes."""
        migrate_database(self.db_path)

    def close(self) -> None:
        """Close all pooled connections and checkpoint WAL. Safe to call multiple times."""
        if self._pool is not None:
            try:
                conn = self._pool.get()
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            self._pool.close_all()

    # -- Core chunk operations ------------------------------------------------

    def store_chunk(
        self,
        chunk_id: str,
        document_path: str,
        content_hash: str,
        semantic_signature: Any,
        start_pos: int,
        end_pos: int,
        token_count: int,
        content: str | None = None,
    ) -> None:
        """Store chunk metadata (and optionally content) in database."""
        now = time.time()

        sig_bytes = sig_to_bytes(semantic_signature)

        with connect(self.db_path) as conn:
            # Get current version
            cursor = conn.execute(
                "SELECT version FROM chunks WHERE chunk_id = ?", (chunk_id,)
            )
            row = cursor.fetchone()
            version = (row[0] + 1) if row else 1

            # Store current version in history
            if row:
                conn.execute(
                    """
                    INSERT INTO chunk_history
                    (chunk_id, version, content_hash, semantic_signature, created_at)
                    SELECT chunk_id, version, content_hash, semantic_signature, created_at
                    FROM chunks WHERE chunk_id = ?
                """,
                    (chunk_id,),
                )

            # Update or insert chunk (preserve access_count on update)
            if row:
                conn.execute(
                    """
                    UPDATE chunks SET
                        document_path = ?, content_hash = ?, semantic_signature = ?,
                        start_pos = ?, end_pos = ?, token_count = ?,
                        last_accessed = ?, version = ?, content = ?
                    WHERE chunk_id = ?
                """,
                    (
                        document_path,
                        content_hash,
                        sig_bytes,
                        start_pos,
                        end_pos,
                        token_count,
                        now,
                        version,
                        content,
                        chunk_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO chunks
                    (chunk_id, document_path, content_hash, semantic_signature,
                     start_pos, end_pos, token_count, created_at, last_accessed,
                     access_count, version, content)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                    (
                        chunk_id,
                        document_path,
                        content_hash,
                        sig_bytes,
                        start_pos,
                        end_pos,
                        token_count,
                        now,
                        now,
                        version,
                        content,
                    ),
                )

    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        """Retrieve chunk metadata by ID."""
        now = time.time()
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE chunks SET last_accessed = ?, access_count = access_count + 1 "
                "WHERE chunk_id = ?",
                (now, chunk_id),
            )
            return dict(row)

    def get_chunk_content(self, chunk_id: str) -> str | None:
        """Retrieve chunk text content by ID."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT content FROM chunks WHERE chunk_id = ?", (chunk_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return row[0]

    def search_chunks(
        self,
        document_path: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search chunks, returning metadata and content."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if document_path:
                cursor = conn.execute(
                    "SELECT * FROM chunks WHERE document_path = ? ORDER BY start_pos",
                    (document_path,),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM chunks ORDER BY document_path, start_pos"
                )

            return [dict(row) for row in cursor.fetchall()]

    def search_text(
        self,
        pattern: str,
        *,
        regex: bool = False,
        document_path: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search chunk content by exact substring or regex pattern.

        Returns matching chunks with match details. Uses SQLite LIKE
        for substring mode, Python re for regex mode. Zero dependencies.
        """
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if document_path:
                rows = conn.execute(
                    "SELECT chunk_id, document_path, content, start_pos, "
                    "end_pos, token_count FROM chunks "
                    "WHERE document_path = ? AND content IS NOT NULL "
                    "ORDER BY start_pos",
                    (document_path,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT chunk_id, document_path, content, start_pos, "
                    "end_pos, token_count FROM chunks "
                    "WHERE content IS NOT NULL "
                    "ORDER BY document_path, start_pos"
                ).fetchall()

            results: list[dict[str, Any]] = []
            compiled = _re.compile(pattern) if regex else None

            for row in rows:
                content = row["content"] or ""
                if regex:
                    matches = [
                        {"start": m.start(), "end": m.end(), "text": m.group()}
                        for m in compiled.finditer(content)  # type: ignore[union-attr]
                    ]
                else:
                    matches = []
                    start = 0
                    while True:
                        idx = content.find(pattern, start)
                        if idx == -1:
                            break
                        matches.append(
                            {
                                "start": idx,
                                "end": idx + len(pattern),
                                "text": pattern,
                            }
                        )
                        start = idx + 1

                if matches:
                    results.append(
                        {
                            "chunk_id": row["chunk_id"],
                            "document_path": row["document_path"],
                            "match_count": len(matches),
                            "matches": matches[:10],
                            "content_preview": content[:200],
                            "token_count": row["token_count"],
                        }
                    )
                    if len(results) >= limit:
                        break

            return results

    def get_document_chunks(self, document_path: str) -> list[dict[str, Any]]:
        """Get all chunks for a document."""
        return self.search_chunks(document_path=document_path)

    # -- Document operations --------------------------------------------------

    def store_document(
        self,
        document_path: str,
        content_hash: str,
        chunk_count: int,
        last_modified: float,
        file_size: int | None = None,
    ) -> None:
        """Store document indexing information.

        Uses INSERT ... ON CONFLICT to preserve lock/version columns
        that would be lost with INSERT OR REPLACE.
        """
        now = time.time()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO documents
                (document_path, content_hash, chunk_count, indexed_at,
                 last_modified, file_size)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_path) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    chunk_count = excluded.chunk_count,
                    indexed_at = excluded.indexed_at,
                    last_modified = excluded.last_modified,
                    file_size = excluded.file_size
            """,
                (
                    document_path,
                    content_hash,
                    chunk_count,
                    now,
                    last_modified,
                    file_size,
                ),
            )

    def get_document(self, document_path: str) -> dict[str, Any] | None:
        """Get document indexing information."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM documents WHERE document_path = ?", (document_path,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_documents(self) -> list[dict[str, Any]]:
        """Get all indexed documents."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM documents ORDER BY document_path")
            return [dict(row) for row in cursor.fetchall()]

    # -- Agent-supplied semantic embedding methods ----------------------------

    def store_semantic_summary(
        self,
        chunk_id: str,
        summary: str,
        agent_signature: Any,
    ) -> bool:
        """Store an agent-supplied semantic summary and its computed signature."""
        sig_bytes = sig_to_bytes(agent_signature)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE chunks SET semantic_summary = ?, agent_signature = ? "
                "WHERE chunk_id = ?",
                (summary, sig_bytes, chunk_id),
            )
            return cursor.rowcount > 0

    def bulk_update_summaries(
        self,
        chunk_ids: list[str],
        summary: str,
        agent_signature: Any,
    ) -> int:
        """Batch-update semantic_summary and agent_signature for multiple chunks."""
        sig_bytes = sig_to_bytes(agent_signature)
        with connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE chunks SET semantic_summary = ?, agent_signature = ? "
                "WHERE chunk_id = ?",
                [(summary, sig_bytes, cid) for cid in chunk_ids],
            )
            return len(chunk_ids)

    def store_agent_signature(
        self,
        chunk_id: str,
        agent_signature: Any,
    ) -> bool:
        """Store a raw agent-supplied embedding vector."""
        sig_bytes = sig_to_bytes(agent_signature)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE chunks SET agent_signature = ? WHERE chunk_id = ?",
                (sig_bytes, chunk_id),
            )
            return cursor.rowcount > 0

    def create_memory_chunk(
        self,
        chunk_id: str,
        content: str,
        agent_signature: Any,
        document_path: str | None = None,
    ) -> bool:
        """Create a memory chunk and store its agent-supplied embedding.

        Used by ``llm_embed`` to persist LLM-generated embeddings for
        arbitrary text content (e.g. session state, project summaries)
        that doesn't come from indexed files.

        Args:
            chunk_id: Unique identifier for this memory chunk.
            content: The text content to store.
            agent_signature: 128-dim unit vector from ``fingerprint_to_vector``.
            document_path: Optional path override. Defaults to ``memory:<chunk_id>``.

        Returns:
            True if the chunk was created and the signature stored.
        """
        import hashlib
        import time as _time

        now = _time.time()
        content_hash = hashlib.sha256(content.encode()).digest()
        # Zero signature as placeholder (Tier 1 is not computed for memory chunks)
        zero_sig = sig_to_bytes([0.0] * 128)
        sig_bytes = sig_to_bytes(agent_signature)
        token_count = len(content) // 4  # rough estimate
        dp = document_path or f"memory:{chunk_id}"

        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO chunks
                (chunk_id, document_path, content_hash, semantic_signature,
                 start_pos, end_pos, token_count, created_at, last_accessed,
                 access_count, version, content, agent_signature)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, 0, 1, ?, ?)
                """,
                (
                    chunk_id,
                    dp,
                    content_hash,
                    zero_sig,
                    len(content),
                    token_count,
                    now,
                    now,
                    content,
                    sig_bytes,
                ),
            )
            return cursor.rowcount > 0

    def get_agent_signature(self, chunk_id: str) -> Any | None:
        """Get agent-supplied signature for a chunk, if any."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT agent_signature FROM chunks WHERE chunk_id = ?",
                (chunk_id,),
            )
            row = cursor.fetchone()
            if row is None or row[0] is None:
                return None
            return sig_from_bytes(row[0])

    def has_agent_signatures(self, chunk_ids: list[str]) -> set[str]:
        """Return subset of chunk_ids that have non-null agent_signature (Tier 2)."""
        if not chunk_ids:
            return set()
        with connect(self.db_path) as conn:
            placeholders = ",".join("?" * len(chunk_ids))
            cursor = conn.execute(
                f"SELECT chunk_id FROM chunks WHERE chunk_id IN ({placeholders}) "
                "AND agent_signature IS NOT NULL",
                chunk_ids,
            )
            return {row[0] for row in cursor.fetchall()}

    # -- Chunk history --------------------------------------------------------

    def get_chunk_history(
        self,
        chunk_id: str | None = None,
        document_path: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query chunk version history."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            conditions: list[str] = []
            params: list[Any] = []

            if chunk_id:
                conditions.append("h.chunk_id = ?")
                params.append(chunk_id)
            if document_path:
                conditions.append("c.document_path = ?")
                params.append(document_path)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            query = (
                "SELECT h.chunk_id, h.version, h.content_hash, "
                "h.created_at, c.document_path "
                "FROM chunk_history h "
                "JOIN chunks c ON h.chunk_id = c.chunk_id "
                f"{where} "
                "ORDER BY h.created_at DESC LIMIT ?"
            )
            params.append(limit)

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # -- Staleness methods ----------------------------------------------------

    def set_staleness(self, chunk_id: str, score: float) -> None:
        """Set staleness score for a chunk."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE chunks SET staleness_score = ? WHERE chunk_id = ?",
                (score, chunk_id),
            )

    def set_staleness_batch(self, updates: list[tuple]) -> None:
        """Set staleness for multiple chunks. Each: (score, chunk_id)."""
        if not updates:
            return
        with connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE chunks SET staleness_score = ? WHERE chunk_id = ?",
                updates,
            )

    def clear_staleness(self) -> None:
        """Reset all staleness scores to 0."""
        with connect(self.db_path) as conn:
            conn.execute("UPDATE chunks SET staleness_score = 0.0")

    def get_stale_chunks(self, threshold: float = 0.3) -> list[dict[str, Any]]:
        """Get chunks with staleness_score >= threshold."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT chunk_id, document_path, staleness_score, token_count, content "
                "FROM chunks WHERE staleness_score >= ? "
                "ORDER BY staleness_score DESC",
                (threshold,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # -- Aggregate stats ------------------------------------------------------

    def get_storage_stats(self) -> dict[str, Any]:
        """Get storage statistics."""
        with connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM chunks")
            chunk_count = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM documents")
            doc_count = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM sessions")
            session_count = cursor.fetchone()[0]

            cursor = conn.execute("SELECT SUM(token_count) FROM chunks")
            total_tokens = cursor.fetchone()[0] or 0

            cursor = conn.execute("SELECT COUNT(*) FROM chunk_history")
            version_count = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM annotations")
            annotation_count = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM change_history")
            history_count = cursor.fetchone()[0]

        kv_size = sum(f.stat().st_size for f in self.kv_dir.rglob("*") if f.is_file())
        db_size = self.db_path.stat().st_size
        symbol_stats = self._symbol_storage.get_symbol_stats()
        lock_stats = self._document_lock_storage.get_lock_stats()

        return {
            "chunk_count": chunk_count,
            "document_count": doc_count,
            "session_count": session_count,
            "total_tokens": total_tokens,
            "version_count": version_count,
            "annotation_count": annotation_count,
            "history_count": history_count,
            "kv_cache_size_bytes": kv_size,
            "database_size_bytes": db_size,
            "storage_dir": str(self.base_dir),
            **symbol_stats,
            **lock_stats,
        }

    # -- Deletion / cleanup ---------------------------------------------------

    def delete_chunks(self, chunk_ids: list[str]) -> int:
        """Delete chunks and their related data. Returns count deleted."""
        if not chunk_ids:
            return 0
        # Clean up symbols and edges first
        self._symbol_storage.clear_chunk_symbols(chunk_ids)
        self._symbol_storage.clear_chunk_edges(chunk_ids)

        placeholders = ",".join("?" * len(chunk_ids))
        with connect(self.db_path) as conn:
            conn.execute(
                f"DELETE FROM session_chunks WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            conn.execute(
                f"DELETE FROM chunk_history WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            conn.execute(
                f"DELETE FROM annotations WHERE target IN ({placeholders}) "
                "AND target_type = 'chunk'",
                chunk_ids,
            )
            cursor = conn.execute(
                f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            return cursor.rowcount

    def remove_document(self, document_path: str) -> dict[str, Any]:
        """Remove a document and all its chunks, annotations, and history."""
        doc = self.get_document(document_path)
        if doc is None:
            return {"removed": False}

        # Get chunk IDs before deleting
        chunks = self.get_document_chunks(document_path)
        chunk_ids = [c["chunk_id"] for c in chunks]

        # Clean up document-level symbols (chunk symbols handled by delete_chunks)
        self._symbol_storage.clear_document_symbols(document_path)

        with connect(self.db_path) as conn:
            # Delete document-level annotations
            cursor = conn.execute(
                "DELETE FROM annotations WHERE target = ? AND target_type = 'document'",
                (document_path,),
            )
            annotations_removed = cursor.rowcount

        # Delegate chunk deletion (handles session_chunks, history, chunk annotations)
        chunks_removed = self.delete_chunks(chunk_ids)

        with connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM documents WHERE document_path = ?", (document_path,)
            )

        return {
            "removed": True,
            "chunk_ids": chunk_ids,
            "chunks_removed": chunks_removed,
            "annotations_removed": annotations_removed,
        }

    def clear_all(self) -> None:
        """Clear all stored data."""
        self._symbol_storage.clear_all_symbols()
        self._symbol_storage.clear_all_edges()

        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM session_chunks")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM chunk_history")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM annotations")
            conn.execute("DELETE FROM change_history")
            conn.execute("DELETE FROM document_conflicts")

        for kv_file in self.kv_dir.rglob("*.kv"):
            kv_file.unlink()
