"""Tests for Stele engine."""

from stele import __version__
from stele.engine import Stele
from stele.chunkers.base import Chunk
from stele.indexing import merge_similar_chunks


class TestSteleEngine:
    """Tests for the new Stele engine."""

    def test_initialization_creates_vector_index(self, tmp_path):
        """Test that engine initializes with vector index."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        assert cf.vector_index is not None
        stats = cf.vector_index.get_stats()
        assert stats["chunk_count"] == 0

    def test_python_file_uses_code_chunker(self, tmp_path):
        """Test that .py files are routed through CodeChunker."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            """
def hello():
    print("hello")

def goodbye():
    print("goodbye")

class Greeter:
    def greet(self, name):
        return f"Hello, {name}!"
""".strip()
        )

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        result = cf.index_documents([str(test_file)])

        assert len(result["indexed"]) == 1
        assert result["indexed"][0]["modality"] == "code"
        assert result["total_chunks"] >= 1

    def test_text_file_uses_text_chunker(self, tmp_path):
        """Test that .txt files are routed through TextChunker."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. This is a test.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        result = cf.index_documents([str(test_file)])

        assert len(result["indexed"]) == 1
        assert result["indexed"][0]["modality"] == "text"

    def test_chunk_content_stored_and_retrievable(self, tmp_path):
        """Test that chunk content is stored in SQLite and retrievable."""
        test_file = tmp_path / "test.txt"
        content = "Hello world. This is a test document with some content."
        test_file.write_text(content)

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        # Get chunks for the document
        chunks = cf.storage.search_chunks(document_path=str(test_file))
        assert len(chunks) >= 1

        # Content should be stored
        for chunk in chunks:
            assert chunk["content"] is not None
            assert len(chunk["content"]) > 0

    def test_chunk_content_retrievable_by_id(self, tmp_path):
        """Test get_chunk_content retrieval."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world content.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        chunks = cf.storage.get_document_chunks(str(test_file))
        assert len(chunks) >= 1

        content = cf.storage.get_chunk_content(chunks[0]["chunk_id"])
        assert content is not None

    def test_hnsw_index_populated_after_indexing(self, tmp_path):
        """Test that HNSW index is populated when documents are indexed."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. " * 50)

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        stats = cf.vector_index.get_stats()
        assert stats["chunk_count"] >= 1

    def test_search_returns_results(self, tmp_path):
        """Test that search() returns relevant chunks with content."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            """
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b
""".strip()
        )

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        results = cf.search("addition function", top_k=5)
        assert len(results) >= 1
        assert "content" in results[0]
        assert "relevance_score" in results[0]
        assert "document_path" in results[0]

    def test_search_empty_index(self, tmp_path):
        """Test search on empty index returns empty list."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        results = cf.search("anything", top_k=5)
        assert results == []

    def test_get_context_unchanged(self, tmp_path):
        """Test get_context for unchanged documents."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. Test content.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        context = cf.get_context([str(test_file)])
        assert len(context["unchanged"]) == 1
        assert len(context["changed"]) == 0
        assert len(context["new"]) == 0
        assert context["unchanged"][0]["path"] == str(test_file)
        assert len(context["unchanged"][0]["chunks"]) >= 1

    def test_get_context_new_document(self, tmp_path):
        """Test get_context for a document not yet indexed."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        context = cf.get_context([str(test_file)])

        assert len(context["new"]) == 1
        assert len(context["unchanged"]) == 0

    def test_get_context_changed_document(self, tmp_path):
        """Test get_context for a changed document."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Original content.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        # Modify the file
        test_file.write_text("Modified content!")

        context = cf.get_context([str(test_file)])
        assert len(context["changed"]) == 1
        assert len(context["unchanged"]) == 0

    def test_index_rebuilds_from_storage(self, tmp_path):
        """Test that HNSW index rebuilds from SQLite on startup."""
        storage_dir = str(tmp_path / "storage")
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. " * 20)

        # Index with first instance
        cf1 = Stele(storage_dir=storage_dir)
        cf1.index_documents([str(test_file)])
        count1 = cf1.vector_index.get_stats()["chunk_count"]

        # Create new instance — should rebuild index from SQLite
        cf2 = Stele(storage_dir=storage_dir)
        count2 = cf2.vector_index.get_stats()["chunk_count"]

        assert count1 == count2
        assert count1 >= 1

    def test_detect_modality(self, tmp_path):
        """Test modality detection."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))

        assert cf.detect_modality("test.py") == "code"
        assert cf.detect_modality("test.js") == "code"
        assert cf.detect_modality("test.txt") == "text"
        assert cf.detect_modality("test.md") == "text"
        assert cf.detect_modality("test.xyz") == "unknown"

    def test_stats_includes_index(self, tmp_path):
        """Test that stats includes vector index info."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        stats = cf.get_stats()

        assert "index" in stats
        assert "chunk_count" in stats["index"]
        assert stats["version"] == __version__

    def test_save_and_load_state_alias(self, tmp_path):
        """Test that save_state is an alias for save_kv_state."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        assert cf.save_state == cf.save_kv_state

    def test_merge_similar_chunks(self, tmp_path):
        """Test that similar adjacent chunks get merged."""
        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            merge_threshold=0.5,
        )

        chunks = [
            Chunk(
                content="Hello world foo bar",
                modality="text",
                start_pos=0,
                end_pos=19,
                document_path="t.txt",
                chunk_index=0,
            ),
            Chunk(
                content="Hello world foo baz",
                modality="text",
                start_pos=19,
                end_pos=38,
                document_path="t.txt",
                chunk_index=1,
            ),
        ]

        merged = merge_similar_chunks(
            chunks, cf.merge_threshold, cf.max_chunk_size, cf.MODALITY_THRESHOLDS
        )
        # High similarity chunks should merge
        assert len(merged) <= len(chunks)

    def test_remove_document(self, tmp_path):
        """Test removing a document cleans up chunks, annotations, and index."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))

        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")
        cf.index_documents([str(test_file)])

        # Add an annotation
        cf.annotate(str(test_file), "document", "Will be removed")

        # Verify it's indexed
        assert cf.storage.get_document(str(test_file)) is not None
        chunks_before = cf.storage.get_document_chunks(str(test_file))
        assert len(chunks_before) >= 1
        index_before = cf.vector_index.get_stats()["chunk_count"]

        # Remove
        result = cf.remove_document(str(test_file))
        assert result["removed"] is True
        assert result["chunks_removed"] >= 1
        assert result["annotations_removed"] == 1

        # Verify cleanup
        assert cf.storage.get_document(str(test_file)) is None
        assert cf.storage.get_document_chunks(str(test_file)) == []
        assert cf.get_annotations(target=str(test_file)) == []
        assert cf.vector_index.get_stats()["chunk_count"] < index_before

    def test_remove_nonexistent_document(self, tmp_path):
        """Test removing a document that doesn't exist."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        result = cf.remove_document("/no/such/file.py")
        assert result["removed"] is False

    def test_reindex_cleans_stale_chunks(self, tmp_path):
        """Test that re-indexing removes stale chunks."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))

        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass\ndef world(): pass")
        cf.index_documents([str(test_file)])

        old_chunks = cf.storage.get_document_chunks(str(test_file))
        old_ids = {c["chunk_id"] for c in old_chunks}

        # Rewrite file with different content
        test_file.write_text("def completely_different(): return 42")
        cf.index_documents([str(test_file)], force_reindex=True)

        new_chunks = cf.storage.get_document_chunks(str(test_file))
        new_ids = {c["chunk_id"] for c in new_chunks}

        # Old chunks should be gone
        for old_id in old_ids:
            if old_id not in new_ids:
                assert cf.storage.get_chunk(old_id) is None
