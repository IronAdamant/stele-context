"""
MCP tool dispatch for the Stele HTTP server.

This module provides:
- execute_tool(): dispatches a tool call with agent_id injection
- _TOOL_SCHEMAS: HTTP-formatted schemas (generated from tool_registry)
- build_tool_map / WRITE_TOOLS: re-exported from tool_registry

Separated from mcp_server.py to keep each file under 500 LOC.
"""

from __future__ import annotations

from typing import Any, Callable

from stele.tool_registry import (
    WRITE_TOOLS,
    build_tool_map,
    get_http_schemas,
)

_TOOL_SCHEMAS = get_http_schemas()

# Re-export so existing imports keep working.
__all__ = ["_TOOL_SCHEMAS", "build_tool_map", "execute_tool", "WRITE_TOOLS"]


def execute_tool(
    tool_name: str,
    parameters: dict[str, Any],
    tool_map: dict[str, Callable[..., Any]],
    server_agent_id: str = "",
) -> dict[str, Any]:
    """Execute a tool by name, returning a JSON-serialisable result dict.

    Handles agent_id injection for write tools, unknown-tool errors,
    parameter validation errors, and general exceptions.
    """
    # Inject server agent_id for write operations when not provided
    if tool_name in WRITE_TOOLS and "agent_id" not in parameters and server_agent_id:
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
