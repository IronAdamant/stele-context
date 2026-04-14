"""
LLM-optimized code search for Stele.

Like grep, but designed for LLM agents:
- Token budget: returns highest-relevance matches within N tokens
- Scope annotation: every match shows its enclosing function/class
- Classification: comment / import / definition / string / code
- Deduplication: collapses structurally identical matches
- Structured output: JSON with summary-first progressive disclosure

Standalone module. Receives a storage object; no circular imports.
"""

from __future__ import annotations

from typing import Any

from stele_context.chunkers.base import estimate_tokens

import sqlite3

# -- Line-level classification heuristics ------------------------------------

_COMMENT_PREFIXES = ("#", "//", "--", ";", "%", "/*", "* ", "///", "/**")

_IMPORT_PREFIXES = (
    "import ",
    "from ",
    "require(",
    "use ",
    "include ",
    "#include",
    "using ",
    "extern crate ",
)

_DEFINITION_PREFIXES = (
    "def ",
    "async def ",
    "class ",
    "function ",
    "async function ",
    "fn ",
    "pub fn ",
    "func ",
    "type ",
    "interface ",
    "struct ",
    "enum ",
    "trait ",
    "impl ",
)


def _classify_line(line: str) -> str:
    """Classify a source line by its syntactic role."""
    stripped = line.lstrip()
    if not stripped:
        return "blank"
    if any(stripped.startswith(p) for p in _COMMENT_PREFIXES):
        return "comment"
    if any(stripped.startswith(p) for p in _IMPORT_PREFIXES):
        return "import"
    if any(stripped.startswith(p) for p in _DEFINITION_PREFIXES):
        return "definition"
    if stripped.startswith(('"""', "'''", 'r"""', "r'''")):
        return "string"
    return "code"


def _find_enclosing_scope(
    definition_symbols: list[dict[str, Any]],
    line_in_chunk: int,
) -> str | None:
    """Find the closest enclosing definition scope for a line.

    Symbols must be pre-sorted by line_number ascending.
    ``line_in_chunk`` is 1-based (matching AST ``lineno``).
    """
    enclosing = None
    for sym in definition_symbols:
        ln = sym.get("line_number")
        if ln is None:
            continue
        if ln <= line_in_chunk:
            enclosing = sym
        else:
            break
    if enclosing is None:
        return None
    return enclosing.get("name", "")


def _compute_base_lines(all_chunks: list[dict[str, Any]]) -> dict[str, int]:
    """Compute document-relative base line number for each chunk.

    Given all chunks for a document sorted by start_pos, returns
    ``{chunk_id: base_line}`` where base_line is the 1-based line
    number of the chunk's first line in the original document.
    """
    base_lines: dict[str, int] = {}
    cumulative = 1
    for chunk in all_chunks:
        cid = chunk.get("chunk_id", "")
        base_lines[cid] = cumulative
        content = chunk.get("content") or ""
        newlines = content.count("\n")
        cumulative += newlines + (0 if content.endswith("\n") else 1)
    return base_lines


def _normalize_excerpt(text: str) -> str:
    """Normalize an excerpt for deduplication comparison."""
    return " ".join(text.split())


def _truncate(s: str, max_len: int) -> str:
    return s if len(s) <= max_len else s[: max_len - 3] + "..."


# -- Main function -----------------------------------------------------------


def agent_grep(
    storage: Any,
    pattern: str,
    *,
    regex: bool = False,
    document_path: str | None = None,
    classify: bool = True,
    include_scope: bool = True,
    group_by: str = "file",
    max_tokens: int = 4000,
    deduplicate: bool = True,
    context_lines: int = 0,
    session_id: str | None = None,
    auto_index_func: Any = None,
) -> dict[str, Any]:
    """LLM-optimized search across indexed chunks.

    Parameters
    ----------
    storage :
        A ``StorageBackend`` instance.
    pattern :
        Text or regex pattern to search for.
    regex :
        Treat *pattern* as a regular expression.
    document_path :
        Scope search to a specific file.
    classify :
        Tag each match with its syntactic classification.
    include_scope :
        Annotate each match with its enclosing function/class.
    group_by :
        ``"file"`` | ``"scope"`` | ``"classification"``.
    max_tokens :
        Token budget for the result payload.
    deduplicate :
        Collapse structurally identical match lines.
    context_lines :
        Lines of context above/below each match (0 = match line only).
    session_id :
        Optional session ID. When provided, records this search in the
        session's search history and auto-indexes files with matches.
    auto_index_func :
        Optional callable ``(list[str]) -> None``. Called with the list
        of file paths that had matches, so the search act also caches
        those files. No-op if ``None``.

    Returns
    -------
    dict
        ``summary``, ``groups``, ``total_matches``, ``shown_matches``,
        ``truncated``, ``total_tokens``, ``files_checked``,
        ``files_with_matches``.
    """
    try:
        return _agent_grep_impl(
            storage,
            pattern,
            regex=regex,
            document_path=document_path,
            classify=classify,
            include_scope=include_scope,
            group_by=group_by,
            max_tokens=max_tokens,
            deduplicate=deduplicate,
            context_lines=context_lines,
            session_id=session_id,
            auto_index_func=auto_index_func,
        )
    except sqlite3.Error as e:
        return {
            "error": f"Database error during agent_grep: {e}. "
            "Try running rebuild_symbols or detect_changes to repair the index, "
            "or check for concurrent database access.",
            "summary": f"Database error for '{_truncate(pattern, 40)}'",
            "groups": [],
            "total_matches": 0,
            "shown_matches": 0,
            "truncated": 0,
            "total_tokens": 0,
            "files_checked": 0,
            "files_with_matches": [],
        }


def _agent_grep_impl(
    storage: Any,
    pattern: str,
    *,
    regex: bool = False,
    document_path: str | None = None,
    classify: bool = True,
    include_scope: bool = True,
    group_by: str = "file",
    max_tokens: int = 4000,
    deduplicate: bool = True,
    context_lines: int = 0,
    session_id: str | None = None,
    auto_index_func: Any = None,
) -> dict[str, Any]:
    """Internal implementation of agent_grep (separated for error handling)."""
    # 1. Raw text search
    raw_matches = storage.search_text(
        pattern, regex=regex, document_path=document_path, limit=500
    )

    if not raw_matches:
        if session_id:
            storage.record_search(
                session_id=session_id,
                pattern=pattern,
                tool="agent_grep",
                files_checked=[],
                files_with_matches=[],
            )
        return {
            "summary": f"0 matches for '{_truncate(pattern, 40)}'",
            "groups": [],
            "total_matches": 0,
            "shown_matches": 0,
            "truncated": 0,
            "total_tokens": 0,
            "files_checked": 0,
            "files_with_matches": [],
        }

    # 2. Collect chunk IDs and document paths
    chunk_ids = [m["chunk_id"] for m in raw_matches]
    doc_paths = sorted({m["document_path"] for m in raw_matches})

    # 3. Record search provenance and auto-index files with matches
    if session_id:
        storage.record_search(
            session_id=session_id,
            pattern=pattern,
            tool="agent_grep",
            files_checked=doc_paths,
            files_with_matches=doc_paths,
        )
        if auto_index_func:
            auto_index_func(doc_paths)

    # 3. Batch-fetch symbols for scope + classification
    chunk_symbols: dict[str, list[dict[str, Any]]] = {}
    if include_scope or classify:
        all_syms = storage.get_symbols_for_chunks(chunk_ids)
        for sym in all_syms:
            chunk_symbols.setdefault(sym["chunk_id"], []).append(sym)
        for cid in chunk_symbols:
            chunk_symbols[cid].sort(key=lambda s: s.get("line_number") or 0)

    # 4. Compute base line numbers per document
    base_lines: dict[str, int] = {}
    for dp in doc_paths:
        doc_chunks = storage.search_chunks(document_path=dp)
        base_lines.update(_compute_base_lines(doc_chunks))

    # 5. Build enriched matches
    enriched: list[dict[str, Any]] = []
    total_match_count = 0

    for chunk_match in raw_matches:
        cid = chunk_match["chunk_id"]
        doc = chunk_match["document_path"]
        full_content = storage.get_chunk_content(cid) or ""
        lines = full_content.split("\n")
        base_line = base_lines.get(cid, 1)

        chunk_defs = [
            s
            for s in chunk_symbols.get(cid, [])
            if s["role"] == "definition"
            and s["kind"] in ("function", "class", "method")
        ]

        for match_info in chunk_match.get("matches", []):
            total_match_count += 1
            char_pos = match_info["start"]

            # Line within chunk (0-based for indexing, 1-based for symbols)
            line_idx = full_content[:char_pos].count("\n")
            doc_line = base_line + line_idx

            # Extract excerpt with optional context
            start_idx = max(0, line_idx - context_lines)
            end_idx = min(len(lines), line_idx + 1 + context_lines)
            excerpt = "\n".join(lines[start_idx:end_idx])
            match_line = lines[line_idx] if line_idx < len(lines) else ""

            entry: dict[str, Any] = {
                "file": doc,
                "line": doc_line,
                "excerpt": excerpt,
            }

            if include_scope:
                # line_idx+1 for 1-based comparison with AST lineno
                entry["scope"] = _find_enclosing_scope(chunk_defs, line_idx + 1)

            if classify:
                entry["classification"] = _classify_line(match_line)

            enriched.append(entry)

    # 6. Deduplicate
    dedup_count = 0
    if deduplicate and enriched:
        seen: dict[str, list[dict[str, Any]]] = {}
        for entry in enriched:
            key = _normalize_excerpt(entry["excerpt"])
            seen.setdefault(key, []).append(entry)

        deduped: list[dict[str, Any]] = []
        for dup_group in seen.values():
            first = dup_group[0].copy()
            if len(dup_group) > 2:
                first["also_in"] = len(dup_group) - 1
                other_files = sorted(
                    {e["file"] for e in dup_group[1:] if e["file"] != first["file"]}
                )
                if other_files:
                    first["also_in_files"] = other_files[:5]
                dedup_count += len(dup_group) - 1
                deduped.append(first)
            else:
                deduped.extend(dup_group)
        enriched = deduped

    # 7. Group
    groups = _group_matches(enriched, group_by)

    # 8. Apply token budget
    budget = max(200, max_tokens - 150)  # reserve for wrapper
    result_groups: list[dict[str, Any]] = []
    tokens_used = 0
    shown = 0

    for group in groups:
        group_tokens = _estimate_group_tokens(group)
        if tokens_used + group_tokens > budget and result_groups:
            break
        result_groups.append(group)
        tokens_used += group_tokens
        shown += len(group.get("matches", []))

    truncated = total_match_count - shown - dedup_count

    file_count = len(doc_paths)
    parts = [
        f"{total_match_count} match{'es' if total_match_count != 1 else ''}",
        f"in {file_count} file{'s' if file_count != 1 else ''}",
    ]
    if dedup_count:
        parts.append(f"({dedup_count} duplicates collapsed)")
    if truncated > 0:
        parts.append(f"(showing {shown}, ~{tokens_used} tokens)")

    return {
        "summary": " ".join(parts),
        "groups": result_groups,
        "total_matches": total_match_count,
        "shown_matches": shown,
        "truncated": max(0, truncated),
        "total_tokens": tokens_used,
        "files_checked": len(doc_paths),
        "files_with_matches": doc_paths,
    }


# -- Grouping helpers --------------------------------------------------------


def _group_matches(
    matches: list[dict[str, Any]],
    group_by: str,
) -> list[dict[str, Any]]:
    """Group matches by the specified key."""

    def _by_scope(m: dict[str, Any]) -> str:
        return m.get("scope") or "(top-level)"

    def _by_classification(m: dict[str, Any]) -> str:
        return m.get("classification") or "code"

    def _by_file(m: dict[str, Any]) -> str:
        return m.get("file", "")

    if group_by == "scope":
        key_fn = _by_scope
    elif group_by == "classification":
        key_fn = _by_classification
    else:
        key_fn = _by_file

    buckets: dict[str, list[dict[str, Any]]] = {}
    for m in matches:
        k = key_fn(m)
        buckets.setdefault(k, []).append(m)

    return [{"key": k, "count": len(ms), "matches": ms} for k, ms in buckets.items()]


def _estimate_group_tokens(group: dict[str, Any]) -> int:
    """Estimate token cost of a group for budget tracking."""
    parts = [group.get("key", "")]
    for m in group.get("matches", []):
        parts.append(m.get("excerpt", ""))
        scope = m.get("scope")
        if scope:
            parts.append(scope)
        cls = m.get("classification")
        if cls:
            parts.append(cls)
        parts.append(f":{m.get('line', 0)}")
    return estimate_tokens(" ".join(parts))
