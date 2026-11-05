# Stele Context

**A local, persistent code index for AI coding agents, served over MCP.** One static binary, no dependencies, no network.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Rust 1.89+](https://img.shields.io/badge/rust-1.89+-orange.svg)](https://www.rust-lang.org/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)](https://github.com/IronAdamant/stele-context)
[![CI](https://github.com/IronAdamant/stele-context/actions/workflows/rust.yml/badge.svg)](https://github.com/IronAdamant/stele-context/actions/workflows/rust.yml)

## What it is

Stele indexes a project once and keeps that index on disk. An agent (Claude Code, Cursor, any MCP client) then asks it for what it needs instead of re-reading the tree every session:

- **Search**: keyword (BM25) by default, exact text or regex, and a hybrid rank when an agent has stored summaries or vectors.
- **Symbols**: where a name is defined, who references it, what an edit would touch.
- **Change detection**: size and mtime first, SHA-256 when they differ; `get_context` returns a diff when a cached file changed.
- **Shared memory between agents and sessions**: annotations, document locks, notifications, search history, and summaries live in the project's `.stele-context/` store, so a second agent or tomorrow's session picks up where the first left off.

Everything runs offline. Stele does not bundle a model and never calls one; Tier-2 summaries and vectors are supplied by the agent.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/IronAdamant/stele-context/v2.2.0/scripts/install.sh | sh
```

On Windows run `scripts/install.ps1`, or download `stele-context-<target>` from the [releases](https://github.com/IronAdamant/stele-context/releases). From source: `cargo build --release` (Rust 1.89+). The binaries are `stele-context` and `stele-context-mcp`.

## Connect an MCP client

**Claude Code**:

```bash
claude mcp add stele-context -- stele-context serve-mcp
```

or in a settings file:

```json
{
  "mcpServers": {
    "stele-context": {
      "command": "stele-context",
      "args": ["serve-mcp"],
      "env": { "STELE_MCP_MODE": "lite" }
    }
  }
}
```

Other clients use the same command. `lite` (the default) exposes 25 tools; `standard` adds sessions, history and bulk embeddings (43); `full` adds the 1.x singleton names (61). Every mode lists each tool with a one-line description and its arguments (types, required ones, allowed values): `tools/list` is 8.5 KB in lite, 12.4 KB in standard and 16.7 KB in full (1.5.2: 26.3, 33.3 and 37.7 KB). Results that can run long come back compact (a definition is its signature and a short preview, `index` reports counts); `full=true` on those tools returns everything, see [MIGRATION.md](MIGRATION.md#deliberate-differences).

## Tool groups

Every MCP tool is in one group. `core` is always on; the others can be switched off so an agent that also runs another IronAdamant tool sees each feature once. Groups only shape the MCP tool list. The CLI is not affected.

| Group | Tools (lite, then the extra ones in standard/full) | Overlaps with |
|---|---|---|
| `core` | doctor, map, get_context, detect_modality, get_supported_formats; environment_check, clean_bytecache, batch, stats, project_brief | always on |
| `index` | index, remove, detect_changes; history, prune_history, get_chunk_history, stale_chunks | |
| `search` | search, search_text, agent_grep, query, get_search_history, get_session_read_files | |
| `symbols` | find_definition, find_references, impact_radius, coupling, register_dynamic_symbols, remove_dynamic_symbols; rebuild_symbols, get_dynamic_symbols | |
| `embeddings` | llm_embed, bulk_store_summaries, enrichment_plan; bulk_store_embeddings, store_semantic_summary, store_embedding | |
| `notes` | annotations; bulk_store_chunk_agent_notes, annotate, get_annotations, update_annotation, delete_annotation, search_annotations, bulk_annotate, store_chunk_agent_notes | Trammel notes, Wikifier notes |
| `locks` | document_lock; list_agents, acquire_document_lock, refresh_document_lock, release_document_lock, get_document_lock_status, release_agent_locks, get_conflicts, reap_expired_locks | Chisel locks, Trammel claims, CoordinationHub leases |
| `messages` | get_notifications (standard/full) | CoordinationHub messages |
| `kv` | save_kv_state, get_relevant_kv, rollback, prune_chunks, list_sessions (standard/full) | |

The agent picks the groups for a project in its AGENTS.md (see [Choosing groups](#choosing-groups)). An operator can override that in the client's `env` block:

```json
"env": { "STELE_DISABLE": "locks,notes" }
```

- `STELE_TOOLS`: comma-separated groups and/or tool names to keep (plus `core`); `all` keeps everything.
- `STELE_DISABLE`: groups and/or tool names to hide, applied after `STELE_TOOLS`.
- Case and spaces are ignored and a blank value counts as unset. They combine with `STELE_MCP_MODE`, which still picks lite, standard or full.
- An unknown name, or `core` or a core tool in `STELE_DISABLE`, stops the server with one line on stderr and exit code 2, so the client shows it as failed.
- A hidden tool is absent from `tools/list`; calling it anyway returns `isError` with the line to change, e.g. `tool "document_lock" is disabled (group "locks") by STELE_DISABLE; remove "locks" from it to enable`.
- `initialize` instructions (and `doctor`, when anything is hidden) carry one line with the groups that are on and off.

`tools/list` size with everything on, then with `locks`, `notes` or `messages` off: lite 8,512 / 8,060 / 7,932 bytes (no `messages` in lite); standard 12,352 / 11,722 / 11,571 / 12,075; full 16,737 / 14,442 / 14,082 / 16,460.

## Choosing groups

An agent sets Stele's groups for a project with `stele:` lines in the first `agent-tools` fenced block of the project's AGENTS.md (in the project root: the directory with `.git` or `.stele-context.toml`, else the server's working directory):

````markdown
```agent-tools
# one agent works here; CoordinationHub owns leases
stele: disable locks, notes
```
````

Turn a group off when something else does its job:

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

## A short session

```text
doctor                                    -> index health and next steps
index {"paths": ["."]}                    -> chunks, symbols, keyword index
query {"query": "how is auth checked", "session_id": "s1"}
find_references {"symbol": "check_token"}
get_context {"document_paths": ["src/auth.py"], "session_id": "s1"}
annotations {"action": "create", "target": "src/auth.py", "target_type": "document",
             "content": "token refresh is racy; see issue 12", "tags": ["handoff"]}
```

A second agent, or the next session, starts with `doctor`, then `annotations {"action": "get", "tags": ["handoff"]}`, `get_search_history`, and `detect_changes`. The same commands exist on the CLI:

```bash
stele-context index .
stele-context query "how is auth checked"
stele-context find-references check_token
stele-context detect
```

CLI output is JSON (`--human` for text).

## How it works

1. **Walk.** The project root is the nearest directory with `.git` or `.stele-context.toml` (an empty file is enough). `.gitignore`, dot-directories and the usual build/vendor folders are skipped. With no marker, paths are stored as absolute paths and the store lives in `~/.stele-context/`.
2. **Chunk.** Code is split at definitions and text into passages near `chunk_size` tokens; similar neighbours merge. PDFs are read page by page. Images, audio and video become metadata chunks.
3. **Index.** Each chunk gets a 128-value signature, BM25 postings, and symbol rows (definitions and references for Python, JS/TS, Go, Rust, Java, C#, Swift and more), linked into a cross-file edge graph.
4. **Store.** Everything is JSON under `<project>/.stele-context/store/` (`snapshot.json`, `signatures.bin`, `bm25.bin`). Cross-worktree locks live in `<git-common-dir>/stele-context/coordination.json`.
5. **Serve.** `serve-mcp` speaks newline JSON-RPC on stdio; `serve` exposes the lite tools over local HTTP.

`.stele-context.toml` keys (`chunk_size`, `max_chunk_size`, `skip_dirs`, `respect_gitignore`, `storage_dir`, ...) are listed in [MIGRATION.md](MIGRATION.md#configuration-keys).

## Coming from the Python 1.5 package

The Python package stays on PyPI (`pip install stele-context` installs 1.5.x) and on the `python-legacy` branch. 2.x is this Rust binary: same tools, same results, faster. A 1.5 `stele_context.db` is copied into the native store on first open and left unmodified. [MIGRATION.md](MIGRATION.md) lists every difference.

## Benchmarks

Recorded on Linux 6.12.94+ x86_64, 4 vCPU, rustc 1.99.1, release build, 5 runs, rich 13.9.4 (496 documents after image indexing). The Python column is the recorded 1.5.1 floor from an 8 vCPU host (`bench/baseline/summary.json`). Every Rust median is strictly below that floor. CI re-checks startup with `stele-bench floor --group b1 --check-floor`.

| Command | Rust median | Rust min-max | Peak RSS | Python 1.5.1 median |
|---|---:|---:|---:|---:|
| `cli --version (startup)` | 1.8 ms | 1.7-2.5 | 10.5 MB | 79.4 ms |
| `cli --help` | 1.7 ms | 1.7-1.8 | 10.6 MB | 75.7 ms |
| `cli index . (cold, empty store)` | 8849.8 ms | 8823.0-8860.3 | 82.8 MB | 10656.1 ms |
| `cli index . (warm, nothing changed)` | 72.6 ms | 71.7-75.8 | 69.6 MB | 197.8 ms |
| `cli detect (nothing changed)` | 66.7 ms | 66.4-66.9 | 69.7 MB | 167.9 ms |
| `cli stats` | 62.7 ms | 62.7-62.8 | 69.6 MB | 162.5 ms |
| `cli doctor` | 50.6 ms | 50.5-51.2 | 52.0 MB | 189.7 ms |
| `cli map --json` | 97.5 ms | 97.2-97.7 | 69.6 MB | 163.3 ms |
| `cli project-brief --json` | 61.4 ms | 61.2-62.2 | 69.7 MB | 165.5 ms |
| `cli search hybrid --json` | 72.2 ms | 72.1-72.3 | 69.6 MB | 169.0 ms |
| `cli search keyword --json` | 71.9 ms | 71.5-72.3 | 69.6 MB | 161.2 ms |
| `cli search-text --json` | 62.5 ms | 62.0-62.9 | 69.6 MB | 141.9 ms |
| `cli agent-grep --json` | 63.9 ms | 63.7-93.6 | 69.7 MB | 228.3 ms |
| `cli query --json` | 73.3 ms | 72.9-74.0 | 69.7 MB | 193.5 ms |
| `cli find-definition --json` | 61.8 ms | 61.5-62.2 | 69.6 MB | 141.4 ms |
| `cli find-references --json` | 63.7 ms | 63.3-64.0 | 69.7 MB | 166.2 ms |
| `cli impact-radius --json` | 66.9 ms | 66.7-67.4 | 69.7 MB | 179.6 ms |
| `cli coupling --json` | 63.4 ms | 63.0-63.5 | 69.7 MB | 185.3 ms |
| `cli enrichment-plan --json` | 62.4 ms | 62.0-68.9 | 69.7 MB | 137.7 ms |
| `cli history --json` | 61.7 ms | 61.2-61.9 | 69.7 MB | 135.1 ms |

## Development

```bash
cargo fmt --check
cargo test        # parity tests need PyPI stele-context==1.5.2 + pymupdf (see CONTRIBUTING.md)
cargo build --release
./target/release/stele-bench versus --binary ./target/release/stele-context --runs 3
```

`[dependencies]`, `[dev-dependencies]` and `[build-dependencies]` stay empty; `cargo_manifest_and_lock_are_std_only` enforces it. Python 1.5.2 is used only as the reference in tests and `stele-bench versus`; the binaries never start Python. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
