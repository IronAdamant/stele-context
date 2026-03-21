"""
Persistent serialization for the HNSW and BM25 indexes.

Saves and loads indexes to compressed JSON files so they don't need
to be rebuilt from SQLite on every startup.  Uses a chunk ID hash
to detect staleness.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import zlib
from pathlib import Path
from typing import Any

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

from stele.index import VectorIndex


INDEX_FILENAME = "hnsw_index.json.zlib"
BM25_FILENAME = "bm25_index.json.zlib"
FORMAT_VERSION = 1


def compute_chunk_ids_hash(storage: Any) -> str:
    """Compute a hash of all chunk IDs in the database.

    Uses incremental hashing to avoid loading all IDs into memory.
    """
    from stele.storage_schema import connect

    h = hashlib.sha256()
    with connect(storage.db_path) as conn:
        cursor = conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")
        for (chunk_id,) in cursor:
            h.update(chunk_id.encode("utf-8"))
            h.update(b"|")
    return h.hexdigest()


# -- Shared save/load helpers ------------------------------------------------


def _lock_path(index_dir: Path, filename: str) -> Path:
    """Return the .lock sidecar path for a given index file."""
    return index_dir / (filename + ".lock")


def _save_compressed_json(data: dict[str, Any], filename: str, index_dir: Path) -> None:
    """Serialize a dict to a compressed JSON file (atomic write).

    Uses fcntl.flock(LOCK_EX) on a sidecar .lock file to prevent
    concurrent writes from multiple processes.
    """
    json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(json_bytes)

    index_dir.mkdir(parents=True, exist_ok=True)
    target = index_dir / filename

    lock_file = _lock_path(index_dir, filename)
    lock_fd = open(lock_file, "w")
    try:
        if _HAS_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

        fd, tmp_path = tempfile.mkstemp(dir=str(index_dir), suffix=".tmp")
        try:
            with open(fd, "wb") as f:
                f.write(compressed)
            Path(tmp_path).replace(target)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
    finally:
        if _HAS_FCNTL:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _load_compressed_json(filename: str, index_dir: Path) -> dict[str, Any] | None:
    """Load a compressed JSON file, returning None on any error.

    Uses fcntl.flock(LOCK_SH) to allow concurrent readers but
    block during writes.
    """
    path = index_dir / filename
    if not path.exists():
        return None

    lock_file = _lock_path(index_dir, filename)
    lock_fd = None
    try:
        if _HAS_FCNTL and lock_file.exists():
            lock_fd = open(lock_file, "r")
            fcntl.flock(lock_fd, fcntl.LOCK_SH)

        compressed = path.read_bytes()
        json_bytes = zlib.decompress(compressed)
        data = json.loads(json_bytes)
        if data.get("_version") != FORMAT_VERSION:
            return None
        return data
    except (zlib.error, json.JSONDecodeError, KeyError):
        return None
    finally:
        if lock_fd is not None:
            if _HAS_FCNTL:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


# -- HNSW persistence --------------------------------------------------------


def save_index(index: VectorIndex, chunk_ids_hash: str, index_dir: Path) -> None:
    """Serialize a VectorIndex to a compressed JSON file."""
    data = index.to_dict()
    data["_version"] = FORMAT_VERSION
    data["_chunk_ids_hash"] = chunk_ids_hash
    _save_compressed_json(data, INDEX_FILENAME, index_dir)


def load_index(index_dir: Path) -> dict[str, Any] | None:
    """Load a persisted index file, returning the raw dict or None."""
    return _load_compressed_json(INDEX_FILENAME, index_dir)


def load_if_fresh(
    index_dir: Path,
    current_hash: str,
) -> VectorIndex | None:
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


def load_bm25_if_fresh(index_dir: Path, current_hash: str) -> Any | None:
    """Load persisted BM25 index if it matches the current chunk state."""
    data = _load_compressed_json(BM25_FILENAME, index_dir)
    if data is None:
        return None
    if data.get("_chunk_ids_hash") != current_hash:
        return None

    try:
        from stele.bm25 import BM25Index

        return BM25Index.from_dict(data)
    except (KeyError, TypeError):
        return None
