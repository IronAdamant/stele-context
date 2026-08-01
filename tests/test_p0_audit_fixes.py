"""Regression tests for audit fixes (P0 schemas + residual P1 honesty).

Drives shipped MCP tool definitions and public Stele APIs — no mocks of the
engine under test.
"""

from __future__ import annotations

import inspect

from stele_context.engine import Stele
from stele_context.mcp_server import execute_tool
from stele_context.mcp_tools_primary import TOOL_DEFINITIONS
from stele_context.tool_registry import (
    _FULL_MODE_ONLY_TOOLS,
    _LITE_TOOLS,
    build_tool_map,
    get_modality_flags,
)


def _tool_def(name: str) -> dict:
    for t in TOOL_DEFINITIONS:
        if t["name"] == name:
            return t
    raise AssertionError(f"tool {name!r} missing from TOOL_DEFINITIONS")


class TestP0McpSchemas:
    """MCP schemas must match engine APIs agents discover."""

    def test_search_text_schema_includes_session_id(self):
        props = _tool_def("search_text")["inputSchema"]["properties"]
        assert "session_id" in props
        assert "pattern" in props
        assert "working_tree" in props

    def test_query_schema_documents_smart_defaults(self):
        q = _tool_def("query")
        desc = q["description"].lower()
        assert "working_tree" in desc
        assert "path_prefix" in desc or "path prefix" in desc
        assert "session" in desc
        # Smart-default behaviour must be visible without reading engine source
        assert "auto" in desc or "smart" in desc
        props = q["inputSchema"]["properties"]
        assert "session_id" in props
        assert "path_prefix" in props

    def test_save_kv_state_schema_matches_engine(self):
        schema = _tool_def("save_kv_state")["inputSchema"]
        props = schema["properties"]
        required = set(schema.get("required") or [])
        assert "session_id" in props
        assert "kv_data" in props
        assert "chunk_ids" in props
        assert "chunk_id" not in props  # singular was the TypeError footgun
        assert "session_id" in required
        assert "kv_data" in required
        assert "chunk_id" not in required

    def test_batch_schema_documents_engine_methods_not_global_lock(self):
        b = _tool_def("batch")
        desc = b["description"].lower()
        assert "single write lock" not in desc
        assert "index_documents" in desc
        assert "engine" in desc
        method_desc = (
            b["inputSchema"]["properties"]["operations"]["items"]["properties"]
            .get("method", {})
            .get("description", "")
            .lower()
        )
        assert "engine" in method_desc or "index_documents" in desc


class TestP0SaveKvStateDispatch:
    """Schema-shaped kwargs must reach the engine without TypeError."""

    def test_execute_tool_save_kv_state_schema_kwargs(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "kv_doc.py"
        f.write_text("def kv_marker_unique():\n    return 1\n")
        engine.index_documents([str(f)])
        # Resolve a real chunk id from the indexed document
        docs = engine.storage.get_all_documents()
        assert docs
        doc_path = docs[0]["document_path"]
        doc_chunks = engine.storage.get_document_chunks(doc_path)
        assert doc_chunks
        cid = doc_chunks[0]["chunk_id"]

        tool_map = build_tool_map(engine, get_modality_flags(), mode="standard")
        # Args shaped like the fixed MCP schema (what agents send after discovery)
        result = execute_tool(
            "save_kv_state",
            {
                "session_id": "p0-kv-session",
                "kv_data": {cid: {"note": "from schema kwargs"}},
                "chunk_ids": [cid],
            },
            tool_map,
        )
        assert result.get("success") is True, result
        assert "error" not in result

        # Old broken schema shape must not be required (and must fail if used alone)
        broken = execute_tool(
            "save_kv_state",
            {
                "session_id": "p0-kv-session-broken",
                "chunk_id": cid,
                "kv_data": {"x": 1},
            },
            tool_map,
        )
        assert broken.get("success") is not True
        assert "error" in broken


class TestP0QuerySearchHistory:
    """query(session_id) must record search history via agent_grep."""

    def test_query_with_session_id_records_search_history(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        unique = "P0QueryHistoryMarkerAlpha"
        f = tmp_path / "history_probe.py"
        f.write_text(
            f"def probe_{unique.lower()}():\n"
            f"    '''{unique} unique string for query history.'''\n"
            f"    return '{unique}'\n"
        )
        engine.index_documents([str(f)])

        session_id = "p0-query-history-session"
        before = engine.get_search_history(session_id=session_id)
        assert before.get("searches") == [] or not before.get("searches")

        out = engine.query(
            query=unique,
            top_k=5,
            session_id=session_id,
            path_prefix=None,
        )
        assert "results" in out or isinstance(out, dict)

        history = engine.get_search_history(session_id=session_id)
        searches = history.get("searches") or []
        assert len(searches) >= 1, (
            f"expected non-empty search history after query(session_id=...); got {history!r}"
        )
        patterns = [s.get("pattern") for s in searches]
        assert unique in patterns or any(unique in (p or "") for p in patterns)
        tools = {s.get("tool") for s in searches}
        assert "agent_grep" in tools

    def test_search_text_with_session_id_records_history(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        marker = "P0SearchTextHistoryBeta"
        f = tmp_path / "st_history.py"
        f.write_text(f"# {marker}\nx = 1\n")
        engine.index_documents([str(f)])

        session_id = "p0-search-text-history"
        engine.search_text(marker, session_id=session_id)
        history = engine.get_search_history(session_id=session_id)
        searches = history.get("searches") or []
        assert len(searches) >= 1
        assert any(s.get("tool") == "search_text" for s in searches)
        assert any(marker in (s.get("pattern") or "") for s in searches)


class TestP1ResidualSchemasAndModes:
    """Residual P1: schema defaults, mode surface honesty."""

    def test_stale_chunks_schema_includes_max_age_seconds(self):
        props = _tool_def("stale_chunks")["inputSchema"]["properties"]
        assert "threshold" in props
        assert "max_age_seconds" in props
        # Engine accepts the same param
        sig = inspect.signature(Stele.stale_chunks)
        assert "max_age_seconds" in sig.parameters

    def test_get_relevant_kv_top_k_default_matches_engine(self):
        schema = _tool_def("get_relevant_kv")["inputSchema"]
        schema_default = schema["properties"]["top_k"].get("default")
        engine_default = (
            inspect.signature(Stele.get_relevant_kv).parameters["top_k"].default
        )
        assert schema_default == engine_default
        assert engine_default == 10

    def test_mcp_modes_lite_standard_full_membership(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        flags = get_modality_flags()
        lite = build_tool_map(engine, flags, mode="lite")
        standard = build_tool_map(engine, flags, mode="standard")
        full = build_tool_map(engine, flags, mode="full")

        # Lite ritual tools present
        for name in ("doctor", "query", "agent_grep", "get_search_history"):
            assert name in lite

        # stale_chunks is standard/full, not lite (AGENTS documents this)
        assert "stale_chunks" not in lite
        assert "stale_chunks" in standard
        assert "stale_chunks" in full

        # Standard keeps non-deprecated tools that were mis-tagged full-only before
        assert "get_chunk_history" in standard
        assert "list_sessions" in standard
        assert "get_chunk_history" not in lite

        # Full-only deprecated singletons: full has them; standard does not
        assert "stats" in _FULL_MODE_ONLY_TOOLS
        assert "stats" in full
        assert "stats" not in standard
        assert "stats" not in lite
        # Every full-only name that is registered appears only in full (not base)
        for name in _FULL_MODE_ONLY_TOOLS:
            assert name not in standard, f"{name} should not be on standard"
            assert name in full, f"{name} should be on full"
            assert name not in _LITE_TOOLS

    def test_stale_chunks_accepts_max_age_seconds(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "stale_probe.py"
        f.write_text("def stale_probe():\n    return 1\n")
        engine.index_documents([str(f)])
        # Should not raise with the schema-advertised param
        out = engine.stale_chunks(threshold=0.3, max_age_seconds=86400)
        assert isinstance(out, dict)

    def test_full_mode_tools_have_schemas(self):
        names = {t["name"] for t in TOOL_DEFINITIONS}
        for name in _FULL_MODE_ONLY_TOOLS:
            assert name in names, f"full-mode tool {name} missing TOOL_DEFINITIONS"

    def test_map_schema_compact_default_true(self):
        props = _tool_def("map")["inputSchema"]["properties"]
        assert props["compact"].get("default") is True

    def test_query_schema_has_search_knobs(self):
        props = _tool_def("query")["inputSchema"]["properties"]
        assert "search_mode" in props
        assert "compact" in props
        assert "max_result_tokens" in props

    def test_detect_changes_session_id_optional_with_default(self):
        schema = _tool_def("detect_changes")["inputSchema"]
        required = schema.get("required") or []
        assert "session_id" not in required
        assert schema["properties"]["session_id"].get("default") == "default"

    def test_query_returns_applied_defaults(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "q_defaults.py"
        f.write_text("def query_defaults_marker():\n    return 42\n")
        engine.index_documents([str(f)])
        out = engine.query("query_defaults_marker", top_k=3, session_id="sess-ad")
        assert "applied_defaults" in out
        ad = out["applied_defaults"]
        assert "working_tree" in ad
        assert "path_prefix" in ad
        assert "path_prefix_auto" in ad
        assert "search_mode" in ad
        assert ad["search_mode"] == "keyword"
        assert ad.get("session_id") == "sess-ad"

    def test_query_respects_search_mode_param(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "q_mode.py"
        f.write_text("# unique_token_xyz_for_mode\ndef foo():\n    pass\n")
        engine.index_documents([str(f)])
        out = engine.query(
            "unique_token_xyz_for_mode",
            search_mode="keyword",
            compact=True,
        )
        assert out["applied_defaults"]["search_mode"] == "keyword"
        assert out["applied_defaults"]["compact"] is True

    def test_cli_scan_new_default_true(self):
        from stele_context.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["detect"])
        assert getattr(args, "scan_new", None) is True
        args_off = parser.parse_args(["detect", "--no-scan-new"])
        assert args_off.scan_new is False

    def test_cli_has_composite_subcommands(self):
        from stele_context.cli import create_parser

        parser = create_parser()
        for cmd in (
            "query",
            "find-definition",
            "find-references",
            "impact-radius",
            "coupling",
        ):
            args = parser.parse_args(
                [cmd, "x"]
                if cmd in ("query", "find-definition", "find-references", "coupling")
                else [cmd, "--symbol", "x"]
            )
            assert args.command == cmd

    def test_detect_changes_default_session(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "dc.py"
        f.write_text("x = 1\n")
        engine.index_documents([str(f)])
        # session_id optional — engine default "default"
        out = engine.detect_changes_and_update()
        assert isinstance(out, dict)
        assert "unchanged" in out or "modified" in out

    def test_self_healing_hint_impact_empty(self):
        from stele_context.tool_registry import (
            hint_for_tool_result,
            self_healing_hint,
        )

        hint = self_healing_hint(
            "impact_radius", Exception("Provide chunk_id, document_path, or symbol")
        )
        assert hint is not None
        assert "seed" in hint.lower() or "symbol" in hint.lower()
        # Structured result path (what the engine actually returns)
        structured = hint_for_tool_result(
            "impact_radius",
            {"error": "Provide chunk_id, document_path, or symbol"},
        )
        assert structured is not None
        assert "seed" in structured.lower() or "symbol" in structured.lower()

    def test_execute_tool_impact_radius_missing_seed_surfaces_hint(self, tmp_path):
        """Live MCP/HTTP path: impact_radius({}) returns structured error + hint."""
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "impact_seed.py"
        f.write_text("def only_local():\n    return 1\n")
        engine.index_documents([str(f)])

        tool_map = build_tool_map(engine, get_modality_flags(), mode="lite")
        assert "impact_radius" in tool_map
        # No seeds — engine returns {error: ...} under success, not an exception
        out = execute_tool("impact_radius", {}, tool_map)
        assert out.get("success") is True, out
        result = out.get("result") or {}
        assert isinstance(result, dict)
        assert result.get("error"), result
        # Client-visible: envelope and/or result body must carry the hint
        envelope_hint = out.get("hint")
        result_hint = result.get("hint")
        assert envelope_hint or result_hint, (
            f"expected self-healing hint on execute_tool impact_radius empty seed; got {out!r}"
        )
        visible = (envelope_hint or result_hint or "").lower()
        assert "seed" in visible or "symbol" in visible or "document_path" in visible
