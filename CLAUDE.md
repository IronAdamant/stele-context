# Stele Context

Local context cache for LLM agents. 100% offline, zero crates.

The product is the Rust 2.0 binary (`stele-context`, `stele-context-mcp`). The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x).

## Purpose

Cache files an LLM has read so it does not re-read unchanged content. Zero runtime deps because every added dependency is a supply-chain attack surface.

## Architecture

```
src/lib.rs
  |-- cli.rs, main.rs, bin/stele-context-mcp.rs -- CLI and MCP entry
  |-- engine/ -- index, detect, search, query, symbols, locks
  |-- chunk/ -- boundaries, merge, signatures
  |-- search/ -- BM25 and stemmer
  |-- store/ -- SQLite-shaped JSON state on disk
  |-- symbols/ -- per-language extractors and edges
  |-- lang/ -- lossless scanners, including Python
  |-- syntax/ -- shared tree, incremental reparse
  |-- media/ -- PNG, GIF, JPEG, audio, video, PDF (std only)
  |-- mcp/ -- newline JSON-RPC
  |-- http_serve.rs -- local HTTP
  |-- walk.rs, gitignore.rs, config.rs, root.rs
  \-- bin/stele-bench/ -- floor check and D13 gate (not a release asset)

parity/fixtures and parity/goldens are the fixture corpus the tests read.
bench/baseline is the recorded Python 1.5.1 floor.
```

## Load-bearing invariants

Changes that violate any of these break the project's basis. Do not relax without explicit discussion.

- **Zero crates.** `[dependencies]`, `[dev-dependencies]`, and `[build-dependencies]` stay empty. `Cargo.lock` lists only `stele-context`. `cargo_manifest_and_lock_are_std_only` enforces it.
- **JSON only.** Index state is JSON. Do not introduce pickle or another binary object format.
- **mtime+size fast path for unchanged files.** Index and detect skip a full read when size and mtime match. That is the core memory feature.
- **Path handling at the CLI boundary.** Storage is worktree-consistent. `--storage-dir` and `STELE_CONTEXT_STORAGE_DIR` are the overrides.
- **No new dependency to parse a language.** Scanners live under `src/lang/` and `src/symbols/` and render the original bytes.

## Design decisions

The design log (ranking, chunk boundaries, symbol rules) lives in [`docs/architecture.md`](docs/architecture.md#design-decisions-log). The 1.x module names in that log are the Python tree on `python-legacy`. On this branch the matching code is under `src/`.

## Module boundaries

- `src/lib.rs` wires the crate. Bins in `src/bin/` call `cli::run` or, for `stele-bench`, stay out of the library.
- `src/search/bm25.rs` and the hash/json helpers do not pull in other subsystems beyond what they need to compile as one crate.
- Chunk and symbol tests read `parity/goldens/fixtures.json`. Do not point them at a generated tree that is not committed.

See [`COMPLETE_PROJECT_DOCUMENTATION.md`](COMPLETE_PROJECT_DOCUMENTATION.md).

## Development

```bash
cargo fmt --check
cargo test
bash scripts/fetch_corpora.sh
cargo build --release
```

Entry points: `stele-context` (CLI), `stele-context-mcp` (MCP stdio server).

## Agent Workflow with Stele Context

Stele's MCP tools complement native tools (Grep, Glob, Read). Pick the right tool for the job.

Default MCP mode is **lite** (`STELE_MCP_MODE=lite`): high-leverage tools only. Standard/full restore broader surfaces. See [AGENTS.md](AGENTS.md) and [docs/agent-workflow.md](docs/agent-workflow.md) for full detail.

### Cold-start ritual (start here)

```
1. doctor                              → health, index age, next_steps, Tier-2 preview
2. query "..." --session-id S           -> universal entry (semantic + symbols + grep)
3. agent_grep / find_definition|find_references as needed
4. get_context --session-id S          → cached file text + trust/diff
5. get_search_history / get_session_read_files  → avoid re-search / re-read
```

After edits: `detect_changes` (or `query`/`agent_grep` with `working_tree=true`) and re-index changed files.

### Grep-first for exact work

For identifiers, renames, and audit proof: **grep to find, grep to cache**. Every `agent_grep` / `search_text` call with `session_id` records search history and auto-indexes match files. `get_search_history` tells you what you already searched this session. Prefer `query` first for broad natural-language questions; use grep for guaranteed recall.

```
1. agent_grep "pattern" --session-id S  → results + files auto-indexed
2. get_search_history --session-id S    → "you grep'd file X, it's fresh"
3. get_context --session-id S           → full cached content, no re-read
4. find_references "symbol"             → symbol graph (definitions + usages)
```

`get_context` with `session_id` records which files were fully read. Call `get_session_read_files --session-id S` to see what you already retrieved before re-calling `get_context`.

### Tool selection guide

| Tool | Use when |
|------|----------|
| `doctor` | Session start / index health / next_steps |
| `query` | Broad natural-language questions (universal entry; merges search + symbols + grep) |
| `agent_grep` | Finding usages, auditing code. Scope-aware grep with a token budget |
| `find_references` | Looking up a specific symbol's definition and all callers |
| `find_definition` | Finding where a symbol is defined |
| `get_context` | Getting full file content from cache (records read in session) |
| `get_search_history` | Checking what you already grep'd / queried this session |
| `get_session_read_files` | Checking what you already fully read this session |
| `search` | Open-ended exploration; default is **keyword (BM25)**; use `search_mode=hybrid` when Tier-2 summaries are populated |
| `search_text` | Guaranteed complete substring/regex search (accepts `session_id` like `agent_grep`) |

### After edits
- `stele-context index --force-reindex` the changed files to update the cache
- `stele-context detect_changes` (or MCP `detect_changes`) to verify what changed
- Or pass `working_tree=true` / `session_id` on `query` / `agent_grep` / `search_text` / `search`

### Multi-agent coordination rules
- **Atomic transformations**: Never split "remove X" and "replace X with Y" across parallel agents. Both steps must happen atomically per file.
- **No overlapping files**: Parallel agents must work on disjoint file sets.
- **Lint between batches**: Run `cargo fmt --check` after each agent completes, not just at the end.
- **Reindex after passes**: After a batch of changes, reindex before the next agent reads.
