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
    from mcp.types import ServerCapabilities, ToolsCapability, ResourcesCapability

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
                name="remove",
                description="Remove a document and all its chunks, annotations, and index entries",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_path": {
                            "type": "string",
                            "description": "Path of the document to remove",
                        },
                    },
                    "required": ["document_path"],
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
                        "reason": {
                            "type": "string",
                            "description": "Reason for the change detection (stored in history)",
                        },
                    },
                    "required": ["session_id"],
                },
            ),
            Tool(
                name="annotate",
                description="Add an annotation to a document or chunk for LLM context",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "Document path or chunk ID to annotate",
                        },
                        "target_type": {
                            "type": "string",
                            "enum": ["document", "chunk"],
                            "description": "Whether target is a document or chunk",
                        },
                        "content": {
                            "type": "string",
                            "description": "Annotation text",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tags for categorization",
                        },
                    },
                    "required": ["target", "target_type", "content"],
                },
            ),
            Tool(
                name="get_annotations",
                description="Retrieve annotations, optionally filtered by target, type, or tags",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "Filter by document path or chunk ID",
                        },
                        "target_type": {
                            "type": "string",
                            "enum": ["document", "chunk"],
                            "description": "Filter by target type",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by tags (any match)",
                        },
                    },
                },
            ),
            Tool(
                name="delete_annotation",
                description="Delete an annotation by ID",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "annotation_id": {
                            "type": "integer",
                            "description": "Annotation ID to delete",
                        },
                    },
                    "required": ["annotation_id"],
                },
            ),
            Tool(
                name="update_annotation",
                description="Update an existing annotation's content and/or tags",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "annotation_id": {
                            "type": "integer",
                            "description": "Annotation ID to update",
                        },
                        "content": {
                            "type": "string",
                            "description": "New annotation text",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "New tags (replaces existing)",
                        },
                    },
                    "required": ["annotation_id"],
                },
            ),
            Tool(
                name="search_annotations",
                description="Search annotation content text (substring match)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Text to search for in annotation content",
                        },
                        "target_type": {
                            "type": "string",
                            "enum": ["document", "chunk"],
                            "description": "Filter by target type",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="bulk_annotate",
                description="Annotate multiple targets in one call",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "annotations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "target": {"type": "string"},
                                    "target_type": {
                                        "type": "string",
                                        "enum": ["document", "chunk"],
                                    },
                                    "content": {"type": "string"},
                                    "tags": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["target", "target_type", "content"],
                            },
                            "description": "List of annotations to create",
                        },
                    },
                    "required": ["annotations"],
                },
            ),
            Tool(
                name="prune_history",
                description="Prune old change history entries by age or count",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "max_age_seconds": {
                            "type": "number",
                            "description": "Delete entries older than this many seconds",
                        },
                        "max_entries": {
                            "type": "integer",
                            "description": "Keep only this many newest entries",
                        },
                    },
                },
            ),
            Tool(
                name="map",
                description="Get project overview: all documents with chunk counts, tokens, and annotations",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="history",
                description="Get change history for indexed documents",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Max entries to return",
                            "default": 20,
                        },
                        "document_path": {
                            "type": "string",
                            "description": "Filter by document path",
                        },
                    },
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
            Tool(
                name="find_references",
                description="Find all definitions and references of a symbol across the codebase (LSP-style)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol name to search for (function, class, CSS class like '.btn', CSS ID like '#app')",
                        },
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="find_definition",
                description="Find where a symbol is defined, with full chunk content",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol name to find definition for",
                        },
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="impact_radius",
                description="Find all chunks affected by changing a chunk (transitive dependents via symbol graph)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chunk_id": {
                            "type": "string",
                            "description": "Chunk ID to analyze impact for",
                        },
                        "depth": {
                            "type": "integer",
                            "description": "Max hops through dependency graph (default: 2)",
                            "default": 2,
                        },
                    },
                    "required": ["chunk_id"],
                },
            ),
            Tool(
                name="rebuild_symbols",
                description="Rebuild the entire symbol graph from stored chunks (use after upgrade or to repair)",
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
            elif name == "remove":
                result = engine.remove_document(
                    document_path=arguments["document_path"],
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
                    reason=arguments.get("reason"),
                )
            elif name == "annotate":
                result = engine.annotate(
                    target=arguments["target"],
                    target_type=arguments["target_type"],
                    content=arguments["content"],
                    tags=arguments.get("tags"),
                )
            elif name == "get_annotations":
                result = engine.get_annotations(
                    target=arguments.get("target"),
                    target_type=arguments.get("target_type"),
                    tags=arguments.get("tags"),
                )
            elif name == "delete_annotation":
                result = engine.delete_annotation(
                    annotation_id=arguments["annotation_id"],
                )
            elif name == "update_annotation":
                result = engine.update_annotation(
                    annotation_id=arguments["annotation_id"],
                    content=arguments.get("content"),
                    tags=arguments.get("tags"),
                )
            elif name == "search_annotations":
                result = engine.search_annotations(
                    query=arguments["query"],
                    target_type=arguments.get("target_type"),
                )
            elif name == "bulk_annotate":
                result = engine.bulk_annotate(
                    annotations=arguments["annotations"],
                )
            elif name == "prune_history":
                result = engine.prune_history(
                    max_age_seconds=arguments.get("max_age_seconds"),
                    max_entries=arguments.get("max_entries"),
                )
            elif name == "map":
                result = engine.get_map()
            elif name == "history":
                result = engine.get_history(
                    limit=arguments.get("limit", 20),
                    document_path=arguments.get("document_path"),
                )
            elif name == "stats":
                result = engine.get_stats()
            elif name == "find_references":
                result = engine.find_references(
                    symbol=arguments["symbol"],
                )
            elif name == "find_definition":
                result = engine.find_definition(
                    symbol=arguments["symbol"],
                )
            elif name == "impact_radius":
                result = engine.impact_radius(
                    chunk_id=arguments["chunk_id"],
                    depth=arguments.get("depth", 2),
                )
            elif name == "rebuild_symbols":
                result = engine.rebuild_symbol_graph()
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
        capabilities=ServerCapabilities(
            tools=ToolsCapability(),
            resources=ResourcesCapability(),
        ),
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
