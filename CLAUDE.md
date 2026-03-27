# Stele Context

Local context cache for LLM agents. 100% offline, zero required dependencies.

## Architecture

```
Stele Context (engine.py) -- thin facade orchestrator
  |-- indexing.py -- document indexing, chunk merging, expand paths
  |-- search_engine.py -- hybrid HNSW+BM25 search, context, stats, project_brief
  |-- agent_response.py -- token-bounded responses for agents (optional helper module)
  |-- agent_grep.py -- LLM-optimized search (scope, classify, budget, dedup)
  |-- change_detection.py -- detect file changes, re-index modified
  |-- engine_utils.py -- path normalization, lock routing, env checks
  |-- Config (config.py) -- .stele-context.toml loader with minimal TOML parser
  |-- Chunkers (text, code, image, pdf, audio, video)
  |     |-- BaseChunker ABC + Chunk dataclass (chunkers/base.py)
  |     |-- CodeChunker: Python AST, tree-sitter (optional), regex fallback
  |     \-- code_patterns.py -- tree-sitter node types, regex patterns
  |-- VectorIndex (HNSW, index.py) + BM25Index (bm25.py)
  |-- IndexStore (index_store.py) -- persistent index serialization
  |-- StorageBackend (storage.py, SQLite + filesystem)
  |     |-- ConnectionPool (connection_pool.py) -- thread-local connection reuse
  |     |-- storage_schema.py -- database init + migrations + pool-aware connect()
  |     |-- storage_delegates.py -- forwarding mixin
  |     |-- SessionStorage (session_storage.py)
  |     |-- MetadataStorage (metadata_storage.py)
  |     \-- SymbolStorage (symbol_storage.py)
  |-- SessionManager (session.py)
  |-- SymbolGraphManager (symbol_graph.py) -- extraction, edges, staleness
  |-- SymbolExtractor (symbols.py) -- dispatcher + Python AST
  \-- symbol_patterns.py -- Symbol dataclass + 10 language regex extractors

APIs:
  |-- CLI (cli.py + cli_metadata.py)
  |-- HTTP REST (mcp_server.py, mcp_handlers.py is backward-compat shim)
  |-- MCP stdio (mcp_stdio.py)
  |-- Tool registry (tool_registry.py) -- shared dispatch + schemas
  \-- Tool definitions (mcp_tool_defs.py + mcp_tool_defs_ext.py)

Concurrency:
  |-- RWLock (rwlock.py) -- read-write lock for engine thread safety
  |-- ThreadedHTTPServer (mcp_server.py) -- one thread per HTTP request
  \-- fcntl file locking (index_store.py) -- cross-process index safety

Conflict prevention:
  |-- LockOps (lock_ops.py) -- shared lock primitives (refresh, conflict, reap)
  |-- DocumentLockStorage (document_lock_storage.py) -- per-worktree locks
  |-- CoordinationBackend (coordination.py) + agent_registry.py + change_notifications.py
  |-- Per-document locks (acquire/release/force-steal with TTL expiry)
  |-- Optimistic locking (doc_version compare-and-swap)
  |-- Conflict log (per-worktree + shared coordination)
  |-- Project-root detection + path normalization (worktree safety)
  |-- Auto-lock acquisition when agent_id is set
  |-- MCP server auto agent_id injection + heartbeat
  \-- Agent registry (cross-worktree visibility)

Environment safety:
  |-- env_checks.py -- stale bytecache detection/cleanup
  |-- Editable install (pip -e) hijack detection
  \-- MCP tools: environment_check, clean_bytecache

Backward compat: core.py re-exports Stele + Chunk
```

## Key Design Decisions

- **Single Chunk class**: `chunkers/base.py:Chunk` is the only Chunk dataclass. `core.py` re-exports it.
- **Engine delegates session ops** to `SessionManager` (no duplicated logic).
- **Storage delegates** to 3 specialized classes: `SessionStorage`, `MetadataStorage`, `SymbolStorage`. Each owns its own SQL tables.
- **JSON only, no pickle**: Session data serialized with JSON+zlib for agent safety.
- **Zero required deps**: Core uses only stdlib. numpy/msgspec have pure-Python fallbacks in `chunkers/numpy_compat.py`.
- **`_read_and_hash(path, modality)`**: Module-level helper in `engine.py` for file reading + SHA-256. Used by `index_documents()`, `detect_changes_and_update()`, `get_context()`.
- **Hybrid search (secondary to symbols + exact search)**: `search` is for exploration, not primary verification — statistical vectors can mis-rank domain queries; `agent_grep` / `search_text` are preferred for exact/code audit workflows. HNSW finds 3x candidates; BM25 also runs `BM25Index.search()` for independent top-`2*top_k` keyword candidates, unioned with HNSW. Ranking uses pure BM25 when HNSW/BM25 top sets disagree past a threshold, when HNSW’s top hit is not in BM25’s top five, when raw HNSW cosines are nearly flat (clustered ~0.69-style scores), or when **max raw HNSW cosine** is below **~0.70** (weak semantic match). `alpha * cosine + (1-alpha) * bm25`. Default `search_alpha` is **0.42** (RecipeLab-tuned: slightly keyword-heavy). Auto-tuned alpha lowers further for natural-language and code-like queries. Proximity re-ranking uses identifier tokens plus words ≥4 chars so prose feature queries get co-occurrence boosts. Min-max normalized within the candidate set; BM25-only candidates get HNSW norm 0. Tier 2 agent signatures get `TIER2_BOOST = 1.3`. `has_agent_signatures(chunk_ids)` on StorageBackend provides the batch lookup. Optional **`path_prefix`** on `search()` / `get_map()` filters by project-relative path prefix (multi-tree indexes).
- **Symbol query diagnostics**: `find_references` / `find_definition` include `symbol_index` (empty vs ready, counts) and `guidance` when there are no hits, so agents can tell an unpopulated graph from a missing symbol.
- **`search(..., search_mode=)`**: `hybrid` (default) = HNSW+BM25; `keyword` = BM25-only for deterministic keyword ranking. CLI: `--search-mode keyword`.
- **`index_health`**: `map` and `stats` use `index_health.compute_index_health_snapshot()` — `documents`, `chunks`, `symbol_rows`, `symbols_ready`, `storage_dir`, `latest_indexed_at`, `seconds_since_last_index`, `symbol_graph_status`, `chunk_store_status`, **`alerts`** (actionable strings: empty index, symbols missing with chunks present, index older than ~7 days). `map`/`stats` also include **`project_root`** (resolved path or `null`).
- **Per-modality thresholds**: Code merge=0.85 (preserve AST), change=0.80 (tolerate edits). Text merge=0.70, change=0.85.
- **AST-boundary merge guard**: Code chunks starting with `def`/`class`/`function`/`const`/`let`/`var`/`module.exports`/`describe(`/`it(`/`test(` are never merged with preceding chunk.
- **Signature cache**: On re-index, `content_hash -> semantic_signature` lookup skips recomputation for unchanged chunks.
- **mtime+size fast-path**: `file_unchanged(abs_path, stored_doc)` in `engine_utils.py` compares `st_mtime` and `st_size` against stored `last_modified` and `file_size` columns. When both match, `index_documents()`, `detect_changes_and_update()`, and `get_context()` skip the full file read + SHA-256 hash entirely. Falls back to full read when `file_size` is NULL (pre-migration data) or on stat error. The `documents` table stores `file_size INTEGER` (added via migration). Both `chunk_and_store()` and `detect_changes_unlocked()` persist mtime+size from `abs_path.stat()` after writes.
- **128-dim semantic signatures**: trigrams (0-63), word unigrams (64-79), bigrams (80-95), structural (96-103), positional (104-115), reserved (116-127). Normalized to unit vectors.
- **Token estimation**: `estimate_tokens()` in `chunkers/base.py` uses BPE merge-corrected regex (~95% accuracy).
- **HNSW persistence**: `indices/hnsw_index.json.zlib`. Staleness via SHA-256 of sorted chunk IDs. FORMAT_VERSION for forward compat.
- **BM25 persistence**: Same pattern alongside HNSW. Lazy-loaded on first search.
- **Symbol graph**: Python uses `ast.walk()`, all others use regex. Edges cleared and rebuilt after batch indexing (O(symbols), <1s for ~30K symbols). Python extractor captures `ast.Name(Load)` nodes as `kind="name"` references (function-as-value, keyword args, assignments, returns) in addition to `kind="function"` from `ast.Call`. Call-target names are deduplicated via `id(node)` to avoid double-counting.
- **CJS require classification**: Non-destructured `const X = require('./path')` emits `kind="import", role="reference"` for the variable name (not `kind="variable", role="definition"`). For external modules (path without `./` or `../` prefix), only the module path reference is emitted — the variable name is suppressed to avoid spurious edges between files importing the same npm package. Destructured `const { a } = require(...)` was already correct.
- **JS module path resolution**: `_module_matches_path()` handles Python dotted imports (`foo.bar` → `foo/bar.py`), JS relative requires (`../models/Recipe` → `models/Recipe.js`), and bare web paths from HTML `<script src>` / `<link href>` (`app.js` → `public/app.js`, `js/main.js` → `public/js/main.js`). Strips `./`/`../` prefixes for relative paths, strips leading `/` for absolute web paths. Matches against file suffixes with common JS/TS/CSS extensions. External requires (no relative prefix, no file extension) never match local files.
- **HTML→JS/CSS dependency edges**: `resolve_symbols` includes a module-to-file fallback. When a `kind="module"` reference (e.g. `"app.js"` from HTML `<script src>`) has no matching definition by name, it checks `_module_matches_path()` against all document paths with definitions. If matched, an edge is created to the first chunk of the target file. This enables `impact_radius` and `coupling` to work for frontend code referenced via `<script>` and `<link>` tags.
- **Cross-language linking**: CSS-prefixed names (`.classname`, `#id`) avoid collisions. HTML attrs -> CSS selectors, JS DOM API -> CSS.
- **Staleness propagation**: BFS through symbol edges after changes. Score = `0.8^depth`. `stale_chunks(threshold)` queries it.
- **Directory indexing**: `_expand_paths()` walks dirs, filters by chunker extensions, skips `.git`/`node_modules`/`__pycache__`/hidden dirs. Configurable via `skip_dirs` param.
- **Module path resolution**: `resolve_symbols()` prefers definitions from imported module paths when multiple match.
- **Adaptive ef_search**: HNSW search width scales with index size (10 for <100, 4x for 10K+).
- **Dynamic versioning**: `__init__.__version__` is the single source. Engine, CLI, and tests all reference it.
- **Thread safety**: `RWLock` in `rwlock.py` protects all engine public methods. Read methods (search, get_context, etc.) allow concurrent access. Write methods (index_documents, detect_changes, etc.) get exclusive access. BM25 lazy-init uses double-checked locking with a separate `threading.Lock`.
- **Multi-agent sessions**: `sessions` table has `agent_id TEXT` column. `create_session(id, agent_id=)`, `list_sessions(agent_id=)`. Backward-compatible (agent_id defaults to None).
- **Cross-process file locking**: `index_store.py` uses `fcntl.flock()` on `.lock` sidecar files. LOCK_EX for writes, LOCK_SH for reads. No-op fallback on Windows.
- **Threaded HTTP**: `ThreadedHTTPServer(ThreadingMixIn, HTTPServer)` handles concurrent requests. Safe because RWLock protects engine state.
- **Per-document ownership**: `acquire_document_lock(path, agent_id, ttl=300)` gives exclusive write access. Other agents can read but writes are rejected with `PermissionError`. Locks auto-expire after TTL. `force=True` steals lock and logs conflict. `release_agent_locks(agent_id)` for cleanup.
- **Optimistic locking**: `doc_version INTEGER` on documents table, auto-incremented on each write. `index_documents(expected_versions={path: N})` rejects if version changed since last read. Prevents silent overwrites.
- **Conflict log**: `document_conflicts` table records ownership violations, version conflicts, and lock steals with full audit trail. `get_conflicts(document_path=, agent_id=)` for querying.
- **Store document upsert**: `store_document()` uses `INSERT ... ON CONFLICT DO UPDATE` instead of `INSERT OR REPLACE` to preserve `locked_by`/`doc_version` columns.
- **Project-root detection**: `_detect_project_root()` walks up from CWD looking for `.git` (file or dir). Works with both normal repos and git worktrees. Returns `None` if no `.git` found (disables normalization, falls back to `~/.stele-context/`).
- **Path normalization**: `_normalize_path()` converts absolute paths to project-relative. Relative paths resolve against project root (not CWD) for idempotent normalization. Paths outside the project root stay absolute. Applied at every engine public method boundary.
- **Per-worktree storage isolation**: Default storage is `<project_root>/.stele-context/` (not `~/.stele-context/`). Each git worktree gets its own `.stele-context/` directory since worktrees have separate directory trees. Priority: explicit `storage_dir` > `STELE_CONTEXT_STORAGE_DIR` env var > `<project_root>/.stele-context/` > `~/.stele-context/`.
- **Auto-lock acquisition**: When `agent_id` is passed to `index_documents()`, locks are auto-acquired on all documents being indexed. New docs get locked after creation; existing unlocked docs get locked before write. Locks persist after indexing (agent must explicitly release).
- **MCP server auto agent_id**: Both HTTP (`mcp_server.py`) and stdio (`mcp_stdio.py`) servers generate a unique agent_id (`stele-context-http-{pid}` / `stele-context-mcp-{pid}`) and inject it into write operations when the caller doesn't provide one and the server_agent_id is truthy. Ensures all MCP-driven writes are attributed and locked.
- **Cross-worktree coordination**: `coordination.py` provides a shared SQLite DB in `<git-common-dir>/stele-context/coordination.db`. Agents from all worktrees share it for locks, registry, and conflict log. `detect_git_common_dir()` parses `.git` file + `commondir` for worktrees. Falls back gracefully when no git repo or read-only `.git/`. Controlled via `enable_coordination` constructor param.
- **Agent registry + heartbeat**: `register_agent()`, `deregister_agent()`, `heartbeat()`, `list_agents()`. MCP servers auto-register on start, heartbeat every 30s, deregister on stop. `reap_stale_agents(timeout=600)` cleans up dead agents and releases their locks.
- **Lock routing**: `_do_acquire_lock()`, `_do_get_lock_status()`, `_do_release_lock()` route through coordination (shared) when available, otherwise fall back to per-worktree local locks. Transparent to callers.
- **Stale bytecache detection**: `env_checks.scan_stale_pycache()` finds `__pycache__` dirs with orphaned `.pyc` files (source `.py` missing). `clean_stale_pycache()` removes them. Exposed via `engine.check_environment()` and `engine.clean_bytecache()`.
- **Editable install detection**: `env_checks.check_editable_installs()` uses `importlib.metadata` to find `pip install -e .` installs pointing outside the project root (worktree hijack). Surfaced via `check_environment()`.
- **Change notifications**: `change_notifications` table in coordination DB. Written after `index_documents()` and `detect_changes_and_update()`. Agents poll via `get_notifications(since=timestamp, exclude_self=agent_id)`. Enables near-real-time awareness: "what files did other agents change since my last check?"
- **SymbolGraphManager**: Extracted from `engine.py` into `symbol_graph.py` following the `SessionManager` delegate pattern. Owns: symbol extraction, edge resolution, staleness propagation, find_references, find_definition, impact_radius, coupling, rebuild_graph. Engine delegates with locking wrappers.
- **find_references verdict**: `find_references()` returns a `verdict` field: `"referenced"` (definitions and usages exist), `"unreferenced"` (defined but never used — dead code candidate), `"external"` (referenced but not defined locally), `"not_found"` (symbol unknown to the graph). Enables LLM agents to answer "is this dead code?" without reasoning over raw reference lists.
- **File-path-based impact_radius**: `impact_radius(document_path=)` accepts a file path as alternative to `chunk_id`. Resolves to all chunks in the file and runs BFS from all of them. Return includes `affected_files` count alongside `affected_chunks`. Default `compact=True` returns per-file summaries (`files`: path, chunk_count, depth_min, depth_max) instead of full `chunks` rows for manageable output; `include_content=False` omits chunk text; `path_filter` substring-filters paths (e.g. exclude tests). **`summary_mode=True`** returns **`depth_distribution`**, **`files`** capped to **`top_n_files`** (by chunk_count), and **`files_total`**. Use `compact=False` for debugging to see all chunks.
- **detect_changes scan_new**: `detect_changes_and_update(..., scan_new=True)` with `document_paths=None` walks `project_root` with `expand_paths()` and appends unindexed files to `new` with reason `New file (scan)` (does not index them). Default `scan_new=True` so new files are automatically discovered on each detect_changes call; pass `scan_new=False` to disable filesystem scanning.
- **Semantic coupling**: `coupling(document_path)` finds files connected via symbol edges. Queries outgoing edges (this file depends on X) and incoming edges (X depends on this file). Returns sorted by edge count with direction (`depends_on` / `depended_on_by` / `bidirectional`) and shared symbol names. Complements Chisel's git-history coupling with semantic coupling.
- **LLM intent-routed tool descriptions**: All tool descriptions include `USE WHEN:` guidance mapping agent intent to tool selection. Tool definition order places search tools first in `_TOOL_DEFINITIONS_CORE`: `agent_grep`, `search_text`, then `search`. Search tool trio clearly differentiated: `agent_grep` for structured verification, `search_text` for raw exact matching, `search` for semantic exploration. `agent_grep` and `search_text` were moved from `mcp_tool_defs_ext.py` to `mcp_tool_defs.py` as primary tools.
- **Cross-worktree chunk sharing**: Not implemented — not architecturally needed. The signature cache (`content_hash → semantic_signature` in `_chunk_and_store`) already prevents recomputation for unchanged content. Each worktree needs its own chunk records because file content may differ between worktrees.
- **`.stele-context.toml` config**: `config.py` loads `<project_root>/.stele-context.toml` with `[stele-context]` section. Uses stdlib `tomllib` (3.11+) with minimal fallback parser for 3.9-3.10. Explicit constructor params override config file values. Supports: `storage_dir`, `chunk_size`, `max_chunk_size`, `merge_threshold`, `change_threshold`, `search_alpha`, `skip_dirs`.
- **Tree-sitter code chunking**: `CodeChunker` tries tree-sitter for JS/TS, Java, C/C++, Go, Rust, Ruby, PHP when installed (`pip install stele-context[tree-sitter]`). Falls back to regex if not available. Uses `_DEFINITION_TYPES` dict to identify top-level node types per language. Grammar packages are lazy-loaded and cached. JS regex fallback pattern includes `module.exports`, `describe()`, `it()`/`test()` boundaries for Node.js modules and test files.
- **Chunk history query**: `get_chunk_history(chunk_id=, document_path=, limit=)` exposes the `chunk_history` table via engine and both MCP servers. History tracks previous versions when the same chunk_id is updated in-place.
- **Performance benchmarks**: `benchmarks/` directory with `bench_chunking.py`, `bench_storage.py`, `bench_search.py`, and `run_all.py` runner. Zero deps, standalone-runnable, `--quick` mode for CI.
- **Agent-supplied semantic embeddings**: Two-tier signature system. Tier 1 (always): 128-dim statistical signatures for change detection. Tier 2 (optional): agent-supplied semantic summaries or raw vectors for search quality. `store_semantic_summary(chunk_id, summary)` computes signature from agent's description; `store_embedding(chunk_id, vector)` stores raw vectors. HNSW index uses agent signature when available, falls back to statistical. Zero new dependencies — the agent IS the embedding model.
- **Inline summaries during indexing**: `index_documents(paths, summaries={path: summary})` applies Tier 2 agent signatures in the same write lock as indexing. All chunks from a file receive the document-level summary. Eliminates the per-chunk `store_semantic_summary` round-trip loop. Summaries are path-normalized before matching. Result includes `summaries_applied` count.
- **Bulk summary storage**: `bulk_store_summaries(summaries={chunk_id: summary})` batch-stores per-chunk summaries with individual signatures. One write lock, one HNSW save. For per-chunk precision after indexing (when chunk IDs are known). Complements inline summaries: inline for document-level, bulk for chunk-level.
- **Thread-local connection pool**: `ConnectionPool` in `connection_pool.py` gives each thread a single reused SQLite connection. The `connect()` helper in `storage_schema.py` is pool-aware: uses the pool when one is initialized (by `StorageBackend.__init__`), falls back to fresh connections otherwise (coordination DB, tests). Eliminates ~70 per-method connection opens. `row_factory` is reset to `None` on each context-manager entry to prevent state leakage. `close_all()` for clean shutdown.
- **Shared lock operations**: `lock_ops.py` contains shared primitives (`refresh_lock`, `record_conflict`, `query_conflicts`, `release_agent_locks`, `reap_expired_locks`, `hydrate_conflicts`) used by both `DocumentLockStorage` and `CoordinationBackend`. Follows the same zero-internal-deps pattern as `agent_registry.py`. The `delete` parameter controls whether release NULLs columns (documents table) or deletes rows (shared_locks table). All functions that set `row_factory` save/restore the previous value to prevent state leakage.
- **MCP server constants**: `DEFAULT_MCP_PORT = 9876` and `HEARTBEAT_INTERVAL = 30` defined in `mcp_server.py`, reused by CLI.
- **Staleness index**: `idx_chunks_staleness` on `chunks(staleness_score)` added during migration for fast stale-chunk queries.
- **Text pattern search**: `search_text(pattern, regex=, document_path=, limit=)` provides perfect-recall exact/regex search across stored chunk content. Complements semantic (HNSW) and keyword (BM25) search. Uses `str.find()` for substring, stdlib `re` for regex. Zero dependencies. Key use case: verify all usages before renaming/removing symbols.
- **LLM-optimized search (agent_grep)**: `agent_grep(pattern, regex=, document_path=, classify=, include_scope=, group_by=, max_tokens=, deduplicate=, context_lines=)` wraps `search_text` with five LLM-specific enrichments: (1) **Token budget** — matches added until `max_tokens` reached, preventing context overflow; (2) **Scope annotation** — each match tagged with enclosing function/class from the symbol graph; (3) **Classification** — line-level heuristic tags: comment/import/definition/string/code; (4) **Deduplication** — structurally identical lines collapsed with `also_in` count and `also_in_files` list; (5) **Structured grouping** — results grouped by file, scope, or classification. Base line numbers computed per-chunk by summing newlines across preceding chunks. `agent_grep.py` is standalone (imports only `estimate_tokens`); `SymbolStorage.get_symbols_for_chunks()` provides batch symbol lookup.
- **Unified tool registry**: `tool_registry.py` is the single source of truth for tool dispatch (`build_tool_map`), write-tool sets (`WRITE_TOOLS`), HTTP schema generation (`get_http_schemas`), and modality flag construction (`get_modality_flags`). Both servers expose identical tool sets (53 tools) with modality_flags for utility tools. Schemas generated from `mcp_tool_defs.py` + `mcp_tool_defs_ext.py`. `WRITE_TOOLS` includes lock operations (`acquire_document_lock`, `release_document_lock`, `refresh_document_lock`, `release_agent_locks`) for auto agent_id injection.
- **No redundant commits**: All modules using `connect()` or `with self._connect() as conn:` context managers never call `conn.commit()` inside the block — the context manager auto-commits on successful exit. This applies to storage modules, coordination modules (`coordination.py`, `agent_registry.py`, `change_notifications.py`), and `storage_schema.py`.
- **MCP stdio server bundle**: `_ServerBundle` dataclass holds server, engine, and agent_id together. Replaces monkey-patching `_stele_engine`/`_stele_agent_id` onto the MCP Server object.
- **Index store context managers**: Lock file handles in `index_store.py` use `with` statements for guaranteed cleanup. Read path uses nested try/finally to ensure unlock before close.
- **WAL checkpoint on close**: `StorageBackend.close()` runs `PRAGMA wal_checkpoint(TRUNCATE)` before closing pooled connections. Prevents unbounded WAL file growth for long-running servers.
- **Typing protocols**: `protocols.py` defines `StorageProto`, `VectorIndexProto`, `SymbolManagerProto`, and `CoordinationProto` as structural protocol types for the delegation boundary. Used as `TYPE_CHECKING`-only documentation — delegation functions keep `Any` at runtime to avoid import cycles, but IDEs and developers can reference the protocols for the exact expected interface.

## SQLite Tables

`chunks` columns include: `semantic_summary TEXT`, `agent_signature BLOB` (agent-supplied)
`chunks`, `chunk_history`, `documents` -- core storage
`sessions` (+ `agent_id`), `session_chunks` -- session lifecycle (SessionStorage)
`annotations`, `change_history` -- metadata (MetadataStorage)
`symbols`, `symbol_edges` -- symbol graph (SymbolStorage)
`document_conflicts` -- conflict audit log (DocumentLockStorage)
`documents` columns: `locked_by`, `locked_at`, `lock_ttl`, `doc_version`, `file_size`

Coordination DB (`<git-common-dir>/stele-context/coordination.db`):
`agents` -- agent registry with heartbeats
`shared_locks` -- cross-worktree document locks
`shared_conflicts` -- cross-worktree conflict log

## Module Boundaries

- `engine.py` is the only file that wires everything together. All other modules are standalone.
- `index.py` and `bm25.py` have zero internal dependencies.
- `numpy_compat.py` is the single source for `sig_to_bytes()`, `sig_from_bytes()`, `cosine_similarity()`.
- Chunker modules only import from `chunkers/base.py`.
- `config.py` imports nothing from stele internals — standalone TOML loader.
- `coordination.py` delegates notifications to `change_notifications.py` (same pattern as `agent_registry.py`).
- `env_checks.py` is standalone with zero internal deps.
- `agent_grep.py` imports only `estimate_tokens` from `chunkers/base.py`. Receives storage as a parameter.
- No circular imports exist in the dependency graph.

## Development

```bash
pip install -e ".[dev]"
pytest                    # 860+ tests (860+ pass, 1 skipped without mcp SDK)
mypy stele_context/
ruff check stele_context/
```

Entry points: `stele-context` (CLI), `stele-context-mcp` (MCP stdio server)

## Agent Workflow with Stele Context

When using Stele Context's MCP tools during refactoring:

### Before bulk edits
- `stele-context agent_grep "<pattern>"` for LLM-optimized search with scope, classification, and token budget — preferred over search/search_text for agent workflows
- `stele-context search "<symbol>"` for semantic/keyword search across chunks
- `stele-context find_references "<symbol>"` for definition/reference graph lookups
- `stele-context get_context` to read current file content from the index

### After edits
- `stele-context index --force-reindex` the changed files to update the cache
- `stele-context detect_changes` to verify what changed

### Multi-agent coordination rules
- **Atomic transformations**: Never split "remove X" and "replace X with Y" across parallel agents. Both steps must happen atomically per file.
- **No overlapping files**: Parallel agents must work on disjoint file sets.
- **Lint between batches**: Run `ruff check stele_context/` after each agent completes, not just at the end.
- **Reindex after passes**: After a batch of changes, reindex before the next agent reads.
