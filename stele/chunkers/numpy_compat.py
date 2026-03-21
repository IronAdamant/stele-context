"""
Numpy compatibility layer for Stele.

Provides a pure-Python fallback for numpy operations when numpy
is not installed. All modules import numpy support from here.
"""

from __future__ import annotations

import math
import struct
from typing import Any

try:
    import numpy as np

    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

    class _NumpyFallback:
        """Minimal numpy fallback using pure Python."""

        float32 = "float32"

        class linalg:
            """Linear algebra operations."""

            @staticmethod
            def norm(a: list[float]) -> float:
                """Compute L2 norm."""
                return math.sqrt(sum(x * x for x in a))

        @staticmethod
        def zeros(shape: int, dtype: Any = None) -> list[float]:
            """Create array of zeros."""
            return [0.0] * shape

        @staticmethod
        def dot(a: list[float], b: list[float]) -> float:
            """Compute dot product."""
            return sum(x * y for x, y in zip(a, b))

        @staticmethod
        def frombuffer(data: bytes, dtype: Any = None) -> list[float]:
            """Convert bytes to array."""
            count = len(data) // 4  # float32 = 4 bytes
            return list(struct.unpack(f"{count}f", data))

    np = _NumpyFallback()  # type: ignore


def cosine_similarity(sig1: Any, sig2: Any) -> float:
    """Compute cosine similarity between two signature vectors."""
    dot = np.dot(sig1, sig2)
    norm1 = np.linalg.norm(sig1)
    norm2 = np.linalg.norm(sig2)
    if norm1 > 0 and norm2 > 0:
        return float(dot / (norm1 * norm2))
    return 0.0


def sig_to_bytes(sig: Any) -> bytes:
    """Convert a semantic signature to bytes for storage."""
    if HAS_NUMPY and hasattr(sig, "tobytes"):
        return sig.tobytes()
    return struct.pack(f"{len(sig)}f", *sig)


def sig_from_bytes(data: bytes) -> Any:
    """Convert bytes back to a semantic signature."""
    return np.frombuffer(data, dtype=np.float32)


def sig_to_list(sig: Any) -> list[float]:
    """Convert a semantic signature to a plain list."""
    if hasattr(sig, "tolist"):
        return sig.tolist()
    return list(sig)
