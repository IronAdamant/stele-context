"""Tests for chunk history query tools."""

from stele.storage import StorageBackend
from stele.engine import Stele


class TestStorageChunkHistory:
    """Tests for StorageBackend.get_chunk_history()."""

    def test_empty_history(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        result = storage.get_chunk_history()
        assert result == []

    def test_history_after_update(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128

        # Store initial version
        storage.store_chunk("chunk-1", "doc.py", "hash1", sig, 0, 100, 50, "content v1")

        # Update (creates history entry)
        storage.store_chunk("chunk-1", "doc.py", "hash2", sig, 0, 100, 50, "content v2")

        history = storage.get_chunk_history(chunk_id="chunk-1")
        assert len(history) == 1
        assert history[0]["chunk_id"] == "chunk-1"
        assert history[0]["version"] == 1
        assert history[0]["content_hash"] == "hash1"

    def test_history_by_document_path(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128

        # Create and update chunks in two different docs
        storage.store_chunk("c1", "a.py", "h1", sig, 0, 10, 5, "a1")
        storage.store_chunk("c1", "a.py", "h2", sig, 0, 10, 5, "a2")

        storage.store_chunk("c2", "b.py", "h3", sig, 0, 10, 5, "b1")
        storage.store_chunk("c2", "b.py", "h4", sig, 0, 10, 5, "b2")

        hist_a = storage.get_chunk_history(document_path="a.py")
        assert len(hist_a) == 1
        assert hist_a[0]["document_path"] == "a.py"

        hist_b = storage.get_chunk_history(document_path="b.py")
        assert len(hist_b) == 1
        assert hist_b[0]["document_path"] == "b.py"

    def test_history_limit(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128

        # Create 5 versions
        for i in range(5):
            storage.store_chunk("c1", "doc.py", f"h{i}", sig, 0, 10, 5, f"v{i}")

        # 4 history entries (first version has no history)
        all_hist = storage.get_chunk_history(chunk_id="c1")
        assert len(all_hist) == 4

        limited = storage.get_chunk_history(chunk_id="c1", limit=2)
        assert len(limited) == 2

    def test_history_all_no_filter(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128

        storage.store_chunk("c1", "a.py", "h1", sig, 0, 10, 5, "a1")
        storage.store_chunk("c1", "a.py", "h2", sig, 0, 10, 5, "a2")
        storage.store_chunk("c2", "b.py", "h3", sig, 0, 10, 5, "b1")
        storage.store_chunk("c2", "b.py", "h4", sig, 0, 10, 5, "b2")

        all_hist = storage.get_chunk_history()
        assert len(all_hist) == 2


class TestEngineChunkHistory:
    """Tests for engine.get_chunk_history()."""

    def test_engine_chunk_history_empty(self, tmp_path):
        (tmp_path / ".git").mkdir()
        engine = Stele(
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        history = engine.get_chunk_history()
        assert history == []

    def test_engine_chunk_history_via_storage(self, tmp_path):
        """Chunk history records versions when the same chunk_id is updated."""
        (tmp_path / ".git").mkdir()
        engine = Stele(
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        sig = [0.0] * 128
        # Directly store and update to create history
        engine.storage.store_chunk("c1", "test.py", "h1", sig, 0, 10, 5, "v1")
        engine.storage.store_chunk("c1", "test.py", "h2", sig, 0, 10, 5, "v2")

        history = engine.get_chunk_history(document_path="test.py")
        assert len(history) == 1
        assert history[0]["document_path"] == "test.py"
        assert history[0]["content_hash"] == "h1"

    def test_engine_chunk_history_by_chunk_id(self, tmp_path):
        (tmp_path / ".git").mkdir()
        engine = Stele(
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        sig = [0.0] * 128
        engine.storage.store_chunk("c1", "a.py", "h1", sig, 0, 10, 5, "v1")
        engine.storage.store_chunk("c1", "a.py", "h2", sig, 0, 10, 5, "v2")
        engine.storage.store_chunk("c1", "a.py", "h3", sig, 0, 10, 5, "v3")

        history = engine.get_chunk_history(chunk_id="c1")
        assert len(history) == 2  # 2 previous versions
