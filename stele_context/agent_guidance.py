"""
Agent-facing guidance: next steps, ritual, enrichment plan, token savings.

Pure stdlib helpers used by doctor_snapshot, CLI init, and query hints.
No imports from engine/storage modules — callers pass plain data.
"""

from __future__ import annotations

from typing import Any

# Recommended MCP surface for cold agents (high-leverage tools only).
RECOMMENDED_MCP_MODE = "lite"

DEFAULT_RITUAL = (
    "doctor → query (session_id) → agent_grep / find_definition|find_references "
    "as needed → get_context (trust/diff) → detect_changes after edits"
)

MCP_CONFIG_SNIPPET = """{
  "mcpServers": {
    "stele-context": {
      "command": "stele-context",
      "args": ["serve-mcp"],
      "env": {
        "STELE_MCP_MODE": "lite"
      }
    }
  }
}"""

# Days of index age before freshness guidance is urgent.
_FRESHNESS_WARN_DAYS = 7
_TIER2_CRITICAL_PCT = 5.0
_TIER2_TARGET_PCT = 20.0


def mcp_config_snippet(*, mode: str | None = None) -> str:
    """Return a ready-to-paste MCP client config (Claude Code / Desktop style)."""
    m = (mode or RECOMMENDED_MCP_MODE).strip().lower() or RECOMMENDED_MCP_MODE
    return MCP_CONFIG_SNIPPET.replace('"lite"', f'"{m}"')


def compute_token_savings(
    *,
    total_indexed_tokens: int,
    document_count: int,
    chunk_count: int,
) -> dict[str, Any]:
    """Estimate avoided re-read value from the persistent index.

    Full-file re-read cost is modeled as total_indexed_tokens. When an agent
    uses get_context + diff_since_cache instead of re-reading every file,
    the cache can avoid re-paying that token mass on unchanged work.
    """
    total = max(0, int(total_indexed_tokens))
    docs = max(0, int(document_count))
    chunks = max(0, int(chunk_count))
    avg_per_doc = (total / docs) if docs else 0.0
    # Conservative "one successful cache hit session" estimate: full tree once.
    return {
        "indexed_tokens": total,
        "document_count": docs,
        "chunk_count": chunks,
        "avg_tokens_per_document": round(avg_per_doc, 1),
        "avoided_reread_tokens_if_cache_hit": total,
        "note": (
            "If the agent reuses the index instead of re-reading every file, "
            f"up to ~{total} tokens of full-file re-read can be avoided "
            "(use get_context + diff_since_cache for deltas)."
        ),
    }


def build_enrichment_plan(
    chunks: list[dict[str, Any]],
    *,
    top_n: int = 15,
    min_tokens: int = 20,
) -> dict[str, Any]:
    """Rank chunks missing Tier-2 (agent_signature / semantic_summary) by token mass.

    Each item in *chunks* should include at least:
    ``chunk_id``, ``document_path``, ``token_count``, and either
    ``agent_signature`` / ``has_tier2`` / ``semantic_summary``.
    """
    missing: list[dict[str, Any]] = []
    tier2_tokens = 0
    total_tokens = 0
    for c in chunks:
        tokens = int(c.get("token_count") or 0)
        total_tokens += tokens
        has = bool(
            c.get("has_tier2")
            or c.get("agent_signature") is not None
            or (c.get("semantic_summary") not in (None, ""))
        )
        if has:
            tier2_tokens += tokens
            continue
        if tokens < min_tokens:
            continue
        missing.append(
            {
                "chunk_id": c.get("chunk_id"),
                "document_path": c.get("document_path"),
                "token_count": tokens,
                "preview": (c.get("content") or c.get("content_preview") or "")[:120],
            }
        )

    missing.sort(
        key=lambda x: (-int(x["token_count"]), str(x.get("document_path") or ""))
    )
    selected = missing[: max(0, top_n)]
    uncovered = sum(int(x["token_count"]) for x in missing)
    pct = (tier2_tokens / total_tokens * 100.0) if total_tokens else 0.0
    return {
        "top_n": top_n,
        "candidates": selected,
        "candidates_total": len(missing),
        "uncovered_tokens": uncovered,
        "tier2_token_coverage_percent": round(pct, 2),
        "total_tokens": total_tokens,
        "how_to_enrich": (
            "Call bulk_store_summaries with {chunk_id: short purpose/exports summary} "
            "for the candidates below. Prefer hot paths agents already read."
        ),
    }


def build_next_steps(
    *,
    document_count: int,
    chunk_count: int,
    symbol_rows: int,
    tier2_coverage_percent: float,
    seconds_since_last_index: float | None,
    index_alerts: list[str] | None = None,
    has_enrichment_candidates: bool = False,
) -> list[dict[str, str]]:
    """Return ordered actionable next steps for a cold or mid-session agent."""
    steps: list[dict[str, str]] = []
    docs = int(document_count)
    chunks = int(chunk_count)
    sym = int(symbol_rows)
    tier2 = float(tier2_coverage_percent or 0.0)
    alerts = list(index_alerts or [])

    if docs == 0 or chunks == 0:
        steps.append(
            {
                "action": "init_or_index",
                "detail": (
                    "No index yet. Run `stele-context init` (or MCP `index` on the "
                    "project root) so search and symbols have material."
                ),
            }
        )
        steps.append(
            {
                "action": "connect_mcp",
                "detail": (
                    f"Use STELE_MCP_MODE={RECOMMENDED_MCP_MODE} (recommended). "
                    "Paste the mcp_config from doctor/init into Claude Code / Desktop."
                ),
            }
        )
        return steps

    if seconds_since_last_index is not None and seconds_since_last_index > (
        _FRESHNESS_WARN_DAYS * 86400
    ):
        days = int(seconds_since_last_index // 86400)
        steps.append(
            {
                "action": "detect_changes",
                "detail": (
                    f"Index is about {days} day(s) old. Run detect_changes "
                    "(or query with session_id / working_tree) before trusting cache."
                ),
            }
        )
    elif any("day(s) old" in a for a in alerts):
        steps.append(
            {
                "action": "detect_changes",
                "detail": "Index freshness alert present — run detect_changes and re-index edited files.",
            }
        )

    if chunks > 0 and sym == 0:
        steps.append(
            {
                "action": "rebuild_symbols",
                "detail": "Symbol graph empty while chunks exist — re-index sources or rebuild_symbols.",
            }
        )

    steps.append(
        {
            "action": "query",
            "detail": (
                "Start broad questions with `query` (pass session_id for auto "
                "working_tree). Prefer agent_grep / find_* for exact identifiers."
            ),
        }
    )

    if tier2 < _TIER2_CRITICAL_PCT or has_enrichment_candidates:
        steps.append(
            {
                "action": "enrich_tier2",
                "detail": (
                    f"Tier-2 coverage is {tier2:.1f}% (target ≥{_TIER2_TARGET_PCT:.0f}% "
                    "on hot paths). Use enrichment_plan / doctor enrichment_preview, "
                    "then bulk_store_summaries on top candidates."
                ),
            }
        )

    steps.append(
        {
            "action": "ritual",
            "detail": DEFAULT_RITUAL,
        }
    )
    return steps


def assemble_doctor_guidance(
    *,
    document_count: int,
    chunk_count: int,
    symbol_rows: int,
    total_indexed_tokens: int,
    tier2_coverage_percent: float,
    seconds_since_last_index: float | None,
    index_alerts: list[str] | None,
    enrichment_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bundle guidance fields for doctor_snapshot."""
    plan = enrichment_plan or {
        "candidates": [],
        "candidates_total": 0,
        "uncovered_tokens": 0,
        "tier2_token_coverage_percent": tier2_coverage_percent,
    }
    has_cands = int(plan.get("candidates_total") or 0) > 0
    next_steps = build_next_steps(
        document_count=document_count,
        chunk_count=chunk_count,
        symbol_rows=symbol_rows,
        tier2_coverage_percent=tier2_coverage_percent,
        seconds_since_last_index=seconds_since_last_index,
        index_alerts=index_alerts,
        has_enrichment_candidates=has_cands,
    )
    savings = compute_token_savings(
        total_indexed_tokens=total_indexed_tokens,
        document_count=document_count,
        chunk_count=chunk_count,
    )
    # Compact enrichment preview for doctor (not full plan dump).
    preview_cands = list(plan.get("candidates") or [])[:8]
    return {
        "recommended_mcp_mode": RECOMMENDED_MCP_MODE,
        "recommended_ritual": DEFAULT_RITUAL,
        "mcp_config": mcp_config_snippet(),
        "next_steps": next_steps,
        "token_savings": savings,
        "enrichment_preview": {
            "candidates_total": plan.get("candidates_total", 0),
            "uncovered_tokens": plan.get("uncovered_tokens", 0),
            "tier2_token_coverage_percent": plan.get(
                "tier2_token_coverage_percent", tier2_coverage_percent
            ),
            "how_to_enrich": plan.get("how_to_enrich"),
            "top_candidates": preview_cands,
        },
    }
