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
from stele_context.storage_schema import connect


# ---------------------------------------------------------------------------
# Semantic search quality helpers
# ---------------------------------------------------------------------------


def _compute_rank_disagreement(
    hnsw_scores: dict[str, float],
    bm25_scores: dict[str, float],
    top_k: int,
) -> float:
    """Return 0.0 (rankings agree) → 1.0 (complete disagreement).

    Measures what fraction of HNSW's top-k is absent from BM25's top-k.
    High disagreement means HNSW retrieved structurally-similar but
    keyword-irrelevant chunks; we should fall back to BM25.
    """
    if not hnsw_scores or not bm25_scores:
        return 0.0

    hnsw_top = {
        cid
        for cid, _ in sorted(hnsw_scores.items(), key=lambda x: x[1], reverse=True)[
            :top_k
        ]
    }
    bm25_top = {
        cid
        for cid, _ in sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)[
            :top_k
        ]
    }

    overlap = len(hnsw_top & bm25_top)
    disagreement = 1.0 - (overlap / max(len(hnsw_top), 1))
    return disagreement


def _has_clear_hnsw_winner(
    hnsw_scores: dict[str, float],
    bm25_scores: dict[str, float],
    top_k: int = 3,
) -> bool:
    """Return True if HNSW has a clear top result that BM25 barely acknowledges.

    When HNSW shows a strong structural winner that BM25 doesn't confirm,
    the winner is likely a structural false positive (similar bracket ratios,
    line counts etc.) rather than genuinely relevant content.
    """
    if not hnsw_scores or len(hnsw_scores) < 2:
        return False

    sorted_hnsw = sorted(hnsw_scores.values(), reverse=True)
    top = sorted_hnsw[0]
    median_rest = sorted_hnsw[top_k] if len(sorted_hnsw) > top_k else sorted_hnsw[-1]
    gap = top - median_rest
    if gap <= _SCORE_GAP_THRESHOLD:
        return False

    # The clear winner must appear in BM25's top results to be trusted
    hnsw_winner = max(hnsw_scores.items(), key=lambda x: x[1])[0]
    bm25_top_5 = {
        cid
        for cid, _ in sorted(bm25_scores.items(), key=lambda x: x[1], reverse=True)[:5]
    }
    return hnsw_winner not in bm25_top_5


def _proximity_score(content: str, query_terms: list[str]) -> float:
    """Score chunks by how query terms co-occur near each other in the content.

    - First-occurrence bonus: earlier occurrence of key terms = better
    - Co-occurrence: rare query terms appearing close together = much better
    - Rare-term bonus: terms with high IDF that appear = high signal

    This catches cases where e.g. "allergen" and "dietary compliance" appear
    in the same paragraph (domain-relevant) vs. just somewhere in the file.
    """
    if not query_terms or not content:
        return 0.0

    content_lower = content.lower()
    first_positions = [content_lower.find(t) for t in query_terms]
    present = [i for i, pos in enumerate(first_positions) if pos >= 0]

    if not present:
        return 0.0

    # First-occurrence score (earlier = better, normalized 0-1)
    min_pos = min(first_positions[i] for i in present)
    first_score = 1.0 - min(min_pos / max(len(content), 1), 1.0)

    # IDF-weighted co-occurrence: how many query terms appear, rare ones weighted more.
    # Approximate with term length (longer = rarer) since no corpus IDF is available.
    idf_weights = [min(len(t) / 8.0, 1.0) for t in query_terms]
    present_weighted = sum(idf_weights[i] for i in present) / max(sum(idf_weights), 1.0)

    # Proximity: penalize when terms are far apart in the content
    positions = [first_positions[i] for i in present]
    max_dist = max(positions) - min(positions) if len(positions) > 1 else 0
    # Normalize to ~1000 chars = full penalty
    proximity_score = 1.0 - min(max_dist / 1000.0, 1.0)

    # Combined: co-occurrence (40%) + first occurrence (30%) + proximity (30%)
    return 0.4 * present_weighted + 0.3 * first_score + 0.3 * proximity_score


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

# Thresholds for the rank-disagreement fallback
# HNSW/BM25 disagreement at which BM25 is trusted over HNSW (0.5 = up to 2/5 overlap)
_RANK_DISAGREEMENT_THRESHOLD = 0.5
# Score gap above which HNSW has a "clear winner" that BM25 should confirm
_SCORE_GAP_THRESHOLD = 0.15
# Raw cosine spread in the HNSW candidate set below this ⇒ all hits equally
# "structurally similar" (RecipeLab-style ~0.69 scores); trust BM25 instead.
_FLAT_HNSW_SPAN_THRESHOLD = 0.035
# When HNSW's best raw cosine is below this, the query matches nothing strongly
# in vector space (weak semantic signal); prefer BM25 keyword ranking.
_LOW_HNSW_TOP_RAW_THRESHOLD = 0.70


def _doc_matches_path_prefix(doc_path: str, prefix: str | None) -> bool:
    """True if document_path is under prefix (path prefix, not substring)."""
    if not prefix:
        return True
    d = doc_path.replace("\\", "/")
    p = prefix.replace("\\", "/").rstrip("/")
    return d == p or d.startswith(p + "/")


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


def extract_proximity_terms(query: str) -> list[str]:
    """Terms for lexical proximity re-ranking (domain / prose queries).

    Superset of identifier-like tokens: also includes whole words (length ≥ 4)
    from the query so natural-language feature descriptions (e.g. RecipeLab MCP
    challenge queries) get co-occurrence boosts when those words appear in text,
    not only CamelCase or snake_case symbols.
    """
    terms: set[str] = {t.lower() for t in extract_query_identifiers(query)}
    for w in re.findall(r"\b[a-zA-Z]{4,}\b", query):
        wl = w.lower()
        if wl not in _QUERY_STOP_WORDS:
            terms.add(wl)
    return list(terms)


def compute_search_alpha(query: str, base_alpha: float) -> float:
    """Auto-tune blend weight based on query characteristics.

    Code-like queries (identifiers, brackets, keywords) get lower
    alpha to weight keyword matching more heavily via BM25.
    Natural-language queries strongly favor BM25 so domain wording is not
    drowned out by statistical fingerprints that align with generic code shape.
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
    # Natural-language: lower alpha further than before (RecipeLab validation:
    # structural similarity is a poor proxy for topical relevance).
    return max(0.08, base_alpha - 0.40)


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
    search_mode: str = "hybrid",
    max_result_tokens: int | None = None,
    compact: bool = False,
    return_response_meta: bool = False,
    path_prefix: str | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Hybrid (HNSW+BM25) or keyword-only (BM25) search across chunks."""
    mode = (search_mode or "hybrid").strip().lower()
    if mode not in ("hybrid", "keyword"):
        mode = "hybrid"

    do_ensure_bm25()
    bm25_index = get_bm25()
    if bm25_index is None:
        raise RuntimeError("BM25 index not initialized")

    rank_limit = top_k * 10 if path_prefix else top_k

    bm25_fallback = mode == "keyword"
    tier2_ids: set[str] = set()
    if mode == "keyword":
        bm25_ranked = bm25_index.search(query, top_k=max(1, max(top_k, rank_limit) * 2))
        candidate_ids = list(dict.fromkeys(cid for cid, _ in bm25_ranked))
        if not candidate_ids:
            return []
        bm25_scores = bm25_index.score_batch(query, candidate_ids)
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 0.0
        if max_bm25 > 0:
            bm25_norm = {k: v / max_bm25 for k, v in bm25_scores.items()}
        else:
            bm25_norm = bm25_scores
        combined = {cid: bm25_norm.get(cid, 0.0) for cid in candidate_ids}
        ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:rank_limit]
    else:
        query_sig = _text_signature(query)

        # Widen HNSW candidate set for re-ranking
        hnsw_results = vector_index.search(query_sig, k=top_k * 3)

        hnsw_scores = dict(hnsw_results) if hnsw_results else {}
        hnsw_span_raw = 0.0
        if hnsw_scores:
            hnsw_span_raw = max(hnsw_scores.values()) - min(hnsw_scores.values())
        # Independent BM25 top-k so keyword-relevant chunks are not missed when
        # HNSW (statistical signatures) returns unrelated neighbours.
        bm25_ranked = bm25_index.search(query, top_k=max(1, top_k * 2))
        bm25_only_ids = {cid for cid, _ in bm25_ranked}
        candidate_ids = list(
            dict.fromkeys(list(hnsw_scores.keys()) + list(bm25_only_ids))
        )
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
                hnsw_norm = {
                    k: (v - min_hnsw) / hnsw_span for k, v in hnsw_scores.items()
                }
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

        # BM25 fallback: replace blended ranking with pure BM25 when HNSW is
        # misleading the results.
        disagreement = _compute_rank_disagreement(hnsw_scores, bm25_scores, top_k=5)
        has_clear_winner = _has_clear_hnsw_winner(hnsw_scores, bm25_scores)
        flat_hnsw = hnsw_span_raw < _FLAT_HNSW_SPAN_THRESHOLD and len(hnsw_scores) >= 4
        max_hnsw_raw = max(hnsw_scores.values()) if hnsw_scores else 0.0
        low_semantic = max_hnsw_raw < _LOW_HNSW_TOP_RAW_THRESHOLD
        if (
            disagreement >= _RANK_DISAGREEMENT_THRESHOLD
            or has_clear_winner
            or flat_hnsw
            or low_semantic
        ) and max(bm25_scores.values(), default=0.0) > 0.0:
            combined = {cid: bm25_norm.get(cid, 0.0) for cid in candidate_ids}
            bm25_fallback = True

        ranked = sorted(combined.items(), key=lambda x: x[1], reverse=True)[:rank_limit]

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
            "source": "bm25" if bm25_fallback else "hnsw",
            "tier2_present": chunk_id in tier2_ids,
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
            if path_prefix and not _doc_matches_path_prefix(
                chunk_meta["document_path"], path_prefix
            ):
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
                "source": "symbol_boost",
                "tier2_present": cid in tier2_ids,
            }
            symbol_manager.attach_edges(entry, cid)
            results.append(entry)
            existing_ids.add(cid)

        # Re-sort and truncate
        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        results = results[:top_k]

    # Proximity re-ranking: boost chunks where query terms co-occur naturally.
    # For domain-specific queries ("allergen dietary compliance"), chunks where
    # key terms appear near each other in natural prose are more relevant than
    # chunks that just happen to score well on structural fingerprints.
    # Uses extract_proximity_terms (identifiers + length≥4 words) for NL queries.
    proximity_terms = extract_proximity_terms(query)
    if not proximity_terms:
        proximity_terms = [t.lower() for t in query_idents]
    if results and proximity_terms:
        query_terms = proximity_terms
        # Stronger nudge for long prose queries (RecipeLab-style descriptions)
        prox_w = 0.35 if len(proximity_terms) >= 5 else 0.28
        for r in results:
            prox = _proximity_score(r.get("content", ""), query_terms)
            if prox > 0:
                # Blend: mostly keep the hybrid score, add a proximity nudge
                r["relevance_score"] = round(
                    r["relevance_score"] * (1.0 - prox_w) + prox * prox_w,
                    6,
                )
                r["proximity_boosted"] = True
        # Final re-sort after proximity nudge
        results.sort(key=lambda r: r["relevance_score"], reverse=True)
        results = results[:top_k]

    if path_prefix:
        results = [
            r
            for r in results
            if _doc_matches_path_prefix(r.get("document_path", ""), path_prefix)
        ]
        results = results[:top_k]

    from stele_context.agent_response import truncate_search_results

    if return_response_meta or max_result_tokens is not None or compact:
        trimmed, meta = truncate_search_results(
            results,
            max_result_tokens=max_result_tokens,
            compact=compact,
        )
        if return_response_meta:
            return {"results": trimmed, "meta": meta}
        return trimmed
    return results


def get_context_unlocked(
    document_paths: list[str],
    *,
    normalize_path: Any,
    resolve_path: Any,
    detect_modality: Any,
    read_and_hash: Any,
    storage: Any,
    include_trust: bool = True,
    max_chunk_content_tokens: int | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Core get_context logic with optional trust hints for LLM calibration."""
    from stele_context.agent_response import (
        parse_agent_notes_field,
        trim_content_to_token_budget,
    )

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
            new_entry: dict[str, Any] = {"path": doc_path}
            if session_id:
                recent = storage.get_recent_search_for_file(session_id, doc_path)
                new_entry["recently_searched"] = recent is not None
                if recent:
                    new_entry["search_pattern"] = recent["pattern"]
            result["new"].append(new_entry)
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
                content = chunk.get("content")
                trunc = False
                if max_chunk_content_tokens is not None and content:
                    content, trunc = trim_content_to_token_budget(
                        content, max_chunk_content_tokens
                    )
                row: dict[str, Any] = {
                    "chunk_id": chunk["chunk_id"],
                    "content": content,
                    "start_pos": chunk["start_pos"],
                    "end_pos": chunk["end_pos"],
                    "token_count": chunk["token_count"],
                    "content_truncated": trunc,
                }
                ss = chunk.get("staleness_score")
                if ss is not None:
                    row["staleness_score"] = float(ss)
                raw_notes = chunk.get("agent_notes")
                if raw_notes:
                    row["agent_notes"] = parse_agent_notes_field(raw_notes)
                chunk_data.append(row)

            try:
                st = abs_path.stat()
                file_mtime = st.st_mtime
                file_size = st.st_size
            except OSError:
                file_mtime = None
                file_size = None

            max_staleness = max(
                (float(c.get("staleness_score") or 0.0) for c in chunks),
                default=0.0,
            )

            entry: dict[str, Any] = {
                "path": doc_path,
                "chunks": chunk_data,
                "total_tokens": sum(c["token_count"] for c in chunk_data),
            }
            if include_trust:
                entry["trust"] = {
                    "document_indexed_at": stored_doc["indexed_at"],
                    "stored_last_modified": stored_doc["last_modified"],
                    "file_mtime": file_mtime,
                    "file_size": file_size,
                    "cache_aligned_with_disk": (
                        file_size is not None
                        and stored_doc.get("file_size") is not None
                        and file_mtime == stored_doc["last_modified"]
                        and file_size == stored_doc["file_size"]
                    ),
                    "max_chunk_staleness": max_staleness,
                    "staleness_hint": max_staleness >= 0.3,
                }
            # Add recently_searched info if session_id provided
            if session_id:
                recent = storage.get_recent_search_for_file(session_id, doc_path)
                entry["recently_searched"] = recent is not None
                if recent:
                    entry["search_pattern"] = recent["pattern"]
            result["unchanged"].append(entry)
        else:
            chg: dict[str, Any] = {
                "path": doc_path,
                "old_hash": stored_doc["content_hash"][:16],
                "new_hash": content_hash[:16],
            }
            if include_trust:
                try:
                    st = abs_path.stat()
                    chg["trust"] = {
                        "file_mtime": st.st_mtime,
                        "file_size": st.st_size,
                        "indexed_cache_outdated": True,
                    }
                except OSError:
                    chg["trust"] = {"indexed_cache_outdated": True}
            result["changed"].append(chg)

    return result


def get_map_unlocked(
    storage: Any,
    *,
    compact: bool = False,
    max_documents: int | None = None,
    max_annotation_chars: int = 200,
    path_prefix: str | None = None,
) -> dict[str, Any]:
    """Build project overview: all documents with chunk counts and annotations."""
    documents = storage.get_all_documents()
    result, total_tokens = [], 0
    for doc in documents:
        if path_prefix and not _doc_matches_path_prefix(
            doc["document_path"], path_prefix
        ):
            continue
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
    data: dict[str, Any] = {
        "documents": result,
        "total_documents": len(result),
        "total_tokens": total_tokens,
        "index_health": storage.get_index_health_snapshot(),
    }
    if compact:
        from stele_context.agent_response import compact_map_payload

        data = compact_map_payload(
            data,
            max_documents=max_documents,
            max_annotation_chars=max_annotation_chars,
        )
    return data


def get_project_brief_unlocked(storage: Any, top_n: int = 40) -> dict[str, Any]:
    """Token-efficient orientation: largest files, extension counts, totals."""
    from stele_context.agent_response import build_project_brief

    return build_project_brief(storage, top_n=top_n)


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


def bulk_store_embeddings_unlocked(
    embeddings: dict[str, list[float]],
    storage: Any,
    vector_index: Any,
    save_index: Any,
) -> dict[str, Any]:
    """Normalize and store raw embedding vectors for multiple chunks."""
    normalized: dict[str, list[float]] = {}
    for cid, vector in embeddings.items():
        norm = sum(x * x for x in vector) ** 0.5
        if norm > 0:
            normalized[cid] = [x / norm for x in vector]
        else:
            normalized[cid] = vector

    result = storage.bulk_store_agent_signatures(normalized)
    stored_ids = [cid for cid in embeddings if cid not in result.get("errors", [])]
    for cid in stored_ids:
        vector_index.remove_chunk(cid)
        vector_index.add_chunk(cid, normalized[cid])
    if stored_ids:
        save_index()
    return result


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


def get_search_quality_snapshot(storage: Any, vector_index: Any) -> dict[str, Any]:
    """Return diagnostic metrics about search quality and Tier-2 coverage."""
    with connect(storage.db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        tier2 = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE agent_signature IS NOT NULL"
        ).fetchone()[0]
        # Probe query to measure HNSW span
        probe_vec = [0.1] * 128
        hnsw_results = (
            vector_index.search(probe_vec, k=10)
            if hasattr(vector_index, "search")
            else []
        )
        hnsw_scores = [s for _, s in hnsw_results if s is not None]
        hnsw_span = (
            (max(hnsw_scores) - min(hnsw_scores)) if len(hnsw_scores) >= 2 else 0.0
        )

    tier2_pct = (tier2 / max(total, 1)) * 100.0
    advice = None
    if tier2_pct < 5.0:
        advice = (
            "Tier-2 coverage is low. Consider adding summaries or calling "
            "llm_embed for key concepts to improve semantic relevance."
        )

    return {
        "total_chunks": total,
        "tier2_chunks": tier2,
        "tier2_coverage_percent": round(tier2_pct, 2),
        "hnsw_span": round(hnsw_span, 4),
        "advice": advice,
    }


def get_stats_unlocked(
    storage: Any,
    vector_index: Any,
    config: dict,
    *,
    compact: bool = False,
) -> dict[str, Any]:
    """Build stats dict."""
    from stele_context import __version__

    data = {
        "version": __version__,
        "storage": storage.get_storage_stats(),
        "index": vector_index.get_stats(),
        "config": config,
        "index_health": storage.get_index_health_snapshot(),
    }
    if compact:
        from stele_context.agent_response import compact_stats_payload

        return compact_stats_payload(data)
    return data
