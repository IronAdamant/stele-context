# Stele Context — agent guide

Stele is a **local, persistent index** of your project: chunks, hybrid search (HNSW + BM25), symbol graph, and optional **Tier 2** semantics **you** supply (summaries or vectors). It does **not** bundle an embedding model.

## Read first

- [docs/philosophy.md](docs/philosophy.md) — why zero core deps, Tier 1 vs Tier 2, cross-session use.
- [docs/agent-workflow.md](docs/agent-workflow.md) — index → enrich → retrieve, tool choice, Tier 2 bootstrap.

## Session vs project truth

- **Index + `.stele-context/`** = durable **project** memory (survives chats).
- **Sessions** = optional **thread-local** working sets (attached chunks, KV). Do not rely on sessions alone for facts that must survive the next session — **index and re-use the project cache**.

## Orientation (start here)

1. **`doctor`** — one screen: version, storage, `index_health`, `db_health`, `search_quality`, env issues, compact map preview.
2. **`map`** — use `compact=true` for bounded token use.
3. **`query`** — **universal entry point** for broad questions. It auto-enables `working_tree` when you pass `session_id` on a dirty repo, and auto-restricts `path_prefix` on large projects unless you ask globally.

## Retrieval discipline

- **Start with `query`** for any broad natural-language question; it merges semantic search, symbol graph, and text grep into one result list.
- **Symbols** (`find_definition`, `find_references`) for identifiers and imports. `find_definition` now annotates shadowed symbols with `definition_index`, `shadowed`, and `shadow_count`.
- **`agent_grep` / `search_text`** for exhaustive or regex proof. Both accept **`session_id`** — they auto-index files with matches and record search history, so **`get_search_history`** tells you what you already searched. Both also accept **`working_tree=true`** to auto-index modified/untracked files before searching.
- **`search`** for exploration; use **`compact`**, **`max_result_tokens**`, or **`return_response_meta`** to cap context. Also supports **`working_tree=true`**. Results now include **`source`** (`hnsw`, `bm25`, `symbol_boost`) and **`tier2_present`** so you can see why a result ranked where it did.
- **`query`** supports **`working_tree=true**`, surfaces sub-search errors in `errors`, and applies smart defaults (auto `working_tree`, auto `path_prefix` on large projects).
- **`impact_radius`** / **`coupling`** — use `significance_threshold > 0` to filter out blast-radius and coupling noise from common stdlib/generic symbols (e.g. `push`, `has`, `addEdge`). `impact_radius` also accepts `symbol` to analyze dynamic/runtime symbols without on-disk files, and `direction` (`dependents`/`dependencies`/`both`). For base classes with high fan-in, `impact_radius` now hybridizes symbol-edge traversal with raw reference lookups and falls back to `file_dependencies` so it no longer returns zero affected chunks.
- **`coupling(..., mode="co_consumers")`** detects files co-imported by the same consumers — useful for finding hidden refactoring clusters.
- **`get_context`** returns **trust** hints (mtime vs index, staleness) and optional **`agent_notes`** per chunk. Pass **`session_id`** to record which files were fully read — check **`get_session_read_files`** to avoid re-fetching.

## Tier 2 and chunk notes

- **Tier 2:** `index` with `summaries`, `bulk_store_summaries`, `bulk_store_embeddings`, `llm_embed` — improves hybrid search.
- **Chunk notes:** `bulk_store_chunk_agent_notes` — JSON or text tied to a `chunk_id` (facts, invariants). Shown in `get_context`; not a substitute for summaries for search vectors.

## Trust

If **`trust.cache_aligned_with_disk`** is false or **`staleness_hint`** is true, treat cached text as potentially stale and prefer **`detect_changes`** + re-index for files you edit.

## Working tree

Use **`working_tree=true`** on `agent_grep`, `search_text`, `search`, and `query` to automatically index modified and untracked files from the git working tree before searching. This closes the gap between your editor and the index without a separate `index` call.

## Staleness calibration

`stale_chunks` defaults to `threshold=0.3`. On active codebases this can produce hundreds of warnings. Use **`threshold=0.5`** for direct-dependency changes only, or **`threshold=0.64`** for transitive changes. You can also pass **`max_age_seconds=86400`** to ignore ancient transitive staleness.

## Scope (zero-dep core)

The **stdlib-only** engine is in a **reasonable stopping place** for hybrid retrieval, symbol tools, and agent-oriented bounds: further gains on "semantic" quality without a bundled model are diminishing; optional **Tier 2** (your summaries/embeddings) remains the supported path for higher intent alignment. **`path_prefix`** on **`map`** / **`search`** reduces cross-tree noise; **`impact_radius(..., summary_mode=true, significance_threshold=0.1)`** keeps blast-radius output small and noise-free. See **STABILITY.md** and [CHANGELOG](CHANGELOG.md) for latest.

## MCP Modes

- **Standard** (default): ~32 tools, simplified surface with unified `document_lock`, `annotations`, `query`, and `batch`.
- **Lite** (`STELE_MCP_MODE=lite`): ~15 high-leverage tools for simpler agents.
- **Full** (`STELE_MCP_MODE=full`): restores deprecated singleton tools for backward compatibility.
