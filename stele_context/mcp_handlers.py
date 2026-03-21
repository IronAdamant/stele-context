"""
Backward-compat shim — logic moved to mcp_server.py.

Re-exports so existing imports keep working.
"""

from __future__ import annotations

from stele_context.mcp_server import _TOOL_SCHEMAS, execute_tool  # noqa: F401
from stele_context.tool_registry import WRITE_TOOLS, build_tool_map  # noqa: F401

__all__ = ["_TOOL_SCHEMAS", "build_tool_map", "execute_tool", "WRITE_TOOLS"]
