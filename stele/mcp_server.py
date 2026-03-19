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
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from stele.engine import Stele
from stele.chunkers import (
    HAS_IMAGE_CHUNKER,
    HAS_PDF_CHUNKER,
    HAS_AUDIO_CHUNKER,
    HAS_VIDEO_CHUNKER,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool schemas — single source of truth for tool discovery.
#
# Every tool in _get_tool_map() MUST have a matching entry here.  If a tool
# is added to the map but not to the schemas, discovery will auto-generate a
# minimal entry so it never silently disappears from /tools.
# ---------------------------------------------------------------------------

_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "index_documents": {
        "description": "Index one or more documents for KV-cache management. Supports text, code, images, PDFs, audio, and video.",
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of document paths to index",
                },
                "force_reindex": {
                    "type": "boolean",
                    "description": "Force re-indexing even if document hasn't changed",
                    "default": False,
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier for ownership checking",
                },
                "expected_versions": {
                    "type": "object",
                    "description": "Map of path -> expected version for optimistic locking",
                },
            },
            "required": ["paths"],
        },
    },
    "detect_modality": {
        "description": "Detect the modality of a file (text, code, image, pdf, audio, video).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to file",
                },
            },
            "required": ["path"],
        },
    },
    "get_supported_formats": {
        "description": "Get list of supported file formats by modality.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "detect_changes_and_update": {
        "description": "Detect changes in documents and update KV-cache accordingly.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "document_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of paths to check (defaults to all indexed)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent identifier for multi-agent tracking",
                },
            },
            "required": ["session_id"],
        },
    },
    "get_relevant_kv": {
        "description": "Get KV-cache for chunks most relevant to a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "query": {
                    "type": "string",
                    "description": "Query text to find relevant chunks for",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of top chunks to return",
                    "default": 10,
                },
            },
            "required": ["session_id", "query"],
        },
    },
    "save_kv_state": {
        "description": "Save KV-cache state for a session.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "kv_data": {
                    "type": "object",
                    "description": "Dictionary mapping chunk_id to KV data",
                },
                "chunk_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of chunk IDs to save (defaults to all)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent identifier for multi-agent tracking",
                },
            },
            "required": ["session_id", "kv_data"],
        },
    },
    "rollback": {
        "description": "Rollback session to a previous turn.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "target_turn": {
                    "type": "integer",
                    "description": "Target turn number to rollback to",
                },
            },
            "required": ["session_id", "target_turn"],
        },
    },
    "prune_chunks": {
        "description": "Prune low-relevance chunks to stay under token limit.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum total tokens to keep",
                },
            },
            "required": ["session_id", "max_tokens"],
        },
    },
    "search": {
        "description": "Semantic search across indexed chunks. Returns content ranked by relevance.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query text",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    "get_context": {
        "description": "Get cached context for documents. Returns unchanged chunks, flags changed/new.",
        "parameters": {
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
    },
    "find_references": {
        "description": "Find all definitions and references for a symbol name across indexed documents.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to search for",
                },
            },
            "required": ["symbol"],
        },
    },
    "find_definition": {
        "description": "Find the definition location of a symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to find",
                },
            },
            "required": ["symbol"],
        },
    },
    "impact_radius": {
        "description": "Find all chunks potentially affected by a change to a given chunk.",
        "parameters": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID to analyze",
                },
                "depth": {
                    "type": "integer",
                    "description": "Maximum hops to traverse (default: 2)",
                    "default": 2,
                },
            },
            "required": ["chunk_id"],
        },
    },
    "rebuild_symbol_graph": {
        "description": "Rebuild the symbol graph for all indexed documents.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "stale_chunks": {
        "description": "Find chunks with staleness scores above a threshold, grouped by document.",
        "parameters": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "Minimum staleness score (default: 0.3)",
                    "default": 0.3,
                },
            },
            "required": [],
        },
    },
    "list_sessions": {
        "description": "List sessions, optionally filtered by agent ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter sessions by agent identifier",
                },
            },
            "required": [],
        },
    },
    "acquire_document_lock": {
        "description": "Acquire exclusive write lock on a document. Other agents can read but not write.",
        "parameters": {
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
    },
    "refresh_document_lock": {
        "description": "Refresh lock TTL without releasing. Prevents expiry during long operations.",
        "parameters": {
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
    },
    "release_document_lock": {
        "description": "Release write lock on a document.",
        "parameters": {
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
    },
    "get_document_lock_status": {
        "description": "Check if a document is locked and by which agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Document path to check",
                },
            },
            "required": ["document_path"],
        },
    },
    "release_agent_locks": {
        "description": "Release all document locks held by an agent (cleanup).",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent whose locks to release",
                },
            },
            "required": ["agent_id"],
        },
    },
    "get_conflicts": {
        "description": "Get conflict history for documents or agents.",
        "parameters": {
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
            "required": [],
        },
    },
    "reap_expired_locks": {
        "description": "Clear all expired document locks and return what was reaped.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "list_agents": {
        "description": "List agents registered across all worktrees with heartbeat status.",
        "parameters": {
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "Only show active agents (default: true)",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    "environment_check": {
        "description": "Check for environment issues: stale __pycache__, editable install mismatches.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "clean_bytecache": {
        "description": "Remove orphaned .pyc files from stale __pycache__ directories.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    "get_notifications": {
        "description": "Get change notifications from other agents (what files changed since last check).",
        "parameters": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "number",
                    "description": "Unix timestamp; only notifications after this time",
                },
                "exclude_self": {
                    "type": "string",
                    "description": "Agent ID to exclude (skip your own changes)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max notifications to return (default: 100)",
                    "default": 100,
                },
            },
            "required": [],
        },
    },
}


# Write tools that should receive a default agent_id when callers omit it.
_WRITE_TOOLS = frozenset({
    "index_documents",
    "detect_changes_and_update",
    "save_kv_state",
})


class MCPRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for MCP server."""

    def __init__(
        self, *args: Any,
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

    def _get_tool_map(self) -> Dict[str, Callable[..., Any]]:
        """Build tool name → callable mapping.

        Uses the same keys as _TOOL_SCHEMAS so discovery and execution
        always stay in sync.
        """
        def _get_supported_formats(**_: Any) -> Dict[str, Any]:
            formats = {
                "text": self.stele.chunkers["text"].supported_extensions(),
                "code": self.stele.chunkers["code"].supported_extensions(),
            }
            for modality, flag in [
                ("image", HAS_IMAGE_CHUNKER),
                ("pdf", HAS_PDF_CHUNKER),
                ("audio", HAS_AUDIO_CHUNKER),
                ("video", HAS_VIDEO_CHUNKER),
            ]:
                if flag and modality in self.stele.chunkers:
                    formats[modality] = self.stele.chunkers[
                        modality
                    ].supported_extensions()
            return {"formats": formats}

        def _detect_modality(path: str = "", **_: Any) -> Dict[str, Any]:
            return {"path": path, "modality": self.stele.detect_modality(path)}

        return {
            "index_documents": self.stele.index_documents,
            "detect_changes_and_update": self.stele.detect_changes_and_update,
            "get_relevant_kv": self.stele.get_relevant_kv,
            "save_kv_state": self.stele.save_kv_state,
            "rollback": self.stele.rollback,
            "prune_chunks": self.stele.prune_chunks,
            "search": self.stele.search,
            "get_context": self.stele.get_context,
            "detect_modality": _detect_modality,
            "get_supported_formats": _get_supported_formats,
            "find_references": self.stele.find_references,
            "find_definition": self.stele.find_definition,
            "impact_radius": self.stele.impact_radius,
            "rebuild_symbol_graph": self.stele.rebuild_symbol_graph,
            "stale_chunks": self.stele.stale_chunks,
            "list_sessions": self.stele.list_sessions,
            "acquire_document_lock": self.stele.acquire_document_lock,
            "refresh_document_lock": self.stele.refresh_document_lock,
            "release_document_lock": self.stele.release_document_lock,
            "get_document_lock_status": self.stele.get_document_lock_status,
            "release_agent_locks": self.stele.release_agent_locks,
            "get_conflicts": self.stele.get_conflicts,
            "reap_expired_locks": self.stele.reap_expired_locks,
            "list_agents": self.stele.list_agents,
            "environment_check": self.stele.check_environment,
            "clean_bytecache": self.stele.clean_bytecache,
            "get_notifications": self.stele.get_notifications,
        }

    def _handle_tools_discovery(self) -> None:
        """Return list of available tools.

        Generated dynamically from _get_tool_map() keys + _TOOL_SCHEMAS.
        Tools added to the map but missing from schemas get a minimal entry,
        so they're always discoverable.
        """
        tool_map = self._get_tool_map()
        tools: List[Dict[str, Any]] = []
        for name in tool_map:
            schema = _TOOL_SCHEMAS.get(name, {
                "description": name,
                "parameters": {"type": "object", "properties": {}, "required": []},
            })
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

            result = self._execute_tool(tool_name, parameters)
            self._send_json_response(result)

        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
        except Exception as e:
            logger.exception("Error executing tool")
            self._send_error(500, str(e))

    def _execute_tool(
        self, tool_name: str, parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a Stele tool by name."""
        # Inject server agent_id for write operations when not provided
        if (
            tool_name in _WRITE_TOOLS
            and "agent_id" not in parameters
            and self._server_agent_id
        ):
            parameters = {**parameters, "agent_id": self._server_agent_id}

        tool_map = self._get_tool_map()

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
                *args, stele=self.stele,
                server_agent_id=self.agent_id, **kwargs,
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
            target=self._heartbeat_loop, daemon=True,
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
