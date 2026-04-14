"""
Thread-local SQLite connection pool for Stele.

Each thread reuses a single connection instead of opening a new one
per method call. Zero external dependencies — uses only stdlib
threading.local and weakref.

The pool integrates with the existing ``connect()`` helper in
storage_schema.py, which becomes pool-aware when a pool is initialized.
Delegate modules (storage.py, session_storage.py, etc.) require no changes.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class ConnectionPool:
    """Thread-local SQLite connection pool.

    Each thread gets a single reused connection, lazily created on first
    access.  All connections are tracked for ``close_all()`` cleanup.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()

    def get(self) -> sqlite3.Connection:
        """Return the connection for the current thread, creating if needed."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA synchronous=NORMAL")
        self._local.conn = conn

        with self._all_lock:
            self._all.append(conn)

        return conn

    def close_all(self) -> None:
        """Close every tracked connection (for shutdown / testing)."""
        with self._all_lock:
            for c in self._all:
                try:
                    c.close()
                except Exception:
                    pass
            self._all.clear()
        self._local.conn = None
