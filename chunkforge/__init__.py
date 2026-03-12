"""
ChunkForge - Purely local, persistent KV-cache rollback and offload engine
with dynamic semantic chunking and hybrid vector-database-style indexing.

This library enables long-horizon agents (especially 1M+ context models) to
avoid ever re-scanning or re-processing unmodified documents or code. Unchanged
chunks instantly restore pre-computed KV states; only modified chunks trigger
a lightweight double-check.

Key Features:
- Dynamic semantic chunking with intelligent merging
- Hybrid indexing with SHA-256 hashes + semantic signatures
- Persistent KV-cache storage with full rollback support
- Disk offloading and automatic pruning
- Built-in local MCP server for agent integration

All operations are 100% offline and local-only. No internet access required.
"""

__version__ = "0.3.0"
__author__ = "ChunkForge Contributors"
__license__ = "MIT"

from chunkforge.core import ChunkForge
from chunkforge.storage import StorageBackend
from chunkforge.mcp_server import MCPServer

__all__ = [
    "ChunkForge",
    "StorageBackend",
    "MCPServer",
    "__version__",
]
