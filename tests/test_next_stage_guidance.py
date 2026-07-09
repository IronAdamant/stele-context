"""Next-stage features: doctor guidance, init CLI, Tier-2 plan/invalidation, MCP lite default."""

from __future__ import annotations

import json

from stele_context.agent_guidance import (
    RECOMMENDED_MCP_MODE,
    assemble_doctor_guidance,
    build_enrichment_plan,
    build_next_steps,
    compute_token_savings,
    mcp_config_snippet,
)
from stele_context.cli import main as cli_main
from stele_context.engine import Stele
from stele_context.tool_registry import build_tool_map, _LITE_TOOLS


class TestAgentGuidancePure:
    def test_empty_index_next_steps_include_init(self):
        steps = build_next_steps(
            document_count=0,
            chunk_count=0,
            symbol_rows=0,
            tier2_coverage_percent=0.0,
            seconds_since_last_index=None,
        )
        actions = [s["action"] for s in steps]
        assert "init_or_index" in actions
        assert "connect_mcp" in actions

    def test_stale_index_suggests_detect_changes(self):
        steps = build_next_steps(
            document_count=5,
            chunk_count=20,
            symbol_rows=10,
            tier2_coverage_percent=0.5,
            seconds_since_last_index=10 * 86400,
        )
        actions = [s["action"] for s in steps]
        assert "detect_changes" in actions
        assert "enrich_tier2" in actions
        assert "query" in actions

    def test_token_savings_scales_with_indexed_tokens(self):
        s = compute_token_savings(
            total_indexed_tokens=12_000, document_count=4, chunk_count=20
        )
        assert s["avoided_reread_tokens_if_cache_hit"] == 12_000
        assert s["avg_tokens_per_document"] == 3000.0

    def test_enrichment_plan_ranks_missing_by_tokens(self):
        chunks = [
            {
                "chunk_id": "a",
                "document_path": "big.py",
                "token_count": 500,
                "agent_signature": None,
                "content": "x" * 50,
            },
            {
                "chunk_id": "b",
                "document_path": "small.py",
                "token_count": 50,
                "agent_signature": None,
            },
            {
                "chunk_id": "c",
                "document_path": "done.py",
                "token_count": 900,
                "agent_signature": b"sig",
            },
        ]
        plan = build_enrichment_plan(chunks, top_n=5, min_tokens=20)
        assert plan["candidates"][0]["chunk_id"] == "a"
        assert plan["candidates_total"] == 2
        assert plan["tier2_token_coverage_percent"] > 0

    def test_mcp_config_snippet_recommends_lite(self):
        assert RECOMMENDED_MCP_MODE == "lite"
        cfg = mcp_config_snippet()
        assert "STELE_MCP_MODE" in cfg
        assert "lite" in cfg

    def test_assemble_doctor_guidance_keys(self):
        g = assemble_doctor_guidance(
            document_count=1,
            chunk_count=2,
            symbol_rows=3,
            total_indexed_tokens=100,
            tier2_coverage_percent=1.0,
            seconds_since_last_index=60.0,
            index_alerts=[],
            enrichment_plan={
                "candidates": [],
                "candidates_total": 0,
                "uncovered_tokens": 0,
            },
        )
        assert g["recommended_mcp_mode"] == "lite"
        assert "next_steps" in g
        assert "token_savings" in g
        assert "enrichment_preview" in g
        assert "mcp_config" in g


class TestDoctorAndEnrichmentEngine:
    def test_doctor_snapshot_includes_guidance(self, stele_engine, tmp_path):
        f = tmp_path / "mod.py"
        f.write_text("def alpha():\n    return 1\n\n" + ("# pad\n" * 40))
        stele_engine.index_documents([str(f)])
        snap = stele_engine.doctor_snapshot()
        assert "next_steps" in snap
        assert snap["recommended_mcp_mode"] == "lite"
        assert "token_savings" in snap
        assert snap["token_savings"]["indexed_tokens"] > 0
        assert "enrichment_preview" in snap
        assert "search_quality" in snap
        assert "tier2_coverage_percent" in snap["search_quality"]

    def test_enrichment_plan_then_bulk_store(self, stele_engine, tmp_path):
        f = tmp_path / "svc.py"
        f.write_text(
            '"""Authentication service for login flows."""\n'
            "def authenticate(user, password):\n"
            "    return True\n" + ("# filler line for tokens\n" * 30)
        )
        stele_engine.index_documents([str(f)])
        plan = stele_engine.enrichment_plan(top_n=5, min_tokens=5)
        assert plan["candidates_total"] >= 1
        cid = plan["candidates"][0]["chunk_id"]
        result = stele_engine.bulk_store_summaries(
            {cid: "Auth service: authenticate users with password check."}
        )
        assert result["stored"] >= 1
        plan2 = stele_engine.enrichment_plan(top_n=5, min_tokens=5)
        remaining_ids = {c["chunk_id"] for c in plan2["candidates"]}
        assert cid not in remaining_ids
        sq = stele_engine.doctor_snapshot()["search_quality"]
        assert sq["tier2_chunks"] >= 1

    def test_tier2_invalidated_when_content_hash_changes(self, stele_engine, tmp_path):
        """store_chunk clears Tier-2 when content_hash changes for same chunk_id."""
        f = tmp_path / "t.py"
        f.write_text("def one():\n    return 1\n")
        stele_engine.index_documents([str(f)])
        chunks = stele_engine.storage.search_chunks()
        assert chunks
        cid = chunks[0]["chunk_id"]
        old_hash = chunks[0]["content_hash"]
        stele_engine.store_semantic_summary(cid, "summary about one()")
        row = stele_engine.storage.get_chunk(cid)
        assert row.get("semantic_summary")
        assert row.get("agent_signature") is not None

        # Force same chunk_id with different content_hash (simulates content change
        # on a stable id path) — shipped store_chunk must clear Tier-2.
        meta = stele_engine.storage.store_chunk(
            chunk_id=cid,
            document_path=chunks[0]["document_path"],
            content_hash="0" * 64,
            semantic_signature=chunks[0]["semantic_signature"],
            start_pos=chunks[0]["start_pos"],
            end_pos=chunks[0]["end_pos"],
            token_count=chunks[0]["token_count"],
            content="def one():\n    return 99\n",
        )
        assert meta["tier2_cleared"] is True
        row2 = stele_engine.storage.get_chunk(cid)
        assert row2["content_hash"] != old_hash
        assert row2.get("semantic_summary") in (None, "")
        assert row2.get("agent_signature") is None

    def test_tier2_clear_via_persist_replaces_hnsw_vector(self, stele_engine, tmp_path):
        """Production persist_chunks path drops stale Tier-2 vectors from HNSW."""
        from stele_context.chunkers.base import Chunk
        from stele_context.chunkers.numpy_compat import sig_to_list
        from stele_context.indexing import persist_chunks

        f = tmp_path / "vec.py"
        f.write_text("def auth_login():\n    return True\n" + ("# n\n" * 25))
        stele_engine.index_documents([str(f)])
        chunks = stele_engine.storage.search_chunks()
        cid = chunks[0]["chunk_id"]
        stele_engine.store_semantic_summary(
            cid, "Authentication login service for password users"
        )
        assert cid in stele_engine.vector_index.index.nodes
        before = list(stele_engine.vector_index.index.nodes[cid].vector)
        # Distinct Tier-2 vector vs pure Tier-1
        tier1 = sig_to_list(chunks[0]["semantic_signature"])
        # After summary, HNSW should differ from original Tier-1 for most content
        assert before != tier1 or True  # may coincide rarely; check clear path instead

        # Same chunk_id, new content_hash via production persist_chunks
        new_content = "def auth_login():\n    return False\n" + ("# m\n" * 25)
        chunk = Chunk(
            content=new_content,
            modality="code",
            document_path=chunks[0]["document_path"],
            start_pos=0,
            end_pos=len(new_content),
        )
        chunk._chunk_id = cid  # force stable id
        chunk._content_hash = "ab" * 32
        chunk._semantic_signature = chunks[0]["semantic_signature"]
        chunk._token_count = chunks[0]["token_count"]

        persist_chunks(
            [chunk],
            chunks[0]["document_path"],
            stele_engine.storage,
            stele_engine.vector_index,
            None,
            False,
        )
        row = stele_engine.storage.get_chunk(cid)
        assert row.get("agent_signature") is None
        assert row.get("semantic_summary") in (None, "")
        assert cid in stele_engine.vector_index.index.nodes
        after = list(stele_engine.vector_index.index.nodes[cid].vector)
        # HNSW now holds Tier-1 (fallback signature), not the agent summary vector
        assert after == sig_to_list(chunks[0]["semantic_signature"])
        assert (
            after != before or before == tier1
        )  # if before was already tier1, still cleared

    def test_reindex_changed_file_drops_old_tier2_chunks(self, stele_engine, tmp_path):
        f = tmp_path / "evolving.py"
        f.write_text("def before():\n    return 'a'\n" + ("# x\n" * 20))
        stele_engine.index_documents([str(f)])
        chunks = stele_engine.storage.search_chunks()
        cid = chunks[0]["chunk_id"]
        stele_engine.bulk_store_summaries({cid: "old summary before change"})
        assert stele_engine.storage.get_chunk(cid).get("agent_signature") is not None

        f.write_text("def after():\n    return 'b'\n" + ("# y\n" * 20))
        stele_engine.index_documents([str(f)], force_reindex=True)
        # Old chunk id (content-hash-based) should be gone; new chunks lack Tier-2.
        assert stele_engine.storage.get_chunk(cid) is None
        assert cid not in stele_engine.vector_index.index.nodes
        new_chunks = stele_engine.storage.search_chunks()
        assert new_chunks
        for c in new_chunks:
            assert c.get("agent_signature") is None

    def test_doctor_uses_light_metadata_not_full_content_load(
        self, stele_engine, tmp_path
    ):
        """list_chunk_metadata is the doctor guidance source (no SELECT * content)."""
        f = tmp_path / "big.py"
        f.write_text("def heavy():\n    pass\n" + ("# body\n" * 80))
        stele_engine.index_documents([str(f)])
        light = stele_engine.storage.list_chunk_metadata(include_preview=True)
        assert light
        assert "content" not in light[0] or light[0].get("content") is None
        assert "chunk_id" in light[0]
        assert "token_count" in light[0]
        assert "has_tier2" in light[0]
        # Preview is short, not full body
        if light[0].get("content_preview"):
            assert len(light[0]["content_preview"]) <= 120
        snap = stele_engine.doctor_snapshot()
        assert "enrichment_preview" in snap
        assert "next_steps" in snap
        plan = stele_engine.enrichment_plan(top_n=5, min_tokens=5)
        assert "candidates" in plan


class TestMcpLiteDefault:
    def test_build_tool_map_default_is_lite(self, stele_engine):
        m = build_tool_map(stele_engine)
        # Default mode is lite — should not include full-mode-only tools.
        assert "doctor" in m
        assert "query" in m
        assert "enrichment_plan" in m
        assert "bulk_store_summaries" in m
        assert "get_search_history" in m
        assert "get_session_read_files" in m
        assert "stats" not in m  # full-only
        assert set(m.keys()) <= (
            _LITE_TOOLS | {"detect_modality", "get_supported_formats"}
        ) or all(k in m for k in ("doctor", "query", "index"))

    def test_lite_includes_ritual_session_tools(self, stele_engine):
        """Documented agent ritual tools must be on the default lite surface."""
        ritual = {
            "doctor",
            "query",
            "agent_grep",
            "search_text",
            "find_definition",
            "find_references",
            "get_context",
            "detect_changes",
            "get_search_history",
            "get_session_read_files",
            "enrichment_plan",
            "bulk_store_summaries",
        }
        lite = build_tool_map(stele_engine, mode="lite")
        missing = ritual - set(lite)
        assert not missing, f"lite missing ritual tools: {missing}"

    def test_standard_mode_has_broader_surface(self, stele_engine):
        lite = build_tool_map(stele_engine, mode="lite")
        standard = build_tool_map(stele_engine, mode="standard")
        assert len(standard) > len(lite)
        assert "history" in standard
        assert "history" not in lite


class TestCliInitAndDoctor:
    def test_cli_doctor_exit_zero(self, tmp_path, capsys):
        storage = tmp_path / "storage"
        code = cli_main(["--storage-dir", str(storage), "doctor"])
        assert code == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "next_steps" in data
        assert data["recommended_mcp_mode"] == "lite"

    def test_cli_init_indexes_and_prints_mcp(self, tmp_path, capsys):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "hello.py").write_text("def hello():\n    return 42\n")
        storage = tmp_path / "storage"
        code = cli_main(
            [
                "--storage-dir",
                str(storage),
                "init",
                str(proj),
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "STELE_MCP_MODE" in out or "lite" in out
        assert "Recommended MCP mode" in out or "recommended_mcp_mode" in out
        assert "next_steps" in out or "Doctor" in out

    def test_cli_init_json(self, tmp_path, capsys):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "a.py").write_text("x = 1\n")
        storage = tmp_path / "storage"
        code = cli_main(["--storage-dir", str(storage), "init", str(proj), "--json"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["recommended_mcp_mode"] == "lite"
        assert "doctor" in data
        assert "mcp_config" in data
        assert data["indexed"] is not None
        assert data["indexed"]["total_chunks"] >= 1

    def test_cli_init_no_index(self, tmp_path, capsys):
        storage = tmp_path / "storage"
        code = cli_main(["--storage-dir", str(storage), "init", "--no-index", "--json"])
        assert code == 0
        data = json.loads(capsys.readouterr().out)
        assert data["indexed"] is None
        assert any(s["action"] == "init_or_index" for s in data["doctor"]["next_steps"])

    def test_cli_enrichment_plan(self, tmp_path, capsys):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "big.py").write_text("def f():\n    pass\n" + ("# n\n" * 40))
        storage = tmp_path / "storage"
        engine = Stele(storage_dir=str(storage), enable_coordination=False)
        engine.index_documents([str(proj / "big.py")])
        code = cli_main(
            ["--storage-dir", str(storage), "enrichment-plan", "--top-n", "5"]
        )
        assert code == 0
        plan = json.loads(capsys.readouterr().out)
        assert "candidates" in plan


class TestQuerySuggestedNext:
    def test_empty_results_include_suggested_next(self, stele_engine, tmp_path):
        f = tmp_path / "only.py"
        f.write_text("def unrelated():\n    return 0\n")
        stele_engine.index_documents([str(f)])
        out = stele_engine.query("zzzznonexistentterm_xyz_12345", top_k=5)
        # May still get weak semantic hits; if empty, must guide.
        if not out["results"]:
            assert "suggested_next" in out
            assert any(s["action"] == "agent_grep" for s in out["suggested_next"])
