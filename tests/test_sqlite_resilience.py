"""Stress tests for SQLite single-writer queue resilience."""

from __future__ import annotations

import threading
from stele_context import Stele


class TestSQLiteResilience:
    def test_concurrent_index_documents_no_busy_errors(self, tmp_path):
        """Fire 50 concurrent index calls and ensure zero OperationalError."""
        stele = Stele(storage_dir=str(tmp_path))
        errors: list[Exception] = []

        def _index(i: int) -> None:
            path = tmp_path / f"file_{i}.py"
            path.write_text(f"def func_{i}(): pass\n")
            try:
                stele.index_documents([str(path)])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_index, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        sqlite_busy = [
            e
            for e in errors
            if "database is locked" in str(e).lower()
            or "busy" in str(e).lower()
            or "unable to open database file" in str(e).lower()
        ]
        assert not sqlite_busy, f"SQLite contention errors: {sqlite_busy}"
        stats = stele.get_stats()
        assert stats["storage"]["document_count"] == 50

    def test_vacuum_db_returns_health(self, tmp_path):
        stele = Stele(storage_dir=str(tmp_path))
        health = stele.storage.vacuum_db()
        assert health["checkpointed"] is True
        assert health["vacuumed"] is True
        assert "estimated_size_bytes" in health
