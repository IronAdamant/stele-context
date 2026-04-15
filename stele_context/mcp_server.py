"""
MCP (Model Context Protocol) server for Stele.

Provides a minimal HTTP/JSON server running on localhost that exposes
Stele tools for compatible coding agents. Uses only Python standard
library (http.server + json) with zero external dependencies.

The server implements the MCP tool discovery protocol, allowing agents
to discover and call Stele tools naturally.

Supports multi-modal content: text, code, images, PDFs, audio, video.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import urlparse

from stele_context.engine import Stele
from stele_context.tool_registry import (
    WRITE_TOOLS,
    build_tool_map,
    get_http_schemas,
    get_modality_flags,
    self_healing_hint,
)

logger = logging.getLogger(__name__)

_TOOL_SCHEMAS = get_http_schemas()


def _accepts_agent_id(func: Any) -> bool:
    """Check whether a callable accepts an 'agent_id' keyword argument."""
    try:
        sig = inspect.signature(func)
        return "agent_id" in sig.parameters
    except (ValueError, TypeError):
        return False


def execute_tool(
    tool_name: str,
    parameters: dict[str, Any],
    tool_map: dict[str, Any],
    server_agent_id: str = "",
) -> dict[str, Any]:
    """Execute a tool by name, returning a JSON-serialisable result dict."""
    if (
        tool_name in WRITE_TOOLS
        and "agent_id" not in parameters
        and server_agent_id
        and tool_name in tool_map
        and _accepts_agent_id(tool_map[tool_name])
    ):
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
        payload: dict[str, Any] = {"error": f"Tool execution failed: {e}"}
        hint = self_healing_hint(tool_name, e)
        if hint:
            payload["hint"] = hint
        return payload


DEFAULT_MCP_PORT = 9876
HEARTBEAT_INTERVAL = 30


class MCPRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for MCP server."""

    def __init__(
        self,
        *args: Any,
        stele: Stele,
        server_agent_id: str = "",
        **kwargs: Any,
    ):
        """Initialize with Stele instance and server agent ID."""
        self.stele = stele
        self._server_agent_id = server_agent_id
        self._tool_map = self._build_tool_map()
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        """Override to use our logger."""
        logger.info(format % args)

    def do_GET(self) -> None:
        """Handle GET requests (tool discovery)."""
        parsed = urlparse(self.path)

        if parsed.path == "/tools":
            self._handle_tools_discovery()
        elif parsed.path == "/health":
            self._handle_health()
        else:
            self._send_error(404, "Not found")

    def do_POST(self) -> None:
        """Handle POST requests (tool execution)."""
        parsed = urlparse(self.path)

        if parsed.path == "/call":
            self._handle_tool_call()
        else:
            self._send_error(404, "Not found")

    def _build_tool_map(self) -> dict[str, Any]:
        """Build tool name -> callable mapping via tool_registry."""
        mode = os.environ.get("STELE_MCP_MODE", "standard")
        return build_tool_map(self.stele, get_modality_flags(), mode=mode)

    def _handle_tools_discovery(self) -> None:
        """Return list of available tools.

        Generated dynamically from the tool map keys + _TOOL_SCHEMAS.
        Tools added to the map but missing from schemas get a minimal entry,
        so they're always discoverable.
        """
        tools: list[dict[str, Any]] = []
        for name in self._tool_map:
            schema = _TOOL_SCHEMAS.get(
                name,
                {
                    "description": name,
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            )
            tools.append({"name": name, **schema})
        self._send_json_response({"tools": tools})

    def _handle_health(self) -> None:
        """Return health status."""
        stats = self.stele.get_stats()
        self._send_json_response(
            {
                "status": "healthy",
                "version": stats["version"],
                "storage": stats["storage"],
            }
        )

    def _handle_tool_call(self) -> None:
        """Handle tool execution request."""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            request = json.loads(body.decode("utf-8"))

            tool_name = request.get("tool")
            parameters = request.get("parameters", {})

            if not tool_name:
                self._send_error(400, "Missing 'tool' field")
                return

            result = execute_tool(
                tool_name, parameters, self._tool_map, self._server_agent_id
            )
            self._send_json_response(result)

        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
        except Exception as e:
            logger.exception("Error executing tool")
            self._send_error(500, str(e))

    def _send_json_response(self, data: dict[str, Any], status: int = 200) -> None:
        """Send JSON response."""
        response = json.dumps(data, indent=2).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        self.wfile.write(response)

    def _send_error(self, status: int, message: str) -> None:
        """Send error response."""
        self._send_json_response({"error": message}, status)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request in a new thread."""

    daemon_threads = True


class MCPServer:
    """MCP server for Stele.

    Provides a threaded HTTP server that exposes Stele tools
    for compatible coding agents. Each request is handled in its
    own thread; the engine's RWLock ensures thread safety.
    """

    def __init__(
        self,
        stele: Stele,
        host: str = "localhost",
        port: int = DEFAULT_MCP_PORT,
        agent_id: str | None = None,
    ):
        self.stele = stele
        self.host = host
        self.port = port
        self.agent_id = agent_id or f"stele-context-http-{os.getpid()}"
        self.server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None

    def start(self, blocking: bool = False) -> None:
        """Start the MCP server."""

        def handler_factory(*args: Any, **kwargs: Any) -> MCPRequestHandler:
            return MCPRequestHandler(
                *args,
                stele=self.stele,
                server_agent_id=self.agent_id,
                **kwargs,
            )

        self.server = ThreadedHTTPServer((self.host, self.port), handler_factory)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
        )
        logger.info(f"Stele MCP server starting on http://{self.host}:{self.port}")
        logger.info("Available endpoints:")
        logger.info("  GET  /tools   - Discover available tools")
        logger.info("  GET  /health  - Health check")
        logger.info("  POST /call    - Execute a tool")

        # Register agent and start heartbeat
        self.stele.register_agent(self.agent_id)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="stele-heartbeat",
        )
        self._heartbeat_thread.start()

        if blocking:
            self.server.serve_forever()
        else:
            self._thread = threading.Thread(
                target=self.server.serve_forever,
                daemon=True,
                name="stele-context-mcp-server",
            )
            self._thread.start()
            logger.info("Server running in background thread")

    def _heartbeat_loop(self) -> None:
        """Background heartbeat for agent registration and lock cleanup."""
        while self.server is not None:
            self.stele.heartbeat(self.agent_id)
            # Reap expired locks every heartbeat to clean up stale ownership.
            # This prevents the 152-expired-locks buildup seen when agents
            # disconnect without releasing their document locks.
            self.stele.reap_expired_locks()
            time.sleep(HEARTBEAT_INTERVAL)

    def stop(self) -> None:
        """Stop the MCP server."""
        if self.server:
            logger.info("Stopping Stele MCP server")
            self.server.shutdown()
            self.server = None

        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)
            self._heartbeat_thread = None

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        self.stele.deregister_agent(self.agent_id)
        self.stele.storage.close()

    def get_url(self) -> str:
        """Get the server URL."""
        return f"http://{self.host}:{self.port}"
