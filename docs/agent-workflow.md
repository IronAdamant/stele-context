# Agent workflow

This document is for **LLM agents** (and humans wiring MCP/HTTP) operating **in a repository** across **multiple sessions**. It describes a practical path from “cold repo” to “fast retrieval,” using Stele’s APIs and tools.

## Mental model

1. **Ingest**: Chunk files → SQLite + HNSW + BM25 (Tier 1 signatures).
2. **Enrich** (optional but recommended): Attach **Tier 2** summaries or vectors so hybrid search reflects *intent*, not only statistics.
3. **Retrieve**: `search`, symbol tools, `get_context`, sessions — without re-reading the whole tree.
4. **Maintain**: `detect_changes_and_update` when files move; re-index or patch Tier 2 when behavior changes.

Storage is **persistent** (project `.stele-context/` by default), so later sessions **reuse** the same index unless you delete it or point `storage_dir` elsewhere.

## Minimal first session

**Goal**: Get the codebase into the index.

- **MCP / HTTP**: `index` with paths (project root or globs as supported), or CLI: `stele-context index <paths>`.
- **Orient cheaply** (token-bounded): MCP/CLI **`doctor`** (one-screen health + compact map preview). CLI: `stele-context doctor`.
- Optionally run **`map`** (`compact=true` for large repos) or **`stats`** (`compact=true`) to confirm **index_health**.

**Python** (`Stele` engine):

```python
engine.index_documents(["src/", "README.md"])
```

After this, **`search`**, **`get_context`**, **`find_references`**, etc. have material to work with.

## When to use which retrieval tool

| Need | Prefer |
|------|--------|
| Exact string / regex proof | `search_text` or **`agent_grep`** (scoped, token budget, dedup; auto-indexes + session history) |
| “Where is X defined / used?” | **`find_definition`**, **`find_references`** (symbol graph) |
| Exploratory “what talks about Y?” | **`search`** (hybrid HNSW + BM25), stronger if Tier 2 is populated |
| Current chunk text | **`get_context`** (optional **trust** + **agent_notes** per chunk; records reads in session) |

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

Rough rule: **symbols first** for identifiers; **hybrid search** for concepts; **grep-style** for exhaustive verification.

### Bounded context on `search` / `map` / `stats`

- **`search`**: `compact=true` (previews only), `max_result_tokens` (cap total body size), `return_response_meta=true` (truncation metadata), **`path_prefix`** when the index spans multiple trees and you want one project’s paths only.
- **`map`**: `compact=true`, `max_documents`, `max_annotation_chars`, **`path_prefix`** to list only documents under a project-relative prefix.
- **`stats`**: `compact=true` for a small JSON-friendly payload.

### Impact radius without huge payloads

- **`impact_radius`**: use **`summary_mode=true`** (optional **`top_n_files`**) for **`depth_distribution`** and a capped **`files`** list plus **`files_total`** — prefer this over **`compact=false`** for hub files.

## Tier 2 bootstrap (two passes)

1. **Index** hot paths (or whole repo), then run **`search`** or **`map`** (`compact=true`) to see where tokens live.
2. **Enrich**: for top chunks you care about, call **`bulk_store_summaries`** with `{chunk_id: summary}` (or **`index_documents(..., summaries=)`** at file level first). Re-run **`search`** — Tier 2 boosts retrieval quality without new dependencies.

## Chunk agent notes (non-vector memory)

Store structured or free-text notes on a chunk ID (facts, invariants, decisions):

- **`store_chunk_agent_notes`** / **`bulk_store_chunk_agent_notes`** — persisted in SQLite; returned in **`get_context`** (parsed as JSON when valid). Complements Tier 2 summaries; does not replace vectors for search unless you also add summaries/embeddings.

## Tier 2: making search “yours”

Tier 1 works out of the box. Tier 2 is how **you** (the agent) vectorize nuance without bundling a model inside Stele.

### Document-level summaries at index time

Pass a map of **normalized file paths → short summary** when indexing:

```python
engine.index_documents(
    ["src/app.py"],
    summaries={"src/app.py": "FastAPI app: routes for /api/v1/users and auth middleware."},
)
```

All chunks from those files receive that summary pipeline (see engine behavior and `summaries_applied` in the result).

### Per-chunk summaries after chunk IDs exist

When you have **`chunk_id`** values (from search or indexing results):

- **`bulk_store_summaries`**: `{chunk_id: summary_text}` in one batch.
- **`store_semantic_summary`**: single chunk.

### Raw vectors

If you already have a vector (e.g. from your own embedding step):

- **`store_embedding(chunk_id, vector)`** — updates HNSW for that chunk.

### Helpers

- **`stele_context.llm_embedding`**: optional utilities for **structured fingerprints** → 128-dim vectors **you** produce; the library does not call remote APIs.

## Multiple sessions

- **Same machine, same repo**: The index persists; open a new chat, connect the same MCP server / same `storage_dir`, run **`detect_changes`** and **`search`** — no full re-ingest unless files changed a lot.
- **Sessions API** (if you use it): create/list/attach chunks for **conversation-scoped** working sets; the **chunk index** remains the long-term store.

## Operational habits

1. After substantive edits: **`index`** changed paths or **`detect_changes_and_update`**.
2. Before large refactors: **`agent_grep`** / **`find_references`** on the symbol you touch.
3. When search feels “statistical”: add **Tier 2** summaries for hot paths or run **`bulk_store_summaries`** on important chunks.
4. Use **`get_context`** **`trust`** fields: if **`cache_aligned_with_disk`** is false or **`staleness_hint`** is true, re-index before relying on cached text.

For **design rationale** (why zero deps, Tier 1 vs Tier 2), see [Philosophy](philosophy.md).
