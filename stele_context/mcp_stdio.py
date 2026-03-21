"""
MCP server for Stele using JSON-RPC over stdio.

Implements the Model Context Protocol (MCP) standard, allowing
MCP clients (like Claude Desktop) to connect via stdio transport.

Requires the ``mcp`` package: pip install stele-context[mcp]

Usage:
    stele-context serve-mcp
    # Or directly:
    python -m stele_context.mcp_stdio

Claude Desktop config:
    "stele-context": {"command": "stele-context", "args": ["serve-mcp"]}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from stele_context import __version__ as _version
from stele_context.mcp_tool_defs import TOOL_DEFINITIONS
from stele_context.tool_registry import WRITE_TOOLS, build_tool_map

logger = logging.getLogger(__name__)

# Guard MCP SDK import
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Tool,
        TextContent,
        Resource,
        ResourceTemplate,
    )

    from mcp.server import InitializationOptions
    from mcp.types import ServerCapabilities, ToolsCapability, ResourcesCapability

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def _create_engine(storage_dir: str | None = None):
    """Create a Stele engine instance."""
    from stele_context.engine import Stele

    return Stele(storage_dir=storage_dir)


class _ServerBundle:
    """Holds the MCP server, engine, and agent_id together."""

    __slots__ = ("server", "engine", "agent_id")

    def __init__(self, server: Any, engine: Any, agent_id: str) -> None:
        self.server = server
        self.engine = engine
        self.agent_id = agent_id


def create_server(storage_dir: str | None = None) -> _ServerBundle:
    """Create and configure an MCP server with Stele tools.

    Returns a ``_ServerBundle`` containing the server, engine, and agent_id.
    """
    if not HAS_MCP:
        raise ImportError(
            "MCP SDK not installed. Install with: pip install stele-context[mcp]"
        )

    from stele_context.chunkers import (
        HAS_IMAGE_CHUNKER,
        HAS_PDF_CHUNKER,
        HAS_AUDIO_CHUNKER,
        HAS_VIDEO_CHUNKER,
    )

    engine = _create_engine(storage_dir)
    server = Server("stele-context")
    server_agent_id = f"stele-context-mcp-{os.getpid()}"

    # Build tool dispatch map once (not per request)
    modality_flags = {
        "image": HAS_IMAGE_CHUNKER,
        "pdf": HAS_PDF_CHUNKER,
        "audio": HAS_AUDIO_CHUNKER,
        "video": HAS_VIDEO_CHUNKER,
    }
    tool_map = build_tool_map(engine, modality_flags)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=td["name"],
                description=td["description"],
                inputSchema=td["inputSchema"],
            )
            for td in TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            # Inject server agent_id for write operations when not provided
            if name in WRITE_TOOLS and "agent_id" not in arguments and server_agent_id:
                arguments = {**arguments, "agent_id": server_agent_id}

            if name not in tool_map:
                result = {"error": f"Unknown tool: {name}"}
            else:
                result = tool_map[name](**arguments)

            return [
                TextContent(
                    type="text",
                    text=json.dumps(result, indent=2, default=str),
                )
            ]
        except Exception as e:
            return [
                TextContent(
                    type="text",
                    text=json.dumps({"error": str(e)}),
                )
            ]

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri="stele-context://documents",  # type: ignore[arg-type]
                name="Indexed Documents",
                description="List of all indexed documents",
                mimeType="application/json",
            ),
        ]

    @server.list_resource_templates()
    async def list_resource_templates() -> list[ResourceTemplate]:
        return [
            ResourceTemplate(
                uriTemplate="stele-context://document/{path}",
                name="Document Chunks",
                description="Chunks for a specific document with content",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        uri_str = str(uri)
        if uri_str == "stele-context://documents":
            docs = engine.get_map()
            return json.dumps(docs, indent=2, default=str)
        elif uri_str.startswith("stele-context://document/"):
            doc_path = uri_str[len("stele-context://document/") :]
            chunks = engine.storage.get_document_chunks(doc_path)
            enriched = []
            for meta in chunks:
                chunk = engine.storage.get_chunk(meta["chunk_id"])
                if chunk:
                    enriched.append(chunk)
            return json.dumps(enriched, indent=2, default=str)
        return json.dumps({"error": f"Unknown resource: {uri_str}"})

    return _ServerBundle(server, engine, server_agent_id)


async def _run_server(bundle: _ServerBundle) -> None:
    """Run the MCP server on stdio transport."""
    bundle.engine.register_agent(bundle.agent_id)

    try:
        init_options = InitializationOptions(
            server_name="stele-context",
            server_version=_version,
            capabilities=ServerCapabilities(
                tools=ToolsCapability(listChanged=False),
                resources=ResourcesCapability(subscribe=False, listChanged=False),
            ),
        )
        async with stdio_server() as (read_stream, write_stream):
            await bundle.server.run(read_stream, write_stream, init_options)
    finally:
        bundle.engine.deregister_agent(bundle.agent_id)
        bundle.engine.storage.close()


def run(storage_dir: str | None = None) -> None:
    """Entry point for ``stele-context serve-mcp``."""
    if not HAS_MCP:
        print(
            "Error: MCP SDK not installed.\nInstall with: pip install stele-context[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    bundle = create_server(storage_dir)
    asyncio.run(_run_server(bundle))


main = run

main = run

if __name__ == "__main__":
    run()
