"""
Tests for multi-agent concurrency features.

Covers:
- RWLock semantics (concurrent reads, exclusive writes)
- BM25 lazy-init thread safety
- Agent-aware sessions (agent_id roundtrip, backward compat)
- Cross-process file locking for index persistence
"""

import multiprocessing
import threading
import time
from pathlib import Path

import pytest

from stele.rwlock import RWLock


# ---------------------------------------------------------------------------
# RWLock unit tests
# ---------------------------------------------------------------------------


class TestRWLock:
    def test_concurrent_reads(self):
        """Multiple readers can hold the lock simultaneously."""
        lock = RWLock()
        inside = threading.Event()
        barrier = threading.Barrier(3)
        results = []

        def reader(n):
            with lock.read_lock():
                barrier.wait(timeout=2)
                results.append(n)
                inside.set()

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3)

        assert len(results) == 3

    def test_write_excludes_reads(self):
        """Writer blocks concurrent readers."""
        lock = RWLock()
        timeline = []
        write_entered = threading.Event()
        write_done = threading.Event()

        def writer():
            with lock.write_lock():
                write_entered.set()
                timeline.append("write_start")
                time.sleep(0.1)
                timeline.append("write_end")
                write_done.set()

        def reader():
            write_entered.wait(timeout=2)
            time.sleep(0.02)  # Give writer time to hold lock
            with lock.read_lock():
                timeline.append("read")

        wt = threading.Thread(target=writer)
        rt = threading.Thread(target=reader)
        wt.start()
        rt.start()
        wt.join(timeout=3)
        rt.join(timeout=3)

        assert timeline.index("read") > timeline.index("write_end")

    def test_write_excludes_writes(self):
        """Only one writer at a time."""
        lock = RWLock()
        active = [0]
        max_active = [0]
        mu = threading.Lock()

        def writer():
            with lock.write_lock():
                with mu:
                    active[0] += 1
                    max_active[0] = max(max_active[0], active[0])
                time.sleep(0.02)
                with mu:
                    active[0] -= 1

        threads = [threading.Thread(target=writer) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert max_active[0] == 1


# ---------------------------------------------------------------------------
# Engine-level concurrency tests
# ---------------------------------------------------------------------------


@pytest.fixture
def engine_with_data(tmp_path):
    """Create a Stele engine with some indexed data."""
    from stele.engine import Stele

    engine = Stele(storage_dir=str(tmp_path / "stele_data"))

    # Create test files
    for i in range(3):
        f = tmp_path / f"test_{i}.txt"
        f.write_text(
            f"This is test document number {i} with some unique content about topic_{i}."
        )

    engine.index_documents([str(tmp_path / f"test_{i}.txt") for i in range(3)])
    return engine


class TestEngineConcurrentReads:
    def test_concurrent_searches(self, engine_with_data):
        """Multiple threads can search simultaneously."""
        engine = engine_with_data
        results = []
        errors = []

        def do_search(query):
            try:
                r = engine.search(query, top_k=2)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=do_search, args=(f"topic_{i}",)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors
        assert len(results) == 10
        for r in results:
            assert isinstance(r, list)

    def test_concurrent_stats(self, engine_with_data):
        """get_stats is safe under concurrent access."""
        engine = engine_with_data
        results = []

        def do_stats():
            results.append(engine.get_stats())

        threads = [threading.Thread(target=do_stats) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(results) == 5
        for r in results:
            assert r["storage"]["chunk_count"] == results[0]["storage"]["chunk_count"]


class TestWriteBlocksReads:
    def test_index_blocks_search(self, tmp_path):
        """index_documents holds write lock, blocking concurrent search."""
        from stele.engine import Stele

        engine = Stele(storage_dir=str(tmp_path / "stele_data"))
        f = tmp_path / "initial.txt"
        f.write_text("Initial content for searching.")
        engine.index_documents([str(f)])

        timeline = []
        index_started = threading.Event()

        def do_index():
            # Create several files to make indexing take longer
            for i in range(5):
                p = tmp_path / f"new_{i}.txt"
                p.write_text(f"New document {i} with content about indexing test {i}.")
            index_started.set()
            engine.index_documents([str(tmp_path / f"new_{i}.txt") for i in range(5)])
            timeline.append("index_done")

        def do_search():
            index_started.wait(timeout=2)
            time.sleep(0.01)  # Let index grab the lock first
            engine.search("content")
            timeline.append("search_done")

        t1 = threading.Thread(target=do_index)
        t2 = threading.Thread(target=do_search)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # Both should complete without error
        assert "index_done" in timeline
        assert "search_done" in timeline


class TestBM25LazyInitThreadSafe:
    def test_multiple_threads_trigger_ensure_bm25(self, engine_with_data):
        """Multiple threads calling search() trigger _ensure_bm25 safely."""
        engine = engine_with_data
        # Reset BM25 state to force re-init
        engine._bm25_ready = False
        engine.bm25_index = None

        results = []
        errors = []

        def do_search(i):
            try:
                r = engine.search(f"topic_{i % 3}", top_k=2)
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_search, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert not errors
        assert len(results) == 8
        assert engine._bm25_ready


# ---------------------------------------------------------------------------
# Agent-aware session tests
# ---------------------------------------------------------------------------


class TestAgentSessions:
    def test_agent_id_roundtrip(self, tmp_path):
        """Create session with agent_id, verify it's stored and queryable."""
        from stele.engine import Stele

        engine = Stele(storage_dir=str(tmp_path / "stele_data"))

        f = tmp_path / "doc.txt"
        f.write_text("Test document for agent session.")
        engine.index_documents([str(f)])

        engine.detect_changes_and_update(
            session_id="sess-1",
            agent_id="agent-alpha",
        )

        sessions = engine.list_sessions(agent_id="agent-alpha")
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-1"
        assert sessions[0]["agent_id"] == "agent-alpha"

    def test_agent_id_backward_compat(self, tmp_path):
        """Calls without agent_id still work (agent_id defaults to None)."""
        from stele.engine import Stele

        engine = Stele(storage_dir=str(tmp_path / "stele_data"))

        f = tmp_path / "doc.txt"
        f.write_text("Test document for backward compat.")
        engine.index_documents([str(f)])

        engine.detect_changes_and_update(session_id="sess-legacy")

        sessions = engine.list_sessions()
        assert any(s["session_id"] == "sess-legacy" for s in sessions)

        # Filtering by agent_id=None should NOT return this (it has no agent)
        filtered = engine.list_sessions(agent_id="nonexistent")
        assert not any(s["session_id"] == "sess-legacy" for s in filtered)

    def test_list_sessions_filter(self, tmp_path):
        """list_sessions correctly filters by agent_id."""
        from stele.engine import Stele

        engine = Stele(storage_dir=str(tmp_path / "stele_data"))

        f = tmp_path / "doc.txt"
        f.write_text("Test doc.")
        engine.index_documents([str(f)])

        engine.detect_changes_and_update(session_id="s1", agent_id="agent-a")
        engine.detect_changes_and_update(session_id="s2", agent_id="agent-b")
        engine.detect_changes_and_update(session_id="s3", agent_id="agent-a")

        a_sessions = engine.list_sessions(agent_id="agent-a")
        assert len(a_sessions) == 2
        assert {s["session_id"] for s in a_sessions} == {"s1", "s3"}

        b_sessions = engine.list_sessions(agent_id="agent-b")
        assert len(b_sessions) == 1

        all_sessions = engine.list_sessions()
        assert len(all_sessions) >= 3

    def test_save_kv_state_with_agent_id(self, tmp_path):
        """save_kv_state with agent_id creates/updates session."""
        from stele.engine import Stele

        engine = Stele(storage_dir=str(tmp_path / "stele_data"))

        f = tmp_path / "doc.txt"
        f.write_text("Content for KV test.")
        engine.index_documents([str(f)])

        engine.save_kv_state(
            session_id="kv-sess",
            kv_data={"some_chunk": {"key": "value"}},
            agent_id="agent-kv",
        )

        sessions = engine.list_sessions(agent_id="agent-kv")
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "kv-sess"


# ---------------------------------------------------------------------------
# Cross-process file locking tests
# ---------------------------------------------------------------------------


def _worker_save(args):
    """Worker function for multiprocessing test."""
    index_dir, worker_id = args
    from stele.index_store import _save_compressed_json

    data = {"_version": 1, "worker": worker_id, "payload": list(range(100))}
    _save_compressed_json(data, "test_index.json.zlib", Path(index_dir))
    return worker_id


class TestFileLockConcurrentSave:
    def test_concurrent_saves_no_corruption(self, tmp_path):
        """Multiple processes saving to the same file don't corrupt it."""
        from stele.index_store import _load_compressed_json

        index_dir = tmp_path / "indices"
        index_dir.mkdir()

        # Run 4 workers saving concurrently
        args = [(str(index_dir), i) for i in range(4)]
        with multiprocessing.Pool(4) as pool:
            results = pool.map(_worker_save, args)

        assert len(results) == 4

        # File should be valid (written by whichever worker finished last)
        data = _load_compressed_json("test_index.json.zlib", index_dir)
        assert data is not None
        assert data["_version"] == 1
        assert "worker" in data
        assert len(data["payload"]) == 100
