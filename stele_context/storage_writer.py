"""
Single-writer queue for SQLite operations.

One daemon thread owns a dedicated SQLite write connection. All write
operations are submitted to this queue and executed serially, eliminating
write contention under multi-threaded load.

Zero external dependencies — uses only stdlib threading and queue.
"""

from __future__ import annotations

import sqlite3
import threading
import queue
from pathlib import Path
from typing import Any
from collections.abc import Callable


class WriterQueue:
    """Serialises SQLite write operations through a single daemon thread."""

    _instances: dict[Path, WriterQueue] = {}
    _lock = threading.Lock()

    _db_path: Path
    _queue: queue.SimpleQueue
    _thread: threading.Thread

    def __new__(cls, db_path: Path) -> WriterQueue:
        with cls._lock:
            if db_path not in cls._instances:
                instance = super().__new__(cls)
                instance._db_path = db_path
                instance._queue = queue.SimpleQueue()
                instance._thread = threading.Thread(
                    target=instance._run, daemon=True, name="stele-writer"
                )
                instance._thread.start()
                cls._instances[db_path] = instance
            return cls._instances[db_path]

    def _run(self) -> None:
        conn = sqlite3.connect(self._db_path, timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        while True:
            item = self._queue.get()
            if item is None:
                break
            func, args, kwargs, event, result_box = item
            try:
                result_box[0] = func(conn, *args, **kwargs)
                result_box[1] = None
            except Exception as e:
                result_box[0] = None
                result_box[1] = e
            event.set()

    def submit(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Submit a callable to the writer thread and block for the result."""
        event = threading.Event()
        result_box: list[Any] = [None, None]
        self._queue.put((func, args, kwargs, event, result_box))
        if not event.wait(timeout=60.0):
            raise sqlite3.OperationalError(
                "Writer queue timeout — database write did not complete within 60s. "
                "Try running vacuum_db or rebuild_symbols to reduce load."
            )
        if result_box[1] is not None:
            raise result_box[1]
        return result_box[0]

    def close(self) -> None:
        """Shut down the writer thread. Safe to call multiple times."""
        with self._lock:
            if self._db_path in self._instances:
                self._queue.put(None)
                self._thread.join(timeout=5.0)
                del self._instances[self._db_path]
