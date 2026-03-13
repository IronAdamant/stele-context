"""Tests for ChunkForge vector index."""

from chunkforge.index import HNSWIndex, VectorIndex


class TestHNSWIndex:
    """Tests for HNSW index."""

    def test_initialization(self):
        """Test index initialization."""
        index = HNSWIndex(M=16, ef_construction=200, ef_search=50)

        assert index.M == 16
        assert index.M_max0 == 32
        assert index.ef_construction == 200
        assert index.ef_search == 50
        assert len(index.nodes) == 0
        assert index.entry_point is None

    def test_insert_single(self):
        """Test inserting a single vector."""
        index = HNSWIndex()
        index.insert("vec1", [1.0, 2.0, 3.0])

        assert len(index.nodes) == 1
        assert "vec1" in index.nodes
        assert index.entry_point == "vec1"

    def test_insert_multiple(self):
        """Test inserting multiple vectors."""
        index = HNSWIndex()

        for i in range(100):
            index.insert(f"vec{i}", [float(i), float(i * 2), float(i * 3)])

        assert len(index.nodes) == 100
        assert index.entry_point is not None

    def test_search_empty(self):
        """Test searching empty index."""
        index = HNSWIndex()
        results = index.search([1.0, 2.0, 3.0], k=5)

        assert results == []

    def test_search_single(self):
        """Test searching with single vector."""
        index = HNSWIndex()
        index.insert("vec1", [1.0, 2.0, 3.0])

        results = index.search([1.0, 2.0, 3.0], k=1)

        assert len(results) == 1
        assert results[0][0] == "vec1"
        assert results[0][1] > 0.99  # Should be very similar

    def test_search_multiple(self):
        """Test searching with multiple vectors."""
        index = HNSWIndex()

        # Insert vectors
        index.insert("vec1", [1.0, 0.0, 0.0])
        index.insert("vec2", [0.0, 1.0, 0.0])
        index.insert("vec3", [0.0, 0.0, 1.0])
        index.insert("vec4", [1.0, 1.0, 0.0])

        # Search for vector similar to vec1
        results = index.search([1.0, 0.0, 0.0], k=2)

        assert len(results) == 2
        assert results[0][0] == "vec1"  # Most similar
        assert results[0][1] > results[1][1]  # Sorted by similarity

    def test_remove(self):
        """Test removing vectors."""
        index = HNSWIndex()
        index.insert("vec1", [1.0, 2.0, 3.0])
        index.insert("vec2", [4.0, 5.0, 6.0])

        assert len(index.nodes) == 2

        # Remove vec1
        result = index.remove("vec1")

        assert result is True
        assert len(index.nodes) == 1
        assert "vec1" not in index.nodes
        assert "vec2" in index.nodes

    def test_remove_nonexistent(self):
        """Test removing non-existent vector."""
        index = HNSWIndex()
        result = index.remove("nonexistent")

        assert result is False

    def test_get_stats(self):
        """Test getting index statistics."""
        index = HNSWIndex()

        # Empty index
        stats = index.get_stats()
        assert stats["node_count"] == 0

        # Add vectors
        for i in range(10):
            index.insert(f"vec{i}", [float(i), float(i * 2)])

        stats = index.get_stats()
        assert stats["node_count"] == 10
        assert stats["max_level"] >= 0
        assert stats["avg_connections"] > 0

    def test_clear(self):
        """Test clearing index."""
        index = HNSWIndex()

        for i in range(10):
            index.insert(f"vec{i}", [float(i), float(i * 2)])

        assert len(index.nodes) == 10

        index.clear()

        assert len(index.nodes) == 0
        assert index.entry_point is None
        assert index.max_level == 0


class TestVectorIndex:
    """Tests for high-level VectorIndex."""

    def test_initialization(self):
        """Test VectorIndex initialization."""
        index = VectorIndex(M=16, ef_construction=200, ef_search=50)

        assert index.index is not None
        assert len(index.chunk_vectors) == 0

    def test_add_chunk(self):
        """Test adding chunks."""
        index = VectorIndex()

        index.add_chunk("chunk1", [1.0, 2.0, 3.0])
        index.add_chunk("chunk2", [4.0, 5.0, 6.0])

        assert len(index.chunk_vectors) == 2
        assert "chunk1" in index.chunk_vectors
        assert "chunk2" in index.chunk_vectors

    def test_search(self):
        """Test searching for similar chunks."""
        index = VectorIndex()

        # Add chunks
        index.add_chunk("chunk1", [1.0, 0.0, 0.0])
        index.add_chunk("chunk2", [0.0, 1.0, 0.0])
        index.add_chunk("chunk3", [0.0, 0.0, 1.0])

        # Search
        results = index.search([1.0, 0.0, 0.0], k=2)

        assert len(results) == 2
        assert results[0][0] == "chunk1"
        assert results[0][1] > 0.99

    def test_remove_chunk(self):
        """Test removing chunks."""
        index = VectorIndex()

        index.add_chunk("chunk1", [1.0, 2.0, 3.0])
        index.add_chunk("chunk2", [4.0, 5.0, 6.0])

        assert len(index.chunk_vectors) == 2

        result = index.remove_chunk("chunk1")

        assert result is True
        assert len(index.chunk_vectors) == 1
        assert "chunk1" not in index.chunk_vectors

    def test_get_stats(self):
        """Test getting statistics."""
        index = VectorIndex()

        for i in range(20):
            index.add_chunk(f"chunk{i}", [float(i), float(i * 2)])

        stats = index.get_stats()

        assert stats["chunk_count"] == 20
        assert stats["node_count"] == 20

    def test_clear(self):
        """Test clearing index."""
        index = VectorIndex()

        for i in range(10):
            index.add_chunk(f"chunk{i}", [float(i), float(i * 2)])

        assert len(index.chunk_vectors) == 10

        index.clear()

        assert len(index.chunk_vectors) == 0
