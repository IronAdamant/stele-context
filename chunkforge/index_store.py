"""
Persistent serialization for the HNSW vector index.

Saves and loads the VectorIndex to compressed JSON files so
the index doesn't need to be rebuilt from SQLite on every startup.
Uses a chunk ID hash to detect staleness.
"""

import hashlib
import json
import tempfile
import zlib
from pathlib import Path
from typing import Any, Dict, Optional

from chunkforge.index import VectorIndex


INDEX_FILENAME = "hnsw_index.json.zlib"
FORMAT_VERSION = 1


def compute_chunk_ids_hash(storage: Any) -> str:
    """Compute a hash of all chunk IDs in the database.

    Used to detect whether the persisted index is stale
    (chunks added or removed since last save).
    """
    import sqlite3

    with sqlite3.connect(storage.db_path) as conn:
        cursor = conn.execute("SELECT chunk_id FROM chunks ORDER BY chunk_id")
        chunk_ids = [row[0] for row in cursor.fetchall()]
    id_string = "|".join(chunk_ids)
    return hashlib.sha256(id_string.encode("utf-8")).hexdigest()


def save_index(index: VectorIndex, chunk_ids_hash: str, index_dir: Path) -> None:
    """Serialize a VectorIndex to a compressed JSON file.

    Uses write-to-temp-then-rename for atomic writes.
    """
    data = index.to_dict()
    data["_version"] = FORMAT_VERSION
    data["_chunk_ids_hash"] = chunk_ids_hash

    json_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(json_bytes)

    index_dir.mkdir(parents=True, exist_ok=True)
    target = index_dir / INDEX_FILENAME

    # Atomic write: temp file then rename
    fd, tmp_path = tempfile.mkstemp(dir=str(index_dir), suffix=".tmp")
    try:
        with open(fd, "wb") as f:
            f.write(compressed)
        Path(tmp_path).replace(target)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def load_index(index_dir: Path) -> Optional[Dict[str, Any]]:
    """Load a persisted index file, returning the raw dict or None."""
    path = index_dir / INDEX_FILENAME
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


def load_if_fresh(
    index_dir: Path,
    current_hash: str,
) -> Optional[VectorIndex]:
    """Load persisted index only if it matches the current chunk state.

    Returns a VectorIndex if the persisted index is fresh, None otherwise.
    """
    data = load_index(index_dir)
    if data is None:
        return None
    if data.get("_chunk_ids_hash") != current_hash:
        return None
    try:
        return VectorIndex.from_dict(data)
    except (KeyError, TypeError):
        return None
