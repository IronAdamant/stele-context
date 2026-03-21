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

from stele.mcp_tool_defs import TOOL_DEFINITIONS

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


def _dispatch_tool(engine, name: str, arguments: Dict[str, Any]) -> Any:
    """Dispatch a tool call to the appropriate engine method.

    Returns the result dict/list from the engine, or an error dict
    for unknown tool names.
    """
    if name == "index":
        return engine.index_documents(
            paths=arguments["paths"],
            force_reindex=arguments.get("force_reindex", False),
            agent_id=arguments.get("agent_id"),
            expected_versions=arguments.get("expected_versions"),
        )
    elif name == "remove":
        return engine.remove_document(
            document_path=arguments["document_path"],
            agent_id=arguments.get("agent_id"),
        )
    elif name == "search":
        return engine.search(
            query=arguments["query"],
            top_k=arguments.get("top_k", 10),
        )
    elif name == "get_context":
        return engine.get_context(
            document_paths=arguments["document_paths"],
        )
    elif name == "detect_changes":
        return engine.detect_changes_and_update(
            session_id=arguments["session_id"],
            document_paths=arguments.get("document_paths"),
            reason=arguments.get("reason"),
            agent_id=arguments.get("agent_id"),
        )
    elif name == "annotate":
        return engine.annotate(
            target=arguments["target"],
            target_type=arguments["target_type"],
            content=arguments["content"],
            tags=arguments.get("tags"),
        )
    elif name == "get_annotations":
        return engine.get_annotations(
            target=arguments.get("target"),
            target_type=arguments.get("target_type"),
            tags=arguments.get("tags"),
        )
    elif name == "delete_annotation":
        return engine.delete_annotation(
            annotation_id=arguments["annotation_id"],
        )
    elif name == "update_annotation":
        return engine.update_annotation(
            annotation_id=arguments["annotation_id"],
            content=arguments.get("content"),
            tags=arguments.get("tags"),
        )
    elif name == "search_annotations":
        return engine.search_annotations(
            query=arguments["query"],
            target_type=arguments.get("target_type"),
        )
    elif name == "bulk_annotate":
        return engine.bulk_annotate(
            annotations=arguments["annotations"],
        )
    elif name == "prune_history":
        return engine.prune_history(
            max_age_seconds=arguments.get("max_age_seconds"),
            max_entries=arguments.get("max_entries"),
        )
    elif name == "map":
        return engine.get_map()
    elif name == "history":
        return engine.get_history(
            limit=arguments.get("limit", 20),
            document_path=arguments.get("document_path"),
        )
    elif name == "stats":
        return engine.get_stats()
    elif name == "find_references":
        return engine.find_references(
            symbol=arguments["symbol"],
        )
    elif name == "find_definition":
        return engine.find_definition(
            symbol=arguments["symbol"],
        )
    elif name == "impact_radius":
        return engine.impact_radius(
            chunk_id=arguments["chunk_id"],
            depth=arguments.get("depth", 2),
        )
    elif name == "rebuild_symbols":
        return engine.rebuild_symbol_graph()
    elif name == "stale_chunks":
        return engine.stale_chunks(
            threshold=arguments.get("threshold", 0.3),
        )
    elif name == "list_sessions":
        return engine.list_sessions(
            agent_id=arguments.get("agent_id"),
        )
    elif name == "acquire_document_lock":
        return engine.acquire_document_lock(
            document_path=arguments["document_path"],
            agent_id=arguments["agent_id"],
            ttl=arguments.get("ttl", 300.0),
            force=arguments.get("force", False),
        )
    elif name == "refresh_document_lock":
        return engine.refresh_document_lock(
            document_path=arguments["document_path"],
            agent_id=arguments["agent_id"],
            ttl=arguments.get("ttl"),
        )
    elif name == "release_document_lock":
        return engine.release_document_lock(
            document_path=arguments["document_path"],
            agent_id=arguments["agent_id"],
        )
    elif name == "get_document_lock_status":
        return engine.get_document_lock_status(
            document_path=arguments["document_path"],
        )
    elif name == "release_agent_locks":
        return engine.release_agent_locks(
            agent_id=arguments["agent_id"],
        )
    elif name == "get_conflicts":
        return engine.get_conflicts(
            document_path=arguments.get("document_path"),
            agent_id=arguments.get("agent_id"),
            limit=arguments.get("limit", 20),
        )
    elif name == "reap_expired_locks":
        return engine.reap_expired_locks()
    elif name == "list_agents":
        return engine.list_agents(
            active_only=arguments.get("active_only", True),
        )
    elif name == "environment_check":
        return engine.check_environment()
    elif name == "clean_bytecache":
        return engine.clean_bytecache()
    elif name == "store_semantic_summary":
        return engine.store_semantic_summary(
            chunk_id=arguments["chunk_id"],
            summary=arguments["summary"],
        )
    elif name == "store_embedding":
        return engine.store_embedding(
            chunk_id=arguments["chunk_id"],
            vector=arguments["vector"],
        )
    elif name == "get_chunk_history":
        return engine.get_chunk_history(
            chunk_id=arguments.get("chunk_id"),
            document_path=arguments.get("document_path"),
            limit=arguments.get("limit", 50),
        )
    elif name == "get_notifications":
        return engine.get_notifications(
            since=arguments.get("since"),
            exclude_self=arguments.get("exclude_self"),
            limit=arguments.get("limit", 100),
        )
    else:
        return {"error": f"Unknown tool: {name}"}


def create_server(storage_dir: Optional[str] = None) -> Any:
    """
    Create and configure an MCP server with Stele tools.

    Returns the configured MCP Server instance.
    """
    if not HAS_MCP:
        raise ImportError("MCP SDK not installed. Install with: pip install stele[mcp]")

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
                name=td["name"],
                description=td["description"],
                inputSchema=td["inputSchema"],
            )
            for td in TOOL_DEFINITIONS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        try:
            # Inject server agent_id for write operations when not provided
            if name in _WRITE_TOOLS and "agent_id" not in arguments:
                arguments = {**arguments, "agent_id": server_agent_id}

            result = _dispatch_tool(engine, name, arguments)

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
                uri="stele://documents",  # type: ignore[arg-type]
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
