"""
Tests for multi-agent conflict prevention features.

Covers:
- Per-document ownership (acquire, release, expiry, force-steal)
- Optimistic locking (version check-and-swap)
- Conflict resolution (detection, logging, history)
- Backward compatibility (all new params default to None)
- Concurrent ownership races
"""

import threading
import time

import pytest

from stele.engine import Stele


@pytest.fixture
def engine(tmp_path):
    """Create a Stele engine with one indexed document."""
    e = Stele(storage_dir=str(tmp_path / "stele_data"), enable_coordination=False)
    f = tmp_path / "doc.py"
    f.write_text("def hello():\n    return 'world'\n")
    e.index_documents([str(f)])
    return e, str(f), tmp_path


# ---------------------------------------------------------------------------
# Per-document ownership
# ---------------------------------------------------------------------------


class TestDocumentOwnership:
    def test_acquire_and_release(self, engine):
        e, doc, _ = engine
        result = e.acquire_document_lock(doc, "agent-a")
        assert result["acquired"] is True

        result = e.release_document_lock(doc, "agent-a")
        assert result["released"] is True

    def test_second_agent_blocked(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        result = e.acquire_document_lock(doc, "agent-b")
        assert result["acquired"] is False
        assert result["locked_by"] == "agent-a"

    def test_expired_lock_reclaimed(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a", ttl=0.01)
        time.sleep(0.02)

        result = e.acquire_document_lock(doc, "agent-b")
        assert result["acquired"] is True

    def test_force_steal_lock(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        result = e.acquire_document_lock(doc, "agent-b", force=True)
        assert result["acquired"] is True

        # Conflict should be logged
        conflicts = e.get_conflicts(document_path=doc)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "lock_stolen"

    def test_release_wrong_agent_fails(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        result = e.release_document_lock(doc, "agent-b")
        assert result["released"] is False
        assert result["reason"] == "not_owner"

    def test_release_agent_locks_bulk(self, engine):
        e, _, tmp_path = engine
        # Create multiple docs
        for i in range(3):
            f = tmp_path / f"multi_{i}.txt"
            f.write_text(f"Content {i}")
        e.index_documents([str(tmp_path / f"multi_{i}.txt") for i in range(3)])

        for i in range(3):
            e.acquire_document_lock(str(tmp_path / f"multi_{i}.txt"), "agent-a")

        result = e.release_agent_locks("agent-a")
        assert result["released_count"] == 3

    def test_lock_status_unlocked(self, engine):
        e, doc, _ = engine
        status = e.get_document_lock_status(doc)
        assert status["locked"] is False

    def test_lock_status_locked(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        status = e.get_document_lock_status(doc)
        assert status["locked"] is True
        assert status["locked_by"] == "agent-a"

    def test_index_blocked_by_lock(self, engine):
        e, doc, tmp_path = engine
        e.acquire_document_lock(doc, "agent-a")

        # Modify the file
        (tmp_path / "doc.py").write_text("def updated():\n    pass\n")

        # Agent B tries to index — should be blocked
        result = e.index_documents([doc], agent_id="agent-b")
        assert len(result["conflicts"]) == 1
        assert len(result["indexed"]) == 0

    def test_index_allowed_by_lock_holder(self, engine):
        e, doc, tmp_path = engine
        e.acquire_document_lock(doc, "agent-a")

        (tmp_path / "doc.py").write_text("def updated():\n    pass\n")

        result = e.index_documents([doc], agent_id="agent-a", force_reindex=True)
        assert len(result["indexed"]) == 1
        assert len(result["conflicts"]) == 0

    def test_remove_blocked_by_lock(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        with pytest.raises(PermissionError):
            e.remove_document(doc, agent_id="agent-b")

    def test_detect_changes_blocked_by_lock(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        result = e.detect_changes_and_update(
            session_id="s1",
            document_paths=[doc],
            agent_id="agent-b",
        )
        assert len(result["conflicts"]) == 1

    def test_same_agent_can_reacquire(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")
        result = e.acquire_document_lock(doc, "agent-a")
        assert result["acquired"] is True

    def test_refresh_lock(self, engine):
        """Refresh resets TTL without releasing."""
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a", ttl=0.05)

        result = e.refresh_document_lock(doc, "agent-a", ttl=300)
        assert result["refreshed"] is True

        # Lock should still be active (not expired)
        status = e.get_document_lock_status(doc)
        assert status["locked"] is True

    def test_refresh_wrong_agent_fails(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        result = e.refresh_document_lock(doc, "agent-b")
        assert result["refreshed"] is False
        assert result["reason"] == "not_owner"

    def test_refresh_keeps_current_ttl(self, engine):
        """Refresh without ttl param keeps the original TTL value."""
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a", ttl=600)

        result = e.refresh_document_lock(doc, "agent-a")
        assert result["refreshed"] is True
        # expires_at should be ~now + 600
        assert result["expires_at"] > time.time() + 500


# ---------------------------------------------------------------------------
# Optimistic locking
# ---------------------------------------------------------------------------


class TestOptimisticLocking:
    def test_version_after_initial_index(self, engine):
        e, doc, _ = engine
        version = e.storage.get_document_version(doc)
        # Initial index increments from default 1 → 2
        assert version == 2

    def test_version_increments_on_index(self, engine):
        e, doc, tmp_path = engine
        v_before = e.storage.get_document_version(doc)

        (tmp_path / "doc.py").write_text("def v2():\n    pass\n")
        e.index_documents([doc], force_reindex=True)

        version = e.storage.get_document_version(doc)
        assert version == v_before + 1

    def test_stale_version_rejected(self, engine):
        e, doc, tmp_path = engine
        (tmp_path / "doc.py").write_text("def v2():\n    pass\n")
        e.index_documents([doc], force_reindex=True)

        # Now version is 2, try with expected_version=1
        (tmp_path / "doc.py").write_text("def v3():\n    pass\n")
        result = e.index_documents(
            [doc],
            force_reindex=True,
            agent_id="agent-a",
            expected_versions={doc: 1},
        )
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0]["reason"] == "version_conflict"

    def test_correct_version_accepted(self, engine):
        e, doc, tmp_path = engine
        version = e.storage.get_document_version(doc)

        (tmp_path / "doc.py").write_text("def v2():\n    pass\n")
        result = e.index_documents(
            [doc],
            force_reindex=True,
            expected_versions={doc: version},
        )
        assert len(result["indexed"]) == 1
        assert len(result.get("conflicts", [])) == 0

    def test_conflict_recorded_on_version_mismatch(self, engine):
        e, doc, tmp_path = engine
        (tmp_path / "doc.py").write_text("def v2():\n    pass\n")
        e.index_documents([doc], force_reindex=True)

        (tmp_path / "doc.py").write_text("def v3():\n    pass\n")
        e.index_documents(
            [doc],
            force_reindex=True,
            agent_id="agent-a",
            expected_versions={doc: 1},
        )

        conflicts = e.get_conflicts(document_path=doc)
        assert len(conflicts) >= 1
        assert any(c["conflict_type"] == "version_conflict" for c in conflicts)

    def test_version_after_detect_changes(self, engine):
        e, doc, tmp_path = engine
        v_before = e.storage.get_document_version(doc)

        # Modify the file so detect_changes finds a change
        (tmp_path / "doc.py").write_text("def changed():\n    pass\n")
        result = e.detect_changes_and_update(session_id="s1")
        assert len(result["modified"]) > 0

        v_after = e.storage.get_document_version(doc)
        assert v_after == v_before + 1

    def test_version_unchanged_no_increment(self, engine):
        e, doc, _ = engine
        v_before = e.storage.get_document_version(doc)
        e.detect_changes_and_update(session_id="s1")

        v_after = e.storage.get_document_version(doc)
        assert v_after == v_before


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------


class TestConflictResolution:
    def test_ownership_violation_logged(self, engine):
        e, doc, tmp_path = engine
        e.acquire_document_lock(doc, "agent-a")

        (tmp_path / "doc.py").write_text("def bad():\n    pass\n")
        e.index_documents([doc], agent_id="agent-b", force_reindex=True)

        conflicts = e.get_conflicts()
        assert any(c["conflict_type"] == "ownership_violation" for c in conflicts)

    def test_get_conflicts_filter_by_doc(self, engine):
        e, doc, tmp_path = engine
        # Create a conflict
        e.acquire_document_lock(doc, "agent-a")
        e.index_documents([doc], agent_id="agent-b", force_reindex=True)

        # Create another doc with no conflicts
        f2 = tmp_path / "other.txt"
        f2.write_text("No conflicts here.")
        e.index_documents([str(f2)])

        conflicts = e.get_conflicts(document_path=doc)
        assert all(c["document_path"] == doc for c in conflicts)

    def test_get_conflicts_filter_by_agent(self, engine):
        e, doc, tmp_path = engine
        e.acquire_document_lock(doc, "agent-a")
        e.index_documents([doc], agent_id="agent-b", force_reindex=True)

        conflicts = e.get_conflicts(agent_id="agent-b")
        assert len(conflicts) >= 1

        conflicts = e.get_conflicts(agent_id="agent-c")
        assert len(conflicts) == 0

    def test_prune_conflicts(self, engine):
        e, doc, _ = engine
        # Generate conflicts
        e.acquire_document_lock(doc, "agent-a")
        for i in range(5):
            e.index_documents([doc], agent_id=f"agent-{i}", force_reindex=True)

        before = len(e.get_conflicts(limit=100))
        assert before >= 5

        pruned = e.storage.prune_conflicts(max_entries=2)
        assert pruned > 0

        after = len(e.get_conflicts(limit=100))
        assert after <= 2


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestExpiredLockReaping:
    def test_reap_expired_locks(self, engine):
        """reap_expired_locks clears all expired locks in one call."""
        e, doc, tmp_path = engine
        # Create multiple docs with short-TTL locks
        for i in range(3):
            f = tmp_path / f"reap_{i}.txt"
            f.write_text(f"Content {i}")
        e.index_documents([str(tmp_path / f"reap_{i}.txt") for i in range(3)])
        for i in range(3):
            e.acquire_document_lock(
                str(tmp_path / f"reap_{i}.txt"), f"agent-{i}", ttl=0.01
            )
        time.sleep(0.02)

        result = e.reap_expired_locks()
        assert result["reaped_count"] == 3
        assert len(result["documents"]) == 3

        # All locks should now be cleared
        for i in range(3):
            status = e.get_document_lock_status(str(tmp_path / f"reap_{i}.txt"))
            assert status["locked"] is False

    def test_reap_preserves_active_locks(self, engine):
        """Active (non-expired) locks are not reaped."""
        e, doc, tmp_path = engine
        e.acquire_document_lock(doc, "agent-a", ttl=300)

        result = e.reap_expired_locks()
        assert result["reaped_count"] == 0

        status = e.get_document_lock_status(doc)
        assert status["locked"] is True

    def test_reap_no_locks(self, engine):
        """Reaping with no locks is a no-op."""
        e, doc, _ = engine
        result = e.reap_expired_locks()
        assert result["reaped_count"] == 0


class TestLockStatsInGetStats:
    def test_stats_include_lock_metrics(self, engine):
        """get_stats() surfaces lock and conflict counts."""
        e, doc, _ = engine
        stats = e.get_stats()
        storage = stats["storage"]

        assert "locked_documents" in storage
        assert "expired_locks" in storage
        assert "active_lock_agents" in storage
        assert "total_conflicts" in storage
        assert "last_conflict_at" in storage

    def test_stats_reflect_active_locks(self, engine):
        """Lock metrics update after acquiring locks."""
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        stats = e.get_stats()["storage"]
        assert stats["locked_documents"] == 1
        assert stats["active_lock_agents"] == 1

    def test_stats_reflect_conflicts(self, engine):
        """Conflict metrics update after conflicts occur."""
        e, doc, tmp_path = engine
        e.acquire_document_lock(doc, "agent-a")
        e.index_documents([doc], agent_id="agent-b", force_reindex=True)

        stats = e.get_stats()["storage"]
        assert stats["total_conflicts"] >= 1
        assert stats["last_conflict_at"] is not None


class TestBackwardCompatibility:
    def test_index_without_agent_id(self, engine):
        e, doc, tmp_path = engine
        (tmp_path / "doc.py").write_text("def compat():\n    pass\n")
        result = e.index_documents([doc], force_reindex=True)
        assert len(result["indexed"]) == 1
        assert len(result.get("conflicts", [])) == 0

    def test_detect_changes_without_agent_id(self, engine):
        e, doc, _ = engine
        result = e.detect_changes_and_update(session_id="s1")
        assert "unchanged" in result
        assert "conflicts" in result
        assert len(result["conflicts"]) == 0

    def test_remove_without_agent_id(self, engine):
        e, doc, _ = engine
        result = e.remove_document(doc)
        assert result["removed"] is True

    def test_existing_documents_no_lock(self, engine):
        e, doc, _ = engine
        status = e.get_document_lock_status(doc)
        assert status["locked"] is False

    def test_lock_does_not_block_read(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        # Reads should still work regardless of lock
        results = e.search("hello")
        assert isinstance(results, list)

        stats = e.get_stats()
        assert "version" in stats


# ---------------------------------------------------------------------------
# Concurrent ownership races
# ---------------------------------------------------------------------------


class TestConcurrentOwnership:
    def test_two_agents_race_for_lock(self, engine):
        e, doc, _ = engine
        results = {"a": None, "b": None}

        def try_lock(agent, key):
            results[key] = e.acquire_document_lock(doc, agent)

        t1 = threading.Thread(target=try_lock, args=("agent-a", "a"))
        t2 = threading.Thread(target=try_lock, args=("agent-b", "b"))
        t1.start()
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)

        # Exactly one should win
        acquired = [k for k, v in results.items() if v and v.get("acquired")]
        assert len(acquired) == 1

    def test_locked_document_allows_concurrent_reads(self, engine):
        e, doc, _ = engine
        e.acquire_document_lock(doc, "agent-a")

        results = []
        errors = []

        def do_search():
            try:
                r = e.search("hello")
                results.append(r)
            except Exception as ex:
                errors.append(ex)

        threads = [threading.Thread(target=do_search) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert len(results) == 5
