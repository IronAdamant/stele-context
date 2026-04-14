"""
Database schema initialization and migrations for Stele.

Contains all CREATE TABLE statements, indexes, and ALTER TABLE
migrations.  Called from StorageBackend.__init__().
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from stele_context.connection_pool import ConnectionPool

# Module-level pool, initialized by StorageBackend.__init__().
_pool: ConnectionPool | None = None


def init_pool(db_path: Path) -> ConnectionPool:
    """Initialize the global thread-local connection pool."""
    global _pool
    _pool = ConnectionPool(db_path)
    return _pool


def get_pool() -> ConnectionPool | None:
    """Return the active pool (for shutdown/testing)."""
    return _pool


@contextmanager
def connect(db_path: Any) -> Any:
    """Context manager that yields a SQLite connection.

    If a connection pool is active and ``db_path`` matches, reuses the
    thread-local connection (zero overhead).  Otherwise falls back to a
    fresh connection (backward-compat for coordination DB, tests, etc.).

    The context manager commits on success and rolls back on exception,
    matching the stdlib ``with sqlite3.connect(...) as conn:`` behavior.
    """
    if _pool is not None and _pool.db_path == db_path:
        conn = _pool.get()
        conn.row_factory = None  # reset to default each time
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    else:
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.row_factory = None  # reset to default for consistency
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_database(db_path: Path) -> None:
    """Create SQLite database with required tables and indexes."""
    with sqlite3.connect(db_path) as conn:
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
            "CREATE INDEX IF NOT EXISTS idx_session_chunks_session "
            "ON session_chunks(session_id)"
        )

        conn.commit()


def migrate_database(db_path: Path) -> None:
    """Run database migrations for schema changes."""
    with connect(db_path) as conn:
        # Chunks table migrations
        cursor = conn.execute("PRAGMA table_info(chunks)")
        columns = {row[1] for row in cursor.fetchall()}
        for col, ddl in [
            ("content", "ALTER TABLE chunks ADD COLUMN content TEXT"),
            ("version", "ALTER TABLE chunks ADD COLUMN version INTEGER DEFAULT 1"),
            (
                "staleness_score",
                "ALTER TABLE chunks ADD COLUMN staleness_score REAL DEFAULT 0.0",
            ),
            ("semantic_summary", "ALTER TABLE chunks ADD COLUMN semantic_summary TEXT"),
            ("agent_signature", "ALTER TABLE chunks ADD COLUMN agent_signature BLOB"),
        ]:
            if col not in columns:
                conn.execute(ddl)

        # Sessions table: agent_id
        cursor = conn.execute("PRAGMA table_info(sessions)")
        session_columns = {row[1] for row in cursor.fetchall()}
        if "agent_id" not in session_columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN agent_id TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id)"
            )

        # Documents table: file_size for mtime+size fast-path
        cursor = conn.execute("PRAGMA table_info(documents)")
        doc_columns = {row[1] for row in cursor.fetchall()}
        if "file_size" not in doc_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN file_size INTEGER")

        # Documents table: ownership + optimistic locking
        if "locked_by" not in doc_columns:
            conn.execute("ALTER TABLE documents ADD COLUMN locked_by TEXT")
            conn.execute("ALTER TABLE documents ADD COLUMN locked_at REAL")
            conn.execute("ALTER TABLE documents ADD COLUMN lock_ttl REAL DEFAULT 300.0")
        if "doc_version" not in doc_columns:
            conn.execute(
                "ALTER TABLE documents ADD COLUMN doc_version INTEGER DEFAULT 1"
            )

        # Index on staleness_score for fast stale-chunk queries
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_staleness ON chunks(staleness_score)"
        )

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

        # Chunks: optional JSON agent_notes (facts, invariants, LLM scratchpad)
        cursor = conn.execute("PRAGMA table_info(chunks)")
        chunk_cols = {row[1] for row in cursor.fetchall()}
        if "agent_notes" not in chunk_cols:
            conn.execute("ALTER TABLE chunks ADD COLUMN agent_notes TEXT")

        # Session search history: tracks every grep/search_text run per session
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                pattern TEXT NOT NULL,
                tool TEXT NOT NULL,
                files_checked INTEGER NOT NULL,
                files_with_matches INTEGER NOT NULL,
                searched_at REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_searches_session "
            "ON session_searches(session_id)"
        )

        # Session file reads: tracks which files were fully read via get_context
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_file_reads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                document_path TEXT NOT NULL,
                chunk_ids_json TEXT NOT NULL,
                read_at REAL NOT NULL,
                UNIQUE(session_id, document_path)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reads_session "
            "ON session_file_reads(session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reads_doc "
            "ON session_file_reads(document_path)"
        )
