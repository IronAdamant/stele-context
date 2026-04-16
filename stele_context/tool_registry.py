"""
Unified tool registry for Stele HTTP and MCP stdio servers.

Single source of truth for:
- WRITE_TOOLS: tool names receiving auto agent_id injection
- build_tool_map(): maps tool names to engine callables
- get_http_schemas(): converts tool definitions to HTTP format

Both servers import from here instead of maintaining independent
tool maps and write-tool sets. Schemas live in `mcp_tools_primary.py`
(non-symbol tools) and `mcp_tools_symbols.py` (symbol-graph tools),
combined and re-exported as `TOOL_DEFINITIONS`.
"""

from __future__ import annotations

from typing import Any, Callable

from stele_context.mcp_tools_primary import TOOL_DEFINITIONS

# Tools that modify state and should receive auto-injected agent_id.
WRITE_TOOLS = frozenset(
    {
        "index",
        "detect_changes",
        "remove",
        "save_kv_state",
        "bulk_store_embeddings",
        "llm_embed",
        "bulk_store_summaries",
        "bulk_store_chunk_agent_notes",
        "document_lock",
        "register_dynamic_symbols",
        "remove_dynamic_symbols",
        "annotations",
        "batch",
    }
)

# Deprecated singleton tools kept in "full" mode for backward compatibility.
_FULL_MODE_ONLY_TOOLS = frozenset(
    {
        "stats",
        "project_brief",
        "annotate",
        "get_annotations",
        "delete_annotation",
        "update_annotation",
        "search_annotations",
        "bulk_annotate",
        "acquire_document_lock",
        "refresh_document_lock",
        "release_document_lock",
        "get_document_lock_status",
        "release_agent_locks",
        "get_conflicts",
        "reap_expired_locks",
        "store_semantic_summary",
        "store_embedding",
        "store_chunk_agent_notes",
        # Moved to full mode to reduce standard surface (Phase 4 rationalisation)
        "get_chunk_history",
        "list_sessions",
        "environment_check",
        "clean_bytecache",
        "prune_history",
        "get_dynamic_symbols",
        "get_notifications",
    }
)

# Lite mode: highest-leverage tools only.
_LITE_TOOLS = frozenset(
    {
        "index",
        "remove",
        "detect_changes",
        "search",
        "agent_grep",
        "search_text",
        "query",
        "find_references",
        "find_definition",
        "impact_radius",
        "coupling",
        "get_context",
        "doctor",
        "map",
        "document_lock",
        "annotations",
        "register_dynamic_symbols",
        "remove_dynamic_symbols",
        "llm_embed",
    }
)


def _wrap_with_telemetry(
    tool_name: str,
    func: Callable[..., Any],
    storage: Any,
) -> Callable[..., Any]:
    """Wrap a tool callable so every invocation is logged to operation_log."""
    import time as _time
    import functools

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = _time.time()
        success = True
        error_type: str | None = None
        try:
            return func(*args, **kwargs)
        except Exception as e:
            success = False
            error_type = type(e).__name__
            raise
        finally:
            try:
                duration_ms = (_time.time() - start) * 1000.0
                storage.log_operation(tool_name, success, error_type, duration_ms)
            except Exception:
                pass

    return wrapper


def build_tool_map(
    engine: Any,
    modality_flags: dict[str, bool] | None = None,
    mode: str = "standard",
) -> dict[str, Callable[..., Any]]:
    """Build a {tool_name: callable} dispatch map from a Stele engine.

    Parameters
    ----------
    engine:
        A ``Stele`` engine instance.
    modality_flags:
        Optional mapping of modality name to availability bool, e.g.
        ``{"image": True, "pdf": False}``.  When provided, the
        ``detect_modality`` and ``get_supported_formats`` utility
        tools are included.
    mode:
        "standard" (default, simplified surface), "lite" (~15 core tools),
        or "full" (includes deprecated singleton tools for backward compat).
    """
    _log = getattr(engine, "storage", None) is not None
    tool_map: dict[str, Callable[..., Any]] = {
        # Core operations
        "index": engine.index_documents,
        "remove": engine.remove_document,
        "search": engine.search,
        "get_context": engine.get_context,
        "detect_changes": engine.detect_changes_and_update,
        # Unified annotations
        "annotations": engine.annotations,
        # History
        "prune_history": engine.prune_history,
        "map": engine.get_map,
        "doctor": engine.doctor_snapshot,
        "history": engine.get_history,
        # Session
        "get_relevant_kv": engine.get_relevant_kv,
        "save_kv_state": engine.save_kv_state,
        "rollback": engine.rollback,
        "prune_chunks": engine.prune_chunks,
        "list_sessions": engine.list_sessions,
        # Symbol graph
        "find_references": engine.find_references,
        "find_definition": engine.find_definition,
        "impact_radius": engine.impact_radius,
        "rebuild_symbols": engine.rebuild_symbol_graph,
        "stale_chunks": engine.stale_chunks,
        "coupling": engine.coupling,
        # Dynamic symbol tracking (runtime symbols)
        "register_dynamic_symbols": engine.register_dynamic_symbols,
        "remove_dynamic_symbols": engine.remove_dynamic_symbols,
        "get_dynamic_symbols": engine.get_dynamic_symbols,
        # Unified document locking
        "document_lock": engine.document_lock,
        # Agent coordination
        "list_agents": engine.list_agents,
        "get_notifications": engine.get_notifications,
        # Environment
        "environment_check": engine.check_environment,
        "clean_bytecache": engine.clean_bytecache,
        # Embeddings (bulk only in standard mode)
        "bulk_store_embeddings": engine.bulk_store_embeddings,
        "llm_embed": engine.llm_embed,
        "bulk_store_summaries": engine.bulk_store_summaries,
        "bulk_store_chunk_agent_notes": engine.bulk_store_chunk_agent_notes,
        # Chunk history
        "get_chunk_history": engine.get_chunk_history,
        # Text pattern search (perfect recall)
        "search_text": engine.search_text,
        # LLM-optimized search
        "agent_grep": engine.agent_grep,
        # Session provenance (grep-to-cache workflow)
        "get_search_history": engine.get_search_history,
        "get_session_read_files": engine.get_session_read_files,
        # Composite tools
        "query": engine.query,
        "batch": engine.batch,
    }

    # Full mode: restore deprecated singleton tools
    if mode == "full":
        tool_map.update(
            {
                "stats": engine.get_stats,
                "project_brief": engine.get_project_brief,
                "annotate": engine.annotate,
                "get_annotations": engine.get_annotations,
                "delete_annotation": engine.delete_annotation,
                "update_annotation": engine.update_annotation,
                "search_annotations": engine.search_annotations,
                "bulk_annotate": engine.bulk_annotate,
                "acquire_document_lock": engine.acquire_document_lock,
                "refresh_document_lock": engine.refresh_document_lock,
                "release_document_lock": engine.release_document_lock,
                "get_document_lock_status": engine.get_document_lock_status,
                "release_agent_locks": engine.release_agent_locks,
                "get_conflicts": engine.get_conflicts,
                "reap_expired_locks": engine.reap_expired_locks,
                "store_semantic_summary": engine.store_semantic_summary,
                "store_embedding": engine.store_embedding,
                "store_chunk_agent_notes": engine.store_chunk_agent_notes,
            }
        )

    if mode == "lite":
        tool_map = {k: v for k, v in tool_map.items() if k in _LITE_TOOLS}

    # Utility tools backed by chunker metadata (not engine methods)
    if modality_flags is not None:

        def _detect_modality(path: str = "", **_: Any) -> dict[str, Any]:
            return {"path": path, "modality": engine.detect_modality(path)}

        def _get_supported_formats(**_: Any) -> dict[str, Any]:
            formats = {
                "text": engine.chunkers["text"].supported_extensions(),
                "code": engine.chunkers["code"].supported_extensions(),
            }
            for modality, available in modality_flags.items():
                if available and modality in engine.chunkers:
                    formats[modality] = engine.chunkers[modality].supported_extensions()
            return {"formats": formats}

        tool_map["detect_modality"] = _detect_modality
        tool_map["get_supported_formats"] = _get_supported_formats

    if _log:
        storage = engine.storage
        tool_map = {k: _wrap_with_telemetry(k, v, storage) for k, v in tool_map.items()}

    return tool_map


def get_modality_flags() -> dict[str, bool]:
    """Build modality availability flags from installed chunkers."""
    from stele_context.chunkers import (
        HAS_IMAGE_CHUNKER,
        HAS_PDF_CHUNKER,
        HAS_AUDIO_CHUNKER,
        HAS_VIDEO_CHUNKER,
    )

    return {
        "image": HAS_IMAGE_CHUNKER,
        "pdf": HAS_PDF_CHUNKER,
        "audio": HAS_AUDIO_CHUNKER,
        "video": HAS_VIDEO_CHUNKER,
    }


def self_healing_hint(tool_name: str, error: Exception) -> str | None:
    """Return an actionable next-step hint for known failure patterns."""
    err = str(error).lower()
    if (
        "database is locked" in err
        or "busy" in err
        or "unable to open database file" in err
    ):
        return "SQLite is under load. Run detect_changes or wait a moment and retry."
    if tool_name in ("impact_radius", "coupling") and "0 affected" in err:
        return (
            "This file may be a base class with high fan-in. "
            "Consider also running find_references on its exported symbols."
        )
    if tool_name == "search" and "hnsw" not in err and "0" in str(error):
        return (
            "Tier-1 semantic search returned no strong matches. "
            "Results may fall back to BM25 keyword ranking. "
            "Consider adding Tier-2 summaries for this topic."
        )
    return None


def get_http_schemas() -> dict[str, dict[str, Any]]:
    """Convert tool definitions to the HTTP server schema format.

    Transforms ``{"name": ..., "inputSchema": ...}`` into
    ``{name: {"description": ..., "parameters": ...}}``.
    """
    return {
        tool["name"]: {
            "description": tool["description"],
            "parameters": tool["inputSchema"],
        }
        for tool in TOOL_DEFINITIONS
    }
