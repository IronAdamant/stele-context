"""Tests for agent-supplied semantic embeddings."""

from stele.engine import Stele
from stele.storage import StorageBackend


class TestStorageAgentEmbeddings:
    """Tests for storage layer agent embedding methods."""

    def test_store_semantic_summary(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128
        storage.store_chunk("c1", "test.py", "h1", sig, 0, 100, 50, "code")

        agent_sig = [1.0] + [0.0] * 127
        ok = storage.store_semantic_summary("c1", "Auth middleware", agent_sig)
        assert ok is True

        chunk = storage.get_chunk("c1")
        assert chunk["semantic_summary"] == "Auth middleware"

    def test_store_semantic_summary_missing_chunk(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        ok = storage.store_semantic_summary("nonexistent", "test", [0.0] * 128)
        assert ok is False

    def test_store_agent_signature(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128
        storage.store_chunk("c1", "test.py", "h1", sig, 0, 100, 50, "code")

        agent_sig = [0.5] * 128
        ok = storage.store_agent_signature("c1", agent_sig)
        assert ok is True

    def test_get_agent_signature(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128
        storage.store_chunk("c1", "test.py", "h1", sig, 0, 100, 50, "code")

        # No agent signature initially
        result = storage.get_agent_signature("c1")
        assert result is None

        # Store and retrieve
        agent_sig = [1.0 / (128**0.5)] * 128
        storage.store_agent_signature("c1", agent_sig)
        result = storage.get_agent_signature("c1")
        assert result is not None
        assert len(result) == 128

    def test_get_agent_signature_missing_chunk(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        assert storage.get_agent_signature("nonexistent") is None


class TestEngineSemanticSummary:
    """Tests for engine.store_semantic_summary()."""

    def test_store_and_improves_search(self, tmp_path):
        (tmp_path / ".git").mkdir()
        test_file = tmp_path / "auth.py"
        test_file.write_text(
            "def check_perms(user, resource):\n"
            "    return user.role in resource.allowed_roles\n"
        )

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        engine.index_documents([str(test_file)])

        chunks = engine.storage.search_chunks(document_path="auth.py")
        chunk_id = chunks[0]["chunk_id"]

        # Search before summary
        before = engine.search("role-based access control", top_k=1)
        score_before = before[0]["relevance_score"] if before else 0

        # Add semantic summary
        result = engine.store_semantic_summary(
            chunk_id=chunk_id,
            summary="Role-based access control check that verifies user permissions against resource ACL",
        )
        assert result["stored"] is True

        # Search after summary — should be better
        after = engine.search("role-based access control", top_k=1)
        score_after = after[0]["relevance_score"] if after else 0
        assert score_after >= score_before

    def test_store_summary_nonexistent_chunk(self, tmp_path):
        (tmp_path / ".git").mkdir()
        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        result = engine.store_semantic_summary("bad_id", "test summary")
        assert result["stored"] is False

    def test_summary_persists_in_db(self, tmp_path):
        (tmp_path / ".git").mkdir()
        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        engine.index_documents([str(test_file)])

        chunks = engine.storage.search_chunks(document_path="test.py")
        chunk_id = chunks[0]["chunk_id"]

        engine.store_semantic_summary(chunk_id, "Simple variable assignment")
        chunk = engine.storage.get_chunk(chunk_id)
        assert chunk["semantic_summary"] == "Simple variable assignment"


class TestEngineStoreEmbedding:
    """Tests for engine.store_embedding()."""

    def test_store_raw_embedding(self, tmp_path):
        (tmp_path / ".git").mkdir()
        test_file = tmp_path / "test.py"
        test_file.write_text("def foo(): pass\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        engine.index_documents([str(test_file)])

        chunks = engine.storage.search_chunks(document_path="test.py")
        chunk_id = chunks[0]["chunk_id"]

        # Store a raw vector
        vector = [1.0] + [0.0] * 127
        result = engine.store_embedding(chunk_id, vector)
        assert result["stored"] is True

    def test_embedding_is_normalized(self, tmp_path):
        (tmp_path / ".git").mkdir()
        test_file = tmp_path / "test.py"
        test_file.write_text("def bar(): pass\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        engine.index_documents([str(test_file)])

        chunks = engine.storage.search_chunks(document_path="test.py")
        chunk_id = chunks[0]["chunk_id"]

        # Store unnormalized vector
        vector = [3.0, 4.0] + [0.0] * 126
        engine.store_embedding(chunk_id, vector)

        # Retrieve and verify normalization
        sig = engine.storage.get_agent_signature(chunk_id)
        assert sig is not None
        norm = sum(x * x for x in sig) ** 0.5
        assert abs(norm - 1.0) < 0.01

    def test_embedding_nonexistent_chunk(self, tmp_path):
        (tmp_path / ".git").mkdir()
        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        result = engine.store_embedding("bad_id", [0.0] * 128)
        assert result["stored"] is False


class TestIndexRebuildWithAgentSignatures:
    """Tests that index rebuild prefers agent signatures."""

    def test_rebuild_uses_agent_signature(self, tmp_path):
        (tmp_path / ".git").mkdir()
        test_file = tmp_path / "test.py"
        test_file.write_text("def process(): pass\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        engine.index_documents([str(test_file)])

        chunks = engine.storage.search_chunks(document_path="test.py")
        chunk_id = chunks[0]["chunk_id"]

        # Store a semantic summary
        engine.store_semantic_summary(chunk_id, "Data processing pipeline entry point")

        # Force index rebuild
        from stele.search_engine import load_or_rebuild_index

        engine.vector_index = load_or_rebuild_index(engine.storage)

        # Verify the rebuilt index uses agent signature
        results = engine.search("data processing pipeline", top_k=1)
        assert len(results) >= 1
        assert results[0]["chunk_id"] == chunk_id
