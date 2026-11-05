# Migrating from stele-context 1.5.x (Python) to 2.x (Rust)

2.x is a std-only Rust rewrite of the Python package. The target is the unmodified PyPI release `stele-context==1.5.2`: the same tools, arguments and results. `tests/mcp_parity.rs` starts both MCP servers on the same trees and compares every call (values, structure and `isError`); a second test probes each item of the fifth verification round. Anything that still differs is listed on this page, in one of four groups:

- [Accepted differences](#accepted-differences): ruled on by the owner.
- [Deliberate differences](#deliberate-differences): 2.x keeps the 1.5.2 function but not a Python defect, internal error text, or the Python packaging. Owner ruling: match function, not quirks.
- [Not supported](#not-supported): 1.5.2 work that 2.x does not do.
- [Where 1.5.2 itself varies](#where-152-itself-varies): output that changes between two runs of 1.5.2, so there is nothing fixed to match.

Open questions for the owner are at the end, under [Flagged for the owner](#flagged-for-the-owner).

## Install and where the Python code is

| Item | 2.x |
| --- | --- |
| Install | GitHub Release binaries. `scripts/install.sh` (Linux, macOS) and `scripts/install.ps1` (Windows) download `stele-context` for `STELE_VERSION` (default `2.3.0`). Or `cargo build --release`. |
| PyPI | `pip install stele-context` stays on 1.5.x. There is no 2.x wheel. A `v2.*` tag runs `.github/workflows/release-rust.yml` and does not upload to PyPI. |
| 1.5.3 | The 1.5.3 wheel prints, on stderr, `2.0.0 is the Rust release: https://github.com/IronAdamant/stele-context/releases/tag/v2.0.0` and then runs the Python commands. It names 2.0.0, not 2.3.0. |
| Python source | The 1.x package, its tests, and `publish.yml` are on the `python-legacy` branch. |
| Platforms | Release assets for `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-gnu`, `aarch64-apple-darwin`, `x86_64-apple-darwin`, `x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`, each with `stele-context` and `stele-context-mcp`. `install.sh` exits with `unsupported platform` elsewhere. |
| Runtime | No Python, no extras, no shared libraries. `[dependencies]` is empty. Python is used only as the 1.5.2 reference in tests and `stele-bench versus`. |

## What stays the same

These match 1.5.2 and are covered by the parity tests unless noted.

- **Names.** `stele-context` and `stele-context-mcp`; `stele-context serve-mcp` is the stdio server. MCP modes `lite` (default, 25 tools), `standard` (43) and `full` (61, with the 18 old singleton names). Handshake versions `2026-07-28`, `2025-11-25`, `2025-06-18`.
- **Arguments.** Each tool accepts exactly the argument names its 1.5.2 function takes (the table in `src/mcp/signature.rs` is `inspect.signature` of the 1.5.2 callables), and the server fills in its own `agent_id` for the same write tools. Only the error text differs; see [Argument errors](#deliberate-differences).
- **Project root.** Walking up from the working directory (resolved, as `Path.cwd().resolve()` does), the nearest `.git` (file or directory) or `.stele-context.toml` wins; an empty marker is enough. With neither there is no project root: document paths are stored as absolute paths, `project_root` is `null`, `detect_changes` does not scan for new files, and storage is `~/.stele-context` (`USERPROFILE` on Windows).
- **Storage override order.** `--storage-dir`, then `storage_dir` in the config, then `STELE_CONTEXT_STORAGE_DIR`, then `<project>/.stele-context`.
- **Configuration.** The `[stele-context]` table, or the top-level keys when the file has no such table (`data.get("stele-context", data)`). See [Configuration keys](#configuration-keys).
- **Walk.** `.gitignore` on by default; a file named explicitly is indexed even when ignored. Default skip set `.git`, `.hg`, `.svn`, `__pycache__`, `node_modules`, `.venv`, `venv`, `.tox`, `.eggs`, `dist`, `build`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, plus every dot-directory, plus `skip_dirs`. Files come back in `sorted(Path.rglob())` order: part by part, case-insensitively on Windows.
- **Chunks and symbols.** Chunk ids, offsets, token counts and f32 signatures in `parity/goldens/fixtures.json` match 1.5.2 with tree-sitter off and numpy not loaded. On rich 13.9.4 both report 496 documents, 1118 chunks and 32687 symbols. A PDF whose pages are all blank is one empty chunk (1 token, end 0), as in 1.5.2.
- **Change detection.** Size and mtime first, SHA-256 when they differ. `detect_changes` checks every path that has chunks, so an `llm_embed` memory chunk (`memory:<id>`) is reported in `removed`; as in 1.5.2 it is not deleted, and the next detect reports it again.
- **Search.** Keyword (BM25) is the default; `hybrid` is opt-in. BM25 over chunks with non-empty content, the identifier split (`renderWidget` adds `render`, `widget`, `renderwidget`), `symbol_boost` rows for query identifiers that name a definition, the proximity nudge, `path_prefix` ranked over `top_k * 10` before filtering, and `compact` / `max_result_tokens` / `return_response_meta`. A memory chunk created after the keyword index was built stays out of keyword results until the index is rebuilt from the store, as with 1.5.2's incremental index.
- **agent_grep and search_text.** One entry per occurrence (the first ten per chunk), more than two identical excerpts collapse with `also_in` / `also_in_files`, the token budget, and the `0 matches for '...'` summary. With `session_id` the search is recorded and matching files are indexed; `get_search_history` returns 1.5.2's rows (`pattern`, `tool`, `files_checked`, `files_with_matches`, `searched_at`). Recording a search does not create a session.
- **query.** The search branch with the caller's `top_k`, `path_prefix`, `search_mode`, `compact` and `max_result_tokens`, definitions and references of up to three query identifiers, then `agent_grep` with a 2000-token budget; `sources` counts every merged row. Automatic `working_tree` (dirty git tree with a `session_id`) and automatic `path_prefix` (more than 500 documents) follow 1.5.2.
- **Symbols.** `find_definition`, `find_references`, `impact_radius`, `coupling`. Registered dynamic symbols count in `symbol_rows` and show up in `find_definition` and `find_references` (verdict, preview text and total as 1.5.2). `register_dynamic_symbols` takes `role` and reports 1.5.2's missing-name and invalid-role errors.
- **Tier 2.** A chunk is Tier 2 when it has an agent signature: a stored summary, a stored embedding, or `llm_embed`. `doctor`, `enrichment_plan` and hybrid search count it that way. `bulk_store_embeddings` reports an empty or non-list vector as `<id>: invalid vector` and leaves it out of `total`; any other length is L2-normalised and stored. `llm_embed` takes exactly the 32 fingerprint values (`Expected 32 fingerprint values, got N` otherwise), clamped to [-1, 1], or fingerprints the first 4000 characters of `text`. Memory chunks keep those 4000 characters and `len // 4` tokens.
- **Graph numbers.** `index.max_level`, `index.avg_connections` and `search_quality.hnsw_span` come from an HNSW graph built with 1.5.2's rules (M 16, levels `random() < 1/ln 16`, the same layer search, agent vectors in place of chunk signatures). `insert_count` counts inserts the way 1.5.2's graph does: the first node is not counted, replacing a vector is one insert.
- **doctor.** Counts, `index_health`, `search_quality`, `map_preview` (its `total_tokens` is the project total), `enrichment_preview`, `next_steps`, `token_savings` and the KV size (`kv_cache_size_bytes`, the sum of the zlib files under `kv_cache/`).
- **Locks.** TTL 300 s; acquire, refresh, release, release_all, status, reap, conflicts; a refused lock returns `locked_by`, `locked_at`, `expires_at`, `worktree`. `index` through the MCP server locks what it indexed for the server's agent id (`stele-context-mcp-<pid>`); without a git directory those locks live in the store, `stats` counts them, and removing a document drops its lock.
- **Annotations, sessions, KV.** Tag filters match any tag; `get` and `search` are newest first; KV is saved per chunk and ranked by signature cosine.
- **CLI flags.** `search`, `query`, `impact-radius`, `coupling` and `agent-grep` apply the 1.5.2 flags (`--top-k`, `--path-prefix`, `--search-mode`, `--compact`, `--max-result-tokens`, `--meta`, `--session-id`, `--depth`, `--chunk-id`, `--document-path`, `--mode`, `--significance-threshold`, `--context-lines`). `annotate` needs `--type` and `--content` and exits 2 without them.
- **HTTP.** `GET /health`, `GET /tools`, `POST /call`; success is `{"success": true, "result": ...}`, a bad call is HTTP 200 `{"error": ...}`, unknown tool adds `available_tools`. Port 9876, CORS `*`.

## Accepted differences

| Difference | 2.x | 1.5.2 |
| --- | --- | --- |
| SQLite fields in `doctor.db_health` | `journal_mode`, `synchronous`, `page_count`, `page_size`, `wal_autocheckpoint`, `busy_timeout_ms` are `null`; `wal_size_bytes` is the size of a legacy `stele_context.db-wal` or 0; `database_size_bytes` is snapshot + journal + `signatures.bin` + `bm25.bin` | SQLite pragmas and file sizes. The 2.x store is JSON, so there is no database to ask. |
| Timestamps | Every time value (`indexed_at`, `created_at`, `searched_at`, ...) is the wall-clock time of the run and follows 1.5.2's rules; tests compare them within 180 s | Same rules, different moment |
| `store_embedding` `embedding` | Accepted as an alias of `vector` (1.5.2's own schema advertises `embedding`). The write is a normal embedding write: `stored: true`, and `insert_count` moves by one exactly as a `vector` write does | Its function takes `vector`, so the call raises `TypeError: ... unexpected keyword argument 'embedding'`, stores nothing, and the operation log counts a failure. Counters read after that call (`insert_count`, `recent_failures_1h`, `busy_ratio`, Tier-2 counts) therefore differ by that one write. |

## Deliberate differences

| Area | 2.x | 1.5.2 |
| --- | --- | --- |
| Argument errors | The same calls are rejected, with an error that names the tool, the field, its type and the arguments to retry with, plus "Did you mean" for a near miss, and `isError: true`. `llm_embed: chunk_id is required (string). Required: text, chunk_id. Optional: fingerprint_values, agent_id.` / `agent_grep: unknown argument 'path_prefix'. Valid arguments: pattern (required), regex, ...` HTTP prefixes `Invalid parameters for <tool>: `. | Python's `TypeError` with internal names: `_IndexMixin.llm_embed() missing 1 required positional argument: 'chunk_id'` |
| Unknown tool | `Unknown tool: X.` plus the mode that has it, the replacement of an old name, or the nearest tool name | `Unknown tool: X` |
| Error results | Any failure is `{"error": ...}` (with `hint` when one applies) and `isError: true`; a result whose only keys are `error`/`hint` is `isError: true` | Same for raised exceptions; engine errors were Python exception text |
| MCP text | The tool result is compact JSON | `json.dumps(result, indent=2)`. Values are the same; 2.x spends fewer tokens. Object key order is not guaranteed to match in every tool (for example `project_brief.extensions`); parsed values are. |
| `get_session_read_files` | Returns the files read with `get_context` in that session | Raises `no such column: d.staleness_score` |
| `get_supported_formats` | Also lists `image`, `audio`, `video`: 2.x reads them without extras | Omits a modality whose Python extra is not installed |
| `doctor.python` | The version of the `python3` (then `python`) on PATH, read from that interpreter's files (`pyvenv.cfg`, `patchlevel.h` `PY_VERSION`, or the constant in its binary); `null` when there is none. Nothing is started. | `sys.version.split()[0]` of the interpreter running 1.5.2 |
| Operation counters | `db_health.recent_operations_1h`, `recent_failures_1h`, `busy_ratio` count this server's tool calls since it started | Rows in the SQLite `operation_log`, which survive a restart |
| Graph numbers on restart | The three graph numbers are kept in `store/hnsw_stats.json` under a fingerprint of the chunk ids and agent vectors and reused while it matches | The graph is saved under `indices/` and reloaded |
| CLI output | One JSON object per command: `{"ok", "schema_version", "command", "result"}`; `--human` prints `result`. Exit codes 0 success, 1 operation error, 2 usage; errors carry `code`, `message`, `hint`. `--version` and `--help` print text. | Prose for `index`, `detect`, `stats`, `search`, `search-text`, `agent-grep` unless `--json`; argparse text on stderr |
| `index` with no path | Indexes `.` | Requires a path |
| `init` | Writes `.stele-context.toml` (`# stele-context 2.0`) if absent and returns its path | Indexes, prints the lite MCP config and doctor `next_steps` |
| `serve` | Blocks in the accept loop on `127.0.0.1` | Background thread unless `--blocking`, default host `localhost` |
| HTTP tools | `POST /call` runs lite tools only, whatever `STELE_MCP_MODE` says | Follows `STELE_MCP_MODE` |
| `tools/list` (2.2) | Every mode lists each tool with a one-line description and an `inputSchema` built from the argument table that accepts or rejects calls: each argument's type, `required`, and the allowed values of actions and modes. Lite 8,623 bytes, standard 12,463, full 16,848. | Long descriptions and schemas: lite 26,298 bytes, standard 33,326, full 37,698 |
| Compact results (2.2) | MCP and HTTP tools that ran past 10 KB on a 200-file project answer briefly unless asked. `find_definition`: `signature` and a 6-line `preview` instead of the chunk `content`, at most 10 rows (`full=true` for the text, `limit` for the row cap). `search`: `compact=true` by default. `search_text`: `limit` 20. `agent_grep`: `max_tokens` 1000, and a match in a file group does not repeat `file`. `map`: `max_documents` 50 (`0` lists all). `index`: the first 20 indexed files, `indexed_count`, `skipped_count`, `unchanged_count`, and only the skips other than `Unchanged`. `detect_changes`: no `unchanged` list (`totals.unchanged` has the count). `history`: summaries without `unchanged`. `get_notifications`: `limit` 20, rows without `worktree_root`. `full=true`, or the 1.5.2 value of the argument, gives the 1.5.2 result. `get_context` is unchanged (its job is the text). The CLI is unchanged. | Full results by default: on this repository `search` 101 KB, `find_definition` 61 KB, `agent_grep` 29 KB, `get_notifications` 30 KB, `map` 25 KB |
| Symbol line numbers (2.3, bug fix) | `line_number` in `find_definition`, `find_references` and the symbol table is the line in the file. The compact `find_definition` signature is that line. A store written before 2.3 is converted the first time it is opened in full (files that changed since get file lines when re-indexed). | The line inside the chunk: `crc32` at line 37 of its file reported line 1, a trait at line 128 reported 70, and the signature was the chunk's first line naming the symbol |
| Rust references (2.3, bug fix) | Also recorded: every name a `use` tree imports, grouped and nested (`use a::{B, c::D}`; `X` of `X as Y`), the item of a `module::item` path (`crate::a::B`, `checksum::crc32(..)`), the capitalised segments of any path, and capitalised type names in code (`dyn Trait`, `impl Trait for Type`), skipping comments, strings and the standard prelude types. On a 1,411-file Rust workspace `find_references` found 21 of 21 files for a trait (14 before), 7 of 7 for a function (4), 17 of 17 and 21 of 21 for two more symbols (6 and 5). | Only `impl X` and the last name of a simple `use a::b::X`; a grouped import recorded an empty name |
| `find_references` rows (2.3) | Each row shows `line`, the text of the line that names the symbol (up to 100 characters), instead of `content_preview` (the first 200 characters of the chunk), and leaves out `symbol`, `chunk_id` and a false `dynamic`; `full=true` gives the 1.5.2 row. | `content_preview` of the chunk head |
| `find_references` languages (2.3) | References are kept to the languages of the symbol's definitions (a Rust `crc32` no longer lists Python `zlib.crc32` calls); `other_language_references` counts what was left out and `all_languages=true` (CLI `--all-languages`) lists everything. A symbol with no definition is not narrowed. | Every language |
| Index-time locks (2.2) | The locks `index` takes for the server's agent yield to an explicit `acquire` by any agent, with no conflict logged, and never replace an explicit lock. `status` marks them `"auto": true`. | Held for 300 s against every other agent id, including the explicit id of the agent whose own server indexed the file |
| Tool groups (2.1) | `initialize` advertises `tools.listChanged: true` and returns one `instructions` line with the groups on and off. The `agent-tools` block of the project's AGENTS.md and `STELE_TOOLS` / `STELE_DISABLE` can hide groups; with none set, `tools/list` is unchanged. See README "Tool groups". | No groups; `listChanged` false; no instructions |
| Media | Images, audio and video are metadata chunks (`image png 640x480 bytes N`, `audio wav 44100Hz 3.2s ...`, `video mp4 bytes N header ...`); WAV over 30 s splits per 30 s | Pixel hashes, MFCC, decoded frames, and only when Pillow, librosa or OpenCV is installed |
| PDF text | Literal and hex strings in stream order, one chunk per page with text, adjacent pages merged with 1.5.2's rule; blank pages keep their page slot. A file that does not start with `%PDF` is one `pdf bytes N` chunk. | PyMuPDF text with its layout and reading order |
| Regex | `regex=true` rejects lookaround and backreferences (`regex_lookaround`, `regex_backreference`) | Python `re` runs them |
| Store | JSON under `<storage>/store/` (`snapshot.json`, `journal.jsonl`, `signatures.bin`, `bm25.bin`); cross-worktree locks in `<git-common-dir>/stele-context/coordination.json` | SQLite `stele_context.db`, `indices/`, `coordination.db` |
| Old databases | `migrate`, or opening a storage directory that only has `stele_context.db`, copies documents, chunks, symbols, edges, annotations, sessions, chunk history and conflicts into the JSON store. The original file is not written. `doctor` says so. | n/a |
| Hybrid search | Exact cosine against every signature up to 4096 chunks, the greedy graph walk above that | HNSW search |

## Not supported

| Item | Notes |
| --- | --- |
| Extras | No `mcp`, `tree-sitter`, `image`, `pdf`, `audio`, `video`, `performance` or `all` extras, and no tree-sitter chunking: boundaries are 1.5.2's regex path. A 1.5 index built with tree-sitter or numpy can differ; re-index. |
| SQLite artefacts | `stele_context.db` is not opened for index or search (only copied), `indices/` and `coordination.db` are not read, embeddings, `agent_signature` blobs, the operation log and search history are not imported. |
| `release` | The 1.5 PyPI release helper is not in this binary. |
| `environment_check` | Flags stale `__pycache__` and `*.egg-info` against `pyproject.toml`; it does not walk `importlib.metadata`, so editable-install checks are not done. |
| `expected_versions` on index | Accepted and ignored. |

## Where 1.5.2 itself varies

- `detect_changes` `unchanged`, `modified` and `removed` come from a Python `set`, so their order changes with `PYTHONHASHSEED`. 2.x orders them its own way; the tests compare them as sets.
- `query` looks up the definitions and references of the first three identifiers of a Python `set`. With more than three identifiers in a query, which three 1.5.2 picks changes from run to run; 2.x takes the first three in query order.
- `max_level` and `avg_connections` depend on random levels in both versions (1.5.2 gave 26.32 and 25.76 for two identical runs). `hnsw_span` follows the graph and matched 1.5.2 on every verification fixture.

## Configuration keys

`.stele-context.toml` is read by a one-level parser (scalars and arrays). 1.5.2 uses `tomllib` on Python 3.11+, so TOML outside that subset (multi-line strings, nested tables) loads there and is ignored by 2.x.

| Key | 2.x |
| --- | --- |
| `storage_dir` | After `--storage-dir`, before `STELE_CONTEXT_STORAGE_DIR` |
| `chunk_size`, `max_chunk_size` | Chunker target and cap (default 256, 4096) |
| `skip_dirs` | Added to the default skip set |
| `respect_gitignore` | `false` indexes gitignored files during a walk |
| `merge_threshold` | Fallback when a modality has no 1.5.2 threshold (code 0.85, text 0.70, pdf 0.75) |
| `change_threshold` | Fallback for modalities other than code (0.80), text (0.85), pdf (0.85) |
| `search_alpha` | Base hybrid weight, default 0.42, lowered for code-like queries as in 1.5.2 |
| `max_history_entries` | Change-log cap after an index that writes; 0 keeps every row; default 1000 |

## Flagged for the owner

- **Lite `tools/list`.** Lite carries no parameter schemas or descriptions, so an agent on the default mode cannot see what `query`, `agent_grep` or `get_context` take until it gets an argument error back. 1.5.2 sends full schemas in every mode. Restoring them makes the lite reply larger than the size the benchmark gate's `mcp-tools-list` row measures against `main`.
- **Index locks.** As in 1.5.2, `index` through the MCP server locks every file it indexed for `stele-context-mcp-<pid>` for 300 s. A second agent that passes its own `agent_id` to `document_lock` is refused for those files until the TTL runs out, even when the first agent never asked for a lock.
- **Memory chunks in `detect_changes`.** Reported as `removed` on every detect although they stay stored (1.5.2's behaviour, kept on purpose).
- **`find_definition` size.** Each definition carries its chunk's full content, as in 1.5.2; a definition inside a large `impl` block can be tens of thousands of tokens.
