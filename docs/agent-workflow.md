# Agent workflow

This document is for LLM agents (and humans wiring MCP or HTTP) operating in a repository across multiple sessions. It describes a practical path from a cold repo to fast retrieval, using the Rust CLI and MCP tools.

The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x).

## Mental model

1. **Ingest**: Chunk files → SQLite + HNSW + BM25 (Tier 1 signatures).
2. **Enrich** (optional but recommended): Attach **Tier 2** summaries or vectors so hybrid search reflects *intent*, not only statistics.
3. **Retrieve**: `search`, symbol tools, `get_context`, sessions — without re-reading the whole tree.
4. **Maintain**: `detect_changes_and_update` when files move; re-index or patch Tier 2 when behavior changes.

Storage is **persistent** (project `.stele-context/` by default), so later sessions **reuse** the same index unless you delete it or point `storage_dir` elsewhere.

## Minimal first session

**Goal**: Get the codebase into the index and orient in one step.

### Preferred: CLI init

```bash
stele-context init .
```

Indexes the tree (gitignore-aware), prints **MCP lite** config + **recommended ritual**, and a full **`doctor`** snapshot (`next_steps`, `token_savings`, `enrichment_preview`).

### Or step by step

- **MCP / HTTP**: `index` with paths (project root or globs as supported), or CLI: `stele-context index <paths>`.
- **Orient cheaply** (token-bounded): MCP/CLI **`doctor`** — health, freshness, `next_steps`, Tier-2 coverage, token-savings estimate. CLI: `stele-context doctor`.
- Optionally run **`map`** (`compact=true` for large repos) or **`stats`** (`compact=true`) to confirm **index_health**.

### Canonical ritual

```
doctor → query (session_id) → agent_grep / find_definition|find_references → get_context
```

After edits: `detect_changes` (or `query` with `session_id` for auto working-tree).  
For hybrid search quality: `enrichment_plan` → `bulk_store_summaries` on hot chunks.

MCP default mode is **`lite`**. Set `STELE_MCP_MODE=standard` only when you need the broader tool surface.

**HTTP vs stdio envelopes:** HTTP wraps `{"success", "result"}`; stdio returns bare tool JSON — intentional dual shape (see architecture design log).

**CLI:** thin subcommands exist for `query`, `find-definition`, `find-references`, `impact-radius`, `coupling` in addition to search/detect/doctor. See AGENTS.md CLI↔MCP map. `detect --scan-new` defaults **true** (use `--no-scan-new` to disable).

After this, **`search`**, **`get_context`**, **`find_references`**, etc. have material to work with.

## When to use which retrieval tool

| Need | Prefer |
|------|--------|
| Exact string / regex proof | `search_text` or **`agent_grep`** (scoped, token budget, dedup; auto-indexes + session history) |
| “Where is X defined / used?” | **`find_definition`**, **`find_references`** (symbol graph) |
| Exploratory “what talks about Y?” | **`search`** (BM25 keyword by default; pass `search_mode="hybrid"` for HNSW+BM25 — only worthwhile when Tier 2 is populated) |
| Current chunk text | **`get_context`** (optional **trust** + **agent_notes** per chunk; records reads in session) |
| Broad NL question (composite) | **`query`** — merges search + symbols + grep; returns **`applied_defaults`** for smart path_prefix/working_tree; optional `search_mode` / `compact` / `max_result_tokens` |
| Blast radius / coupling | **`impact_radius`** / **`coupling`** (also CLI `impact-radius` / `coupling`) |
| Text inside strings or comments, files outside the index | plain grep: Stele skips comments and strings when it reads Rust code |

### When Stele beats grep (2.4)

- **"Where is X defined?"** `find_definition` returns the definition rows only (fn, struct, trait, `macro_rules!`, const, `pub(crate) fn` ...), with the signature line. grep returns every call site too.
- **"Who uses X?" / "Is X dead?"** `find_references` puts the definition first, then every code line that calls, method-calls, imports or names X, and answers `referenced` or `unreferenced`. For Rust it has grep's recall over code (on a Specula sample, 185 of 185 code lines that `grep -w` found outside strings).
- **"What breaks if I change X?"** `impact_radius` follows the symbol graph across files. grep cannot.
- **"Where is the Game trait?"** `search` ranks the chunk that defines the names in the query first.

### Working in a git worktree (2.4)

The server's index is the main checkout's. Pass **`worktree_root`** (the worktree's absolute path, the same value CoordinationHub's `register_agent` takes) to any read tool, or `--worktree PATH` on the CLI. Stele answers from the main index plus an in-memory overlay of the files that differ in the worktree (new, changed, deleted), re-checked by size and mtime on every call, so the answer is as current as grep. Replies add `worktree` and `overlay_files`. A server started inside a worktree that has no index of its own does this automatically.

### Grep-first: searching = caching

Every **`agent_grep`** or **`search_text`** call with a **`session_id`** automatically
indexes the files it finds matches in — no separate `index` call needed. The session
records what you searched and what files were cached:

```
agent_grep “createRouter” --session-id S
get_search_history --session-id S    # “you grep'd file X, it's fresh”
get_context file_x.js --session-id S  # full cached content, no disk re-read
get_session_read_files --session-id S # all files fully read this session
```

This makes **`get_search_history`** the “post-it note” — it tells the LLM what it
already looked at before re-running a search or re-reading a file.

Rough rule: **symbols first** for identifiers; **`search`** for concepts (keyword-only by default — add `search_mode="hybrid"` when you have Tier-2 summaries); **grep-style** for exhaustive verification.

### Bounded context on `search` / `map` / `stats`

- **`search`**: `compact=true` (previews only), `max_result_tokens` (cap total body size), `return_response_meta=true` (truncation metadata), **`path_prefix`** when the index spans multiple trees and you want one project’s paths only.
- **`map`**: `compact=true`, `max_documents`, `max_annotation_chars`, **`path_prefix`** to list only documents under a project-relative prefix.
- **`stats`**: `compact=true` for a small JSON-friendly payload.

### Impact radius without huge payloads

- **`impact_radius`**: use **`summary_mode=true`** (optional **`top_n_files`**) for **`depth_distribution`** and a capped **`files`** list plus **`files_total`** — prefer this over **`compact=false`** for hub files.

## Tier 2 bootstrap (two passes)

1. **Index** hot paths (or whole repo), then run **`doctor`** / **`enrichment_plan`** (CLI: `stele-context enrichment-plan`) to see uncovered token mass.
2. **Enrich**: for top candidates, call **`bulk_store_summaries`** with `{chunk_id: summary}` (or **`index_documents(..., summaries=)`** at file level first). Re-run **`search`** with `search_mode=hybrid` — Tier 2 boosts retrieval quality without new dependencies.

When chunk **content_hash** changes on re-index / `store_chunk`, prior `semantic_summary` and `agent_signature` are **cleared** so stale Tier 2 cannot silently rank.

## Chunk agent notes (non-vector memory)

Store structured or free-text notes on a chunk ID (facts, invariants, decisions):

- **`store_chunk_agent_notes`** / **`bulk_store_chunk_agent_notes`** — persisted in SQLite; returned in **`get_context`** (parsed as JSON when valid). Complements Tier 2 summaries; does not replace vectors for search unless you also add summaries/embeddings.

## Tier 2: making search “yours”

Tier 1 works out of the box. Tier 2 is how **you** (the agent) vectorize nuance without bundling a model inside Stele.

### Document-level summaries at index time

Pass a map of **normalized file paths → short summary** when indexing:

Pass summaries through the MCP `index` tool or `bulk_store_summaries` after you have chunk ids. All chunks from a file that was indexed with a document summary pick up that summary.

### Per-chunk summaries after chunk IDs exist

When you have **`chunk_id`** values (from search or indexing results):

- **`bulk_store_summaries`**: `{chunk_id: summary_text}` in one batch.
- **`store_semantic_summary`**: single chunk.

### Raw vectors

If you already have a vector (e.g. from your own embedding step):

- **`store_embedding(chunk_id, vector)`** — updates HNSW for that chunk.

### Helpers

- **`llm_embed`**: structured fingerprints become 128-dim vectors **you** produce. The binary does not call a remote API.

## Multiple sessions

- **Same machine, same repo**: The index persists; open a new chat, connect the same MCP server / same `storage_dir`, run **`detect_changes`** and **`search`** — no full re-ingest unless files changed a lot.
- **Sessions API** (if you use it): create/list/attach chunks for **conversation-scoped** working sets; the **chunk index** remains the long-term store.

## Operational habits

1. After substantive edits: **`index`** changed paths or **`detect_changes_and_update`**.
2. Before large refactors: **`agent_grep`** / **`find_references`** on the symbol you touch.
3. When search feels “statistical”: add **Tier 2** summaries for hot paths or run **`bulk_store_summaries`** on important chunks.
4. Use **`get_context`** **`trust`** fields: if **`cache_aligned_with_disk`** is false or **`staleness_hint`** is true, re-index before relying on cached text.

For **design rationale** (why zero deps, Tier 1 vs Tier 2), see [Philosophy](philosophy.md).
