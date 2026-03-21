"""
MCP tool dispatch for the Stele HTTP server.

This module contains:
- _WRITE_TOOLS: tool names that receive auto-injected agent_id
- build_tool_map(): builds tool-name -> callable mapping from a Stele engine
- execute_tool(): dispatches a tool call with agent_id injection

Separated from mcp_server.py to keep each file under 500 LOC.
All functions receive the engine as a parameter (no stele-internal imports).
Schemas live in mcp_schemas.py (pure data, no logic).
"""

from typing import Any, Callable, Dict

from stele.mcp_schemas import _TOOL_SCHEMAS

# Re-export so existing imports keep working.
__all__ = ["_TOOL_SCHEMAS", "build_tool_map", "execute_tool"]

# Write tools that should receive a default agent_id when callers omit it.
_WRITE_TOOLS = frozenset(
    {
        "index_documents",
        "detect_changes_and_update",
        "save_kv_state",
    }
)


def build_tool_map(
    stele: Any,
    modality_flags: Dict[str, bool],
) -> Dict[str, Callable[..., Any]]:
    """Build tool-name -> callable mapping from a Stele engine instance.

    Parameters
    ----------
    stele:
        A ``Stele`` engine instance (typed as ``Any`` to avoid importing
        from stele internals -- this module stays dependency-free).
    modality_flags:
        Mapping of modality name to availability bool, e.g.
        ``{"image": True, "pdf": False, ...}``.

    Returns
    -------
    Dict mapping tool name strings to callables that accept ``**kwargs``.
    """

    def _get_supported_formats(**_: Any) -> Dict[str, Any]:
        formats = {
            "text": stele.chunkers["text"].supported_extensions(),
            "code": stele.chunkers["code"].supported_extensions(),
        }
        for modality, available in modality_flags.items():
            if available and modality in stele.chunkers:
                formats[modality] = stele.chunkers[modality].supported_extensions()
        return {"formats": formats}

    def _detect_modality(path: str = "", **_: Any) -> Dict[str, Any]:
        return {"path": path, "modality": stele.detect_modality(path)}

    return {
        "index_documents": stele.index_documents,
        "detect_changes_and_update": stele.detect_changes_and_update,
        "get_relevant_kv": stele.get_relevant_kv,
        "save_kv_state": stele.save_kv_state,
        "rollback": stele.rollback,
        "prune_chunks": stele.prune_chunks,
        "search": stele.search,
        "get_context": stele.get_context,
        "detect_modality": _detect_modality,
        "get_supported_formats": _get_supported_formats,
        "find_references": stele.find_references,
        "find_definition": stele.find_definition,
        "impact_radius": stele.impact_radius,
        "rebuild_symbol_graph": stele.rebuild_symbol_graph,
        "stale_chunks": stele.stale_chunks,
        "list_sessions": stele.list_sessions,
        "acquire_document_lock": stele.acquire_document_lock,
        "refresh_document_lock": stele.refresh_document_lock,
        "release_document_lock": stele.release_document_lock,
        "get_document_lock_status": stele.get_document_lock_status,
        "release_agent_locks": stele.release_agent_locks,
        "get_conflicts": stele.get_conflicts,
        "reap_expired_locks": stele.reap_expired_locks,
        "list_agents": stele.list_agents,
        "environment_check": stele.check_environment,
        "clean_bytecache": stele.clean_bytecache,
        "store_semantic_summary": stele.store_semantic_summary,
        "store_embedding": stele.store_embedding,
        "get_chunk_history": stele.get_chunk_history,
        "get_notifications": stele.get_notifications,
    }


def execute_tool(
    tool_name: str,
    parameters: Dict[str, Any],
    tool_map: Dict[str, Callable[..., Any]],
    server_agent_id: str = "",
) -> Dict[str, Any]:
    """Execute a tool by name, returning a JSON-serialisable result dict.

    Handles agent_id injection for write tools, unknown-tool errors,
    parameter validation errors, and general exceptions.

    Parameters
    ----------
    tool_name:
        Name of the tool to execute (must be a key in *tool_map*).
    parameters:
        Keyword arguments forwarded to the tool callable.
    tool_map:
        Mapping returned by :func:`build_tool_map`.
    server_agent_id:
        Default agent_id injected into write tools when the caller
        does not supply one.
    """
    # Inject server agent_id for write operations when not provided
    if tool_name in _WRITE_TOOLS and "agent_id" not in parameters and server_agent_id:
        parameters = {**parameters, "agent_id": server_agent_id}

    if tool_name not in tool_map:
        return {
            "error": f"Unknown tool: {tool_name}",
            "available_tools": list(tool_map.keys()),
        }

    try:
        result = tool_map[tool_name](**parameters)
        return {"success": True, "result": result}
    except TypeError as e:
        return {"error": f"Invalid parameters for {tool_name}: {e}"}
    except Exception as e:
        return {"error": f"Tool execution failed: {e}"}
