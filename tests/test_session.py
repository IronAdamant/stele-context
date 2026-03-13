"""Tests for ChunkForge session management."""

from chunkforge.engine import ChunkForge
from chunkforge.session import SessionManager


class TestSessionManager:
    """Tests for SessionManager."""

    def _setup(self, tmp_path):
        """Create a ChunkForge instance and index a test file."""
        storage_dir = str(tmp_path / "storage")
        test_file = tmp_path / "test.py"
        test_file.write_text(
            """
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b

def multiply(a, b):
    return a * b
""".strip()
        )

        cf = ChunkForge(storage_dir=storage_dir)
        cf.index_documents([str(test_file)])

        # Store some KV state
        chunks = cf.storage.get_document_chunks(str(test_file))
        kv_data = {c["chunk_id"]: {"cached": True} for c in chunks}
        cf.save_kv_state("test-session", kv_data)

        sm = SessionManager(cf.storage, cf.vector_index)
        return cf, sm, chunks

    def test_get_relevant_chunks(self, tmp_path):
        """Test getting relevant chunks with content."""
        cf, sm, chunks = self._setup(tmp_path)

        result = sm.get_relevant_chunks("test-session", "addition", top_k=5)

        assert "query" in result
        assert "chunks" in result
        assert "total_tokens" in result
        assert len(result["chunks"]) >= 1

        for chunk in result["chunks"]:
            assert "chunk_id" in chunk
            assert "relevance_score" in chunk
            assert "token_count" in chunk

    def test_get_relevant_chunks_empty_session(self, tmp_path):
        """Test get_relevant_chunks on non-existent session."""
        cf = ChunkForge(storage_dir=str(tmp_path / "storage"))
        sm = SessionManager(cf.storage, cf.vector_index)

        result = sm.get_relevant_chunks("nonexistent", "query")
        assert result["chunks"] == []
        assert result["total_tokens"] == 0

    def test_save_state(self, tmp_path):
        """Test saving state."""
        storage_dir = str(tmp_path / "storage")
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world content.")

        cf = ChunkForge(storage_dir=storage_dir)
        cf.index_documents([str(test_file)])

        sm = SessionManager(cf.storage, cf.vector_index)
        chunks = cf.storage.get_document_chunks(str(test_file))
        kv_data = {c["chunk_id"]: {"data": "test"} for c in chunks}

        result = sm.save_state("new-session", kv_data)
        assert result["chunks_saved"] == len(chunks)
        assert result["session_id"] == "new-session"

    def test_rollback(self, tmp_path):
        """Test session rollback."""
        cf, sm, chunks = self._setup(tmp_path)

        # Save more state (turn 1)
        kv_data2 = {chunks[0]["chunk_id"]: {"turn": 1}}
        sm.save_state("test-session", kv_data2)

        session = cf.storage.get_session("test-session")
        assert session["turn_count"] == 2

        # Rollback to turn 0
        result = sm.rollback("test-session", 0)
        assert result["previous_turn"] == 2
        assert result["current_turn"] == 0

    def test_rollback_nonexistent_session(self, tmp_path):
        """Test rollback on non-existent session."""
        cf = ChunkForge(storage_dir=str(tmp_path / "storage"))
        sm = SessionManager(cf.storage, cf.vector_index)

        result = sm.rollback("nonexistent", 0)
        assert "error" in result

    def test_prune(self, tmp_path):
        """Test pruning low-relevance chunks."""
        cf, sm, chunks = self._setup(tmp_path)

        # Prune to very low token limit
        result = sm.prune("test-session", max_tokens=1)
        assert "chunks_pruned" in result

    def test_prune_already_under_limit(self, tmp_path):
        """Test pruning when already under limit."""
        cf, sm, chunks = self._setup(tmp_path)

        result = sm.prune("test-session", max_tokens=999999)
        assert result["chunks_pruned"] == 0
        assert result["message"] == "Already under limit"

    def test_save_kv_state_alias(self, tmp_path):
        """Test that save_kv_state is an alias for save_state."""
        cf = ChunkForge(storage_dir=str(tmp_path / "storage"))
        sm = SessionManager(cf.storage, cf.vector_index)
        assert sm.save_kv_state == sm.save_state
