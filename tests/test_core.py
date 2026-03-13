"""Tests for ChunkForge core functionality (backward compat)."""

from chunkforge import ChunkForge
from chunkforge.core import Chunk


class TestChunk:
    """Tests for the Chunk class (now from chunkers.base via core shim)."""

    def test_chunk_creation(self):
        """Test basic chunk creation."""
        chunk = Chunk(
            content="Hello, world!",
            modality="text",
            start_pos=0,
            end_pos=13,
            document_path="test.txt",
            chunk_index=0,
        )

        assert chunk.content == "Hello, world!"
        assert chunk.start_pos == 0
        assert chunk.end_pos == 13
        assert chunk.document_path == "test.txt"
        assert chunk.chunk_index == 0

    def test_chunk_content_hash(self):
        """Test content hash computation."""
        chunk1 = Chunk(
            content="Hello, world!",
            modality="text",
            start_pos=0,
            end_pos=13,
            document_path="test.txt",
        )
        chunk2 = Chunk(
            content="Hello, world!",
            modality="text",
            start_pos=0,
            end_pos=13,
            document_path="test.txt",
        )
        chunk3 = Chunk(
            content="Different content",
            modality="text",
            start_pos=0,
            end_pos=17,
            document_path="test.txt",
        )

        assert chunk1.content_hash == chunk2.content_hash
        assert chunk1.content_hash != chunk3.content_hash

    def test_chunk_token_count(self):
        """Test token count estimation."""
        chunk = Chunk(
            content="Hello, world! This is a test.",
            modality="text",
            start_pos=0,
            end_pos=29,
            document_path="test.txt",
        )

        assert chunk.token_count > 0
        # Regex tokenizer: "Hello", ",", " ", "world", "!", " ", "This", " ",
        # "is", " ", "a", " ", "test", "." = ~14 tokens
        assert 8 <= chunk.token_count <= 20

    def test_chunk_id(self):
        """Test chunk ID generation."""
        chunk = Chunk(
            content="Hello, world!",
            modality="text",
            start_pos=0,
            end_pos=13,
            document_path="test.txt",
        )

        assert isinstance(chunk.chunk_id, str)

        chunk2 = Chunk(
            content="Hello, world!",
            modality="text",
            start_pos=0,
            end_pos=13,
            document_path="test.txt",
        )
        assert chunk.chunk_id == chunk2.chunk_id

    def test_chunk_similarity(self):
        """Test chunk similarity computation."""
        chunk1 = Chunk(
            content="Hello, world! This is a test.",
            modality="text",
            start_pos=0,
            end_pos=29,
            document_path="test.txt",
        )
        chunk2 = Chunk(
            content="Hello, world! This is another test.",
            modality="text",
            start_pos=0,
            end_pos=35,
            document_path="test.txt",
            chunk_index=1,
        )
        chunk3 = Chunk(
            content="Completely different content here.",
            modality="text",
            start_pos=0,
            end_pos=34,
            document_path="test.txt",
            chunk_index=2,
        )

        sim1 = chunk1.similarity(chunk2)
        assert 0.0 <= sim1 <= 1.0

        sim2 = chunk1.similarity(chunk3)
        assert 0.0 <= sim2 <= 1.0


class TestChunkForge:
    """Tests for the ChunkForge class."""

    def test_initialization(self, tmp_path):
        """Test ChunkForge initialization."""
        storage_dir = str(tmp_path / "storage")
        cf = ChunkForge(storage_dir=storage_dir)

        assert cf.chunk_size == 256
        assert cf.max_chunk_size == 4096
        assert cf.merge_threshold == 0.7
        assert cf.change_threshold == 0.85

    def test_index_documents(self, tmp_path):
        """Test document indexing."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, world! This is a test document.")

        storage_dir = str(tmp_path / "storage")
        cf = ChunkForge(storage_dir=storage_dir)
        result = cf.index_documents([str(test_file)])

        assert result["total_chunks"] == 1
        assert result["total_tokens"] > 0
        assert len(result["indexed"]) == 1
        assert len(result["errors"]) == 0

    def test_index_nonexistent_file(self, tmp_path):
        """Test indexing a non-existent file."""
        storage_dir = str(tmp_path / "storage")
        cf = ChunkForge(storage_dir=storage_dir)
        result = cf.index_documents(["nonexistent.txt"])

        assert len(result["errors"]) == 1
        assert result["errors"][0]["error"] == "File not found"

    def test_index_unchanged_document(self, tmp_path):
        """Test that unchanged documents are skipped."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, world!")

        storage_dir = str(tmp_path / "storage")
        cf = ChunkForge(storage_dir=storage_dir)
        result1 = cf.index_documents([str(test_file)])

        result2 = cf.index_documents([str(test_file)])

        assert result1["total_chunks"] == 1
        assert result2["total_chunks"] == 0
        assert len(result2["skipped"]) == 1

    def test_detect_changes(self, tmp_path):
        """Test change detection."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, world!")

        storage_dir = str(tmp_path / "storage")
        cf = ChunkForge(storage_dir=storage_dir)
        cf.index_documents([str(test_file)])

        result = cf.detect_changes_and_update(session_id="test-session")

        assert len(result["unchanged"]) == 1
        assert result["unchanged"][0] == str(test_file)

    def test_get_stats(self, tmp_path):
        """Test getting statistics."""
        storage_dir = str(tmp_path / "storage")
        cf = ChunkForge(storage_dir=storage_dir)
        stats = cf.get_stats()

        assert "version" in stats
        assert "storage" in stats
        assert "config" in stats
        assert stats["version"] == "0.5.3"


class TestStorageBackend:
    """Tests for the StorageBackend class."""

    def test_initialization(self, tmp_path):
        """Test storage backend initialization."""
        from chunkforge.storage import StorageBackend

        storage_dir = str(tmp_path / "storage")
        storage = StorageBackend(base_dir=storage_dir)

        assert storage.base_dir.exists()
        assert storage.kv_dir.exists()
        assert storage.index_dir.exists()
        assert storage.db_path.exists()

    def test_store_and_retrieve_chunk(self, tmp_path):
        """Test storing and retrieving chunks."""
        from chunkforge.storage import StorageBackend

        storage_dir = str(tmp_path / "storage")
        storage = StorageBackend(base_dir=storage_dir)

        try:
            import numpy as np

            sig = np.zeros(128, dtype=np.float32)
        except ImportError:
            sig = [0.0] * 128

        storage.store_chunk(
            chunk_id="test-chunk-1",
            document_path="test.txt",
            content_hash="abc123",
            semantic_signature=sig,
            start_pos=0,
            end_pos=100,
            token_count=25,
        )

        chunk = storage.get_chunk("test-chunk-1")

        assert chunk is not None
        assert chunk["chunk_id"] == "test-chunk-1"
        assert chunk["document_path"] == "test.txt"
        assert chunk["content_hash"] == "abc123"
        assert chunk["token_count"] == 25

    def test_create_and_get_session(self, tmp_path):
        """Test creating and retrieving sessions."""
        from chunkforge.storage import StorageBackend

        storage_dir = str(tmp_path / "storage")
        storage = StorageBackend(base_dir=storage_dir)

        storage.create_session("test-session")

        session = storage.get_session("test-session")

        assert session is not None
        assert session["session_id"] == "test-session"
        assert session["turn_count"] == 0
        assert session["total_tokens"] == 0
