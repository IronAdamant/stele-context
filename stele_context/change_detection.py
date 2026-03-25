"""
Change detection helpers for the Stele engine.

All functions receive engine state as explicit parameters — no imports
back to engine.py, no circular dependencies.
"""

from __future__ import annotations

from typing import Any

from stele_context.chunkers.numpy_compat import (
    cosine_similarity,
    sig_from_bytes,
    sig_to_list,
)

from stele_context.engine_utils import file_unchanged
from stele_context.indexing import (
    merge_similar_chunks,
    persist_chunks,
    remove_stale_chunks,
    check_document_ownership,
    expand_paths,
)


def classify_chunks_for_change(
    new_chunks: list,
    old_chunks_meta: list,
    modality: str,
    doc_path: str,
    results: dict[str, Any],
    change_threshold: float,
    modality_thresholds: dict[str, dict[str, float]],
    vector_index: Any,
) -> None:
    """Compare new chunks against old metadata; update results counters."""
    change_thresh = modality_thresholds.get(modality, {}).get(
        "change", change_threshold
    )

    old_by_pos: dict = {}
    for meta in old_chunks_meta:
        old_by_pos[(meta["start_pos"], meta["end_pos"])] = meta

    for new_chunk in new_chunks:
        old_meta = old_by_pos.get((new_chunk.start_pos, new_chunk.end_pos))

        if old_meta is None:
            search_results = vector_index.search(
                sig_to_list(new_chunk.semantic_signature), k=1
            )
            if search_results and search_results[0][1] >= change_thresh:
                results["kv_restored"] += 1
            else:
                results["new"].append(
                    {
                        "path": doc_path,
                        "chunk_id": new_chunk.chunk_id,
                        "reason": "New chunk",
                    }
                )
                results["kv_reprocessed"] += 1
        elif new_chunk.content_hash == old_meta["content_hash"]:
            results["kv_restored"] += 1
        else:
            old_sig = sig_from_bytes(old_meta["semantic_signature"])
            similarity = cosine_similarity(old_sig, new_chunk.semantic_signature)
            if similarity >= change_thresh:
                results["kv_restored"] += 1
            else:
                results["kv_reprocessed"] += 1


def detect_changes_unlocked(
    session_id: str,
    document_paths: list[str] | None,
    reason: str | None,
    agent_id: str | None,
    *,
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
    change_threshold: float,
    modality_thresholds: dict[str, dict[str, float]],
    do_get_lock_status: Any,
    do_record_conflict: Any,
    save_index: Any,
    save_bm25: Any,
    coordination: Any,
    scan_new: bool = False,
    project_root: Any = None,
    skip_dirs: set[str] | None = None,
) -> dict[str, Any]:
    """Core body of detect_changes_and_update, extracted for engine delegation."""
    storage.create_session(session_id, agent_id=agent_id)

    results: dict[str, Any] = {
        "unchanged": [],
        "modified": [],
        "new": [],
        "removed": [],
        "conflicts": [],
        "kv_restored": 0,
        "kv_reprocessed": 0,
    }

    if document_paths is None:
        all_chunks = storage.search_chunks()
        document_paths = list({c["document_path"] for c in all_chunks})
        if scan_new and project_root is not None:
            dirs = skip_dirs if skip_dirs is not None else set()
            expanded = expand_paths(
                [str(project_root)],
                chunkers,
                dirs,
                normalize_path,
            )
            indexed_set = {d["document_path"] for d in storage.get_all_documents()}
            for p in expanded:
                if p not in indexed_set:
                    results["new"].append(
                        {"path": p, "reason": "New file (scan)"},
                    )
    else:
        document_paths = [normalize_path(p) for p in document_paths]

    session = storage.get_session(session_id)

    for doc_path in document_paths:
        abs_path = resolve_path(doc_path)

        if not abs_path.exists():
            results["removed"].append(doc_path)
            # Inline removal to avoid re-acquiring write lock
            rm_result = storage.remove_document(doc_path)
            if rm_result.get("removed"):
                for cid in rm_result.get("chunk_ids", []):
                    vector_index.remove_chunk(cid)
                    if bm25_ready and bm25_index is not None:
                        bm25_index.remove_document(cid)
            continue

        try:
            check_document_ownership(
                doc_path, agent_id, do_get_lock_status, do_record_conflict
            )
        except PermissionError as e:
            results["conflicts"].append({"path": doc_path, "error": str(e)})
            continue

        stored_doc = storage.get_document(doc_path)
        if stored_doc is None:
            results["new"].append({"path": doc_path, "reason": "Not indexed"})
            continue

        # Fast-path: skip full read if mtime+size unchanged
        if file_unchanged(abs_path, stored_doc):
            results["unchanged"].append(doc_path)
            chunks = storage.get_document_chunks(doc_path)
            if session and session["turn_count"] > 0:
                for chunk_meta in chunks:
                    kv_data = storage.load_kv_state(
                        session_id,
                        chunk_meta["chunk_id"],
                        session["turn_count"] - 1,
                    )
                    if kv_data is not None:
                        results["kv_restored"] += 1
            continue

        try:
            modality = detect_modality(str(abs_path))
            content, content_hash = read_and_hash(abs_path, modality)
        except (OSError, UnicodeDecodeError, ValueError):
            results["modified"].append({"path": doc_path, "reason": "Read error"})
            continue

        if stored_doc["content_hash"] == content_hash:
            results["unchanged"].append(doc_path)
            chunks = storage.get_document_chunks(doc_path)
            if session and session["turn_count"] > 0:
                for chunk_meta in chunks:
                    kv_data = storage.load_kv_state(
                        session_id,
                        chunk_meta["chunk_id"],
                        session["turn_count"] - 1,
                    )
                    if kv_data is not None:
                        results["kv_restored"] += 1
        else:
            results["modified"].append(
                {
                    "path": doc_path,
                    "old_hash": stored_doc["content_hash"][:16],
                    "new_hash": content_hash[:16],
                }
            )
            old_chunks_meta = storage.get_document_chunks(doc_path)

            # Re-chunk, inject cached sigs, merge, classify
            chunker = chunkers.get(modality, chunkers["text"])
            sig_cache = {
                c["content_hash"]: c["semantic_signature"] for c in old_chunks_meta
            }
            new_chunks = chunker.chunk(content, doc_path)
            for nc in new_chunks:
                cached = sig_cache.get(nc.content_hash)
                if cached is not None:
                    nc._semantic_signature = sig_from_bytes(cached)
            new_chunks = merge_similar_chunks(
                new_chunks, merge_threshold, max_chunk_size, modality_thresholds
            )

            classify_chunks_for_change(
                new_chunks,
                old_chunks_meta,
                modality,
                doc_path,
                results,
                change_threshold,
                modality_thresholds,
                vector_index,
            )

            # Persist updated chunks and clean up stale ones
            persist_chunks(
                new_chunks, doc_path, storage, vector_index, bm25_index, bm25_ready
            )
            symbol_manager.extract_document_symbols(doc_path, new_chunks)
            old_chunk_ids = {m["chunk_id"] for m in old_chunks_meta}
            new_chunk_ids = {c.chunk_id for c in new_chunks}
            remove_stale_chunks(
                old_chunk_ids,
                new_chunk_ids,
                vector_index,
                bm25_index,
                bm25_ready,
                storage,
            )

            st = abs_path.stat()
            storage.store_document(
                document_path=doc_path,
                content_hash=content_hash,
                chunk_count=len(new_chunks),
                last_modified=st.st_mtime,
                file_size=st.st_size,
            )
            storage.increment_doc_version(doc_path)

    if results["modified"] or results["removed"]:
        save_index()
        save_bm25()
        modified_chunk_ids: set = set()
        for doc_info in results["modified"]:
            for c in storage.get_document_chunks(doc_info["path"]):
                modified_chunk_ids.add(c["chunk_id"])
        symbol_manager.rebuild_edges(affected_chunk_ids=modified_chunk_ids or None)
        if modified_chunk_ids:
            symbol_manager.propagate_staleness(modified_chunk_ids)

    storage.record_change(summary=results, session_id=session_id, reason=reason)

    # Notify other agents about changes
    if coordination:
        changes = []
        for d in results.get("modified", []):
            changes.append((d["path"], "modified"))
        for path in results.get("removed", []):
            changes.append((path, "removed"))
        if changes:
            coordination.notify_changes_batch(
                changes,
                agent_id or "",
            )

    return results
