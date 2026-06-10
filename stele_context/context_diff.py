"""
Diff-since-cache for changed files.

When ``get_context`` finds a file changed on disk, the agent normally has
to re-read the whole file. This module reconstructs the cached version
from stored chunks and produces a token-bounded unified diff, so the
agent can read only the delta.

Reconstruction fidelity is verified against the stored document hash:
chunk merging can normalize whitespace between chunks, so the diff is
flagged ``diff_exact: false`` when the rebuilt text doesn't hash-match
the original. An inexact diff still shows the real edits, plus possible
blank-line noise at old chunk boundaries.

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
    """Rebuild document text from stored chunks ordered by position.

    Returns None when there are no chunks or any chunk content is
    missing/non-text (binary modalities have no meaningful text diff).
    """
    if not chunks:
        return None
    parts: list[str] = []
    for chunk in sorted(chunks, key=lambda c: c.get("start_pos") or 0):
        content = chunk.get("content")
        if not isinstance(content, str):
            return None
        parts.append(content)
    return "".join(parts)


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
      diff_exact       True when cached text was rebuilt byte-exact
                       (verified against the stored document hash)
      added_lines      count of ``+`` lines
      removed_lines    count of ``-`` lines
      diff_tokens      estimated token cost of the diff text
      diff_truncated   True when the diff was cut to max_diff_tokens
      recommendation   "read_diff" when the diff is cheaper than
                       re-reading the file, otherwise "reread_file"
    """
    if not isinstance(new_content, str):
        return None
    cached_text = reconstruct_cached_text(chunks)
    if cached_text is None:
        return None

    rebuilt_hash = hashlib.sha256(cached_text.encode("utf-8")).hexdigest()
    exact = rebuilt_hash == stored_hash

    diff_lines = list(
        difflib.unified_diff(
            cached_text.splitlines(),
            new_content.splitlines(),
            fromfile="cached",
            tofile="disk",
            lineterm="",
        )
    )
    added = sum(
        1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++")
    )
    removed = sum(
        1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---")
    )

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
