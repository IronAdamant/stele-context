"""
MCP (Model Context Protocol) server for Stele.

Provides a minimal HTTP/JSON server running on localhost that exposes
Stele tools for compatible coding agents. Uses only Python standard
library (http.server + json) with zero external dependencies.

The server implements the MCP tool discovery protocol, allowing agents
to discover and call Stele tools naturally.

Supports multi-modal content: text, code, images, PDFs, audio, video.
"""

import json
import logging
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from stele.engine import Stele
from stele.chunkers import (
    HAS_IMAGE_CHUNKER,
    HAS_PDF_CHUNKER,
    HAS_AUDIO_CHUNKER,
    HAS_VIDEO_CHUNKER,
)
from stele.mcp_handlers import (
    _TOOL_SCHEMAS,
    build_tool_map,
    execute_tool,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


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

    def _build_tool_map(self) -> Dict[str, Any]:
        """Build tool name -> callable mapping via mcp_handlers."""
        modality_flags = {
            "image": HAS_IMAGE_CHUNKER,
            "pdf": HAS_PDF_CHUNKER,
            "audio": HAS_AUDIO_CHUNKER,
            "video": HAS_VIDEO_CHUNKER,
        }
        return build_tool_map(self.stele, modality_flags)

    def _handle_tools_discovery(self) -> None:
        """Return list of available tools.

        Generated dynamically from the tool map keys + _TOOL_SCHEMAS.
        Tools added to the map but missing from schemas get a minimal entry,
        so they're always discoverable.
        """
        tool_map = self._build_tool_map()
        tools: List[Dict[str, Any]] = []
        for name in tool_map:
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

            tool_map = self._build_tool_map()
            result = execute_tool(
                tool_name, parameters, tool_map, self._server_agent_id
            )
            self._send_json_response(result)

        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
        except Exception as e:
            logger.exception("Error executing tool")
            self._send_error(500, str(e))

    def _send_json_response(self, data: Dict[str, Any], status: int = 200) -> None:
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
        port: int = 9876,
        agent_id: Optional[str] = None,
    ):
        self.stele = stele
        self.host = host
        self.port = port
        self.agent_id = agent_id or f"stele-http-{os.getpid()}"
        self.server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._heartbeat_thread: Optional[threading.Thread] = None

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
                name="stele-mcp-server",
            )
            self._thread.start()
            logger.info("Server running in background thread")

    def _heartbeat_loop(self) -> None:
        """Background heartbeat for agent registration."""
        while self.server is not None:
            self.stele.heartbeat(self.agent_id)
            time.sleep(30)

    def stop(self) -> None:
        """Stop the MCP server."""
        if self.server:
            logger.info("Stopping Stele MCP server")
            self.server.shutdown()
            self.server = None

        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        self.stele.deregister_agent(self.agent_id)

    def get_url(self) -> str:
        """Get the server URL."""
        return f"http://{self.host}:{self.port}"
