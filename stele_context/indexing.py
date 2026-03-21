"""
Document indexing helpers for the Stele engine.

All functions receive engine state as explicit parameters -- no imports
back to engine.py, no circular dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stele_context.chunkers.base import Chunk
from stele_context.chunkers.numpy_compat import (
    sig_from_bytes,
    sig_to_list,
)


# Keywords that signal a new definition boundary in code
_DEF_STARTS = (
    "def ",
    "class ",
    "function ",
    "func ",
    "fn ",
    "pub fn ",
    "async def ",
    "async function ",
    "export function ",
    "export class ",
    "export default ",
)


def merge_similar_chunks(
    chunks: list[Chunk],
    merge_threshold: float,
    max_chunk_size: int,
    modality_thresholds: dict[str, dict[str, float]],
) -> list[Chunk]:
    """Merge adjacent chunks with high similarity (single-pass).

    Uses modality-specific merge thresholds: code chunks require
    higher similarity to merge (preserving AST boundaries), while
    prose chunks merge more aggressively.
    """
    if len(chunks) <= 1:
        return chunks

    modality = chunks[0].modality
    threshold = modality_thresholds.get(modality, {}).get("merge", merge_threshold)

    merged = [chunks[0]]
    for chunk in chunks[1:]:
        current = merged[-1]

        # Never merge across function/class boundaries in code
        if modality == "code" and isinstance(chunk.content, str):
            leading = chunk.content.lstrip()
            if any(leading.startswith(kw) for kw in _DEF_STARTS):
                merged.append(chunk)
                continue

        similarity = current.similarity(chunk)
        combined_tokens = current.token_count + chunk.token_count

        if (
            similarity >= threshold
            and combined_tokens <= max_chunk_size
            and isinstance(current.content, str)
            and isinstance(chunk.content, str)
        ):
            merged_content = current.content + "\n\n" + chunk.content
            merged[-1] = Chunk(
                content=merged_content,
                modality=current.modality,
                start_pos=current.start_pos,
                end_pos=chunk.end_pos,
                document_path=current.document_path,
                chunk_index=current.chunk_index,
                metadata={**current.metadata, **chunk.metadata},
            )
        else:
            merged.append(chunk)

    return merged


def persist_chunks(
    chunks: list[Chunk],
    doc_path: str,
    storage: Any,
    vector_index: Any,
    bm25_index: Any,
    bm25_ready: bool,
) -> None:
    """Store chunks and add them to the vector and keyword indexes."""
    for chunk in chunks:
        chunk_content = chunk.content if isinstance(chunk.content, str) else None
        storage.store_chunk(
            chunk_id=chunk.chunk_id,
            document_path=doc_path,
            content_hash=chunk.content_hash,
            semantic_signature=chunk.semantic_signature,
            start_pos=chunk.start_pos,
            end_pos=chunk.end_pos,
            token_count=chunk.token_count,
            content=chunk_content,
        )
        vector_index.add_chunk(
            chunk.chunk_id,
            sig_to_list(chunk.semantic_signature),
        )
        if bm25_ready and bm25_index is not None and chunk_content:
            bm25_index.add_document(chunk.chunk_id, chunk_content)


def remove_stale_chunks(
    old_ids: set,
    new_ids: set,
    vector_index: Any,
    bm25_index: Any,
    bm25_ready: bool,
    storage: Any,
) -> None:
    """Remove chunks that no longer exist after re-indexing."""
    stale_ids = old_ids - new_ids
    if stale_ids:
        for cid in stale_ids:
            vector_index.remove_chunk(cid)
            if bm25_ready and bm25_index is not None:
                bm25_index.remove_document(cid)
        storage.delete_chunks(list(stale_ids))


def check_document_ownership(
    document_path: str,
    agent_id: str | None,
    do_get_lock_status: Any,
    do_record_conflict: Any,
) -> None:
    """Raise PermissionError if document is locked by another agent.

    Called from within write-locked methods.  If ``agent_id`` is
    ``None``, ownership checking is skipped (backward compat).
    Routes through coordination (shared locks) when available,
    otherwise falls back to local per-worktree locks.
    """
    if agent_id is None:
        return
    status = do_get_lock_status(document_path)
    if status.get("locked") and status["locked_by"] != agent_id:
        do_record_conflict(
            document_path=document_path,
            agent_a=status["locked_by"],
            agent_b=agent_id,
            conflict_type="ownership_violation",
        )
        worktree_info = ""
        if status.get("worktree"):
            worktree_info = f" (worktree: {status['worktree']})"
        raise PermissionError(
            f"Document '{document_path}' is locked by agent "
            f"'{status['locked_by']}'{worktree_info}"
        )


def chunk_and_store(
    abs_path: Path,
    doc_path: str,
    content: Any,
    content_hash: str,
    modality: str,
    storage: Any,
    chunkers: dict[str, Any],
    vector_index: Any,
    bm25_index: Any,
    bm25_ready: bool,
    symbol_manager: Any,
    merge_threshold: float,
    max_chunk_size: int,
    modality_thresholds: dict[str, dict[str, float]],
) -> list:
    """Chunk a single file, persist chunks, extract symbols, return Chunk list.

    Args:
        abs_path: Absolute filesystem path (for stat/mtime).
        doc_path: Normalized path used as the storage key.
    """
    existing_doc = storage.get_document(doc_path)

    # Build signature cache from old chunks (skip recomputation)
    old_chunks_meta: list = []
    if existing_doc:
        old_chunks_meta = storage.get_document_chunks(doc_path)
    sig_cache = {c["content_hash"]: c["semantic_signature"] for c in old_chunks_meta}

    # Route through appropriate chunker
    chunker = chunkers.get(modality, chunkers["text"])
    chunks = chunker.chunk(content, doc_path)

    # Inject cached signatures for unchanged chunks
    for chunk in chunks:
        cached_sig = sig_cache.get(chunk.content_hash)
        if cached_sig is not None:
            chunk._semantic_signature = sig_from_bytes(cached_sig)

    chunks = merge_similar_chunks(
        chunks, merge_threshold, max_chunk_size, modality_thresholds
    )

    # Clean up stale chunks from previous indexing
    if old_chunks_meta:
        old_ids = {c["chunk_id"] for c in old_chunks_meta}
        new_ids = {c.chunk_id for c in chunks}
        remove_stale_chunks(
            old_ids, new_ids, vector_index, bm25_index, bm25_ready, storage
        )

    persist_chunks(chunks, doc_path, storage, vector_index, bm25_index, bm25_ready)
    symbol_manager.extract_document_symbols(doc_path, chunks)

    storage.store_document(
        document_path=doc_path,
        content_hash=content_hash,
        chunk_count=len(chunks),
        last_modified=abs_path.stat().st_mtime,
    )
    return chunks


def index_documents_unlocked(
    paths: list[str],
    force_reindex: bool,
    agent_id: str | None,
    expected_versions: dict[str, int] | None,
    *,
    expand_paths: Any,
    normalize_path: Any,
    resolve_path: Any,
    detect_modality: Any,
    read_and_hash: Any,
    storage: Any,
    chunkers: dict[str, Any],
    vector_index: Any,
    bm25_index: Any,
    bm25_ready: bool,
    symbol_manager: Any,
    merge_threshold: float,
    max_chunk_size: int,
    modality_thresholds: dict[str, dict[str, float]],
    do_get_lock_status: Any,
    do_acquire_lock: Any,
    do_record_conflict: Any,
    save_index: Any,
    save_bm25: Any,
    coordination: Any,
) -> dict[str, Any]:
    """Core body of index_documents, extracted for engine delegation."""
    paths = expand_paths(paths)

    if expected_versions:
        expected_versions = {normalize_path(k): v for k, v in expected_versions.items()}

    results: dict[str, Any] = {
        "indexed": [],
        "skipped": [],
        "errors": [],
        "conflicts": [],
        "total_chunks": 0,
        "total_tokens": 0,
    }

    for norm_path in paths:
        abs_path = resolve_path(norm_path)

        if not abs_path.exists():
            results["errors"].append({"path": norm_path, "error": "File not found"})
            continue
        if not abs_path.is_file():
            results["errors"].append({"path": norm_path, "error": "Not a file"})
            continue

        try:
            # Ownership check (raises if locked by another agent)
            check_document_ownership(
                norm_path, agent_id, do_get_lock_status, do_record_conflict
            )

            # Auto-acquire lock when agent_id is set and doc exists unlocked
            existing_doc = storage.get_document(norm_path)
            if agent_id and existing_doc:
                status = do_get_lock_status(norm_path)
                if not status.get("locked"):
                    do_acquire_lock(norm_path, agent_id)

            # Optimistic version check
            if expected_versions and norm_path in expected_versions:
                ver_result = storage.check_and_increment_doc_version(
                    norm_path, expected_versions[norm_path]
                )
                if not ver_result.get("success"):
                    if agent_id:
                        do_record_conflict(
                            document_path=norm_path,
                            agent_a="unknown",
                            agent_b=agent_id,
                            conflict_type="version_conflict",
                            expected_version=ver_result.get("expected"),
                            actual_version=ver_result.get("actual"),
                        )
                    results["conflicts"].append(
                        {
                            "path": norm_path,
                            "reason": "version_conflict",
                            **ver_result,
                        }
                    )
                    continue

            modality = detect_modality(str(abs_path))
            file_content, content_hash = read_and_hash(abs_path, modality)

            if existing_doc and not force_reindex:
                if existing_doc["content_hash"] == content_hash:
                    results["skipped"].append(
                        {
                            "path": norm_path,
                            "reason": "Unchanged",
                            "chunk_count": existing_doc["chunk_count"],
                        }
                    )
                    continue

            chunks = chunk_and_store(
                abs_path,
                norm_path,
                file_content,
                content_hash,
                modality,
                storage,
                chunkers,
                vector_index,
                bm25_index,
                bm25_ready,
                symbol_manager,
                merge_threshold,
                max_chunk_size,
                modality_thresholds,
            )

            # Auto-acquire lock on newly-created documents
            if agent_id and not existing_doc:
                do_acquire_lock(norm_path, agent_id)

            # Increment version (if not already done by optimistic check)
            if not (expected_versions and norm_path in expected_versions):
                storage.increment_doc_version(norm_path)

            total_tokens = sum(c.token_count for c in chunks)
            results["indexed"].append(
                {
                    "path": norm_path,
                    "chunk_count": len(chunks),
                    "total_tokens": total_tokens,
                    "modality": modality,
                }
            )
            results["total_chunks"] += len(chunks)
            results["total_tokens"] += total_tokens

        except PermissionError as e:
            results["conflicts"].append({"path": norm_path, "error": str(e)})
        except Exception as e:
            results["errors"].append({"path": norm_path, "error": str(e)})

    if results["indexed"]:
        save_index()
        save_bm25()
        affected = set()
        for doc_info in results["indexed"]:
            for c in storage.get_document_chunks(doc_info["path"]):
                affected.add(c["chunk_id"])
        symbol_manager.rebuild_edges(affected_chunk_ids=affected or None)

    # Notify other agents about changes
    if coordination and results["indexed"]:
        coordination.notify_changes_batch(
            [(d["path"], "indexed") for d in results["indexed"]],
            agent_id or "",
        )

    return results


def detect_modality(file_path: str, chunkers: dict[str, Any]) -> str:
    """Detect file modality from extension (no lock needed)."""
    ext = Path(file_path).suffix.lower()
    if ext in chunkers["code"].supported_extensions():
        return "code"
    for modality, chunker in chunkers.items():
        if modality != "text" and ext in chunker.supported_extensions():
            return modality
    if ext in chunkers["text"].supported_extensions():
        return "text"
    return "unknown"


def expand_paths(
    paths: list[str],
    chunkers: dict[str, Any],
    skip_dirs: set[str],
    normalize_path: Any,
) -> list[str]:
    """Expand directories and globs into individual file paths."""
    supported: set = set()
    for chunker in chunkers.values():
        supported.update(chunker.supported_extensions())
    expanded: list[str] = []
    for path_str in paths:
        p = Path(path_str)
        if p.is_file():
            expanded.append(normalize_path(str(p)))
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                rel_parts = child.relative_to(p).parts
                if any(part in skip_dirs or part.startswith(".") for part in rel_parts):
                    continue
                if child.is_file() and child.suffix.lower() in supported:
                    expanded.append(normalize_path(str(child)))
        else:
            expanded.append(normalize_path(path_str))
    return expanded


def annotate_unlocked(
    target: str,
    target_type: str,
    content: str,
    tags: list[str] | None,
    storage: Any,
    normalize_path: Any,
) -> dict[str, Any]:
    """Validate and store an annotation (no lock -- caller holds it)."""
    if target_type == "document":
        target = normalize_path(target)
    if target_type not in ("document", "chunk"):
        return {"error": "target_type must be 'document' or 'chunk'"}
    if target_type == "document":
        if storage.get_document(target) is None:
            return {"error": f"Document not found: {target}"}
    elif storage.get_chunk(target) is None:
        return {"error": f"Chunk not found: {target}"}
    aid = storage.store_annotation(target, target_type, content, tags)
    return {"id": aid, "target": target, "target_type": target_type}


def remove_document_unlocked(
    document_path: str,
    agent_id: str | None,
    *,
    normalize_path: Any,
    do_get_lock_status: Any,
    do_record_conflict: Any,
    storage: Any,
    vector_index: Any,
    bm25_index: Any,
    bm25_ready: bool,
    save_index: Any,
    save_bm25: Any,
    coordination: Any,
) -> dict[str, Any]:
    """Remove a document and all its chunks, annotations, and index entries."""
    document_path = normalize_path(document_path)
    check_document_ownership(
        document_path, agent_id, do_get_lock_status, do_record_conflict
    )
    result = storage.remove_document(document_path)
    if result.get("removed"):
        for chunk_id in result.get("chunk_ids", []):
            vector_index.remove_chunk(chunk_id)
            if bm25_ready and bm25_index is not None:
                bm25_index.remove_document(chunk_id)
        save_index()
        save_bm25()
        if coordination:
            coordination.notify_change(document_path, "removed", agent_id or "")
    return result
