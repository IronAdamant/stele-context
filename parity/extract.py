"""Capture chunk and symbol records the way 1.5.2 indexes a file.

Paths stored in the record are project-relative and '/'-separated. File text
is read the way the engine reads it: UTF-8 with replacement, universal
newlines. Signatures are the zero-dependency float path packed to float32.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from stele_context import __version__
from stele_context.chunkers.code import CodeChunker
from stele_context.chunkers.numpy_compat import HAS_NUMPY
from stele_context.chunkers.text import TextChunker
from stele_context.engine import Stele
from stele_context.gitignore import GitignoreMatcher
from stele_context.indexing import expand_paths, merge_similar_chunks
from stele_context.symbols import SymbolExtractor

from parity import GOLDEN_SCHEMA, PYTHON_PACKAGE, REFERENCE
from parity.inventory import build_inventory

CHUNK_SIZE = 256
MAX_CHUNK_SIZE = 4096


def _require_reference_runtime() -> None:
    # 1.5.3 is the notice release of this same indexer. The capture record
    # stays PYTHON_PACKAGE so the corpus hashes do not move.
    if __version__ not in {PYTHON_PACKAGE, "1.5.3"}:
        raise RuntimeError(
            f"parity target is stele-context {PYTHON_PACKAGE}, found {__version__}"
        )
    if HAS_NUMPY:
        raise RuntimeError(
            "numpy is loaded; signature goldens are the zero-dependency path. "
            "Run via python -m parity so numpy is blocked before import."
        )


def _f32_hex(values: list[float]) -> str:
    return struct.pack("<" + str(len(values)) + "f", *values).hex()


def _views(symbols: list[dict]) -> dict:
    calls = [s for s in symbols if s["role"] == "reference" and s["kind"] == "function"]
    imports = [
        s
        for s in symbols
        if s["role"] == "reference" and s["kind"] in ("module", "import")
    ]
    scopes: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    for sym in symbols:
        if sym["role"] != "definition":
            continue
        container = sym["container"] or ""
        grouped.setdefault(container, []).append(
            {"name": sym["name"], "kind": sym["kind"], "line": sym["line"]}
        )
    for container, definitions in grouped.items():
        scopes.append({"container": container, "definitions": definitions})
    return {"calls": calls, "imports": imports, "scopes": scopes}


def _chunkers() -> dict:
    return {
        "text": TextChunker(chunk_size=CHUNK_SIZE, max_chunk_size=MAX_CHUNK_SIZE),
        "code": CodeChunker(chunk_size=CHUNK_SIZE, max_chunk_size=MAX_CHUNK_SIZE),
    }


def capture_file(path: Path, document_path: str, chunkers: dict) -> dict:
    """Chunk one file and extract symbols from each stripped chunk."""
    _require_reference_runtime()
    ext = path.suffix.lower()
    code_exts = set(chunkers["code"].supported_extensions())
    text_exts = set(chunkers["text"].supported_extensions())
    if ext in code_exts:
        modality = "code"
    elif ext in text_exts:
        modality = "text"
    else:
        raise ValueError(f"unsupported extension: {path}")
    content = path.read_text(encoding="utf-8", errors="replace")
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    chunks = chunkers[modality].chunk(content, document_path)
    # The engine merges adjacent chunks before it extracts symbols or stores ids.
    chunks = merge_similar_chunks(
        chunks,
        Stele.DEFAULT_MERGE_THRESHOLD,
        MAX_CHUNK_SIZE,
        Stele.MODALITY_THRESHOLDS,
    )
    extractor = SymbolExtractor()
    language = ext.removeprefix(".")
    chunk_rows = []
    symbols: list[dict] = []
    for chunk in chunks:
        signature = chunk.semantic_signature
        if len(signature) != 128:
            raise RuntimeError(f"signature width {len(signature)} for {document_path}")
        chunk_rows.append(
            {
                "index": chunk.chunk_index,
                "start_pos": chunk.start_pos,
                "end_pos": chunk.end_pos,
                "chunk_id": chunk.chunk_id,
                "content_sha256": chunk.content_hash,
                "token_count": chunk.token_count,
                "signature_f32_le": _f32_hex(list(signature)),
                "language": (chunk.metadata or {}).get("language"),
            }
        )
        if isinstance(chunk.content, str) and modality == "code":
            for sym in extractor.extract(
                chunk.content, document_path, chunk.chunk_id, language
            ):
                symbols.append(
                    {
                        "name": sym.name,
                        "kind": sym.kind,
                        "role": sym.role,
                        "line": sym.line_number,
                        "container": sym.container,
                        "chunk_index": chunk.chunk_index,
                        "chunk_id": sym.chunk_id,
                    }
                )
    record = {
        "path": document_path,
        "extension": language,
        "modality": modality,
        "content_sha256": content_sha256,
        "chunks": chunk_rows,
        "symbols": symbols,
    }
    record.update(_views(symbols))
    return record


def _normalize_relative(root: Path):
    base = root.resolve()

    def normalize(path_str: str) -> str:
        path = Path(path_str)
        try:
            rel = path.resolve().relative_to(base)
        except ValueError:
            rel = path
        return rel.as_posix()

    return normalize


def capture_tree(root: Path, *, gitignore: bool) -> dict:
    """Capture every code and text file under root, in path order."""
    _require_reference_runtime()
    root = root.resolve()
    chunkers = _chunkers()
    matcher = GitignoreMatcher.load(root) if gitignore else None
    paths = expand_paths(
        [str(root)],
        chunkers,
        set(Stele.DEFAULT_SKIP_DIRS),
        _normalize_relative(root),
        gitignore=matcher,
        respect_gitignore=gitignore,
    )
    files = [capture_file(root / rel, rel, chunkers) for rel in paths]
    files.sort(key=lambda row: row["path"])
    symbol_count = sum(len(row["symbols"]) for row in files)
    chunk_count = sum(len(row["chunks"]) for row in files)
    return {
        "schema": GOLDEN_SCHEMA,
        "reference": REFERENCE,
        "python_package": PYTHON_PACKAGE,
        "root_name": root.name,
        "file_count": len(files),
        "chunk_count": chunk_count,
        "symbol_count": symbol_count,
        "files": files,
    }


def capture_fixtures(fixtures: Path) -> dict:
    record = capture_tree(fixtures, gitignore=False)
    record["kind"] = "fixtures"
    record["inventory"] = build_inventory()
    return record
