"""
Storage backend for ChunkForge.

Handles persistent storage of:
- Chunk metadata (hashes, semantic signatures, positions)
- Chunk text content for retrieval
- Session state and rollback history (delegated to SessionStorage)
- Document indexing information
- Chunk versioning and history

Uses SQLite for metadata and filesystem for KV-cache blobs.
All storage is local-only with zero network dependencies.
"""

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from chunkforge.metadata_storage import MetadataStorage
from chunkforge.session_storage import SessionStorage

from chunkforge.chunkers.numpy_compat import sig_to_bytes


class StorageBackend:
    """
    Persistent storage backend for ChunkForge.

    Manages SQLite database for metadata and filesystem for KV-cache blobs.
    Delegates session operations to SessionStorage.
    """

    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialize storage backend.

        Args:
            base_dir: Base directory for storage. Defaults to ~/.chunkforge/
        """
        if base_dir is None:
            base_dir = os.path.expanduser("~/.chunkforge")

        self.base_dir = Path(base_dir)
        self.db_path = self.base_dir / "chunkforge.db"
        self.kv_dir = self.base_dir / "kv_cache"
        self.index_dir = self.base_dir / "indices"

        # Create directories
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.kv_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_database()
        self._migrate_database()

        # Initialize storage delegates
        self._session_storage = SessionStorage(self.db_path, self.kv_dir)
        self._metadata_storage = MetadataStorage(self.db_path)

    def _init_database(self) -> None:
        """Initialize SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    semantic_signature BLOB NOT NULL,
                    start_pos INTEGER NOT NULL,
                    end_pos INTEGER NOT NULL,
                    token_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    version INTEGER DEFAULT 1
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS chunk_history (
                    chunk_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    semantic_signature BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (chunk_id, version),
                    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    document_path TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    indexed_at REAL NOT NULL,
                    last_modified REAL NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    last_updated REAL NOT NULL,
                    turn_count INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_chunks (
                    session_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    turn_number INTEGER NOT NULL,
                    kv_path TEXT,
                    relevance_score REAL DEFAULT 1.0,
                    PRIMARY KEY (session_id, chunk_id, turn_number),
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id),
                    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    target_type TEXT NOT NULL CHECK(target_type IN ('document', 'chunk')),
                    content TEXT NOT NULL,
                    tags TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS change_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    session_id TEXT,
                    summary_json TEXT NOT NULL,
                    reason TEXT
                )
            """)

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_annotations_target "
                "ON annotations(target, target_type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_change_history_ts "
                "ON change_history(timestamp)"
            )

            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(content_hash)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_chunks_session ON session_chunks(session_id)"
            )

            conn.commit()

    def _migrate_database(self) -> None:
        """Run database migrations for schema changes."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("PRAGMA table_info(chunks)")
            columns = {row[1] for row in cursor.fetchall()}

            changed = False
            if "content" not in columns:
                conn.execute("ALTER TABLE chunks ADD COLUMN content TEXT")
                changed = True
            if "version" not in columns:
                conn.execute(
                    "ALTER TABLE chunks ADD COLUMN version INTEGER DEFAULT 1"
                )
                changed = True
            if changed:
                conn.commit()

    def store_chunk(
        self,
        chunk_id: str,
        document_path: str,
        content_hash: str,
        semantic_signature: Any,
        start_pos: int,
        end_pos: int,
        token_count: int,
        content: Optional[str] = None,
    ) -> None:
        """
        Store chunk metadata (and optionally content) in database.

        Args:
            chunk_id: Unique identifier for the chunk
            document_path: Path to source document
            content_hash: SHA-256 hash of chunk content
            semantic_signature: Numpy array or list of semantic features
            start_pos: Start character position in document
            end_pos: End character position in document
            token_count: Estimated token count
            content: Optional text content to store for retrieval
        """
        now = time.time()

        sig_bytes = sig_to_bytes(semantic_signature)

        with sqlite3.connect(self.db_path) as conn:
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
            conn.commit()

    def get_chunk(self, chunk_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve chunk metadata by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
            )
            row = cursor.fetchone()

            if row is None:
                return None

            conn.execute(
                """
                UPDATE chunks
                SET last_accessed = ?, access_count = access_count + 1
                WHERE chunk_id = ?
            """,
                (time.time(), chunk_id),
            )
            conn.commit()

            return dict(row)

    def get_chunk_content(self, chunk_id: str) -> Optional[str]:
        """Retrieve chunk text content by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT content FROM chunks WHERE chunk_id = ?", (chunk_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return row[0]

    def search_chunks(
        self,
        document_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search chunks, returning metadata and content.

        Args:
            document_path: Optional filter by document path

        Returns:
            List of chunk dictionaries with metadata and content
        """
        with sqlite3.connect(self.db_path) as conn:
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

    def get_chunks_by_hash(self, content_hash: str) -> List[Dict[str, Any]]:
        """Find all chunks with a given content hash."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM chunks WHERE content_hash = ?", (content_hash,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_document_chunks(self, document_path: str) -> List[Dict[str, Any]]:
        """Get all chunks for a document."""
        return self.search_chunks(document_path=document_path)

    def store_document(
        self,
        document_path: str,
        content_hash: str,
        chunk_count: int,
        last_modified: float,
    ) -> None:
        """Store document indexing information."""
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO documents
                (document_path, content_hash, chunk_count, indexed_at, last_modified)
                VALUES (?, ?, ?, ?, ?)
            """,
                (document_path, content_hash, chunk_count, now, last_modified),
            )
            conn.commit()

    def get_document(self, document_path: str) -> Optional[Dict[str, Any]]:
        """Get document indexing information."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM documents WHERE document_path = ?", (document_path,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # Metadata methods — delegated to MetadataStorage

    def store_annotation(
        self,
        target: str,
        target_type: str,
        content: str,
        tags: Optional[List[str]] = None,
    ) -> int:
        """Store an annotation on a document or chunk."""
        return self._metadata_storage.store_annotation(target, target_type, content, tags)

    def get_annotations(
        self,
        target: Optional[str] = None,
        target_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve annotations with optional filters."""
        return self._metadata_storage.get_annotations(target, target_type, tags)

    def delete_annotation(self, annotation_id: int) -> bool:
        """Delete an annotation by ID."""
        return self._metadata_storage.delete_annotation(annotation_id)

    def record_change(
        self,
        summary: Dict[str, Any],
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> int:
        """Record a change history entry."""
        return self._metadata_storage.record_change(summary, session_id, reason)

    def get_change_history(
        self,
        limit: int = 20,
        document_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve change history entries."""
        return self._metadata_storage.get_change_history(limit, document_path)

    def update_annotation(
        self,
        annotation_id: int,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> bool:
        """Update an annotation's content and/or tags."""
        return self._metadata_storage.update_annotation(annotation_id, content, tags)

    def search_annotations(
        self, query: str, target_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search annotations by content text."""
        return self._metadata_storage.search_annotations(query, target_type)

    def prune_history(
        self,
        max_age_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
    ) -> int:
        """Prune change history entries."""
        return self._metadata_storage.prune_history(max_age_seconds, max_entries)

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all indexed documents."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM documents ORDER BY document_path"
            )
            return [dict(row) for row in cursor.fetchall()]

    # Session methods — delegated to SessionStorage

    def create_session(self, session_id: str) -> None:
        """Create a new KV-cache session."""
        self._session_storage.create_session(session_id)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session information."""
        return self._session_storage.get_session(session_id)

    def update_session(
        self,
        session_id: str,
        turn_count: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        """Update session metadata."""
        self._session_storage.update_session(session_id, turn_count, total_tokens)

    def store_kv_state(
        self,
        session_id: str,
        chunk_id: str,
        turn_number: int,
        kv_data: Any,
        relevance_score: float = 1.0,
    ) -> str:
        """Store KV-cache state for a chunk in a session."""
        return self._session_storage.store_kv_state(
            session_id, chunk_id, turn_number, kv_data, relevance_score
        )

    def load_kv_state(
        self,
        session_id: str,
        chunk_id: str,
        turn_number: int,
    ) -> Optional[Any]:
        """Load KV-cache state for a chunk in a session."""
        return self._session_storage.load_kv_state(session_id, chunk_id, turn_number)

    def get_session_chunks(
        self,
        session_id: str,
        turn_number: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get all chunks associated with a session."""
        return self._session_storage.get_session_chunks(session_id, turn_number)

    def rollback_session(self, session_id: str, target_turn: int) -> int:
        """Rollback session to a previous turn."""
        return self._session_storage.rollback_session(session_id, target_turn)

    def prune_chunks(self, session_id: str, max_tokens: int) -> int:
        """Prune low-relevance chunks to stay under token limit."""
        return self._session_storage.prune_chunks(session_id, max_tokens)

    def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        with sqlite3.connect(self.db_path) as conn:
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
        db_size = self.db_path.stat().st_size if self.db_path.exists() else 0

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
        }

    def clear_all(self) -> None:
        """Clear all stored data."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM session_chunks")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM chunk_history")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM annotations")
            conn.execute("DELETE FROM change_history")
            conn.commit()

        for kv_file in self.kv_dir.rglob("*.kv"):
            kv_file.unlink()

    def get_chunk_history(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get version history for a chunk."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT * FROM chunk_history
                WHERE chunk_id = ?
                ORDER BY version DESC
            """,
                (chunk_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
