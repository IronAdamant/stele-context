"""Tests for BM25 keyword index."""

from stele_context.bm25 import BM25Index
from stele_context.index_store import save_bm25, load_bm25_if_fresh


class TestBM25Index:
    """Tests for BM25Index scoring and lifecycle."""

    def test_add_and_score(self):
        """Test basic document addition and scoring."""
        idx = BM25Index()
        idx.add_document("d1", "the quick brown fox")
        idx.add_document("d2", "the lazy brown dog")

        score1 = idx.score("quick fox", "d1")
        score2 = idx.score("quick fox", "d2")

        assert score1 > score2
        assert score1 > 0
        assert score2 == 0  # "quick" and "fox" not in d2

    def test_score_batch(self):
        """Test batch scoring."""
        idx = BM25Index()
        idx.add_document("d1", "authentication login user")
        idx.add_document("d2", "database connection pool")
        idx.add_document("d3", "user profile settings")

        scores = idx.score_batch("user login", ["d1", "d2", "d3"])

        assert scores["d1"] > scores["d2"]
        assert scores["d3"] > scores["d2"]

    def test_search_top_k(self):
        """Independent search ranks all docs by query relevance."""
        idx = BM25Index()
        idx.add_document("c1", "allergen dietary compliance checking service")
        idx.add_document("c2", "unrelated units test boilerplate")
        idx.add_document("c3", "express route handler generic")

        ranked = idx.search("allergen dietary compliance", top_k=2)
        assert len(ranked) >= 1
        assert ranked[0][0] == "c1"
        if len(ranked) > 1:
            assert ranked[0][1] >= ranked[1][1]

    def test_search_empty_index(self):
        assert BM25Index().search("hello", top_k=5) == []

    def test_remove_document(self):
        """Test document removal updates frequencies."""
        idx = BM25Index()
        idx.add_document("d1", "hello world")
        idx.add_document("d2", "hello there")

        assert idx.n_docs == 2
        idx.remove_document("d1")
        assert idx.n_docs == 1
        assert "d1" not in idx.term_freqs

        # Score should still work for remaining doc
        score = idx.score("hello", "d2")
        assert score > 0

    def test_remove_nonexistent(self):
        """Test removing a doc that doesn't exist is a no-op."""
        idx = BM25Index()
        idx.remove_document("nonexistent")
        assert idx.n_docs == 0

    def test_replace_document(self):
        """Test that adding a doc with same ID replaces it."""
        idx = BM25Index()
        idx.add_document("d1", "old content here")
        idx.add_document("d1", "new content there")

        assert idx.n_docs == 1
        assert idx.score("old", "d1") == 0
        assert idx.score("new", "d1") > 0

    def test_empty_index_score(self):
        """Test scoring against empty index."""
        idx = BM25Index()
        assert idx.score("hello", "d1") == 0.0

    def test_empty_query(self):
        """Test scoring with empty query."""
        idx = BM25Index()
        idx.add_document("d1", "hello world")
        assert idx.score("", "d1") == 0.0

    def test_empty_document(self):
        """Test adding empty document."""
        idx = BM25Index()
        idx.add_document("d1", "")
        assert idx.n_docs == 1
        assert idx.doc_lengths["d1"] == 0

    def test_single_char_tokens_filtered(self):
        """Test that single-character tokens are filtered out."""
        idx = BM25Index()
        idx.add_document("d1", "a b c def ghi")
        # Only "def" and "ghi" should be indexed (len > 1)
        assert idx.score("a", "d1") == 0.0
        assert idx.score("def", "d1") > 0


class TestBM25Serialization:
    """Tests for BM25 round-trip serialization."""

    def test_round_trip(self):
        """Test to_dict/from_dict preserves all state."""
        idx = BM25Index(k1=1.2, b=0.8)
        idx.add_document("d1", "the quick brown fox")
        idx.add_document("d2", "the lazy dog")

        data = idx.to_dict()
        restored = BM25Index.from_dict(data)

        assert restored.k1 == 1.2
        assert restored.b == 0.8
        assert restored.n_docs == 2
        assert restored.score("quick fox", "d1") == idx.score("quick fox", "d1")
        assert restored.score("lazy dog", "d2") == idx.score("lazy dog", "d2")

    def test_persistence(self, tmp_path):
        """Test save and load from disk."""
        idx = BM25Index()
        idx.add_document("c1", "hello world")
        idx.add_document("c2", "hello there")

        save_bm25(idx, "test_hash", tmp_path)
        assert (tmp_path / "bm25_index.json.zlib").exists()

        loaded = load_bm25_if_fresh(tmp_path, "test_hash")
        assert loaded is not None
        assert loaded.n_docs == 2
        assert loaded.score("hello", "c1") == idx.score("hello", "c1")

    def test_stale_hash_returns_none(self, tmp_path):
        """Test that mismatched hash returns None."""
        idx = BM25Index()
        idx.add_document("c1", "test")
        save_bm25(idx, "old_hash", tmp_path)

        result = load_bm25_if_fresh(tmp_path, "new_hash")
        assert result is None

    def test_missing_file_returns_none(self, tmp_path):
        """Test that missing file returns None."""
        result = load_bm25_if_fresh(tmp_path, "any_hash")
        assert result is None
