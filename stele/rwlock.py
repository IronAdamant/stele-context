"""
Read-write lock for Stele engine thread safety.

Allows multiple concurrent readers OR a single exclusive writer.
Uses only stdlib (threading.Lock + threading.Condition).
"""

from __future__ import annotations

import threading
from contextlib import contextmanager


class RWLock:
    """Simple read-write lock.

    Multiple readers can hold the lock simultaneously.
    A writer gets exclusive access (blocks all readers and other writers).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._readers = 0
        self._writer = False

    @contextmanager
    def read_lock(self):
        """Context manager for shared read access."""
        with self._cond:
            while self._writer:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextmanager
    def write_lock(self):
        """Context manager for exclusive write access."""
        with self._cond:
            while self._writer or self._readers > 0:
                self._cond.wait()
            self._writer = True
        try:
            yield
        finally:
            with self._cond:
                self._writer = False
                self._cond.notify_all()
