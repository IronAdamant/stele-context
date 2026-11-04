# Migrating from stele-context 1.5.x (Python) to 2.0 (Rust)

2.0.1 closes the gaps below that 2.0.0 left open. Rows marked **same** now match 1.5.2 for that input. Rows that are still different stay marked **intentional change** or **not supported**, with the reason.

Closed in 2.0.1 (same, unless noted):

- Standard tools that used to return `note: accepted` run: `prune_history`, `rollback`, `prune_chunks`, `list_sessions`, `rebuild_symbols`, `stale_chunks`, `get_dynamic_symbols`, `list_agents`, `get_notifications`, `environment_check`, `clean_bytecache`, `bulk_store_embeddings`, `bulk_store_chunk_agent_notes`, `get_chunk_history`, `batch`.
- Full mode runs the 18 old singleton names by calling those implementations. They do not return `tool_removed`.
- `migrate` reads `document_path` and the other 1.5 tables (`chunks`, `symbols`, `symbol_edges`, `annotations`, `sessions`, `chunk_history`, `document_conflicts`). A storage directory that has `stele_context.db` and no native documents is copied on open. The original database is not written. `doctor` says the copy happened.
- `chunk_size`, `skip_dirs` (added to the defaults, not a replacement), and `respect_gitignore` are applied.
- CLI flags `--compact`, `--path-prefix`, `--regex`, `--working-tree`, `--confirm`, `--limit`, `--document`, `--session`, `--depth`, `--significance-threshold`, `--mode`, `--group-by`, `--summary-mode`, `--top-n`, and `--min-tokens` are accepted. `--regex` is required before a pattern is a regex. `--confirm` is required before `clear` deletes the store.
- HTTP `GET /tools` and stdio `tools/list` in `standard` and `full` list the properties the dispatcher reads. Lite stdio `tools/list` stays `{"type":"object"}` so that reply stays the same size as 2.0.0. `paths`, `summaries`, `document_paths`, and `working_tree` are honoured.
- PDF page text from uncompressed and Flate streams ends with a newline, so the one-page fixture matches PyMuPDF `get_text()` (`[Page 1]\nHello PDF symbols\n`).

Still not the same, and why:

- Image, audio, and video chunk bodies are header metadata. Pixel hashes, MFCC, and decoded frames need Pillow, librosa, or OpenCV, which 2.x does not link.
- PDF text is literal and hex strings in stream order. PyMuPDF layout, fonts, and reading order are not reproduced. A PDF that is not `%PDF` or has no text streams is one metadata chunk, not an empty PyMuPDF document.
- User-facing regex rejects lookaround and backreferences.
- There is no on-disk HNSW graph. Hybrid search uses exact cosine of the 128-d signature, and the HNSW cut only when the corpus is above 4096 chunks.
- `environment_check` reports stale `__pycache__` and `*.egg-info` against `pyproject.toml` (orphaned name, version different from the project). It does not walk `importlib.metadata`, so an egg-info that matches `pyproject.toml` but not a separately installed wheel is not flagged. Editable-install checks need that same installed-distribution list.
- The CLI still prints the `ok` / `schema_version` / `command` / `result` envelope. `--human` prints `result` only.

This is the list of differences between the Python package on PyPI (1.5.x, source on the `python-legacy` branch) and the Rust 2.0.1 binary on `main`. It was checked against that branch, `parity/goldens`, `CHANGELOG.md`, and [PR #5](https://github.com/IronAdamant/stele-context/pull/5) (merged as the Rust rewrite; the parity notes are the PR body plus commit `491bd8a`).

Each row is marked:

- **same**: a 1.5.2 caller gets the same result, or the same rule
- **intentional change**: 2.0 does this on purpose
- **bug fixed from Python**: 2.0 does not copy a 1.5 defect. The result change is stated
- **not supported**: the 1.5 input, flag, or tool does not do that work in 2.0

The parity audit's six extractor and token gaps were Rust defects. They were fixed so 2.0 matches 1.5.2. They are listed under [Parity audit](#parity-audit-rust-was-brought-in-line-with-152) and are not marked "bug fixed from Python".

## What stays the same

- Program names: `stele-context` and `stele-context-mcp`. `stele-context serve-mcp` is the stdio server. The `stele-context-mcp` binary is the same server, selected by the file name.
- Project root: walking upward, the nearest `.git` (directory or file, so a worktree counts) or `.stele-context.toml`. An empty marker file is enough. With neither, storage is `~/.stele-context` (`USERPROFILE` on Windows).
- Storage override order: `--storage-dir`, then `storage_dir` in `[stele-context]`, then `STELE_CONTEXT_STORAGE_DIR`, then `<project>/.stele-context`.
- Unchanged files: index and detect skip a full read when size and mtime match. A change is then hashed with SHA-256.
- Default search mode is `keyword` (BM25). `hybrid` is opt-in. That default dates from 1.3.0.
- `.gitignore` is on by default for directory walks. A file you name on the command line is indexed even when gitignore matches it.
- Default skip directory names match 1.5.2: `.git`, `.hg`, `.svn`, `__pycache__`, `node_modules`, `.venv`, `venv`, `.tox`, `.eggs`, `dist`, `build`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`. 2.0 also skips every name that starts with `.` during a directory walk.
- Default chunk size 256, max chunk size 4096, merge threshold 0.7, search alpha 0.42, history cap 1000. The toml keys of those names are applied. See [Configuration keys](#configuration-keys).
- `detect` / `detect_changes` scan for new files by default. `--no-scan-new` turns that off.
- MCP mode: `STELE_MCP_MODE` defaults to `lite`. `standard` and `full` add the same tool names as 1.5.2 (25 lite, 43 standard, 61 full).
- HTTP `POST /call` returns `{"success": true, "result": ...}` or `{"error": ...}`. Stdio MCP returns the tool result, not that HTTP envelope. Port 9876. Routes: `GET /health`, `GET /tools`, `POST /call`.
- Lock TTL is 300 seconds. `force` on acquire records a conflict and the new holder is the one that shows up afterwards (PR #5 follow-up, commit `796ae2c`).
- With tree-sitter not installed and numpy not loaded, chunk ids, offsets, token counts, and f32 signatures in `parity/goldens/fixtures.json` match 1.5.2. On rich 13.9.4 with images indexed, both sides report 496 documents, 1118 chunks, and 32687 symbols. A full symbol-edge rebuild is 4939 edges (4412 `cross_file`, 461 `intra_file`, 66 `test_of`). Keyword and hybrid top-10 overlap on the 25 audit queries was 10/10 (PR #5).
- `find_references` and `find_definition` read the symbol table. They do not read the edge table. The live resolver drops `container` before matching, as 1.5.2 does.

## Install, platforms, and where the Python code is

| Item | Mark | What 2.0 does |
| --- | --- | --- |
| Install | intentional change | GitHub Release binaries. `scripts/install.sh` (Linux and macOS) and `scripts/install.ps1` (Windows) download `stele-context` for `STELE_VERSION` (default `2.0.0`) from `IronAdamant/stele-context`. Or download the asset. Or `cargo build --release` from this repo. |
| PyPI | same for 1.5 | `pip install stele-context` stays on 1.5.x. There is no 2.x wheel. A `v2.*` tag runs `.github/workflows/release-rust.yml` and does not upload to PyPI. A `v1.*` tag is cut from `python-legacy`, where `publish.yml` still lives. |
| 1.5.3 process | same on the Python side | The 1.5.3 `stele-context` command prints, on stderr, that 2.0.0 is the Rust release and the v2.0.0 URL, then runs the Python commands. It is not the Rust binary. |
| Python source | same location | The Python package, tests, and `publish.yml` are on `python-legacy`. |
| Platforms | intentional change | Release assets: `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-gnu`, `aarch64-apple-darwin`, `x86_64-apple-darwin`, `x86_64-pc-windows-msvc`, `aarch64-pc-windows-msvc`. Each triple also has `stele-context-mcp-<triple>`. `install.sh` / `install.ps1` install `stele-context` only. `serve-mcp` works on that binary. |
| `install.sh` platforms | not supported | Anything other than Linux x86_64, Linux aarch64, Darwin arm64, and Darwin x86_64 exits with `unsupported platform`. Windows uses `install.ps1`. |
| Extras | not supported | No `mcp`, `tree-sitter`, `image`, `pdf`, `audio`, `video`, `performance`, or `all` extras. `[dependencies]` is empty. No tree-sitter, numpy, Pillow, PyMuPDF, librosa, or OpenCV. |
| Python requirement | not supported | 2.0 does not need Python 3.10+. `migrate` reads the SQLite file itself. |
| npm launcher | not supported as the install | `npm/stele-context` is a launcher. The tag workflows do not publish it. |

## Index files and old databases

1.5 stores SQLite at `<storage>/stele_context.db` (WAL) and vector files under `<storage>/indices/`. Cross-worktree locks are `<git-common-dir>/stele-context/coordination.db`.

2.0 stores:

| Path | Role |
| --- | --- |
| `<storage>/store/snapshot.json` | Documents, chunks, symbols, edges, annotations, sessions, locks |
| `<storage>/store/journal.jsonl` | One JSON object per successful mutation |
| `<storage>/store/signatures.bin` | f32 signatures |
| `<storage>/store/bm25.bin` | BM25 postings |
| `<storage>/store/lock` | Process lock for the snapshot |
| `<git-common-dir>/stele-context/coordination.json` | Shared holders and the conflict log |

| Item | Mark | Result |
| --- | --- | --- |
| Opening `stele_context.db` for index, search, or detect | not supported | The file is not opened. `doctor` sets `legacy_sqlite_present` when the file is there. |
| `indices/` and any HNSW file | not supported | Not read. Hybrid search does not load that directory. |
| `coordination.db` | not supported | Not read. Old holders do not carry over. |
| Sessions, annotations, embeddings, `agent_signature` blobs, chunk history, operation log, search history | not supported | Not imported. |
| Re-index | the path that works | `stele-context index .` builds the native store from the tree. |
| `stele-context migrate` and open | same | A 1.5 database is read in place, including its `-wal` file. `documents.document_path` is the path column. Chunks, symbols, edges, annotations, sessions, chunk history, and conflicts are copied. The original files are not written. If the native store already has documents, the database is left untouched. |
| `doctor` when the db was copied | same | `next_steps` says the database was copied into the native store and was not modified. |

`version_count` in `GET /health` is 0. There is no `chunk_history` table.

## Bug fixed from Python

| Item | Mark | What changed in results |
| --- | --- | --- |
| PDF open path in 1.5.1 | bug fixed from Python | 1.5.1 `read_and_hash` decoded the PDF as UTF-8 text. PyMuPDF `fitz.open` treated that string as a filesystem path and raised `FileNotFoundError: no such file: '%PDF-1.7'` plus the rest of the decoded file. 1.5.2 already reads the bytes (CHANGELOG 1.5.2). 2.0 also reads bytes and does not call PyMuPDF. A valid PDF indexes instead of raising that error. Page text is still not the PyMuPDF string; that part is an intentional change, next section. |

No other 1.5 extractor defect is copied differently by 2.0. The audit items below were the other way around.

## Parity audit: Rust was brought in line with 1.5.2

PR #5's parity pass (commit `491bd8a`, message "Match Python 1.5.2 chunk and symbol counts on rich") started from Rust at 1088 chunks and 30491 symbols on rich and finished at the 1.5.2 counts (1097 chunks and 32687 symbols before images; 1118 chunks and the same 32687 symbols with Pillow images). The notes call these Rust extractor bugs. After the fix, a 1.5.2 tree-sitter-off index and a 2.0 index agree. Mark: **same**.

| Gap in Rust before the fix | 1.5.2 behaviour that 2.0 now follows | Count move cited in the audit |
| --- | --- | --- |
| Annotation scan consumed `=` / `)` across whitespace, and a class dedent ended the module | The walk stays in the module through a multi-line annotated signature | about +4650 symbols (rich was 30491 vs 32687) |
| A `def` span ended before a comment-first or blank-first body | The suite is indented after the header | +8 chunks (`pretty.py` and seven tests) |
| Invalid chunks still used the hand parser | `ast.parse` failure keeps the regex fallback (definitions and imports only), including a bare trailing `@decorator` | -1659 symbols if the hand parser is kept |
| Attribute middles (`self.text.wrap` emitting `text`) and Store targets were emitted | Those are not reference loads | -1824 symbols |
| Names inside `from x import (` and some annotation loads were missed | Those names are recorded | +1192 symbols |
| Token regex did not split ACRONYM words or Devanagari marks the way Python `\w` does | `estimate_tokens` follows that pattern, including `JSONHighlighter` (two tokens) and `README.hi.md` | chunk boundaries line up; the audit cited 5596 vs 4860 tokens on `README.hi.md` before the fix |

Call symbols use the first line of the call expression (`ast.Call.lineno`), not the callee's line (commit `796ae2c`). Text and code symbol lines on rich match 1.5.2 at 32687 symbols.

Non-Python symbols follow the 1.5.2 regex extractors. 2.0 does not add names a fuller parser would find and 1.5.2 missed. That was a choice to keep the goldens, not a fix of those extractors.

## Configuration keys

`[stele-context]` is read by a one-level parser (scalars and arrays). A file it cannot parse yields an empty config, which is also what 1.5 does on a parse error. 1.5 on Python 3.11+ uses `tomllib`, so multiline strings and other TOML that 2.0 rejects still load there. Mark: **intentional change** for syntax outside the subset.

| Key | Mark | 2.0 |
| --- | --- | --- |
| `storage_dir` | same | Used, after the flag and before the environment variable |
| `chunk_size`, `max_chunk_size` | same | Passed to the text and code chunkers. Default 256 and 4096. |
| `skip_dirs` | same | Added to the default skip set. Names starting with `.` are still skipped, as in 1.5.2. |
| `respect_gitignore` | same | `false` indexes gitignored files during a directory walk. An explicit file path is still indexed. |
| `merge_threshold` | same | Fallback when the modality is not in the 1.5.2 table (code 0.85, text 0.70, pdf 0.75). |
| `change_threshold` | same | Fallback for modalities other than code (0.80), text (0.85), and pdf (0.85) when a file's bytes change. |
| `search_alpha` | same | Base hybrid weight. Default 0.42. The query still lowers it for code-like text, as in 1.5.2. |
| `max_history_entries` | same | After an index that writes, the change log is capped at this count. 0 keeps every row. Default 1000. |

## CLI

2.0 prints one JSON object on stdout for commands:

```json
{"ok": true, "schema_version": "2.0.1", "command": "stats", "result": {}}
```

`--human` prints `result` only, still JSON, not the 1.5 prose. `--json` is accepted and changes nothing, because JSON is already the default. `--version` prints `stele-context 2.0.1` and `--help` prints text. Neither is JSON.

1.5 prints prose for `index`, `detect`, `stats`, `search`, `search-text`, and `agent-grep` unless `--json` is set. `query`, `find-definition`, `find-references`, `impact-radius`, and `coupling` default to indented JSON of the engine object, with no `ok` or `schema_version` wrapper. 1.5.3 also prints the Rust release URL on stderr before the command.

Exit codes in 2.0: 0 success, 1 operation error, 2 usage. A closed stdout pipe exits 0. An error body is `error.code`, `error.message`, `error.hint`, `error.retryable` (false). 1.5 argparse usage is text on stderr and exit 2. Command failures are text on stderr and exit 1.

| Command | Mark | 2.0 |
| --- | --- | --- |
| `doctor`, `index`, `remove`, `detect`, `stats`, `map`, `project-brief`, `search`, `search-text`, `agent-grep`, `query`, `find-definition`, `find-references`, `impact-radius`, `coupling`, `enrichment-plan`, `history`, `init`, `serve`, `serve-mcp`, `clear` | same names | Present. `clear`, `get-context`, `annotations`, and `document-lock` run, and they are not listed in `--help`. |
| `index` with no path | intentional change | Indexes `.`. 1.5 requires at least one path. |
| `index` of a directory | same result keys | Drops stored documents under that directory that the walk no longer sees. `index one-file` does not drop other documents. The result matches 1.5.2: `indexed` (path, chunk_count, total_tokens, modality), `skipped` (path, reason, chunk_count), `errors`, `conflicts`, `total_chunks`, `total_tokens`. |
| `index --force` and `--force-reindex` | same effect | Both reindex. There is no summaries or `expected_versions` flag. |
| `detect` | same keys | Compares size and mtime. Result keys: `unchanged` (paths), `modified`, `new`, `removed`, `conflicts`, `kv_restored`, `kv_reprocessed`. New files are listed and not indexed. Changed files are reindexed. `limit` defaults to 200 and adds `totals` when a list is cut. |
| `search` | same list | `--search-mode keyword\|hybrid` and `--top-k` (default 10). The engine result is the 1.5 list (chunk_id, content, document_path, relevance_score, token_count, start_pos, end_pos, source, tier2_present, and `proximity_boosted` when the proximity nudge runs). The CLI wraps that list in the envelope. `--path-prefix` filters. `--compact` on MCP replaces `content` with `content_preview`. |
| `search-text` and `agent-grep` | same shape | A pattern is a substring unless `--regex` is set. Lookaround and backreferences are rejected. `search-text` returns `pattern`, `regex`, `match_count`, `chunk_count`, `results`, `files_checked`, `files_with_matches`. `agent-grep` returns `summary`, `groups`, `total_matches`, `shown_matches`, `truncated`, `total_tokens`, `files_checked`, `files_with_matches`. `--session-id` records the search and indexes files that matched. |
| `query` | intentional change | One positional string. Result keys: `search` (keyword, top 8), `definitions`, `grep`. Not one merged list, and no `applied_defaults` or `suggested_next`. No `--path-prefix`, `--working-tree`, `--compact`, or `--max-result-tokens`. `session_id` on the MCP call is not applied to this tool. |
| `find-definition` | same | Payload is `symbol`, `definitions`, `count`, `symbol_index`, `guidance`. Shadow fields are set only when one file has more than one definition, and `definition_index` starts at 1. `find-references` adds `verdict`, `references`, and `total`. |
| `impact-radius` | intentional change | `--symbol NAME`, or the first positional as the symbol. Depth is 2. Result: `symbol`, `affected_files`, `affected_chunks`, `affected` (paths). `--summary-mode` is accepted and ignored. `--depth`, `--document-path`, `--chunk-id`, `--significance-threshold`, and `--direction` are unknown flags (exit 2). PR #5 compared file sets, not this JSON: `Console` at depth 2 was 162 files on both sides, and `coupling` of `rich/console.py` was 144 files on both sides. |
| `coupling` | intentional change | One path. Result includes `coupled_files`. No `--mode` (`edges` vs `co_consumers`) and no `--significance-threshold`. |
| `map` | intentional change | `documents` count and `files` rows (`path`, `chunks`, `symbols`, `bytes`), sorted by path. No `--compact`, `--max-documents`, or `--path-prefix`. |
| `stats` and `project-brief` | intentional change | Both return `documents`, `chunks`, `symbols`, `annotations`. `project-brief` is that object. No `--compact` or `--top-n`. |
| `history` | intentional change | The journal: `entries` and `history`. No `--limit` or `--document`. |
| `enrichment-plan` | intentional change | `chunks_missing_summary` and up to 50 `next` rows (`chunk_id`, `path`). No `--top-n` or `--min-tokens`. |
| `init` | intentional change | Writes `.stele-context.toml` containing the line `# stele-context 2.0` if the file is absent, and returns `path`. It does not index and it does not print an MCP snippet. 1.5 `init` indexes, prints the lite client config, and prints doctor `next_steps`. |
| `clear` | intentional change | Deletes the in-memory state and saves. No `--confirm` and no prompt. |
| `doctor` | intentional change | JSON under `result`: `version`, `implementation` (`rust`), `project_root`, `storage_dir`, `config_loaded`, `native_store_present`, `legacy_sqlite_present`, counts, `index_health`, `db_health`, `search_quality`, `environment.issues` (always empty), `map_preview`, `recommended_mcp_mode` (`lite`), `token_savings`, `next_steps` (strings). Not the 1.5 doctor object (`python` is null). |
| `get-context PATH` | same shape | `{unchanged, changed, new}`. An unchanged file has `chunks` (`chunk_id`, `content`, `start_pos`, `end_pos`, `token_count`, `content_truncated`, `staleness_score`), `total_tokens`, and `trust`. A changed file has `old_hash`, `new_hash`, and `diff_since_cache` (unified diff, `diff_exact`, added and removed line counts, `diff_tokens`, `diff_truncated`, `recommendation`). `--session-id` records the read. |
| `annotations` and `document-lock` | intentional change | `--action` and a positional. See MCP section for the actions that run. |
| `annotate`, `get-annotations`, `delete-annotation`, `update-annotation` | not supported | Use `annotations`. |
| `release` | not supported | The 1.5 PyPI helper is not in this binary. |
| `serve` | intentional change | Blocks in the accept loop. Default `--host` is `127.0.0.1` (1.5 default is `localhost`). `--port` default 9876. No `--blocking`. 1.5 starts a background thread unless `--blocking`, then the CLI still waits. |
| `--summary-mode` | not supported | Parsed and ignored on every command. |

## Errors

| Case | Mark | 2.0 |
| --- | --- | --- |
| Unknown command or flag | intentional change | Exit 2, `error.code` `usage`, hint `run stele-context --help` |
| Storage or command failure | intentional change | Exit 1, `error.code` `storage` or `command_failed` |
| Regex lookaround or backreference | not supported | `regex_lookaround` or `regex_backreference` from the search call. 1.5 `re` would run those patterns. |
| MCP unknown method | same class of error | JSON-RPC error, code `-32700` on a line that is not JSON. A notification (no `id`) is ignored. |
| Old singleton MCP name | same | Full mode runs the name. The call is the unified tool (`annotations`, `document_lock`, `doctor`, `bulk_store_summaries`, `bulk_store_embeddings`, or `bulk_store_chunk_agent_notes`). |
| Listed tool with no body | same | The 15 standard names run. An unknown name returns `unknown tool`. |

## MCP tool names and schemas

Lite names match 1.5.2, including `detect_modality` and `get_supported_formats`: `index`, `remove`, `detect_changes`, `search`, `agent_grep`, `search_text`, `query`, `find_references`, `find_definition`, `impact_radius`, `coupling`, `get_context`, `doctor`, `map`, `document_lock`, `annotations`, `register_dynamic_symbols`, `remove_dynamic_symbols`, `llm_embed`, `bulk_store_summaries`, `enrichment_plan`, `get_search_history`, `get_session_read_files`, `detect_modality`, `get_supported_formats`.

Handshake protocol versions: `2026-07-28`, `2025-11-25`, `2025-06-18`.

| Surface | Mark | What a caller sees |
| --- | --- | --- |
| Stdio `tools/list` | intentional change | `inputSchema` is `{"type":"object"}` with no properties. `description` is `stele-context <name>`, except removed names, whose description is `Removed in 2.0. Use <replacement>.` |
| HTTP `GET /tools` | intentional change | The body is the 1.5 lite schema file (`src/mcp/http_tools.json`): `name`, `description`, and `parameters` with the 1.5 properties. The call path does not read every property. Unknown keys are ignored. |
| HTTP tool set | intentional change | `POST /call` allows lite names only, even when `STELE_MCP_MODE` is `standard` or `full`. 1.5 HTTP follows that variable. |
| Argument names the dispatcher reads | intentional change | `path` or `document_path` (string), `query` or `text` or `pattern`, `symbol` or `name`, `session_id`, `search_mode`, `action`, `agent_id`, `force`, `key`, `value`, `chunk_id`, `summary`, `body`. |

Calls that send only the 1.5 property names therefore miss:

| Tool | Mark | What happens |
| --- | --- | --- |
| `index` | not supported for the 1.5 body | Schema requires `paths` (array). The dispatcher reads `path`. A call with only `paths` indexes `.`. `force_reindex`, `summaries`, and `expected_versions` are ignored. |
| `search` | same for the knobs that change the set | `top_k`, `search_mode`, `path_prefix`, and `working_tree` are read. `compact` and `max_result_tokens` are accepted and do not trim bodies. |
| `search_text`, `agent_grep` | same | `regex` defaults off, so `file.py` is a substring. `document_path`, `limit`, `group_by`, `max_tokens`, and `working_tree` are read. `session_id` records the search and indexes files that matched. |
| `query` | intentional change | See the CLI row. Not a merged list. |
| `get_context` | same for the path list | `document_paths` returns `unchanged`, `changed`, and `new`. A file whose size or mtime differs includes `diff_since_cache` (`diff`, `diff_exact`, added and removed line counts, `recommendation`). |
| `detect_changes` | same | `document_paths`, `limit`, and `scan_new` are read. New files are reported and not indexed. Changed files are reindexed. |
| `impact_radius` | intentional change | Symbol only, depth 2. `chunk_id`, `document_path`, `summary_mode`, `significance_threshold`, `direction`, and `depth` are ignored. |
| `coupling` | intentional change | `document_path` only. `mode` and `significance_threshold` are ignored. |
| `annotations` | intentional change | Actions `create`, `get`, `delete`, `update`, `search`, `bulk_create`. `bulk_create` stores one note. The dispatcher reads `path`, not `target`, and `body`, not `content`. Tags and `annotation_id` are ignored. `delete` matches id or path. |
| `document_lock` | intentional change | Actions `acquire`, `release`, `refresh`, `status`, `release_all`, `reap`, `conflicts`. `ttl` and `limit` are ignored (TTL 300). `force` is read. Storage is the JSON coordination file, not `coordination.db`. |
| `bulk_store_summaries` | same | `summaries` is a chunk id to text object. One `chunk_id` plus `summary` still works. |
| `register_dynamic_symbols` | same | `symbols` is a list, stored under `runtime:<agent_id>:<name>`. |
| `remove_dynamic_symbols` | same | `agent_id` removes that agent's runtime symbols. |
| `llm_embed` | same | With no `fingerprint_values`, the vector is 1.5.2 `semantic_fingerprint` spread by the same Gaussian. A 32-value list uses that spread. A 128-value list is stored as given. |
| `get_search_history`, `get_session_read_files` | same role, smaller payload | Session id defaults to `default` when omitted. |
| `save_kv_state`, `get_relevant_kv` | intentional change | Standard mode. KV is a list of `{k, v}` strings on the session. Not the 1.5 relevance lookup. |
| `history` | intentional change | Standard mode. The journal, not `change_history`. |
| `doctor`, `map`, `enrichment_plan`, `detect_modality`, `get_supported_formats` | intentional change | They run. Shapes are the 2.0 objects, not the schema's 1.5 description. `get_supported_formats` reports metadata for images, one metadata line for non-WAV audio, WAV duration (30s segments when longer), container brand for video, and uncompressed or Flate PDF text. |

Those 15 standard names run in 2.0.1 (**same**).

Full-mode names run in 2.0.1 by calling the tool in the replacement column (**same**):

| Removed name | Replacement named in the error |
| --- | --- |
| `stats`, `project_brief` | `doctor` |
| `annotate`, `get_annotations`, `delete_annotation`, `update_annotation`, `search_annotations`, `bulk_annotate` | `annotations` |
| `acquire_document_lock`, `refresh_document_lock`, `release_document_lock`, `get_document_lock_status`, `release_agent_locks`, `get_conflicts`, `reap_expired_locks` | `document_lock` |
| `store_semantic_summary` | `bulk_store_summaries` |
| `store_embedding` | `bulk_store_embeddings` |
| `store_chunk_agent_notes` | `bulk_store_chunk_agent_notes` |

`stats` and `project_brief` run as CLI commands and as full-mode MCP tools. `stats` calls `doctor`'s count object.

There is no self-healing `hint` on HTTP tool errors beyond the `error` string. 1.5 attaches hints for some failures. Mark: **not supported**.

## Locks

| Item | Mark | 2.0 |
| --- | --- | --- |
| TTL 300, acquire, refresh, release, release_all, status, reap, conflicts | same actions | Refresh by a non-owner returns `refreshed: false` and `reason: not_owner`. Release by a non-owner returns `not_owner`. |
| Force steal | same outcome | A conflict row is written (`path`, `holder`, `requester`, `at`). The coordination file then shows the new holder. |
| Where it is stored | intentional change | `<git-common-dir>/stele-context/coordination.json` when `.git` resolves. The snapshot also keeps a copy. No git directory: snapshot only. `coordination.db` is not opened. |
| Optimistic `expected_versions` on index | not supported | Ignored. |
| Agent registry and notifications | same | `list_agents` lists lock holders. `get_notifications` returns the coordination rows written when a file is indexed, then the count. |

## HTTP serve

| Item | Mark | 2.0 |
| --- | --- | --- |
| Routes and envelope | same | `GET /health`, `GET /tools`, `POST /call` with `{"tool", "parameters"}`. Success is `{"success": true, "result"}`. A tool error string is HTTP 200 `{"error": "..."}`. Unknown tool is HTTP 200 `{"error": "Unknown tool: ...", "available_tools": [...]}`. Bad JSON or a missing `tool` is HTTP 400. |
| `/health` storage fields | same names, different counts | The 20 keys match 1.5 `get_storage_stats`: `chunk_count`, `document_count`, `session_count`, `total_tokens`, `version_count`, `annotation_count`, `history_count`, `kv_cache_size_bytes`, `database_size_bytes`, `storage_dir`, `symbol_count`, `definition_count`, `reference_count`, `edge_count`, `runtime_symbol_count`, `locked_documents`, `expired_locks`, `active_lock_agents`, `total_conflicts`, `last_conflict_at`. `version_count` is 0 (no chunk history). `history_count` is journal lines, not `change_history` rows. `database_size_bytes` is snapshot + journal + `signatures.bin` + `bm25.bin`. `runtime_symbol_count` is the dynamic-symbol list, not rows whose chunk id starts with `runtime:`. `locked_documents` is the lock list length. |
| Bind | intentional change | One thread per connection, `std::net`, host `127.0.0.1` unless `--host` is set. No `--blocking`. |
| Mode | intentional change | HTTP is lite-only. See MCP. |
| CORS | same header | `Access-Control-Allow-Origin: *`. |

## Media and PDF

2.0 always indexes the extensions in `src/walk.rs` (`png`, `jpg`, `jpeg`, `gif`, `webp`, `bmp`, `tiff`, `tif`, `ico`, `mp3`, `wav`, `ogg`, `flac`, `m4a`, `aac`, `wma`, `mp4`, `mov`, `mkv`, `webm`, `avi`, `pdf`). 1.5 indexes images, PDF, audio, and video only when Pillow, PyMuPDF, librosa, or OpenCV import. Mark for "no extra package required": **intentional change**.

| Case | Mark | Result |
| --- | --- | --- |
| Images with Pillow not installed | intentional change | 1.5 skips them. 2.0 stores one metadata chunk, `image <ext> <width>x<height> bytes <len>` (width and height 0 when the header is not read). On rich 13.9.4 that is 475 documents and 1097 chunks without those images, and 496 documents and 1118 chunks with them. Symbol count stays 32687. |
| Images, audio, and video with the 1.5 extras | intentional change | 1.5 stores bytes (pixel hash, histogram, samples, frames). 2.0 stores metadata text. Content hashes differ. The generated fixture (`parity/fixtures/media`, golden `parity/goldens/media.json`) is 7 documents, 7 chunks, 0 symbols on both sides. |
| WAV | intentional change | `audio wav <rate>Hz <seconds>s bytes <len>`. Longer than 30 seconds: one chunk per 30s span, `audio wav <rate>Hz <start>-<end>s`. A non-RIFF wav falls back to `audio wav bytes <len>`. |
| Other audio | intentional change | One line, `audio <ext> bytes <len>`. No MFCC. |
| Video | intentional change | One line, `video <ext> bytes <len> header <brand>` (`mp4`, `webm`, `avi`, or `unknown`). No frames. |
| PDF text vs PyMuPDF | intentional change | 2.0 chunk text is `[Page N]` plus literal and hex strings from uncompressed and FlateDecode streams (`Tj`, `TJ`, `'`). Not layout order, fonts, or a reading-order pass. On `note.pdf`, 1.5.2 with PyMuPDF stores `[Page 1]\nHello PDF symbols\n` and 2.0 stores `[Page 1]\nHello PDF symbols` (no trailing newline). One chunk either way. |
| PDF that does not start with `%PDF` | intentional change | One chunk, `pdf bytes <len>`. |
| PDF with no text streams | intentional change | One chunk, `pdf pages 0 bytes <len>`. |
| Tree-sitter chunking | not supported | Boundaries are the 1.5.2 regex path (tree-sitter absent), and the Python definition spans for `.py`, `.pyw`, and `.pyx`. A 1.5 install that had the `tree-sitter` extra can split JS, TS, Java, C, C++, Go, Rust, Ruby, PHP, and Swift differently. |
| numpy signatures | not supported | Goldens are the zero-dependency f32 path. A 1.5 index built with numpy loaded can differ in the signature blob. Re-index to get the 2.0 signature. |

## Removed and added

Removed, relative to a 1.5.2 process that had the extras installed:

- PyPI install of 2.0, tree-sitter, numpy, Pillow, PyMuPDF, librosa, OpenCV, msgspec
- SQLite store, `indices/`, `coordination.db`, bytecache, operation log, chunk history
- CLI `release` and the singular annotation commands
- Lookaround and backreference regex
- Pip egg-info and editable-install checks inside `environment_check`
- `working_tree` auto-index, `diff_since_cache`, HNSW files, pickle (already gone in 1.5; 2.0 has none)

Added in 2.0:

- The JSON snapshot, journal, `signatures.bin`, `bm25.bin`, and `coordination.json`
- Release binaries for the six targets above
- Media indexing with no extra package
- CLI `migrate` (present, and it does not import a 1.5 database; see above)
- CLI `get-context`, `annotations`, and `document-lock` (not in `--help`)
- JSON envelope on stdout (`ok`, `schema_version`, `command`, `result`)
