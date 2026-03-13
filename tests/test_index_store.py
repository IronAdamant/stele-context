"""Tests for persistent HNSW index serialization."""

from chunkforge.engine import ChunkForge
from chunkforge.index import HNSWIndex, VectorIndex
from chunkforge.index_store import (
    INDEX_FILENAME,
    compute_chunk_ids_hash,
    load_if_fresh,
    load_index,
    save_index,
)


class TestHNSWSerialization:
    """Tests for HNSWIndex to_dict/from_dict round-trip."""

    def test_empty_index_round_trip(self):
        """Test serializing and deserializing an empty index."""
        index = HNSWIndex(M=16, ef_construction=200, ef_search=50)
        data = index.to_dict()
        restored = HNSWIndex.from_dict(data)

        assert len(restored.nodes) == 0
        assert restored.entry_point is None
        assert restored.M == 16
        assert restored.ef_construction == 200
        assert restored.ef_search == 50

    def test_populated_index_round_trip(self):
        """Test round-trip with vectors inserted."""
        index = HNSWIndex()
        index.insert("a", [1.0, 0.0, 0.0])
        index.insert("b", [0.0, 1.0, 0.0])
        index.insert("c", [0.0, 0.0, 1.0])

        data = index.to_dict()
        restored = HNSWIndex.from_dict(data)

        assert len(restored.nodes) == 3
        assert set(restored.nodes.keys()) == {"a", "b", "c"}
        assert restored.entry_point is not None

        # Search should still work
        results = restored.search([1.0, 0.0, 0.0], k=1)
        assert len(results) == 1
        assert results[0][0] == "a"
        assert results[0][1] > 0.99

    def test_connections_preserved(self):
        """Test that graph connections survive round-trip."""
        index = HNSWIndex()
        for i in range(20):
            index.insert(f"v{i}", [float(i), float(i * 2), float(i * 3)])

        data = index.to_dict()
        restored = HNSWIndex.from_dict(data)

        # Check that connections exist at level 0
        for nid, node in restored.nodes.items():
            original = index.nodes[nid]
            assert node.connections[0] == original.connections[0]


class TestVectorIndexSerialization:
    """Tests for VectorIndex to_dict/from_dict round-trip."""

    def test_round_trip(self):
        """Test full VectorIndex round-trip."""
        vi = VectorIndex()
        vi.add_chunk("chunk1", [1.0, 0.0, 0.0])
        vi.add_chunk("chunk2", [0.0, 1.0, 0.0])

        data = vi.to_dict()
        restored = VectorIndex.from_dict(data)

        assert len(restored.chunk_vectors) == 2
        assert "chunk1" in restored.chunk_vectors
        assert restored.chunk_vectors["chunk1"] == [1.0, 0.0, 0.0]

        results = restored.search([1.0, 0.0, 0.0], k=1)
        assert results[0][0] == "chunk1"


class TestIndexStore:
    """Tests for persistent index save/load."""

    def test_save_and_load(self, tmp_path):
        """Test saving and loading index from disk."""
        vi = VectorIndex()
        vi.add_chunk("c1", [1.0, 2.0, 3.0])
        vi.add_chunk("c2", [4.0, 5.0, 6.0])

        save_index(vi, "test_hash", tmp_path)
        assert (tmp_path / INDEX_FILENAME).exists()

        data = load_index(tmp_path)
        assert data is not None
        assert data["_chunk_ids_hash"] == "test_hash"

    def test_load_nonexistent(self, tmp_path):
        """Test loading from empty directory returns None."""
        assert load_index(tmp_path) is None

    def test_load_corrupt_file(self, tmp_path):
        """Test loading corrupt file returns None."""
        (tmp_path / INDEX_FILENAME).write_bytes(b"not valid data")
        assert load_index(tmp_path) is None

    def test_load_if_fresh_matching(self, tmp_path):
        """Test load_if_fresh returns index when hash matches."""
        vi = VectorIndex()
        vi.add_chunk("c1", [1.0, 2.0])
        save_index(vi, "abc123", tmp_path)

        result = load_if_fresh(tmp_path, "abc123")
        assert result is not None
        assert len(result.chunk_vectors) == 1

    def test_load_if_fresh_stale(self, tmp_path):
        """Test load_if_fresh returns None when hash differs."""
        vi = VectorIndex()
        vi.add_chunk("c1", [1.0, 2.0])
        save_index(vi, "old_hash", tmp_path)

        result = load_if_fresh(tmp_path, "new_hash")
        assert result is None


class TestIndexPersistenceIntegration:
    """Integration tests with ChunkForge engine."""

    def test_index_persisted_after_indexing(self, tmp_path):
        """Test that index file is created after indexing documents."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world test content.")

        storage_dir = str(tmp_path / "storage")
        cf = ChunkForge(storage_dir=storage_dir)
        cf.index_documents([str(test_file)])

        index_path = cf.storage.index_dir / INDEX_FILENAME
        assert index_path.exists()

    def test_index_loaded_from_disk_on_restart(self, tmp_path):
        """Test that second startup loads persisted index (no rebuild)."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. " * 20)

        storage_dir = str(tmp_path / "storage")
        cf1 = ChunkForge(storage_dir=storage_dir)
        cf1.index_documents([str(test_file)])
        count1 = cf1.vector_index.get_stats()["chunk_count"]

        # Second startup should load from disk
        cf2 = ChunkForge(storage_dir=storage_dir)
        count2 = cf2.vector_index.get_stats()["chunk_count"]

        assert count1 == count2
        assert count1 >= 1

    def test_stale_index_triggers_rebuild(self, tmp_path):
        """Test that adding a document invalidates persisted index."""
        storage_dir = str(tmp_path / "storage")

        test_file1 = tmp_path / "test1.txt"
        test_file1.write_text("First document content.")
        cf1 = ChunkForge(storage_dir=storage_dir)
        cf1.index_documents([str(test_file1)])
        count1 = cf1.vector_index.get_stats()["chunk_count"]

        # Index a second document directly into storage (simulating external change)
        test_file2 = tmp_path / "test2.txt"
        test_file2.write_text("Second document content.")
        cf1.index_documents([str(test_file2)])
        count2 = cf1.vector_index.get_stats()["chunk_count"]

        # Third startup should detect stale index and rebuild
        cf3 = ChunkForge(storage_dir=storage_dir)
        count3 = cf3.vector_index.get_stats()["chunk_count"]

        assert count3 == count2
        assert count3 > count1

    def test_search_works_after_reload(self, tmp_path):
        """Test that search works on a reloaded index."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def add(a, b): return a + b")

        storage_dir = str(tmp_path / "storage")
        cf1 = ChunkForge(storage_dir=storage_dir)
        cf1.index_documents([str(test_file)])

        # Reload
        cf2 = ChunkForge(storage_dir=storage_dir)
        results = cf2.search("addition function", top_k=5)
        assert len(results) >= 1
        assert "content" in results[0]

    def test_chunk_ids_hash_deterministic(self, tmp_path):
        """Test that chunk ID hash is deterministic."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Test content.")

        storage_dir = str(tmp_path / "storage")
        cf = ChunkForge(storage_dir=storage_dir)
        cf.index_documents([str(test_file)])

        hash1 = compute_chunk_ids_hash(cf.storage)
        hash2 = compute_chunk_ids_hash(cf.storage)
        assert hash1 == hash2
