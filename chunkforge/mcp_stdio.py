"""
MCP server for ChunkForge using JSON-RPC over stdio.

Implements the Model Context Protocol (MCP) standard, allowing
MCP clients (like Claude Desktop) to connect via stdio transport.

Requires the `mcp` package: pip install chunkforge[mcp]

Usage:
    chunkforge serve-mcp
    # Or directly:
    python -m chunkforge.mcp_stdio

Claude Desktop config:
    "chunkforge": {"command": "chunkforge", "args": ["serve-mcp"]}
"""

import asyncio
import json
import logging
import sys
from typing import Any, Dict, List, Optional

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
    from mcp.types import ServerCapabilities

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def _create_engine(storage_dir: Optional[str] = None):
    """Create a ChunkForge engine instance."""
    from chunkforge.engine import ChunkForge

    return ChunkForge(storage_dir=storage_dir)


def create_server(storage_dir: Optional[str] = None) -> Any:
    """
    Create and configure an MCP server with ChunkForge tools.

    Returns the configured MCP Server instance.
    """
    if not HAS_MCP:
        raise ImportError(
            "MCP SDK not installed. Install with: pip install chunkforge[mcp]"
        )

    engine = _create_engine(storage_dir)
    server = Server("chunkforge")

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        return [
            Tool(
                name="index",
                description="Index documents for semantic chunking and caching",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "File paths to index",
                        },
                        "force_reindex": {
                            "type": "boolean",
                            "description": "Force re-indexing even if unchanged",
                            "default": False,
                        },
                    },
                    "required": ["paths"],
                },
            ),
            Tool(
                name="search",
                description="Semantic search across indexed chunks, returns content",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query text",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="get_context",
                description="Get cached context for documents (unchanged/changed/new)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Document paths to get context for",
                        },
                    },
                    "required": ["document_paths"],
                },
            ),
            Tool(
                name="detect_changes",
                description="Detect changes in indexed documents",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "Session identifier",
                        },
                        "document_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Paths to check (default: all indexed)",
                        },
                    },
                    "required": ["session_id"],
                },
            ),
            Tool(
                name="stats",
                description="Get ChunkForge statistics",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        try:
            if name == "index":
                result = engine.index_documents(
                    paths=arguments["paths"],
                    force_reindex=arguments.get("force_reindex", False),
                )
            elif name == "search":
                result = engine.search(
                    query=arguments["query"],
                    top_k=arguments.get("top_k", 10),
                )
            elif name == "get_context":
                result = engine.get_context(
                    document_paths=arguments["document_paths"],
                )
            elif name == "detect_changes":
                result = engine.detect_changes_and_update(
                    session_id=arguments["session_id"],
                    document_paths=arguments.get("document_paths"),
                )
            elif name == "stats":
                result = engine.get_stats()
            else:
                result = {"error": f"Unknown tool: {name}"}

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
    async def list_resources() -> List[Resource]:
        return [
            Resource(
                uri="chunkforge://documents",
                name="Indexed Documents",
                description="List of all indexed documents",
                mimeType="application/json",
            ),
        ]

    @server.list_resource_templates()
    async def list_resource_templates() -> List[ResourceTemplate]:
        return [
            ResourceTemplate(
                uriTemplate="chunkforge://document/{path}",
                name="Document Chunks",
                description="Chunks for a specific document with content",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        uri_str = str(uri)

        if uri_str == "chunkforge://documents":
            # Get unique documents from indexed chunks
            all_chunks = engine.storage.search_chunks()
            doc_paths = sorted({c["document_path"] for c in all_chunks})
            docs = []
            for path in doc_paths:
                doc = engine.storage.get_document(path)
                if doc:
                    docs.append(doc)
            return json.dumps(docs, indent=2, default=str)

        if uri_str.startswith("chunkforge://document/"):
            doc_path = uri_str[len("chunkforge://document/") :]
            chunks = engine.storage.search_chunks(document_path=doc_path)
            # Convert non-serializable fields
            for chunk in chunks:
                if "semantic_signature" in chunk:
                    del chunk["semantic_signature"]
            return json.dumps(chunks, indent=2, default=str)

        return json.dumps({"error": f"Unknown resource: {uri_str}"})

    return server


async def _run_server(storage_dir: Optional[str] = None) -> None:
    """Run the MCP server over stdio."""
    from chunkforge import __version__

    server = create_server(storage_dir)
    init_options = InitializationOptions(
        server_name="chunkforge",
        server_version=__version__,
        capabilities=ServerCapabilities(tools=None, resources=None),
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main(storage_dir: Optional[str] = None) -> None:
    """Entry point for MCP stdio server."""
    if not HAS_MCP:
        print(
            "Error: MCP SDK not installed.\n"
            "Install with: pip install 'mcp>=1.0.0'\n"
            "Or: pip install chunkforge[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    asyncio.run(_run_server(storage_dir))


if __name__ == "__main__":
    main()
