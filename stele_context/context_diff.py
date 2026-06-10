"""
Diff-since-cache for changed files.

When ``get_context`` finds a file changed on disk, the agent normally has
to re-read the whole file. This module reconstructs the cached version
from stored chunks and produces a token-bounded unified diff, so the
agent can read only the delta.

Chunkers strip surrounding whitespace from chunk content but keep
``start_pos``/``end_pos`` spanning the raw source region, so reconstruction
re-inserts the missing characters as newlines: pad up to ``start_pos``,
write the content, pad out to ``end_pos``. Several candidate rebuilds are
hashed against the stored document hash; a match means ``diff_exact: true``.
When none match (legacy rows indexed before offsets were region-faithful,
prose chunkers that rewrite whitespace), the candidate producing the least
diff churn is used and flagged ``diff_exact: false``.

Standalone module — imports only ``estimate_tokens`` (same pattern as
agent_grep.py). Pure stdlib: difflib + hashlib.
"""

from __future__ import annotations

import difflib
import hashlib
from typing import Any

from stele_context.chunkers.base import estimate_tokens

# Diffs above this estimated-token budget are truncated at line boundaries.
DEFAULT_MAX_DIFF_TOKENS = 2000

# When the diff costs at least this fraction of re-reading the new file,
# recommend a full re-read instead.
_REREAD_RATIO = 0.8


def reconstruct_cached_text(chunks: list[dict[str, Any]]) -> str | None:
    """Rebuild document text from stored chunks using content + offsets.

    Whitespace the chunker stripped from a region is re-inserted as
    newlines (the separator chunkers actually consume in code files).
    Returns None when there are no chunks or any chunk content is
    missing/non-text (binary modalities have no meaningful text diff).
    """
    if not chunks:
        return None
    parts: list[str] = []
    cursor = 0
    for chunk in sorted(chunks, key=lambda c: c.get("start_pos") or 0):
        content = chunk.get("content")
        if not isinstance(content, str):
            return None
        start = int(chunk.get("start_pos") or 0)
        if start > cursor:
            parts.append("\n" * (start - cursor))
            cursor = start
        parts.append(content)
        cursor += len(content)
        end = int(chunk.get("end_pos") or 0)
        if end > cursor:
            parts.append("\n" * (end - cursor))
            cursor = end
    return "".join(parts)


def _reconstruction_candidates(chunks: list[dict[str, Any]]) -> list[str]:
    """Plausible rebuilds of the cached text, most likely first.

    - offset-padded (current chunkers: regions span raw text)
    - offset-padded + final newline (legacy rows whose last chunk's
      end_pos used the stripped length, dropping the trailing newline)
    - plain concatenation (pre-offset behavior)
    """
    padded = reconstruct_cached_text(chunks)
    if padded is None:
        return []
    plain = "".join(
        c["content"] for c in sorted(chunks, key=lambda c: c.get("start_pos") or 0)
    )
    candidates: list[str] = []
    for cand in (padded, padded + "\n", plain):
        if cand not in candidates:
            candidates.append(cand)
    return candidates


def _diff_lines(cached_text: str, new_lines: list[str]) -> list[str]:
    return list(
        difflib.unified_diff(
            cached_text.splitlines(),
            new_lines,
            fromfile="cached",
            tofile="disk",
            lineterm="",
        )
    )


def _count_changes(diff_lines: list[str]) -> tuple[int, int]:
    added = sum(
        1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++")
    )
    removed = sum(
        1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")
    )
    return added, removed


def build_change_diff(
    chunks: list[dict[str, Any]],
    stored_hash: str,
    new_content: Any,
    max_diff_tokens: int = DEFAULT_MAX_DIFF_TOKENS,
) -> dict[str, Any] | None:
    """Build a unified diff between cached chunks and current disk content.

    Returns a dict with the diff text, fidelity and cost metadata, or
    None when no text diff can be produced (binary content, no chunks).

    Keys:
      diff             unified diff text (``cached`` -> ``disk``)
      diff_exact       True when a rebuild hash-matched the stored
                       document hash (the diff has no reconstruction noise)
      added_lines      count of ``+`` lines
      removed_lines    count of ``-`` lines
      diff_tokens      estimated token cost of the diff text
      diff_truncated   True when the diff was cut to max_diff_tokens
      recommendation   "read_diff" when the diff is cheaper than
                       re-reading the file, otherwise "reread_file"
    """
    if not isinstance(new_content, str):
        return None
    candidates = _reconstruction_candidates(chunks)
    if not candidates:
        return None

    new_lines = new_content.splitlines()
    exact = False
    diff_lines: list[str] | None = None
    for cand in candidates:
        if hashlib.sha256(cand.encode("utf-8")).hexdigest() == stored_hash:
            exact = True
            diff_lines = _diff_lines(cand, new_lines)
            break
    if diff_lines is None:
        # No rebuild is provably the old text; use the one that diffs
        # cleanest so reconstruction noise is minimized.
        diff_lines = min(
            (_diff_lines(cand, new_lines) for cand in candidates),
            key=lambda d: sum(_count_changes(d)),
        )

    added, removed = _count_changes(diff_lines)
    diff_text, truncated = _trim_diff_to_budget(diff_lines, max_diff_tokens)
    diff_tokens = estimate_tokens(diff_text) if diff_text else 0

    new_tokens = estimate_tokens(new_content)
    cheap = not truncated and diff_tokens < new_tokens * _REREAD_RATIO

    return {
        "diff": diff_text,
        "diff_exact": exact,
        "added_lines": added,
        "removed_lines": removed,
        "diff_tokens": diff_tokens,
        "diff_truncated": truncated,
        "recommendation": "read_diff" if cheap else "reread_file",
    }


def _trim_diff_to_budget(diff_lines: list[str], max_tokens: int) -> tuple[str, bool]:
    """Join diff lines, cutting at a line boundary once the budget is hit."""
    kept: list[str] = []
    used = 0
    for line in diff_lines:
        cost = estimate_tokens(line)
        if used + cost > max_tokens:
            return "\n".join(kept), True
        kept.append(line)
        used += cost
    return "\n".join(kept), False
