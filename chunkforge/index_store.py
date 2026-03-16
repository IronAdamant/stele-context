"""
Persistent serialization for the HNSW and BM25 indexes.

Saves and loads indexes to compressed JSON files so they don't need
to be rebuilt from SQLite on every startup.  Uses a chunk ID hash
to detect staleness.
"""

import hashlib
import json
import tempfile
import zlib
from pathlib import Path
from typing import Any, Dict, Optional

from chunkforge.index import VectorIndex


INDEX_FILENAME = "hnsw_index.json.zlib"
BM25_FILENAME = "bm25_index.json.zlib"
FORMAT_VERSION = 1


def compute_chunk_ids_hash(storage: Any) -> str:
    """Compute a hash of all chunk IDs in the database.

    Uses incremental hashing to avoid loading all IDs into memory.
    """
    import sqlite3

    h = hashlib.sha256()
    with sqlite3.connect(storage.db_path) as conn:
        cursor = conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")
        for (chunk_id,) in cursor:
            h.update(chunk_id.encode("utf-8"))
            h.update(b"|")
    return h.hexdigest()


# -- Shared save/load helpers ------------------------------------------------


def _save_compressed_json(
    data: Dict[str, Any], filename: str, index_dir: Path
) -> None:
    """Serialize a dict to a compressed JSON file (atomic write)."""
    json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(json_bytes)

    index_dir.mkdir(parents=True, exist_ok=True)
    target = index_dir / filename

    fd, tmp_path = tempfile.mkstemp(dir=str(index_dir), suffix=".tmp")
    try:
        with open(fd, "wb") as f:
            f.write(compressed)
        Path(tmp_path).replace(target)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _load_compressed_json(
    filename: str, index_dir: Path
) -> Optional[Dict[str, Any]]:
    """Load a compressed JSON file, returning None on any error."""
    path = index_dir / filename
    if not path.exists():
        return None

    try:
        compressed = path.read_bytes()
        json_bytes = zlib.decompress(compressed)
        data = json.loads(json_bytes)
        if data.get("_version") != FORMAT_VERSION:
            return None
        return data
    except (zlib.error, json.JSONDecodeError, KeyError):
        return None


# -- HNSW persistence --------------------------------------------------------


def save_index(index: VectorIndex, chunk_ids_hash: str, index_dir: Path) -> None:
    """Serialize a VectorIndex to a compressed JSON file."""
    data = index.to_dict()
    data["_version"] = FORMAT_VERSION
    data["_chunk_ids_hash"] = chunk_ids_hash
    _save_compressed_json(data, INDEX_FILENAME, index_dir)


def load_index(index_dir: Path) -> Optional[Dict[str, Any]]:
    """Load a persisted index file, returning the raw dict or None."""
    return _load_compressed_json(INDEX_FILENAME, index_dir)


def load_if_fresh(
    index_dir: Path,
    current_hash: str,
) -> Optional[VectorIndex]:
    """Load persisted index only if it matches the current chunk state."""
    data = load_index(index_dir)
    if data is None:
        return None
    if data.get("_chunk_ids_hash") != current_hash:
        return None
    try:
        return VectorIndex.from_dict(data)
    except (KeyError, TypeError):
        return None


# -- BM25 persistence --------------------------------------------------------


def save_bm25(bm25_index: Any, chunk_ids_hash: str, index_dir: Path) -> None:
    """Serialize a BM25Index to a compressed JSON file."""
    data = bm25_index.to_dict()
    data["_version"] = FORMAT_VERSION
    data["_chunk_ids_hash"] = chunk_ids_hash
    _save_compressed_json(data, BM25_FILENAME, index_dir)


def load_bm25_if_fresh(index_dir: Path, current_hash: str) -> Optional[Any]:
    """Load persisted BM25 index if it matches the current chunk state."""
    data = _load_compressed_json(BM25_FILENAME, index_dir)
    if data is None:
        return None
    if data.get("_chunk_ids_hash") != current_hash:
        return None

    try:
        from chunkforge.bm25 import BM25Index

        return BM25Index.from_dict(data)
    except (KeyError, TypeError):
        return None
