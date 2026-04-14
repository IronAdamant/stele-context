# Stele Context — agent guide

Stele is a **local, persistent index** of your project: chunks, hybrid search (HNSW + BM25), symbol graph, and optional **Tier 2** semantics **you** supply (summaries or vectors). It does **not** bundle an embedding model.

## Read first

- [docs/philosophy.md](docs/philosophy.md) — why zero core deps, Tier 1 vs Tier 2, cross-session use.
- [docs/agent-workflow.md](docs/agent-workflow.md) — index → enrich → retrieve, tool choice, Tier 2 bootstrap.

## Session vs project truth

- **Index + `.stele-context/`** = durable **project** memory (survives chats).
- **Sessions** = optional **thread-local** working sets (attached chunks, KV). Do not rely on sessions alone for facts that must survive the next session — **index and re-use the project cache**.

## Orientation (start here)

1. **`doctor`** — one screen: version, storage, counts, `index_health`, env issues, compact map preview.
2. **`project_brief`** — largest files by tokens, extension counts, totals.
3. **`map`** — use `compact=true` for bounded token use.
4. **`stats`** — use `compact=true` for a small JSON summary.

## Retrieval discipline

- **Symbols** (`find_definition`, `find_references`) for identifiers and imports. `find_definition` now annotates shadowed symbols with `definition_index`, `shadowed`, and `shadow_count`.
- **`agent_grep` / `search_text`** for exhaustive or regex proof. Both accept **`session_id`** — they auto-index files with matches and record search history, so **`get_search_history`** tells you what you already searched.
- **`search`** for exploration; use **`compact`**, **`max_result_tokens`**, or **`return_response_meta`** to cap context.
- **`impact_radius`** / **`coupling`** — use `significance_threshold > 0` to filter out blast-radius and coupling noise from common stdlib/generic symbols (e.g. `push`, `has`, `addEdge`).
- **`get_context`** returns **trust** hints (mtime vs index, staleness) and optional **`agent_notes`** per chunk. Pass **`session_id`** to record which files were fully read — check **`get_session_read_files`** to avoid re-fetching.

## Tier 2 and chunk notes

- **Tier 2:** `index` with `summaries`, `bulk_store_summaries`, `store_semantic_summary`, `store_embedding` — improves hybrid search.
- **Chunk notes:** `store_chunk_agent_notes` / `bulk_store_chunk_agent_notes` — JSON or text tied to a `chunk_id` (facts, invariants). Shown in `get_context`; not a substitute for summaries for search vectors.

## Trust

If **`trust.cache_aligned_with_disk`** is false or **`staleness_hint`** is true, treat cached text as potentially stale and prefer **`detect_changes`** + re-index for files you edit.

## Scope (zero-dep core)

The **stdlib-only** engine is in a **reasonable stopping place** for hybrid retrieval, symbol tools, and agent-oriented bounds: further gains on "semantic" quality without a bundled model are diminishing; optional **Tier 2** (your summaries/embeddings) remains the supported path for higher intent alignment. **`path_prefix`** on **`map`** / **`search`** reduces cross-tree noise; **`impact_radius(..., summary_mode=true, significance_threshold=0.1)`** keeps blast-radius output small and noise-free. See **STABILITY.md** and [CHANGELOG](CHANGELOG.md) for latest.
