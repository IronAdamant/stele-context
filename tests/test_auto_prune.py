"""Tests for bounded history auto-pruning and doctor growth alerts."""

from __future__ import annotations

import sqlite3
import time

from stele_context.engine import Stele


class TestAutoPruneHistory:
    def test_change_history_stays_bounded(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"), max_history_entries=5)
        f = tmp_path / "doc.py"
        f.write_text("def f():\n    return 0\n")
        engine.index_documents([str(f)])
        for i in range(10):
            engine.detect_changes_and_update(f"sess-{i}", scan_new=False)
        history = engine.storage.get_change_history(limit=100)
        assert 0 < len(history) <= 5

    def test_zero_disables_auto_prune(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"), max_history_entries=0)
        f = tmp_path / "doc.py"
        f.write_text("def f():\n    return 0\n")
        engine.index_documents([str(f)])
        for i in range(10):
            engine.detect_changes_and_update(f"sess-{i}", scan_new=False)
        history = engine.storage.get_change_history(limit=100)
        assert len(history) >= 10

    def test_index_documents_prunes_preexisting_bloat(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"), max_history_entries=5)
        for i in range(20):
            engine.storage.record_change({"n": i}, session_id="seed")
        f = tmp_path / "doc.py"
        f.write_text("def f():\n    return 0\n")
        engine.index_documents([str(f)])
        history = engine.storage.get_change_history(limit=100)
        assert len(history) <= 5

    def test_config_file_sets_bound(self, tmp_path):
        root = tmp_path / "proj"
        root.mkdir()
        (root / ".stele-context.toml").write_text(
            "[stele-context]\nmax_history_entries = 3\n"
        )
        engine = Stele(storage_dir=str(tmp_path / "storage"), project_root=str(root))
        assert engine.max_history_entries == 3

    def test_detect_changes_also_prunes(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"), max_history_entries=4)
        f = tmp_path / "doc.py"
        f.write_text("def f():\n    return 0\n")
        engine.index_documents([str(f)])
        for i in range(8):
            engine.detect_changes_and_update(f"sess-{i}", scan_new=False)
        history = engine.storage.get_change_history(limit=100)
        assert len(history) <= 4


class TestPruneOperationLog:
    def test_prune_keeps_newest(self, stele_engine):
        storage = stele_engine.storage
        for i in range(30):
            storage.log_operation(f"tool_{i}", True, None, 1.0)
        deleted = storage.prune_operation_log(10)
        assert deleted == 20
        remaining = storage.get_operation_log(limit=100)
        assert len(remaining) == 10

    def test_engine_prune_history_compacts_telemetry(self, stele_engine):
        storage = stele_engine.storage
        for _ in range(50):
            storage.log_operation("noisy_tool", True, None, 1.0)
        result = stele_engine.prune_history(max_entries=2)
        assert result["operation_log_pruned"] == 30
        assert len(storage.get_operation_log(limit=100)) == 20


class TestDoctorGrowthAlerts:
    def test_snapshot_has_growth_fields(self, stele_engine):
        snap = stele_engine.storage.get_db_health_snapshot()
        assert "table_rows" in snap
        assert "growth_alerts" in snap
        assert snap["growth_alerts"] == []
        assert set(snap["table_rows"]) == {
            "change_history",
            "chunk_history",
            "operation_log",
            "sessions",
        }

    def test_bloated_change_history_triggers_alert(self, stele_engine):
        db_path = stele_engine.storage.db_path
        now = time.time()
        with sqlite3.connect(db_path) as conn:
            conn.executemany(
                "INSERT INTO change_history (timestamp, session_id, summary_json, reason) "
                "VALUES (?, ?, ?, ?)",
                [(now, "s", "{}", "test") for _ in range(5001)],
            )
        snap = stele_engine.storage.get_db_health_snapshot()
        assert any("change_history" in a for a in snap["growth_alerts"])
