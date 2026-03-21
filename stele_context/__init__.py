"""
Stele Context — Local context cache for LLM agents with semantic chunking
and vector search.

Smart context cache that avoids re-reading unchanged files by caching
chunk data with semantic search. Routes documents through modality-specific
chunkers, stores chunk content with HNSW vector indexing, and provides
fast retrieval via semantic search.

Key Features:
- Dynamic semantic chunking with modality-specific chunkers
- HNSW vector index for O(log n) similarity search
- Chunk content persistence for instant retrieval
- Change detection with hash + semantic comparison
- Session management with rollback support
- Built-in MCP server for agent integration

All operations are 100% offline and local-only. No internet access required.
"""

__version__ = "0.11.0"
__author__ = "Stele Context Contributors"
__license__ = "MIT"

from stele_context.engine import Stele
from stele_context.storage import StorageBackend
from stele_context.session import SessionManager
from stele_context.mcp_server import MCPServer
from stele_context.chunkers.base import Chunk

__all__ = [
    "Stele",
    "Chunk",
    "StorageBackend",
    "SessionManager",
    "MCPServer",
    "__version__",
]
