"""
MCP (Model Context Protocol) server for ChunkForge.

Provides a minimal HTTP/JSON server running on localhost that exposes
ChunkForge tools for compatible coding agents. Uses only Python standard
library (http.server + json) with zero external dependencies.

The server implements the MCP tool discovery protocol, allowing agents
to discover and call ChunkForge tools naturally.

Supports multi-modal content: text, code, images, PDFs, audio, video.
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from chunkforge.core import ChunkForge
from chunkforge.chunkers import (
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


class MCPRequestHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for MCP server.
    
    Handles tool discovery and execution requests from agents.
    """
    
    def __init__(self, *args: Any, chunkforge: ChunkForge, **kwargs: Any):
        """Initialize with ChunkForge instance."""
        self.chunkforge = chunkforge
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
    
    def _handle_tools_discovery(self) -> None:
        """Return list of available tools."""
        tools = [
            {
                "name": "index_documents",
                "description": "Index one or more documents for KV-cache management. Supports text, code, images, PDFs, audio, and video. Performs dynamic semantic chunking and stores chunk metadata.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of document paths to index (supports .txt, .md, .py, .js, .png, .jpg, .pdf, .mp3, .mp4, etc.)",
                        },
                        "force_reindex": {
                            "type": "boolean",
                            "description": "Force re-indexing even if document hasn't changed",
                            "default": False,
                        },
                    },
                    "required": ["paths"],
                },
            },
            {
                "name": "detect_modality",
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
            {
                "name": "get_supported_formats",
                "description": "Get list of supported file formats by modality.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "detect_changes_and_update",
                "description": "Detect changes in documents and update KV-cache accordingly. Unchanged chunks instantly restore pre-saved KV states; only modified chunks trigger a lightweight double-check.",
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
                    },
                    "required": ["session_id"],
                },
            },
            {
                "name": "get_relevant_kv",
                "description": "Get KV-cache for chunks most relevant to a query. Uses semantic similarity to find the most relevant chunks.",
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
            {
                "name": "save_kv_state",
                "description": "Save KV-cache state for a session. Stores KV tensors for rollback and future restoration.",
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
                    },
                    "required": ["session_id", "kv_data"],
                },
            },
            {
                "name": "rollback",
                "description": "Rollback session to a previous turn. Removes all KV states after the target turn.",
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
            {
                "name": "prune_chunks",
                "description": "Prune low-relevance chunks to stay under token limit. Removes lowest relevance chunks first.",
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
        ]
        
        self._send_json_response({"tools": tools})
    
    def _handle_health(self) -> None:
        """Return health status."""
        stats = self.chunkforge.get_stats()
        self._send_json_response({
            "status": "healthy",
            "version": stats["version"],
            "storage": stats["storage"],
        })
    
    def _handle_tool_call(self) -> None:
        """Handle tool execution request."""
        try:
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            request = json.loads(body.decode("utf-8"))
            
            tool_name = request.get("tool")
            parameters = request.get("parameters", {})
            
            if not tool_name:
                self._send_error(400, "Missing 'tool' field")
                return
            
            # Execute tool
            result = self._execute_tool(tool_name, parameters)
            self._send_json_response(result)
            
        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON")
        except Exception as e:
            logger.exception("Error executing tool")
            self._send_error(500, str(e))
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a ChunkForge tool.
        
        Args:
            tool_name: Name of the tool to execute
            parameters: Tool parameters
            
        Returns:
            Tool execution result
        """
        # Standard tools
        tool_map: Dict[str, Callable[..., Any]] = {
            "index_documents": self.chunkforge.index_documents,
            "detect_changes_and_update": self.chunkforge.detect_changes_and_update,
            "get_relevant_kv": self.chunkforge.get_relevant_kv,
            "save_kv_state": self.chunkforge.save_kv_state,
            "rollback": self.chunkforge.rollback,
            "prune_chunks": self.chunkforge.prune_chunks,
        }
        
        # Multi-modal tools
        if tool_name == "detect_modality":
            path = parameters.get("path", "")
            modality = self.chunkforge.detect_modality(path)
            return {"success": True, "result": {"path": path, "modality": modality}}
        
        if tool_name == "get_supported_formats":
            formats = {
                "text": self.chunkforge.chunkers["text"].supported_extensions(),
                "code": self.chunkforge.chunkers["code"].supported_extensions(),
            }
            if HAS_IMAGE_CHUNKER:
                formats["image"] = self.chunkforge.chunkers["image"].supported_extensions()
            if HAS_PDF_CHUNKER:
                formats["pdf"] = self.chunkforge.chunkers["pdf"].supported_extensions()
            if HAS_AUDIO_CHUNKER:
                formats["audio"] = self.chunkforge.chunkers["audio"].supported_extensions()
            if HAS_VIDEO_CHUNKER:
                formats["video"] = self.chunkforge.chunkers["video"].supported_extensions()
            
            return {"success": True, "result": {"formats": formats}}
        
        if tool_name not in tool_map:
            return {
                "error": f"Unknown tool: {tool_name}",
                "available_tools": list(tool_map.keys()) + ["detect_modality", "get_supported_formats"],
            }
        
        try:
            result = tool_map[tool_name](**parameters)
            return {"success": True, "result": result}
        except TypeError as e:
            return {
                "error": f"Invalid parameters for {tool_name}: {e}",
            }
        except Exception as e:
            return {
                "error": f"Tool execution failed: {e}",
            }
    
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


class MCPServer:
    """
    MCP server for ChunkForge.
    
    Provides a minimal HTTP server that exposes ChunkForge tools
    for compatible coding agents.
    """
    
    def __init__(
        self,
        chunkforge: ChunkForge,
        host: str = "localhost",
        port: int = 9876,
    ):
        """
        Initialize MCP server.
        
        Args:
            chunkforge: ChunkForge instance to use
            host: Host to bind to (default: localhost)
            port: Port to bind to (default: 9876)
        """
        self.chunkforge = chunkforge
        self.host = host
        self.port = port
        self.server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
    
    def start(self, blocking: bool = False) -> None:
        """
        Start the MCP server.
        
        Args:
            blocking: If True, block the current thread. If False, run in background.
        """
        # Create handler factory
        def handler_factory(*args: Any, **kwargs: Any) -> MCPRequestHandler:
            return MCPRequestHandler(*args, chunkforge=self.chunkforge, **kwargs)
        
        # Create server
        self.server = HTTPServer((self.host, self.port), handler_factory)
        
        logger.info(f"ChunkForge MCP server starting on http://{self.host}:{self.port}")
        logger.info("Available endpoints:")
        logger.info("  GET  /tools   - Discover available tools")
        logger.info("  GET  /health  - Health check")
        logger.info("  POST /call    - Execute a tool")
        
        if blocking:
            self.server.serve_forever()
        else:
            self._thread = threading.Thread(
                target=self.server.serve_forever,
                daemon=True,
                name="chunkforge-mcp-server",
            )
            self._thread.start()
            logger.info("Server running in background thread")
    
    def stop(self) -> None:
        """Stop the MCP server."""
        if self.server:
            logger.info("Stopping ChunkForge MCP server")
            self.server.shutdown()
            self.server = None
        
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
    
    def get_url(self) -> str:
        """Get the server URL."""
        return f"http://{self.host}:{self.port}"
