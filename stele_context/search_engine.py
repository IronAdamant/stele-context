"""
Search and context retrieval helpers for the Stele engine.

All functions receive engine state as explicit parameters -- no imports
back to engine.py, no circular dependencies.
"""

from __future__ import annotations

import re
import struct
import threading
from typing import Any

from stele_context.chunkers.base import Chunk
from stele_context.chunkers.numpy_compat import sig_to_list
from stele_context.engine_utils import file_unchanged
from stele_context.index_store import (
    compute_chunk_ids_hash,
    save_bm25 as _save_bm25_store,
    load_bm25_if_fresh,
)
from stele_context.index import VectorIndex


# Multiplicative boost for chunks with Tier 2 agent-supplied signatures.
# Agent summaries encode human-readable semantics ("JWT auth middleware") which
# produce more query-relevant signatures than statistical fingerprints alone.
# Without this boost, the statistical Tier 1 signal can drown out Tier 2,
# causing summary-enriched chunks to rank below irrelevant matches.
TIER2_BOOST = 1.3

_QUERY_STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "have",
        "are",
        "was",
        "not",
        "all",
        "but",
        "how",
    }
)


def _text_signature(text: str) -> list[float]:
    """Compute a semantic signature from a text string."""
    return sig_to_list(
        Chunk(
            content=text,
            modality="text",
            start_pos=0,
            end_pos=len(text),
            document_path="<query>",
        ).semantic_signature
    )


def extract_query_identifiers(query: str) -> list[str]:
    """Extract identifier-like tokens from a search query.

    Splits on whitespace, underscores, and camelCase boundaries.
    Returns unique tokens >= 3 chars, suitable for symbol name matching.
    """
    parts = re.findall(
        r"[A-Z]?[a-z]{2,}|[A-Z]{2,}(?=[A-Z][a-z]|\b)|[a-z_]\w{2,}", query
    )
    full = re.findall(r"[a-zA-Z_]\w{2,}", query)
    tokens = set(parts + full)
    return [t for t in tokens if t.lower() not in _QUERY_STOP_WORDS]


def compute_search_alpha(query: str, base_alpha: float) -> float:
    """Auto-tune blend weight based on query characteristics.

    Code-like queries (identifiers, brackets, keywords) get lower
    alpha to weight keyword matching more heavily via BM25.
    Natural-language queries keep or raise alpha so the HNSW
    statistical signal can complement BM25 keyword matches.
    """
    signals = sum(
        (
            "_" in query,
            bool(re.search(r"[A-Z][a-z]+[A-Z]", query)),
            any(c in query for c in "{}[]();"),
            bool(
                re.search(
                    r"\b(def|class|function|import|const|let|var|fn|pub)\b", query
                )
            ),
            "." in query and not query.endswith("."),
        )
    )
    if signals >= 3:
        # Heavy code signals: rely on BM25 for identifier matching.
        return max(0.3, base_alpha - 0.3)
    if signals >= 1:
        # Moderate code signals: slightly favor BM25.
        return max(0.3, base_alpha - 0.15)
    # Natural-language: keep base_alpha so HNSW can complement BM25.
    # For pure keyword queries (no semantic intent), BM25 dominates.
    return base_alpha


def ensure_bm25(
    storage: Any,
    bm25_init_lock: threading.Lock,
    get_bm25_ready: Any,
    set_bm25: Any,
) -> None:
    """Lazily initialize BM25 index -- load from disk or rebuild.

    Uses double-checked locking so concurrent readers don't race
    during initialization.

    Args:
        storage: StorageBackend instance.
        bm25_init_lock: threading.Lock for double-checked init.
        get_bm25_ready: callable returning (bm25_ready, bm25_index).
        set_bm25: callable(bm25_index, ready=True) to update engine state.
    """
    ready, _ = get_bm25_ready()
    if ready:
        return
    with bm25_init_lock:
        ready, _ = get_bm25_ready()
        if ready:
            return
        from stele_context.bm25 import BM25Index

        # Try loading persisted BM25 index
        current_hash = compute_chunk_ids_hash(storage)
        loaded = load_bm25_if_fresh(storage.index_dir, current_hash)
        if loaded is not None:
            set_bm25(loaded, True)
            return

        # Rebuild from SQLite
        bm25 = BM25Index()
        for chunk in storage.search_chunks():
            content = chunk.get("content")
            if content:
                bm25.add_document(chunk["chunk_id"], content)
        set_bm25(bm25, True)
        _save_bm25_store(bm25, current_hash, storage.index_dir)


def save_bm25(
    bm25_index: Any,
    bm25_ready: bool,
    storage: Any,
) -> None:
    """Persist BM25 index alongside HNSW."""
    if bm25_ready and bm25_index is not None:
        current_hash = compute_chunk_ids_hash(storage)
        _save_bm25_store(bm25_index, current_hash, storage.index_dir)


def search_unlocked(
    query: str,
    top_k: int,
    *,
    vector_index: Any,
    storage: Any,
    get_bm25: Any,
    search_alpha: float,
    symbol_manager: Any,
    do_ensure_bm25: Any,
) -> list[dict[str, Any]]:
    """Core hybrid semantic + keyword search logic."""
    query_sig = _text_signature(query)

    # Widen HNSW candidate set for re-ranking
    hnsw_results = vector_index.search(query_sig, k=top_k * 3)

    # BM25 re-ranking -- ensure_bm25 may initialize the index
    do_ensure_bm25()
    bm25_index = get_bm25()
    if bm25_index is None:
        raise RuntimeError("BM25 index not initialized")

    hnsw_scores = dict(hnsw_results) if hnsw_results else {}
    # Independent BM25 top-k so keyword-relevant chunks are not missed when
    # HNSW (statistical signatures) returns unrelated neighbours.
    bm25_ranked = bm25_index.search(query, top_k=max(1, top_k * 2))
    bm25_only_ids = {cid for cid, _ in bm25_ranked}
    candidate_ids = list(dict.fromkeys(list(hnsw_scores.keys()) + list(bm25_only_ids)))
    if not candidate_ids:
        return []

    bm25_scores = bm25_index.score_batch(query, candidate_ids)

    # Normalize HNSW cosine scores within candidate set.
    # Statistical signatures share structural features, producing clustered
    # cosine values (typically 0.6-1.0).  Min-max scaling widens the
    # effective range so blending with BM25 produces more differentiated scores.
    if hnsw_scores:
        max_hnsw = max(hnsw_scores.values())
        min_hnsw = min(hnsw_scores.values())
        hnsw_span = max_hnsw - min_hnsw
        if hnsw_span > 0:
            hnsw_norm = {k: (v - min_hnsw) / hnsw_span for k, v in hnsw_scores.items()}
        else:
            hnsw_norm = {k: 1.0 for k in hnsw_scores}
    else:
        hnsw_norm = hnsw_scores

    # Normalize BM25 scores to [0, 1]
    max_bm25 = max(bm25_scores.values()) if bm25_scores else 0.0
    if max_bm25 > 0:
        bm25_norm = {k: v / max_bm25 for k, v in bm25_scores.items()}
    else:
        bm25_norm = bm25_scores

    # Identify Tier 2 chunks (agent-supplied signatures) for boost
    tier2_ids = storage.has_agent_signatures(candidate_ids)

    # Blend: alpha * vector + (1 - alpha) * keyword
    alpha = compute_search_alpha(query, search_alpha)
    combined = {}
    for cid in candidate_ids:
        vec_score = hnsw_norm.get(cid, 0.0)
        kw_score = bm25_norm.get(cid, 0.0)
        if cid in tier2_ids:
            vec_score *= TIER2_BOOST
        combined[cid] = alpha * vec_score + (1.0 - alpha) * kw_score

    # BM25 fallback: when top HNSW similarity is near zero the query doesn't
    # match the semantic structure of the codebase well.  Fall back to pure BM25
    # so natural-language queries like "allergen dietary compliance" aren't
    # drowned out by structurally-similar but semantically-irrelevant chunks.
    top_hnsw = max(hnsw_scores.values()) if hnsw_scores else 0.0
    if top_hnsw < 0.1 and max(bm25_scores.values(), 0.0) > 0.0:
        # Pure BM25 ranking for this candidate set.
        combined = {cid: bm25_norm.get(cid, 0.0) for cid in candidate_ids}

    ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:top_k]

    results = []
    for chunk_id, score in ranked:
        chunk_meta = storage.get_chunk(chunk_id)
        if chunk_meta is None:
            continue

        content = storage.get_chunk_content(chunk_id)

        entry: dict[str, Any] = {
            "chunk_id": chunk_id,
            "content": content,
            "document_path": chunk_meta["document_path"],
            "relevance_score": float(score),
            "token_count": chunk_meta["token_count"],
            "start_pos": chunk_meta["start_pos"],
            "end_pos": chunk_meta["end_pos"],
        }

        symbol_manager.attach_edges(entry, chunk_id)
        results.append(entry)

    # Symbol-boosted search: find chunks defining symbols that match
    # query identifiers but weren't found by HNSW+BM25
    existing_ids = {r["chunk_id"] for r in results}
    query_idents = extract_query_identifiers(query)
    if query_idents:
        sym_matches = storage.search_symbol_names(query_idents)
        min_score = results[-1]["relevance_score"] if results else 0.1
        for sym in sym_matches:
            cid = sym["chunk_id"]
            if cid in existing_ids:
                continue
            chunk_meta = storage.get_chunk(cid)
            if chunk_meta is None:
                continue
            content = storage.get_chunk_content(cid)
            entry = {
                "chunk_id": cid,
                "content": content,
                "document_path": sym["document_path"],
                "relevance_score": round(min_score * 0.85, 4),
                "token_count": chunk_meta["token_count"],
                "start_pos": chunk_meta["start_pos"],
                "end_pos": chunk_meta["end_pos"],
                "symbol_match": sym["name"],
            }
            symbol_manager.attach_edges(entry, cid)
            results.append(entry)
            existing_ids.add(cid)

        # Re-sort and truncate
        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        results = results[:top_k]

    return results


def get_context_unlocked(
    document_paths: list[str],
    *,
    normalize_path: Any,
    resolve_path: Any,
    detect_modality: Any,
    read_and_hash: Any,
    storage: Any,
) -> dict[str, Any]:
    """Core get_context logic."""
    document_paths = [normalize_path(p) for p in document_paths]

    result: dict[str, Any] = {
        "unchanged": [],
        "changed": [],
        "new": [],
    }

    for doc_path in document_paths:
        abs_path = resolve_path(doc_path)

        if not abs_path.exists():
            continue

        stored_doc = storage.get_document(doc_path)
        if stored_doc is None:
            result["new"].append({"path": doc_path})
            continue

        # Fast-path: skip full read if mtime+size unchanged
        fast_unchanged = file_unchanged(abs_path, stored_doc)
        if not fast_unchanged:
            try:
                modality = detect_modality(str(abs_path))
                _, content_hash = read_and_hash(abs_path, modality)
            except (OSError, UnicodeDecodeError, ValueError):
                result["changed"].append({"path": doc_path, "reason": "Read error"})
                continue
            hash_unchanged = stored_doc["content_hash"] == content_hash
        else:
            hash_unchanged = True

        if hash_unchanged:
            chunks = storage.search_chunks(document_path=doc_path)
            chunk_data = []
            for chunk in chunks:
                chunk_data.append(
                    {
                        "chunk_id": chunk["chunk_id"],
                        "content": chunk.get("content"),
                        "start_pos": chunk["start_pos"],
                        "end_pos": chunk["end_pos"],
                        "token_count": chunk["token_count"],
                    }
                )
            result["unchanged"].append(
                {
                    "path": doc_path,
                    "chunks": chunk_data,
                    "total_tokens": sum(c["token_count"] for c in chunk_data),
                }
            )
        else:
            result["changed"].append(
                {
                    "path": doc_path,
                    "old_hash": stored_doc["content_hash"][:16],
                    "new_hash": content_hash[:16],
                }
            )

    return result


def get_map_unlocked(storage: Any) -> dict[str, Any]:
    """Build project overview: all documents with chunk counts and annotations."""
    documents = storage.get_all_documents()
    result, total_tokens = [], 0
    for doc in documents:
        chunks = storage.search_chunks(document_path=doc["document_path"])
        doc_tokens = sum(c["token_count"] for c in chunks)
        total_tokens += doc_tokens
        anns = storage.get_annotations(
            target=doc["document_path"], target_type="document"
        )
        result.append(
            {
                "path": doc["document_path"],
                "chunk_count": doc["chunk_count"],
                "total_tokens": doc_tokens,
                "indexed_at": doc["indexed_at"],
                "annotations": [
                    {"id": a["id"], "content": a["content"], "tags": a["tags"]}
                    for a in anns
                ],
            }
        )
    return {
        "documents": result,
        "total_documents": len(result),
        "total_tokens": total_tokens,
    }


def store_semantic_summary_unlocked(
    chunk_id: str,
    summary: str,
    storage: Any,
    vector_index: Any,
    save_index: Any,
) -> dict[str, Any]:
    """Compute signature from agent summary, update HNSW index."""
    sig = _text_signature(summary)
    ok = storage.store_semantic_summary(chunk_id, summary, sig)
    if not ok:
        return {"stored": False, "error": "chunk not found"}
    vector_index.remove_chunk(chunk_id)
    vector_index.add_chunk(chunk_id, sig)
    save_index()
    return {"stored": True, "chunk_id": chunk_id}


def bulk_store_summaries_unlocked(
    summaries: dict[str, str],
    storage: Any,
    vector_index: Any,
    save_index: Any,
) -> dict[str, Any]:
    """Batch-store per-chunk semantic summaries, update HNSW index.

    Args:
        summaries: Mapping of chunk_id to semantic summary text.
    """
    stored = 0
    errors: list[str] = []
    for chunk_id, summary in summaries.items():
        sig = _text_signature(summary)
        ok = storage.store_semantic_summary(chunk_id, summary, sig)
        if ok:
            vector_index.remove_chunk(chunk_id)
            vector_index.add_chunk(chunk_id, sig)
            stored += 1
        else:
            errors.append(chunk_id)
    if stored:
        save_index()
    return {"stored": stored, "errors": errors, "total": len(summaries)}


def store_embedding_unlocked(
    chunk_id: str,
    vector: list[float],
    storage: Any,
    vector_index: Any,
    save_index: Any,
) -> dict[str, Any]:
    """Normalize and store raw embedding vector, update HNSW index."""
    norm = sum(x * x for x in vector) ** 0.5
    if norm > 0:
        vector = [x / norm for x in vector]
    ok = storage.store_agent_signature(chunk_id, vector)
    if not ok:
        return {"stored": False, "error": "chunk not found"}
    vector_index.remove_chunk(chunk_id)
    vector_index.add_chunk(chunk_id, vector)
    save_index()
    return {"stored": True, "chunk_id": chunk_id}


def load_or_rebuild_index(storage: Any) -> VectorIndex:
    """Load persisted index if fresh, otherwise rebuild from SQLite."""
    from stele_context.chunkers.numpy_compat import sig_from_bytes
    from stele_context.index_store import load_if_fresh, save_index as _save_idx

    current_hash = compute_chunk_ids_hash(storage)
    index = load_if_fresh(storage.index_dir, current_hash)
    if index is not None:
        return index
    index = VectorIndex()
    for chunk in storage.search_chunks():
        try:
            raw_sig = chunk.get("agent_signature") or chunk["semantic_signature"]
            sig = sig_from_bytes(raw_sig)
            index.add_chunk(chunk["chunk_id"], sig_to_list(sig))
        except (TypeError, ValueError, KeyError, struct.error):
            continue
    _save_idx(index, current_hash, storage.index_dir)
    return index


def init_chunkers(chunk_size: int, max_chunk_size: int) -> dict[str, Any]:
    """Initialize modality-specific chunkers."""
    from stele_context.chunkers import (
        TextChunker,
        CodeChunker,
        ImageChunker,
        PDFChunker,
        AudioChunker,
        VideoChunker,
        HAS_IMAGE_CHUNKER,
        HAS_PDF_CHUNKER,
        HAS_AUDIO_CHUNKER,
        HAS_VIDEO_CHUNKER,
    )

    chunkers: dict[str, Any] = {
        "text": TextChunker(chunk_size=chunk_size, max_chunk_size=max_chunk_size),
        "code": CodeChunker(chunk_size=chunk_size, max_chunk_size=max_chunk_size),
    }
    if HAS_IMAGE_CHUNKER:
        chunkers["image"] = ImageChunker()
    if HAS_PDF_CHUNKER:
        chunkers["pdf"] = PDFChunker(
            chunk_size=chunk_size, max_chunk_size=max_chunk_size
        )
    if HAS_AUDIO_CHUNKER:
        chunkers["audio"] = AudioChunker()
    if HAS_VIDEO_CHUNKER:
        chunkers["video"] = VideoChunker()
    return chunkers


def get_stats_unlocked(storage: Any, vector_index: Any, config: dict) -> dict[str, Any]:
    """Build stats dict."""
    from stele_context import __version__

    return {
        "version": __version__,
        "storage": storage.get_storage_stats(),
        "index": vector_index.get_stats(),
        "config": config,
    }
