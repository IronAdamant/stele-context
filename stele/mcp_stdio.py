"""
MCP server for Stele using JSON-RPC over stdio.

Implements the Model Context Protocol (MCP) standard, allowing
MCP clients (like Claude Desktop) to connect via stdio transport.

Requires the `mcp` package: pip install stele[mcp]

Usage:
    stele serve-mcp
    # Or directly:
    python -m stele.mcp_stdio

Claude Desktop config:
    "stele": {"command": "stele", "args": ["serve-mcp"]}
"""

import asyncio
import json
import logging
import os
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
    """Create a Stele engine instance."""
    from stele.engine import Stele

    return Stele(storage_dir=storage_dir)


def create_server(storage_dir: Optional[str] = None) -> Any:
    """
    Create and configure an MCP server with Stele tools.

    Returns the configured MCP Server instance.
    """
    if not HAS_MCP:
        raise ImportError(
            "MCP SDK not installed. Install with: pip install stele[mcp]"
        )

    engine = _create_engine(storage_dir)
    server = Server("stele")
    server_agent_id = f"stele-mcp-{os.getpid()}"

    # Store engine and agent_id for lifecycle management
    server._stele_engine = engine  # type: ignore[attr-defined]
    server._stele_agent_id = server_agent_id  # type: ignore[attr-defined]

    # Write tools that should receive a default agent_id
    _WRITE_TOOLS = frozenset({"index", "detect_changes", "remove"})

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
                        "agent_id": {
                            "type": "string",
                            "description": "Optional agent identifier for multi-agent tracking",
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
                description="Get Stele statistics",
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
            Tool(
                name="stale_chunks",
                description="Get chunks whose dependencies changed — detects context rot through the symbol graph",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "threshold": {
                            "type": "number",
                            "description": "Minimum staleness score (0.0-1.0, default 0.3). 0.8 = direct dep changed, 0.64 = transitive",
                            "default": 0.3,
                        },
                    },
                },
            ),
            Tool(
                name="list_sessions",
                description="List sessions, optionally filtered by agent ID",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "Filter sessions by agent identifier",
                        },
                    },
                },
            ),
            Tool(
                name="acquire_document_lock",
                description="Acquire exclusive write lock on a document for multi-agent ownership",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_path": {
                            "type": "string",
                            "description": "Document path to lock",
                        },
                        "agent_id": {
                            "type": "string",
                            "description": "Agent claiming ownership",
                        },
                        "ttl": {
                            "type": "number",
                            "description": "Lock TTL in seconds (default: 300)",
                            "default": 300,
                        },
                        "force": {
                            "type": "boolean",
                            "description": "Force-steal lock from another agent",
                            "default": False,
                        },
                    },
                    "required": ["document_path", "agent_id"],
                },
            ),
            Tool(
                name="refresh_document_lock",
                description="Refresh lock TTL without releasing — prevents expiry during long operations",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_path": {
                            "type": "string",
                            "description": "Document path whose lock to refresh",
                        },
                        "agent_id": {
                            "type": "string",
                            "description": "Agent that holds the lock",
                        },
                        "ttl": {
                            "type": "number",
                            "description": "New TTL in seconds (default: keep current)",
                        },
                    },
                    "required": ["document_path", "agent_id"],
                },
            ),
            Tool(
                name="release_document_lock",
                description="Release write lock on a document",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_path": {
                            "type": "string",
                            "description": "Document path to unlock",
                        },
                        "agent_id": {
                            "type": "string",
                            "description": "Agent releasing ownership",
                        },
                    },
                    "required": ["document_path", "agent_id"],
                },
            ),
            Tool(
                name="get_document_lock_status",
                description="Check if a document is locked and by which agent",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_path": {
                            "type": "string",
                            "description": "Document path to check",
                        },
                    },
                    "required": ["document_path"],
                },
            ),
            Tool(
                name="release_agent_locks",
                description="Release all document locks held by an agent (cleanup on disconnect)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "agent_id": {
                            "type": "string",
                            "description": "Agent whose locks to release",
                        },
                    },
                    "required": ["agent_id"],
                },
            ),
            Tool(
                name="get_conflicts",
                description="Get conflict history for documents or agents",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "document_path": {
                            "type": "string",
                            "description": "Filter by document path",
                        },
                        "agent_id": {
                            "type": "string",
                            "description": "Filter by agent ID",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max entries to return",
                            "default": 20,
                        },
                    },
                },
            ),
            Tool(
                name="reap_expired_locks",
                description="Clear all expired document locks and return what was reaped",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="list_agents",
                description="List agents registered across all worktrees with heartbeat status",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "active_only": {
                            "type": "boolean",
                            "description": "Only show active agents (default: true)",
                            "default": True,
                        },
                    },
                },
            ),
            Tool(
                name="environment_check",
                description="Check for stale __pycache__, editable install mismatches, and other issues",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="clean_bytecache",
                description="Remove orphaned .pyc files from stale __pycache__ directories",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
            Tool(
                name="store_semantic_summary",
                description="Store agent's semantic summary for a chunk — improves search using agent's understanding",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chunk_id": {
                            "type": "string",
                            "description": "Chunk ID to annotate",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Semantic description (e.g. 'JWT middleware that validates tokens')",
                        },
                    },
                    "required": ["chunk_id", "summary"],
                },
            ),
            Tool(
                name="store_embedding",
                description="Store a raw embedding vector for a chunk — for agents with embedding API access",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chunk_id": {
                            "type": "string",
                            "description": "Chunk ID to update",
                        },
                        "vector": {
                            "type": "array",
                            "items": {"type": "number"},
                            "description": "Embedding vector (normalized to unit length)",
                        },
                    },
                    "required": ["chunk_id", "vector"],
                },
            ),
            Tool(
                name="get_chunk_history",
                description="Get chunk version history — shows how chunks changed over time",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "chunk_id": {
                            "type": "string",
                            "description": "Filter by specific chunk ID",
                        },
                        "document_path": {
                            "type": "string",
                            "description": "Filter by document path",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max entries to return (default: 50)",
                            "default": 50,
                        },
                    },
                },
            ),
            Tool(
                name="get_notifications",
                description="Get change notifications from other agents (what files changed since last check)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "since": {
                            "type": "number",
                            "description": "Unix timestamp; only show notifications after this",
                        },
                        "exclude_self": {
                            "type": "string",
                            "description": "Agent ID to exclude from results",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max notifications (default: 100)",
                            "default": 100,
                        },
                    },
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        try:
            # Inject server agent_id for write operations when not provided
            if name in _WRITE_TOOLS and "agent_id" not in arguments:
                arguments = {**arguments, "agent_id": server_agent_id}

            if name == "index":
                result = engine.index_documents(
                    paths=arguments["paths"],
                    force_reindex=arguments.get("force_reindex", False),
                    agent_id=arguments.get("agent_id"),
                    expected_versions=arguments.get("expected_versions"),
                )
            elif name == "remove":
                result = engine.remove_document(
                    document_path=arguments["document_path"],
                    agent_id=arguments.get("agent_id"),
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
                    agent_id=arguments.get("agent_id"),
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
            elif name == "stale_chunks":
                result = engine.stale_chunks(
                    threshold=arguments.get("threshold", 0.3),
                )
            elif name == "list_sessions":
                result = engine.list_sessions(
                    agent_id=arguments.get("agent_id"),
                )
            elif name == "acquire_document_lock":
                result = engine.acquire_document_lock(
                    document_path=arguments["document_path"],
                    agent_id=arguments["agent_id"],
                    ttl=arguments.get("ttl", 300.0),
                    force=arguments.get("force", False),
                )
            elif name == "refresh_document_lock":
                result = engine.refresh_document_lock(
                    document_path=arguments["document_path"],
                    agent_id=arguments["agent_id"],
                    ttl=arguments.get("ttl"),
                )
            elif name == "release_document_lock":
                result = engine.release_document_lock(
                    document_path=arguments["document_path"],
                    agent_id=arguments["agent_id"],
                )
            elif name == "get_document_lock_status":
                result = engine.get_document_lock_status(
                    document_path=arguments["document_path"],
                )
            elif name == "release_agent_locks":
                result = engine.release_agent_locks(
                    agent_id=arguments["agent_id"],
                )
            elif name == "get_conflicts":
                result = engine.get_conflicts(
                    document_path=arguments.get("document_path"),
                    agent_id=arguments.get("agent_id"),
                    limit=arguments.get("limit", 20),
                )
            elif name == "reap_expired_locks":
                result = engine.reap_expired_locks()
            elif name == "list_agents":
                result = engine.list_agents(
                    active_only=arguments.get("active_only", True),
                )
            elif name == "environment_check":
                result = engine.check_environment()
            elif name == "clean_bytecache":
                result = engine.clean_bytecache()
            elif name == "store_semantic_summary":
                result = engine.store_semantic_summary(
                    chunk_id=arguments["chunk_id"],
                    summary=arguments["summary"],
                )
            elif name == "store_embedding":
                result = engine.store_embedding(
                    chunk_id=arguments["chunk_id"],
                    vector=arguments["vector"],
                )
            elif name == "get_chunk_history":
                result = engine.get_chunk_history(
                    chunk_id=arguments.get("chunk_id"),
                    document_path=arguments.get("document_path"),
                    limit=arguments.get("limit", 50),
                )
            elif name == "get_notifications":
                result = engine.get_notifications(
                    since=arguments.get("since"),
                    exclude_self=arguments.get("exclude_self"),
                    limit=arguments.get("limit", 100),
                )
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
                uri="stele://documents",
                name="Indexed Documents",
                description="List of all indexed documents",
                mimeType="application/json",
            ),
        ]

    @server.list_resource_templates()
    async def list_resource_templates() -> List[ResourceTemplate]:
        return [
            ResourceTemplate(
                uriTemplate="stele://document/{path}",
                name="Document Chunks",
                description="Chunks for a specific document with content",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        uri_str = str(uri)

        if uri_str == "stele://documents":
            # Get unique documents from indexed chunks
            all_chunks = engine.storage.search_chunks()
            doc_paths = sorted({c["document_path"] for c in all_chunks})
            docs = []
            for path in doc_paths:
                doc = engine.storage.get_document(path)
                if doc:
                    docs.append(doc)
            return json.dumps(docs, indent=2, default=str)

        if uri_str.startswith("stele://document/"):
            doc_path = uri_str[len("stele://document/") :]
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
    from stele import __version__

    server = create_server(storage_dir)
    engine = server._stele_engine  # type: ignore[attr-defined]
    agent_id = server._stele_agent_id  # type: ignore[attr-defined]

    # Register agent for cross-worktree visibility
    engine.register_agent(agent_id)

    async def _heartbeat_loop() -> None:
        while True:
            try:
                engine.heartbeat(agent_id)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                break

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    init_options = InitializationOptions(
        server_name="stele",
        server_version=__version__,
        capabilities=ServerCapabilities(
            tools=ToolsCapability(),
            resources=ResourcesCapability(),
        ),
    )

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, init_options)
    finally:
        heartbeat_task.cancel()
        engine.deregister_agent(agent_id)


def main(storage_dir: Optional[str] = None) -> None:
    """Entry point for MCP stdio server."""
    if not HAS_MCP:
        print(
            "Error: MCP SDK not installed.\n"
            "Install with: pip install 'mcp>=1.0.0'\n"
            "Or: pip install stele[mcp]",
            file=sys.stderr,
        )
        sys.exit(1)

    asyncio.run(_run_server(storage_dir))


if __name__ == "__main__":
    main()
