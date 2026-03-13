"""
Backward-compatibility shim for ChunkForge core.

All functionality has moved to:
- chunkforge.engine: ChunkForge class
- chunkforge.chunkers.base: Chunk class

This module re-exports everything so existing imports continue working:
    from chunkforge.core import ChunkForge, Chunk
"""

from chunkforge.engine import ChunkForge
from chunkforge.chunkers.base import Chunk

__all__ = [
    "ChunkForge",
    "Chunk",
]
