"""
Unified tool registry for Stele HTTP and MCP stdio servers.

Single source of truth for:
- WRITE_TOOLS: tool names receiving auto agent_id injection
- build_tool_map(): maps tool names to engine callables
- get_http_schemas(): converts tool definitions to HTTP format

Both servers import from here instead of maintaining independent
tool maps and write-tool sets. Schemas remain in mcp_tool_defs.py
(the canonical source for all tool definitions).
"""

from __future__ import annotations

from typing import Any, Callable

from stele.mcp_tool_defs import TOOL_DEFINITIONS

# Tools that modify state and should receive auto-injected agent_id.
WRITE_TOOLS = frozenset(
    {
        "index",
        "detect_changes",
        "remove",
        "save_kv_state",
        "store_semantic_summary",
        "store_embedding",
    }
)


def build_tool_map(
    engine: Any,
    modality_flags: dict[str, bool] | None = None,
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
    """
    tool_map: dict[str, Callable[..., Any]] = {
        # Core operations
        "index": engine.index_documents,
        "remove": engine.remove_document,
        "search": engine.search,
        "get_context": engine.get_context,
        "detect_changes": engine.detect_changes_and_update,
        # Annotations
        "annotate": engine.annotate,
        "get_annotations": engine.get_annotations,
        "delete_annotation": engine.delete_annotation,
        "update_annotation": engine.update_annotation,
        "search_annotations": engine.search_annotations,
        "bulk_annotate": engine.bulk_annotate,
        # History & stats
        "prune_history": engine.prune_history,
        "map": engine.get_map,
        "history": engine.get_history,
        "stats": engine.get_stats,
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
        # Document locking
        "acquire_document_lock": engine.acquire_document_lock,
        "refresh_document_lock": engine.refresh_document_lock,
        "release_document_lock": engine.release_document_lock,
        "get_document_lock_status": engine.get_document_lock_status,
        "release_agent_locks": engine.release_agent_locks,
        "get_conflicts": engine.get_conflicts,
        "reap_expired_locks": engine.reap_expired_locks,
        # Agent coordination
        "list_agents": engine.list_agents,
        "get_notifications": engine.get_notifications,
        # Environment
        "environment_check": engine.check_environment,
        "clean_bytecache": engine.clean_bytecache,
        # Embeddings
        "store_semantic_summary": engine.store_semantic_summary,
        "store_embedding": engine.store_embedding,
        # Chunk history
        "get_chunk_history": engine.get_chunk_history,
    }

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

    return tool_map


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
