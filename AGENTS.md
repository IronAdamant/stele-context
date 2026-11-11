# Stele Context: agent guide

Stele is a **local, persistent index** of your project: chunks, hybrid search (HNSW + BM25), symbol graph, and optional **Tier 2** semantics **you** supply (summaries or vectors). It does **not** bundle an embedding model.

The product on this branch is the Rust 2.0 binary. The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x).

## Read first

- [docs/philosophy.md](docs/philosophy.md) — why zero core deps, Tier 1 vs Tier 2, cross-session use.
- [docs/agent-workflow.md](docs/agent-workflow.md) — index → enrich → retrieve, tool choice, Tier 2 bootstrap.

## Session vs project truth

- **Index + `.stele-context/`** = durable **project** memory (survives chats).
- **Sessions** = optional **thread-local** working sets (attached chunks, KV). Do not rely on sessions alone for facts that must survive the next session — **index and re-use the project cache**.

## Orientation (start here)

1. **`doctor`** — one screen: version, storage, `index_health`, `db_health`, `search_quality`, `next_steps`, `token_savings`, `enrichment_preview`, env issues, compact map preview.
2. **`map`** — use `compact=true` for bounded token use.
3. **`query`** — **universal entry point** for broad questions. It auto-enables `working_tree` when you pass `session_id` on a dirty repo, and auto-restricts `path_prefix` on large projects unless you ask globally.

Cold-start ritual (all on **MCP lite** by default):  
`doctor` → `query` (`session_id`) → `agent_grep` / `find_*` → `get_context` → `get_search_history` / `get_session_read_files` as needed.

## Retrieval discipline

- **When Stele beats grep (2.4).** `find_definition` for "where is X defined" (definition rows only, `macro_rules!` and `pub(crate) fn` included); `find_references` for "who uses X" / "is X dead" (definition first, every code line that names X, a verdict); `impact_radius` for what a change breaks; `search` puts the defining chunk first. Grep for text in strings or comments.
- **Worktrees (2.4).** In a git worktree, pass `worktree_root=<absolute path>` to read tools (CLI `--worktree`): main index plus an overlay of the files that differ, re-checked every call. Replies add `worktree` and `overlay_files`.

- **Start with `query`** for any broad natural-language question; it merges semantic search, symbol graph, and text grep into one result list.
- **Compact by default (2.2).** Tools that can run long answer briefly: `find_definition` gives the `signature` line and a short `preview` (pass `full=true` for the chunk text), `search` returns previews (`compact=false` for text), `index` reports counts, `map` lists the 50 largest documents. Ask for more only when you need it.
- **Symbols** (`find_definition`, `find_references`) for identifiers and imports. Line numbers are file lines (2.3). `find_references` stays in the language the symbol is defined in; pass `all_languages=true` for every language. Rust references include grouped `use` imports, `crate::a::B` paths and type names. `find_definition` annotates shadowed symbols with `definition_index`, `shadowed`, and `shadow_count`.
- **Locks.** `index` marks the files it touched with an index-time lock (`"auto": true` in `status`). It never blocks `document_lock acquire`, yours or another agent's; an explicit lock does block other agents until it is released or expires.
- **`agent_grep` / `search_text`** for exhaustive or regex proof. Both accept **`session_id`** — they auto-index files with matches and record search history, so **`get_search_history`** tells you what you already searched. Both also accept **`working_tree=true`** to auto-index modified/untracked files before searching.
- **`search`** for exploration; use **`compact`**, **`max_result_tokens**`, or **`return_response_meta`** to cap context. Also supports **`working_tree=true`**. Results now include **`source`** (`hnsw`, `bm25`, `symbol_boost`) and **`tier2_present`** so you can see why a result ranked where it did.
- **`query`** supports **`working_tree=true**`, surfaces sub-search errors in `errors`, and applies smart defaults (auto `working_tree`, auto `path_prefix` on large projects).
- **`impact_radius`** / **`coupling`** — use `significance_threshold > 0` to filter out blast-radius and coupling noise from common stdlib/generic symbols (e.g. `push`, `has`, `addEdge`). `impact_radius` also accepts `symbol` to analyze dynamic/runtime symbols without on-disk files, and `direction` (`dependents`/`dependencies`/`both`). For base classes with high fan-in, `impact_radius` now hybridizes symbol-edge traversal with raw reference lookups and falls back to `file_dependencies` so it no longer returns zero affected chunks.
- **`coupling(..., mode="co_consumers")`** detects files co-imported by the same consumers — useful for finding hidden refactoring clusters.
- **`get_context`** returns **trust** hints (mtime vs index, staleness) and optional **`agent_notes`** per chunk. Pass **`session_id`** to record which files were fully read — check **`get_session_read_files`** to avoid re-fetching.

## Tier 2 and chunk notes

- **Tier 2 (lite):** `index` with `summaries`, **`bulk_store_summaries`**, **`llm_embed`**, **`enrichment_plan`** — improves hybrid search.
- **Tier 2 (standard/full only):** `bulk_store_embeddings` — not on default lite MCP mode; set `STELE_MCP_MODE=standard` (or use CLI/Python API).
- **Chunk notes (standard/full only):** `bulk_store_chunk_agent_notes` — JSON or text tied to a `chunk_id` (facts, invariants). Shown in `get_context`; not a substitute for summaries for search vectors.

## Trust

If **`trust.cache_aligned_with_disk`** is false or **`staleness_hint`** is true, treat cached text as potentially stale and prefer **`detect_changes`** + re-index for files you edit.

## Working tree

Use **`working_tree=true`** on `agent_grep`, `search_text`, `search`, and `query` to automatically index modified and untracked files from the git working tree before searching. This closes the gap between your editor and the index without a separate `index` call.

## Staleness calibration

`stale_chunks` defaults to `threshold=0.3`. On active codebases this can produce hundreds of warnings. Use **`threshold=0.5`** for direct-dependency changes only, or **`threshold=0.64`** for transitive changes. You can also pass **`max_age_seconds=86400`** to ignore ancient transitive staleness.

**MCP mode note:** `stale_chunks` is **not** on default **lite** mode — use `STELE_MCP_MODE=standard` (or full), or call the Python/CLI path. Lite still covers day-to-day ritual via `doctor` / `detect_changes` / `get_context` trust hints.

## Scope (zero-dep core)

The **stdlib-only** engine is in a **reasonable stopping place** for hybrid retrieval, symbol tools, and agent-oriented bounds: further gains on "semantic" quality without a bundled model are diminishing; optional **Tier 2** (your summaries/embeddings) remains the supported path for higher intent alignment. **`path_prefix`** on **`map`** / **`search`** reduces cross-tree noise; **`impact_radius(..., summary_mode=true, significance_threshold=0.1)`** keeps blast-radius output small and noise-free. See **STABILITY.md** and [CHANGELOG](CHANGELOG.md) for latest.

## MCP Modes

- **Lite** (**default**, recommended): high-leverage tools (`doctor`, `query`, `agent_grep`, symbols, cache, `enrichment_plan`, `bulk_store_summaries`, session provenance, …) plus modality utils when enabled. Prefer qualitative “high-leverage subset” over hard-coded tool counts.
- **Standard** (`STELE_MCP_MODE=standard`): broader surface with history, batch, sessions, `stale_chunks`, `bulk_store_embeddings`, chunk notes, …
- **Full** (`STELE_MCP_MODE=full`): restores deprecated singleton tool **names** for backward compatibility (prefer unified `annotations` / `document_lock` / bulk Tier-2 tools).

Cold-start ritual: `doctor` → `query` (with `session_id`) → `agent_grep` / `find_*` → `get_context`. First-run CLI: `stele-context init .`.

## Choosing groups

An agent sets Stele's groups for a project with `stele:` lines in the first `agent-tools` fenced block of the project's AGENTS.md (in the project root: the directory with `.git` or `.stele-context.toml`, else the server's working directory):

````markdown
```agent-tools
# one agent works here; CoordinationHub owns leases
stele: disable locks, notes
```
````

Groups: `core` (always on: doctor, map, get_context, ...), `index`, `search`, `symbols`, `embeddings`, `notes`, `locks`, `messages`, `kv`. The README's [Tool groups](README.md#tool-groups) table lists every tool. Turn a group off when something else does its job:

| Group | Turn it off when | Line |
|---|---|---|
| `locks` | one agent works on the project, or CoordinationHub, Trammel or Chisel coordinates the agents | `stele: disable locks` |
| `notes` | Trammel or Wikifier keeps the notes and handoffs | `stele: disable notes` |
| `messages` | CoordinationHub carries agent messages (standard/full only) | `stele: disable messages` |
| `embeddings` | no agent writes Tier-2 summaries or vectors; keyword search is enough | `stele: disable embeddings` |
| `kv` | you never save or restore session state (standard/full only) | `stele: disable kv` |
| `symbols` | the project has no code (docs, data) | `stele: disable symbols` |
| `index`, `search` | keep on: they are the cache and the lookups | |

To keep only a few groups instead, list them: `stele: tools search, symbols` (`core` stays on). Lists take commas or spaces, and `#` starts a comment. Stele reads only its own lines, so other tools' lines can share the block.

Stele checks the file's size and mtime on every request. When an edit changes the tool list it sends `notifications/tools/list_changed` and the client refetches the list; no restart. A line with an unknown name, or one that tries to switch off `core`, is ignored and reported as a warning in the `initialize` instructions and `doctor`. An env var beats the matching line: `STELE_TOOLS` replaces `stele: tools`, `STELE_DISABLE` replaces `stele: disable`.

## CLI ↔ MCP name map

| CLI | MCP / engine |
|-----|----------------|
| `detect` (`--scan-new` default **true**) | `detect_changes` (`scan_new` default true; `session_id` default `"default"`) |
| `search-text` | `search_text` |
| `agent-grep` | `agent_grep` |
| `query` | `query` |
| `find-definition` / `find-references` | `find_definition` / `find_references` |
| `impact-radius` / `coupling` | `impact_radius` / `coupling` |
| `enrichment-plan` | `enrichment_plan` |
| `serve-mcp` | MCP stdio server |

## `session_id` meanings (do not conflate)

| Use | Tools | Effect |
|-----|-------|--------|
| Search history + grep auto-index | `agent_grep`, `search_text`, `query` (grep branch) | `record_search`; may index match files |
| Read tracking | `get_context` | Records fully-read files for `get_session_read_files` |
| Working-tree / locks agent_id | `working_tree=true`, index writes | Passed as `agent_id` for index/lock attribution |

## Auto-index surfaces

| Trigger | When | What indexes |
|---------|------|----------------|
| `session_id` on `agent_grep` / `search_text` | After search | Documents with matches |
| `working_tree=true` | Before search | Git modified + untracked |
| `query` + `session_id` + dirty tree | Before search (auto) | Same as working_tree; see `applied_defaults` in response |
| `detect_changes` / `index` | Explicit | Paths / scan_new new files |

## HTTP vs stdio envelopes

HTTP returns `{"success", "result"}` or `{"error"}`. Stdio MCP returns the **bare** tool result. Both are intentional; do not unify silently.

## Rust crate

`[dependencies]` stays empty. Release steps, tag names, and secrets are in [docs/release-2.0.md](docs/release-2.0.md).

```bash
cargo test
cargo build --release
bash scripts/fetch_corpora.sh
./target/release/stele-bench floor \
  --binary target/release/stele-context --group b1 --runs 10 --check-floor
```

`stele-context` prints JSON by default (`--human` for text). `--version` prints `stele-context 2.4.0`. `stele-context serve-mcp` speaks newline JSON-RPC. Lite lists 25 tools, standard 43, full 61. Full mode runs the 18 old singleton names by routing them to the current tools.

Fixture goldens live in `parity/goldens/`. `cargo test` checks chunks and symbols against `fixtures.json`. `bash scripts/fetch_corpora.sh` fills `parity/.cache` so the rich 13.9.4 and flask 3.1.0 round-trip tests run. Historical timings are `bench/baseline/` and `bench/rust-b1.json`.

### Adding a language

1. Add the extension to `src/walk.rs` (`CODE_EXTENSIONS` or `TEXT_EXTENSIONS`) and to `src/chunk/patterns.rs` if chunk boundaries are not the JavaScript pattern.
2. Teach `src/symbols/` to emit the same names, kinds, roles, lines, and containers as the 1.5.2 `SymbolExtractor`. The fixture golden is `parity/goldens/fixtures.json`.
3. The shared scanner in `src/lang/scan.rs` already round-trips other extensions. A dedicated grammar replaces that scanner only when it still renders the original bytes, including the corpus cache.
4. Re-run `cargo test`.
