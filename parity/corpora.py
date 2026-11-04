"""Download the locked corpora and hash the reference capture.

rich 13.9.4 and flask 3.1.0 are the phase 0 corpora. The tarball digest is
pinned after the first capture so a changed archive fails closed.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

from parity.extract import capture_tree

CORPORA = (
    {
        "id": "rich-13.9.4",
        "url": "https://github.com/Textualize/rich/archive/refs/tags/v13.9.4.tar.gz",
    },
    {
        "id": "flask-3.1.0",
        "url": "https://github.com/pallets/flask/archive/refs/tags/3.1.0.tar.gz",
    },
)

CACHE = Path(__file__).resolve().parent / ".cache"
USER_AGENT = "stele-context-parity/2.0.0"


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def _extract(blob: bytes, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        # Python 3.12+ filter; refuse links that escape the dest.
        archive.extractall(dest, filter="data")
    children = [child for child in dest.iterdir() if child.is_dir()]
    if len(children) != 1:
        raise RuntimeError(
            f"expected one top-level directory in {dest}, got {children}"
        )
    return children[0]


def materialize(spec: dict) -> tuple[Path, str]:
    """Return the extracted corpus root and the tarball sha256."""
    CACHE.mkdir(parents=True, exist_ok=True)
    archive_path = CACHE / f"{spec['id']}.tar.gz"
    if archive_path.is_file():
        blob = archive_path.read_bytes()
    else:
        blob = _download(spec["url"])
        archive_path.write_bytes(blob)
    digest = hashlib.sha256(blob).hexdigest()
    root = CACHE / spec["id"]
    if not root.is_dir():
        _extract(blob, root)
    extracted = [child for child in root.iterdir() if child.is_dir()]
    if len(extracted) != 1:
        raise RuntimeError(f"corpus {spec['id']} did not extract to one directory")
    return extracted[0], digest


def capture_corpora(pins: dict[str, str] | None = None) -> dict:
    """Capture each corpus. When pins is set, the tarball digest must match."""
    rows = []
    for spec in CORPORA:
        source, digest = materialize(spec)
        pinned = None if pins is None else pins.get(spec["id"])
        if pinned is not None and pinned != digest:
            raise RuntimeError(
                f"{spec['id']} tarball sha256 {digest} != pinned {pinned}"
            )
        captured = capture_tree(source, gitignore=True)
        payload = json.dumps(captured, sort_keys=True, separators=(",", ":"))
        rows.append(
            {
                "id": spec["id"],
                "url": spec["url"],
                "tarball_sha256": digest,
                "file_count": captured["file_count"],
                "chunk_count": captured["chunk_count"],
                "symbol_count": captured["symbol_count"],
                "capture_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            }
        )
    return {"schema": "stele-parity-corpora/1", "corpora": rows}
