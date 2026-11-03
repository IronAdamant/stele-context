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
import inspect
import json
import logging
import os
import sys
from typing import Any

from stele_context import __version__ as _version
from stele_context.mcp_tools_primary import TOOL_DEFINITIONS
from stele_context.tool_registry import (
    WRITE_TOOLS,
    build_tool_map,
    get_modality_flags,
    hint_for_tool_result,
    self_healing_hint,
)

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 30  # seconds between heartbeats and lock reaping


def _accepts_agent_id(func: Any) -> bool:
    """Check whether a callable accepts an 'agent_id' keyword argument."""
    try:
        sig = inspect.signature(func)
        return "agent_id" in sig.parameters
    except (ValueError, TypeError):
        return False


# Guard MCP SDK import. Both the 1.x decorator API and the 2.x constructor
# handler API export these names; handler registration is chosen at runtime.
try:
    from mcp.server import InitializationOptions, Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Resource,
        ResourceTemplate,
        ResourcesCapability,
        ServerCapabilities,
        TextContent,
        Tool,
        ToolsCapability,
    )

    HAS_MCP = True
except ImportError:
    HAS_MCP = False


def _mcp(cls: Any, /, **kwargs: Any) -> Any:
    """Construct an MCP model using the 1.x field names.

    mcp 2.x accepts those names as aliases. Calling through ``Any`` keeps the
    source type-checkable against either major: the installed stubs describe
    only one of them.
    """
    return cls(**kwargs)


def uses_constructor_handlers(server_cls: Any) -> bool:
    """True when ``server_cls`` is the mcp 2.x low-level ``Server``.

    mcp 2 replaced ``@server.list_tools()`` decorators with ``on_list_tools``
    and the other ``on_*`` constructor callbacks. mcp 1.x still uses decorators.
    """
    try:
        params = inspect.signature(server_cls).parameters
    except (TypeError, ValueError):
        return False
    return "on_list_tools" in params


def build_initialization_options(server: Any) -> Any:
    """Build the options object passed to ``Server.run``.

    Both mcp 1.x and 2.x implement ``create_initialization_options``. The
    explicit fallback covers an older 1.x build that only accepts the
    ``InitializationOptions`` constructor.
    """
    if not HAS_MCP:
        raise ImportError(
            "MCP SDK not installed. Install with: pip install stele-context[mcp]"
        )
    create = getattr(server, "create_initialization_options", None)
    if callable(create):
        return create()
    return InitializationOptions(
        server_name="stele-context",
        server_version=_version,
        capabilities=_mcp(
            ServerCapabilities,
            tools=_mcp(ToolsCapability, listChanged=False),
            resources=_mcp(ResourcesCapability, subscribe=False, listChanged=False),
        ),
    )


def _as_arguments(raw: Any) -> dict[str, Any]:
    """Normalize a tools/call arguments payload to a plain dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        dumped = dump()
        if isinstance(dumped, dict):
            return dumped
    return dict(raw)


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

    engine = _create_engine(storage_dir)
    server_agent_id = f"stele-context-mcp-{os.getpid()}"

    # Build tool dispatch map once (not per request)
    # Lite is the recommended default for cold agents (high-leverage surface).
    # Set STELE_MCP_MODE=standard or full to expand.
    mode = os.environ.get("STELE_MCP_MODE", "lite")
    tool_map = build_tool_map(engine, get_modality_flags(), mode=mode)

    tool_names = set(tool_map.keys())

    def _tool_models() -> list[Any]:
        return [
            _mcp(
                Tool,
                name=td["name"],
                description=td["description"],
                inputSchema=td["inputSchema"],
            )
            for td in TOOL_DEFINITIONS
            if td["name"] in tool_names
        ]

    def _invoke_tool(name: str, arguments: Any) -> tuple[str, bool]:
        """Run one tool. Returns ``(json_text, is_error)``."""
        try:
            args = _as_arguments(arguments)
            # Inject server agent_id for write operations when not provided
            if (
                name in WRITE_TOOLS
                and "agent_id" not in args
                and server_agent_id
                and name in tool_map
                and _accepts_agent_id(tool_map[name])
            ):
                args = {**args, "agent_id": server_agent_id}

            if name not in tool_map:
                result: Any = {"error": f"Unknown tool: {name}"}
                errored = True
            else:
                result = tool_map[name](**args)
                errored = False

            # Attach self-healing hints for structured empty/error results
            # (impact_radius returns {error: ...} without raising).
            if isinstance(result, dict):
                hint = hint_for_tool_result(name, result)
                if hint and "hint" not in result:
                    result = {**result, "hint": hint}
                if "error" in result and set(result) <= {"error", "hint"}:
                    errored = True

            return json.dumps(result, indent=2, default=str), errored
        except Exception as exc:
            payload: dict[str, Any] = {"error": str(exc)}
            hint = self_healing_hint(name, exc)
            if hint:
                payload["hint"] = hint
            return json.dumps(payload), True

    def _text_blocks(text: str) -> list[Any]:
        return [TextContent(type="text", text=text)]

    def _read_resource_text(uri: Any) -> str:
        uri_str = str(uri)
        if uri_str == "stele-context://documents":
            docs = engine.get_map()
            return json.dumps(docs, indent=2, default=str)
        if uri_str.startswith("stele-context://document/"):
            doc_path = uri_str[len("stele-context://document/") :]
            chunks = engine.storage.get_document_chunks(doc_path)
            enriched = []
            for meta in chunks:
                chunk = engine.storage.get_chunk(meta["chunk_id"])
                if chunk:
                    enriched.append(chunk)
            return json.dumps(enriched, indent=2, default=str)
        return json.dumps({"error": f"Unknown resource: {uri_str}"})

    def _resource_models() -> list[Any]:
        return [
            _mcp(
                Resource,
                uri="stele-context://documents",
                name="Indexed Documents",
                description="List of all indexed documents",
                mimeType="application/json",
            ),
        ]

    def _template_models() -> list[Any]:
        return [
            _mcp(
                ResourceTemplate,
                uriTemplate="stele-context://document/{path}",
                name="Document Chunks",
                description="Chunks for a specific document with content",
                mimeType="application/json",
            ),
        ]

    if uses_constructor_handlers(Server):
        server = _server_with_handlers(
            _tool_models,
            _invoke_tool,
            _text_blocks,
            _read_resource_text,
            _resource_models,
            _template_models,
        )
    else:
        # mcp 1.x registers handlers with decorators. Bound to Any so
        # type-checking against an installed mcp 2 stub still succeeds.
        legacy: Any = Server("stele-context", version=_version)
        server = legacy

        @legacy.list_tools()
        async def list_tools() -> list[Any]:
            return _tool_models()

        @legacy.call_tool()
        async def call_tool(
            name: str, arguments: dict[str, Any] | None = None
        ) -> list[Any]:
            text, _errored = _invoke_tool(name, arguments)
            return _text_blocks(text)

        @legacy.list_resources()
        async def list_resources() -> list[Any]:
            return _resource_models()

        @legacy.list_resource_templates()
        async def list_resource_templates() -> list[Any]:
            return _template_models()

        @legacy.read_resource()
        async def read_resource(uri: str) -> str:
            return _read_resource_text(uri)

    return _ServerBundle(server, engine, server_agent_id)


def _server_with_handlers(
    tool_models: Any,
    invoke_tool: Any,
    text_blocks: Any,
    read_resource_text: Any,
    resource_models: Any,
    template_models: Any,
) -> Any:
    """Register mcp 2.x ``on_*`` handlers.

    Imports of the 2.x result types stay inside this function so importing
    the module still succeeds on mcp 1.x, where those names are absent.
    """
    from mcp.types import (
        CallToolResult,
        ListResourceTemplatesResult,
        ListResourcesResult,
        ListToolsResult,
        ReadResourceResult,
        TextResourceContents,
    )

    async def on_list_tools(_ctx: Any, _params: Any) -> Any:
        return ListToolsResult(tools=tool_models())

    async def on_call_tool(_ctx: Any, params: Any) -> Any:
        text, errored = invoke_tool(params.name, getattr(params, "arguments", None))
        return CallToolResult(content=text_blocks(text), is_error=errored)

    async def on_list_resources(_ctx: Any, _params: Any) -> Any:
        return ListResourcesResult(resources=resource_models())

    async def on_list_resource_templates(_ctx: Any, _params: Any) -> Any:
        return ListResourceTemplatesResult(resource_templates=template_models())

    async def on_read_resource(_ctx: Any, params: Any) -> Any:
        uri = params.uri
        return ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri=uri,
                    text=read_resource_text(uri),
                    mime_type="application/json",
                )
            ]
        )

    return Server(
        "stele-context",
        version=_version,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_list_resource_templates=on_list_resource_templates,
        on_read_resource=on_read_resource,
    )


async def _run_server(bundle: _ServerBundle) -> None:
    """Run the MCP server on stdio transport."""
    bundle.engine.register_agent(bundle.agent_id)

    async def heartbeat_loop() -> None:
        """Background heartbeat and lock cleanup for stdio transport."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            bundle.engine.heartbeat(bundle.agent_id)
            bundle.engine.reap_expired_locks()

    heartbeat_task = asyncio.ensure_future(heartbeat_loop())

    try:
        init_options = build_initialization_options(bundle.server)
        async with stdio_server() as (read_stream, write_stream):
            await bundle.server.run(read_stream, write_stream, init_options)
    finally:
        heartbeat_task.cancel()
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

if __name__ == "__main__":
    run()
