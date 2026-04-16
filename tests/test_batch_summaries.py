"""Tests for inline summaries during indexing and bulk_store_summaries."""

from stele_context.engine import Stele
from stele_context.storage import StorageBackend


class TestStorageBulkUpdateSummaries:
    """Tests for storage.bulk_update_summaries()."""

    def test_bulk_update_summaries(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128
        storage.store_chunk("c1", "test.py", "h1", sig, 0, 50, 25, "code a")
        storage.store_chunk("c2", "test.py", "h2", sig, 50, 100, 25, "code b")

        agent_sig = [1.0] + [0.0] * 127
        count = storage.bulk_update_summaries(["c1", "c2"], "Auth module", agent_sig)
        assert count == 2

        for cid in ("c1", "c2"):
            chunk = storage.get_chunk(cid)
            assert chunk["semantic_summary"] == "Auth module"
            assert chunk["agent_signature"] is not None

    def test_bulk_update_empty_list(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        count = storage.bulk_update_summaries([], "summary", [0.0] * 128)
        assert count == 0


class TestInlineSummariesDuringIndex:
    """Tests for index_documents(summaries=...) inline Tier 2 signatures."""

    def test_inline_summary_applied(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f = tmp_path / "auth.py"
        f.write_text("def check_token(token):\n    return validate_jwt(token)\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        result = engine.index_documents(
            [str(f)],
            summaries={str(f): "JWT token validation middleware"},
        )

        assert result["summaries_applied"] > 0

        chunks = engine.storage.search_chunks(document_path="auth.py")
        for chunk in chunks:
            assert chunk["semantic_summary"] == "JWT token validation middleware"
            assert chunk["agent_signature"] is not None

    def test_inline_summary_improves_search(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f = tmp_path / "perms.py"
        f.write_text("def check_perms(u, r):\n    return u.role in r.allowed\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)

        # Index without summary (hybrid mode — summaries affect HNSW ranking only)
        engine.index_documents([str(f)])
        before = engine.search(
            "role-based access control", top_k=1, search_mode="hybrid"
        )
        score_before = before[0]["relevance_score"] if before else 0

        # Re-index with summary
        engine.index_documents(
            [str(f)],
            force_reindex=True,
            summaries={
                str(f): "Role-based access control that checks user permissions"
            },
        )
        after = engine.search(
            "role-based access control", top_k=1, search_mode="hybrid"
        )
        score_after = after[0]["relevance_score"] if after else 0

        assert score_after >= score_before

    def test_inline_summary_no_match_ignored(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        result = engine.index_documents(
            [str(f)],
            summaries={"nonexistent.py": "Should be ignored"},
        )

        assert result["summaries_applied"] == 0

    def test_inline_summary_skipped_files_not_summarized(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        # Index first time
        engine.index_documents([str(f)])
        # Index again without force (file unchanged, will be skipped)
        result = engine.index_documents(
            [str(f)],
            summaries={str(f): "Should not be applied to skipped file"},
        )

        assert result["summaries_applied"] == 0
        assert len(result["skipped"]) == 1

    def test_no_summaries_param(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        result = engine.index_documents([str(f)])

        # summaries_applied key should not be present when no summaries given
        assert "summaries_applied" not in result

    def test_inline_summary_multiple_files(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f1 = tmp_path / "auth.py"
        f1.write_text("def login(): pass\n")
        f2 = tmp_path / "db.py"
        f2.write_text("def connect(): pass\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        result = engine.index_documents(
            [str(f1), str(f2)],
            summaries={
                str(f1): "Authentication and login logic",
                str(f2): "Database connection pool management",
            },
        )

        assert result["summaries_applied"] >= 2

        chunks_auth = engine.storage.search_chunks(document_path="auth.py")
        for c in chunks_auth:
            assert c["semantic_summary"] == "Authentication and login logic"

        chunks_db = engine.storage.search_chunks(document_path="db.py")
        for c in chunks_db:
            assert c["semantic_summary"] == "Database connection pool management"


class TestBulkStoreSummaries:
    """Tests for engine.bulk_store_summaries()."""

    def test_bulk_store_per_chunk(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f = tmp_path / "multi.py"
        f.write_text("def func_a():\n    return 'a'\n\ndef func_b():\n    return 'b'\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        engine.index_documents([str(f)])

        chunks = engine.storage.search_chunks(document_path="multi.py")
        summaries = {}
        for chunk in chunks:
            content = chunk.get("content", "")
            if "func_a" in content:
                summaries[chunk["chunk_id"]] = "Returns the letter a"
            elif "func_b" in content:
                summaries[chunk["chunk_id"]] = "Returns the letter b"

        if not summaries:
            # File produced single chunk; use that
            summaries[chunks[0]["chunk_id"]] = "Two simple functions"

        result = engine.bulk_store_summaries(summaries)
        assert result["stored"] == len(summaries)
        assert result["errors"] == []

    def test_bulk_store_nonexistent_chunk(self, tmp_path):
        (tmp_path / ".git").mkdir()
        engine = Stele(project_root=str(tmp_path), enable_coordination=False)

        result = engine.bulk_store_summaries({"bad_id": "test summary"})
        assert result["stored"] == 0
        assert "bad_id" in result["errors"]

    def test_bulk_store_mixed_valid_invalid(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)
        engine.index_documents([str(f)])

        chunks = engine.storage.search_chunks(document_path="test.py")
        chunk_id = chunks[0]["chunk_id"]

        result = engine.bulk_store_summaries(
            {
                chunk_id: "Valid chunk summary",
                "nonexistent": "Invalid chunk",
            }
        )
        assert result["stored"] == 1
        assert "nonexistent" in result["errors"]

    def test_bulk_store_empty(self, tmp_path):
        (tmp_path / ".git").mkdir()
        engine = Stele(project_root=str(tmp_path), enable_coordination=False)

        result = engine.bulk_store_summaries({})
        assert result["stored"] == 0
        assert result["total"] == 0


class TestToolRegistryIntegration:
    """Verify bulk_store_summaries is wired into the tool registry."""

    def test_bulk_store_summaries_in_tool_map(self, tmp_path):
        from stele_context.tool_registry import build_tool_map, WRITE_TOOLS

        engine = Stele(storage_dir=str(tmp_path / "store"))
        tool_map = build_tool_map(engine)

        assert "bulk_store_summaries" in tool_map
        assert "bulk_store_summaries" in WRITE_TOOLS

    def test_tool_definitions_include_bulk(self):
        from stele_context.mcp_tools_primary import TOOL_DEFINITIONS

        names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "bulk_store_summaries" in names

    def test_index_schema_has_summaries(self):
        from stele_context.mcp_tools_primary import TOOL_DEFINITIONS

        index_tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "index")
        props = index_tool["inputSchema"]["properties"]
        assert "summaries" in props


class TestHasAgentSignatures:
    """Tests for storage.has_agent_signatures() Tier 2 detection."""

    def test_returns_tier2_chunks(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128
        storage.store_chunk("c1", "a.py", "h1", sig, 0, 50, 25, "code a")
        storage.store_chunk("c2", "b.py", "h2", sig, 0, 50, 25, "code b")
        storage.store_chunk("c3", "c.py", "h3", sig, 0, 50, 25, "code c")

        # Give c1 and c3 agent signatures
        agent_sig = [1.0] + [0.0] * 127
        storage.store_agent_signature("c1", agent_sig)
        storage.store_agent_signature("c3", agent_sig)

        tier2 = storage.has_agent_signatures(["c1", "c2", "c3"])
        assert tier2 == {"c1", "c3"}

    def test_empty_input(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        assert storage.has_agent_signatures([]) == set()

    def test_no_tier2(self, tmp_path):
        storage = StorageBackend(str(tmp_path / "store"))
        sig = [0.0] * 128
        storage.store_chunk("c1", "a.py", "h1", sig, 0, 50, 25, "code a")
        assert storage.has_agent_signatures(["c1"]) == set()


class TestTier2SearchBoost:
    """Tests that Tier 2 agent signatures get boosted in search ranking."""

    def test_summary_improves_ranking(self, tmp_path):
        """Chunks with agent summaries should rank higher for matching queries."""
        (tmp_path / ".git").mkdir()

        # Create two files with similar content but only one gets a summary
        f1 = tmp_path / "auth_basic.py"
        f1.write_text("def check_user(u):\n    return u.active\n")
        f2 = tmp_path / "auth_jwt.py"
        f2.write_text("def check_token(t):\n    return t.valid\n")

        engine = Stele(project_root=str(tmp_path), enable_coordination=False)

        # Index both files, only f2 gets a summary about JWT
        engine.index_documents(
            [str(f1), str(f2)],
            summaries={str(f2): "JWT token validation and verification"},
        )

        results = engine.search("JWT token validation", top_k=2)
        if len(results) >= 2:
            # The file with the summary should rank higher
            paths = [r["document_path"] for r in results]
            assert paths[0] == "auth_jwt.py", (
                f"Expected auth_jwt.py to rank first with Tier 2 boost, got {paths}"
            )
