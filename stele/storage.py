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

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from stele.document_lock_storage import DocumentLockStorage
from stele.metadata_storage import MetadataStorage
from stele.session_storage import SessionStorage
from stele.symbol_storage import SymbolStorage

from stele.chunkers.numpy_compat import sig_to_bytes, sig_from_bytes


class StorageBackend:
    """
    Persistent storage backend for Stele.

    Manages SQLite database for metadata and filesystem for KV-cache blobs.
    Delegates session operations to SessionStorage.
    """

    def __init__(self, base_dir: Optional[str] = None):
        """
        Initialize storage backend.

        Args:
            base_dir: Base directory for storage. Defaults to ~/.stele/
        """
        if base_dir is None:
            base_dir = str(Path("~/.stele").expanduser())

        self.base_dir = Path(base_dir)
        self.db_path = self.base_dir / "stele.db"
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
        self._symbol_storage = SymbolStorage(self.db_path)
        self._document_lock_storage = DocumentLockStorage(self.db_path)

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
            if "staleness_score" not in columns:
                conn.execute(
                    "ALTER TABLE chunks ADD COLUMN staleness_score REAL DEFAULT 0.0"
                )
                changed = True
            if "semantic_summary" not in columns:
                conn.execute(
                    "ALTER TABLE chunks ADD COLUMN semantic_summary TEXT"
                )
                changed = True
            if "agent_signature" not in columns:
                conn.execute(
                    "ALTER TABLE chunks ADD COLUMN agent_signature BLOB"
                )
                changed = True

            # Migrate sessions table for agent_id
            cursor = conn.execute("PRAGMA table_info(sessions)")
            session_columns = {row[1] for row in cursor.fetchall()}
            if "agent_id" not in session_columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN agent_id TEXT")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sessions_agent "
                    "ON sessions(agent_id)"
                )
                changed = True

            # Migrate documents table for ownership + optimistic locking
            cursor = conn.execute("PRAGMA table_info(documents)")
            doc_columns = {row[1] for row in cursor.fetchall()}
            if "locked_by" not in doc_columns:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN locked_by TEXT"
                )
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN locked_at REAL"
                )
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN lock_ttl REAL DEFAULT 300.0"
                )
                changed = True
            if "doc_version" not in doc_columns:
                conn.execute(
                    "ALTER TABLE documents ADD COLUMN doc_version INTEGER DEFAULT 1"
                )
                changed = True

            # Conflict log table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS document_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_path TEXT NOT NULL,
                    agent_a TEXT NOT NULL,
                    agent_b TEXT NOT NULL,
                    conflict_type TEXT NOT NULL,
                    expected_version INTEGER,
                    actual_version INTEGER,
                    resolution TEXT,
                    details_json TEXT,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conflicts_doc "
                "ON document_conflicts(document_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conflicts_time "
                "ON document_conflicts(created_at)"
            )

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
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                UPDATE chunks
                SET last_accessed = ?, access_count = access_count + 1
                WHERE chunk_id = ?
            """,
                (now, chunk_id),
            )
            cursor = conn.execute(
                "SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)
            )
            row = cursor.fetchone()
            conn.commit()

            return dict(row) if row else None

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
        """Store document indexing information.

        Uses INSERT ... ON CONFLICT to preserve lock/version columns
        that would be lost with INSERT OR REPLACE.
        """
        now = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO documents
                (document_path, content_hash, chunk_count, indexed_at, last_modified)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(document_path) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    chunk_count = excluded.chunk_count,
                    indexed_at = excluded.indexed_at,
                    last_modified = excluded.last_modified
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

    # Symbol methods — delegated to SymbolStorage

    def store_symbols(self, symbols: Any) -> None:
        """Store a batch of Symbol objects."""
        self._symbol_storage.store_symbols(symbols)

    def store_edges(self, edges: Any) -> None:
        """Store a batch of symbol edges."""
        self._symbol_storage.store_edges(edges)

    def clear_document_symbols(self, document_path: str) -> None:
        """Remove all symbols for a document."""
        self._symbol_storage.clear_document_symbols(document_path)

    def clear_chunk_edges(self, chunk_ids: List[str]) -> None:
        """Remove all edges involving the given chunk IDs."""
        self._symbol_storage.clear_chunk_edges(chunk_ids)

    def clear_chunk_symbols(self, chunk_ids: List[str]) -> None:
        """Remove all symbols for the given chunk IDs."""
        self._symbol_storage.clear_chunk_symbols(chunk_ids)

    def clear_all_symbols(self) -> None:
        """Remove all symbols."""
        self._symbol_storage.clear_all_symbols()

    def clear_all_edges(self) -> None:
        """Remove all edges."""
        self._symbol_storage.clear_all_edges()

    def get_all_symbols(self) -> List[Dict[str, Any]]:
        """Get all symbols."""
        return self._symbol_storage.get_all_symbols()

    def find_definitions(self, name: str) -> List[Dict[str, Any]]:
        """Find all definitions for a symbol name."""
        return self._symbol_storage.find_definitions(name)

    def find_references_by_name(self, name: str) -> List[Dict[str, Any]]:
        """Find all references to a symbol name."""
        return self._symbol_storage.find_references_by_name(name)

    def get_edges_for_chunk(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get all edges involving a chunk."""
        return self._symbol_storage.get_edges_for_chunk(chunk_id)

    def get_incoming_edges(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get edges where other chunks reference this chunk."""
        return self._symbol_storage.get_incoming_edges(chunk_id)

    def get_outgoing_edges(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get edges where this chunk references other chunks."""
        return self._symbol_storage.get_outgoing_edges(chunk_id)

    def search_symbol_names(self, tokens: List[str]) -> List[Dict[str, Any]]:
        """Find definition symbols whose names match query tokens."""
        return self._symbol_storage.search_symbol_names(tokens)

    def get_symbol_stats(self) -> Dict[str, Any]:
        """Get symbol and edge statistics."""
        return self._symbol_storage.get_symbol_stats()

    # Agent-supplied semantic embedding methods

    def store_semantic_summary(
        self,
        chunk_id: str,
        summary: str,
        agent_signature: Any,
    ) -> bool:
        """Store an agent-supplied semantic summary and its computed signature.

        Args:
            chunk_id: Chunk to annotate with semantic summary
            summary: Agent's semantic description of the chunk
            agent_signature: 128-dim signature computed from the summary

        Returns:
            True if chunk was found and updated, False otherwise.
        """
        sig_bytes = sig_to_bytes(agent_signature)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE chunks SET semantic_summary = ?, agent_signature = ? "
                "WHERE chunk_id = ?",
                (summary, sig_bytes, chunk_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def store_agent_signature(
        self,
        chunk_id: str,
        agent_signature: Any,
    ) -> bool:
        """Store a raw agent-supplied embedding vector.

        Args:
            chunk_id: Chunk to update
            agent_signature: 128-dim vector from agent

        Returns:
            True if chunk was found and updated, False otherwise.
        """
        sig_bytes = sig_to_bytes(agent_signature)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE chunks SET agent_signature = ? WHERE chunk_id = ?",
                (sig_bytes, chunk_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_agent_signature(self, chunk_id: str) -> Optional[Any]:
        """Get agent-supplied signature for a chunk, if any."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT agent_signature FROM chunks WHERE chunk_id = ?",
                (chunk_id,),
            )
            row = cursor.fetchone()
            if row is None or row[0] is None:
                return None
            return sig_from_bytes(row[0])

    # Chunk history methods

    def get_chunk_history(
        self,
        chunk_id: Optional[str] = None,
        document_path: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query chunk version history.

        Args:
            chunk_id: Filter by specific chunk ID
            document_path: Filter by document path (joins via chunks table)
            limit: Max entries to return

        Returns:
            List of history entries with chunk_id, version, content_hash,
            created_at, and document_path.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if chunk_id and document_path:
                cursor = conn.execute(
                    "SELECT h.chunk_id, h.version, h.content_hash, "
                    "h.created_at, c.document_path "
                    "FROM chunk_history h "
                    "JOIN chunks c ON h.chunk_id = c.chunk_id "
                    "WHERE h.chunk_id = ? AND c.document_path = ? "
                    "ORDER BY h.created_at DESC LIMIT ?",
                    (chunk_id, document_path, limit),
                )
            elif chunk_id:
                cursor = conn.execute(
                    "SELECT h.chunk_id, h.version, h.content_hash, "
                    "h.created_at, c.document_path "
                    "FROM chunk_history h "
                    "JOIN chunks c ON h.chunk_id = c.chunk_id "
                    "WHERE h.chunk_id = ? "
                    "ORDER BY h.created_at DESC LIMIT ?",
                    (chunk_id, limit),
                )
            elif document_path:
                cursor = conn.execute(
                    "SELECT h.chunk_id, h.version, h.content_hash, "
                    "h.created_at, c.document_path "
                    "FROM chunk_history h "
                    "JOIN chunks c ON h.chunk_id = c.chunk_id "
                    "WHERE c.document_path = ? "
                    "ORDER BY h.created_at DESC LIMIT ?",
                    (document_path, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT h.chunk_id, h.version, h.content_hash, "
                    "h.created_at, c.document_path "
                    "FROM chunk_history h "
                    "JOIN chunks c ON h.chunk_id = c.chunk_id "
                    "ORDER BY h.created_at DESC LIMIT ?",
                    (limit,),
                )

            return [dict(row) for row in cursor.fetchall()]

    # Staleness methods

    def set_staleness(self, chunk_id: str, score: float) -> None:
        """Set staleness score for a chunk."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE chunks SET staleness_score = ? WHERE chunk_id = ?",
                (score, chunk_id),
            )
            conn.commit()

    def set_staleness_batch(self, updates: List[tuple]) -> None:
        """Set staleness for multiple chunks. Each: (score, chunk_id)."""
        if not updates:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE chunks SET staleness_score = ? WHERE chunk_id = ?",
                updates,
            )
            conn.commit()

    def clear_staleness(self) -> None:
        """Reset all staleness scores to 0."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE chunks SET staleness_score = 0.0")
            conn.commit()

    def get_stale_chunks(self, threshold: float = 0.3) -> List[Dict[str, Any]]:
        """Get chunks with staleness_score >= threshold."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT chunk_id, document_path, staleness_score, token_count, content "
                "FROM chunks WHERE staleness_score >= ? "
                "ORDER BY staleness_score DESC",
                (threshold,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_documents(self) -> List[Dict[str, Any]]:
        """Get all indexed documents."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM documents ORDER BY document_path"
            )
            return [dict(row) for row in cursor.fetchall()]

    # Session methods — delegated to SessionStorage

    def create_session(
        self, session_id: str, agent_id: Optional[str] = None
    ) -> None:
        """Create a new KV-cache session."""
        self._session_storage.create_session(session_id, agent_id=agent_id)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session information."""
        return self._session_storage.get_session(session_id)

    def list_sessions(
        self, agent_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by agent_id."""
        return self._session_storage.list_sessions(agent_id=agent_id)

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

    def delete_chunks(self, chunk_ids: List[str]) -> int:
        """Delete chunks and their related data. Returns count deleted."""
        if not chunk_ids:
            return 0
        # Clean up symbols and edges first
        self._symbol_storage.clear_chunk_symbols(chunk_ids)
        self._symbol_storage.clear_chunk_edges(chunk_ids)

        placeholders = ",".join("?" * len(chunk_ids))
        with sqlite3.connect(self.db_path) as conn:
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
            conn.commit()
            return cursor.rowcount

    def remove_document(self, document_path: str) -> Dict[str, Any]:
        """Remove a document and all its chunks, annotations, and history."""
        doc = self.get_document(document_path)
        if doc is None:
            return {"removed": False}

        # Get chunk IDs before deleting
        chunks = self.get_document_chunks(document_path)
        chunk_ids = [c["chunk_id"] for c in chunks]

        # Clean up symbols and edges
        self._symbol_storage.clear_document_symbols(document_path)
        if chunk_ids:
            self._symbol_storage.clear_chunk_edges(chunk_ids)

        with sqlite3.connect(self.db_path) as conn:
            # Delete document-level annotations
            cursor = conn.execute(
                "DELETE FROM annotations WHERE target = ? AND target_type = 'document'",
                (document_path,),
            )
            annotations_removed = cursor.rowcount
            conn.commit()

        # Delegate chunk deletion (handles session_chunks, history, chunk annotations)
        chunks_removed = self.delete_chunks(chunk_ids)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM documents WHERE document_path = ?", (document_path,)
            )
            conn.commit()

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

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM session_chunks")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM chunk_history")
            conn.execute("DELETE FROM chunks")
            conn.execute("DELETE FROM documents")
            conn.execute("DELETE FROM annotations")
            conn.execute("DELETE FROM change_history")
            conn.execute("DELETE FROM document_conflicts")
            conn.commit()

        for kv_file in self.kv_dir.rglob("*.kv"):
            kv_file.unlink()

    # Document lock methods — delegated to DocumentLockStorage

    def acquire_document_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: float = 300.0,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Acquire exclusive ownership of a document."""
        return self._document_lock_storage.acquire_lock(
            document_path, agent_id, ttl, force
        )

    def refresh_document_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Refresh lock TTL without releasing."""
        return self._document_lock_storage.refresh_lock(
            document_path, agent_id, ttl
        )

    def release_document_lock(
        self, document_path: str, agent_id: str
    ) -> Dict[str, Any]:
        """Release ownership of a document."""
        return self._document_lock_storage.release_lock(document_path, agent_id)

    def get_document_lock_status(self, document_path: str) -> Dict[str, Any]:
        """Check lock status of a document."""
        return self._document_lock_storage.get_lock_status(document_path)

    def release_agent_locks(self, agent_id: str) -> Dict[str, Any]:
        """Release all locks held by an agent."""
        return self._document_lock_storage.release_agent_locks(agent_id)

    def check_and_increment_doc_version(
        self, document_path: str, expected_version: int
    ) -> Dict[str, Any]:
        """Atomic compare-and-swap on doc_version."""
        return self._document_lock_storage.check_and_increment_version(
            document_path, expected_version
        )

    def increment_doc_version(self, document_path: str) -> int:
        """Increment document version after write."""
        return self._document_lock_storage.increment_version(document_path)

    def get_document_version(self, document_path: str) -> Optional[int]:
        """Get current document version."""
        return self._document_lock_storage.get_version(document_path)

    def record_conflict(
        self,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        **kwargs: Any,
    ) -> int:
        """Log a conflict event."""
        return self._document_lock_storage.record_conflict(
            document_path, agent_a, agent_b, conflict_type, **kwargs
        )

    def get_conflicts(
        self,
        document_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Retrieve conflict history."""
        return self._document_lock_storage.get_conflicts(
            document_path, agent_id, limit
        )

    def prune_conflicts(
        self,
        max_age_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
    ) -> int:
        """Prune old conflict entries."""
        return self._document_lock_storage.prune_conflicts(
            max_age_seconds, max_entries
        )

    def reap_expired_locks(self) -> Dict[str, Any]:
        """Clear all expired document locks."""
        return self._document_lock_storage.reap_expired_locks()

    def get_lock_stats(self) -> Dict[str, Any]:
        """Get aggregate lock and conflict statistics."""
        return self._document_lock_storage.get_lock_stats()

