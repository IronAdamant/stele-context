# Stele

Local context cache for LLM agents. 100% offline, zero required dependencies.

## Architecture

```
Stele (engine.py) -- thin facade orchestrator
  |-- indexing.py -- document indexing, chunk merging, expand paths
  |-- search_engine.py -- hybrid HNSW+BM25 search, context, stats
  |-- change_detection.py -- detect file changes, re-index modified
  |-- engine_utils.py -- path normalization, lock routing, env checks
  |-- Config (config.py) -- .stele.toml loader with minimal TOML parser
  |-- Chunkers (text, code, image, pdf, audio, video)
  |     |-- BaseChunker ABC + Chunk dataclass (chunkers/base.py)
  |     |-- CodeChunker: Python AST, tree-sitter (optional), regex fallback
  |     \-- code_patterns.py -- tree-sitter node types, regex patterns
  |-- VectorIndex (HNSW, index.py) + BM25Index (bm25.py)
  |-- IndexStore (index_store.py) -- persistent index serialization
  |-- StorageBackend (storage.py, SQLite + filesystem)
  |     |-- storage_schema.py -- database init + migrations
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
  |-- HTTP REST (mcp_server.py + mcp_handlers.py)
  |-- MCP stdio (mcp_stdio.py)
  |-- Tool registry (tool_registry.py) -- shared dispatch + schemas
  \-- Tool definitions (mcp_tool_defs.py + mcp_tool_defs_ext.py)

Concurrency:
  |-- RWLock (rwlock.py) -- read-write lock for engine thread safety
  |-- ThreadedHTTPServer (mcp_server.py) -- one thread per HTTP request
  \-- fcntl file locking (index_store.py) -- cross-process index safety

Conflict prevention:
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
- **Hybrid search**: HNSW finds 3x candidates, BM25 re-ranks. `alpha * cosine + (1-alpha) * bm25`. Auto-tuned alpha lowers for code-like queries.
- **Per-modality thresholds**: Code merge=0.85 (preserve AST), change=0.80 (tolerate edits). Text merge=0.70, change=0.85.
- **AST-boundary merge guard**: Code chunks starting with `def`/`class`/`function` are never merged with preceding chunk.
- **Signature cache**: On re-index, `content_hash -> semantic_signature` lookup skips recomputation for unchanged chunks.
- **128-dim semantic signatures**: trigrams (0-63), word unigrams (64-79), bigrams (80-95), structural (96-103), positional (104-115), reserved (116-127). Normalized to unit vectors.
- **Token estimation**: `estimate_tokens()` in `chunkers/base.py` uses BPE merge-corrected regex (~95% accuracy).
- **HNSW persistence**: `indices/hnsw_index.json.zlib`. Staleness via SHA-256 of sorted chunk IDs. FORMAT_VERSION for forward compat.
- **BM25 persistence**: Same pattern alongside HNSW. Lazy-loaded on first search.
- **Symbol graph**: Python uses `ast.walk()`, all others use regex. Edges cleared and rebuilt after batch indexing (O(symbols), <1s for ~30K symbols).
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
- **Project-root detection**: `_detect_project_root()` walks up from CWD looking for `.git` (file or dir). Works with both normal repos and git worktrees. Returns `None` if no `.git` found (disables normalization, falls back to `~/.stele/`).
- **Path normalization**: `_normalize_path()` converts absolute paths to project-relative. Relative paths resolve against project root (not CWD) for idempotent normalization. Paths outside the project root stay absolute. Applied at every engine public method boundary.
- **Per-worktree storage isolation**: Default storage is `<project_root>/.stele/` (not `~/.stele/`). Each git worktree gets its own `.stele/` directory since worktrees have separate directory trees. Priority: explicit `storage_dir` > `STELE_STORAGE_DIR` env var > `<project_root>/.stele/` > `~/.stele/`.
- **Auto-lock acquisition**: When `agent_id` is passed to `index_documents()`, locks are auto-acquired on all documents being indexed. New docs get locked after creation; existing unlocked docs get locked before write. Locks persist after indexing (agent must explicitly release).
- **MCP server auto agent_id**: Both HTTP (`mcp_server.py`) and stdio (`mcp_stdio.py`) servers generate a unique agent_id (`stele-http-{pid}` / `stele-mcp-{pid}`) and inject it into write operations when the caller doesn't provide one. Ensures all MCP-driven writes are attributed and locked.
- **Cross-worktree coordination**: `coordination.py` provides a shared SQLite DB in `<git-common-dir>/stele/coordination.db`. Agents from all worktrees share it for locks, registry, and conflict log. `detect_git_common_dir()` parses `.git` file + `commondir` for worktrees. Falls back gracefully when no git repo or read-only `.git/`. Controlled via `enable_coordination` constructor param.
- **Agent registry + heartbeat**: `register_agent()`, `deregister_agent()`, `heartbeat()`, `list_agents()`. MCP servers auto-register on start, heartbeat every 30s, deregister on stop. `reap_stale_agents(timeout=600)` cleans up dead agents and releases their locks.
- **Lock routing**: `_do_acquire_lock()`, `_do_get_lock_status()`, `_do_release_lock()` route through coordination (shared) when available, otherwise fall back to per-worktree local locks. Transparent to callers.
- **Stale bytecache detection**: `env_checks.scan_stale_pycache()` finds `__pycache__` dirs with orphaned `.pyc` files (source `.py` missing). `clean_stale_pycache()` removes them. Exposed via `engine.check_environment()` and `engine.clean_bytecache()`.
- **Editable install detection**: `env_checks.check_editable_installs()` uses `importlib.metadata` to find `pip install -e .` installs pointing outside the project root (worktree hijack). Surfaced via `check_environment()`.
- **Change notifications**: `change_notifications` table in coordination DB. Written after `index_documents()` and `detect_changes_and_update()`. Agents poll via `get_notifications(since=timestamp, exclude_self=agent_id)`. Enables near-real-time awareness: "what files did other agents change since my last check?"
- **SymbolGraphManager**: Extracted from `engine.py` into `symbol_graph.py` following the `SessionManager` delegate pattern. Owns: symbol extraction, edge resolution, staleness propagation, find_references, find_definition, impact_radius, rebuild_graph. Engine delegates with locking wrappers.
- **Cross-worktree chunk sharing**: Not implemented — not architecturally needed. The signature cache (`content_hash → semantic_signature` in `_chunk_and_store`) already prevents recomputation for unchanged content. Each worktree needs its own chunk records because file content may differ between worktrees.
- **`.stele.toml` config**: `config.py` loads `<project_root>/.stele.toml` with `[stele]` section. Uses stdlib `tomllib` (3.11+) with minimal fallback parser for 3.9-3.10. Explicit constructor params override config file values. Supports: `storage_dir`, `chunk_size`, `max_chunk_size`, `merge_threshold`, `change_threshold`, `search_alpha`, `skip_dirs`.
- **Tree-sitter code chunking**: `CodeChunker` tries tree-sitter for JS/TS, Java, C/C++, Go, Rust, Ruby, PHP when installed (`pip install stele[tree-sitter]`). Falls back to regex if not available. Uses `_DEFINITION_TYPES` dict to identify top-level node types per language. Grammar packages are lazy-loaded and cached.
- **Chunk history query**: `get_chunk_history(chunk_id=, document_path=, limit=)` exposes the `chunk_history` table via engine and both MCP servers. History tracks previous versions when the same chunk_id is updated in-place.
- **Performance benchmarks**: `benchmarks/` directory with `bench_chunking.py`, `bench_storage.py`, `bench_search.py`, and `run_all.py` runner. Zero deps, standalone-runnable, `--quick` mode for CI.
- **Agent-supplied semantic embeddings**: Two-tier signature system. Tier 1 (always): 128-dim statistical signatures for change detection. Tier 2 (optional): agent-supplied semantic summaries or raw vectors for search quality. `store_semantic_summary(chunk_id, summary)` computes signature from agent's description; `store_embedding(chunk_id, vector)` stores raw vectors. HNSW index uses agent signature when available, falls back to statistical. Zero new dependencies — the agent IS the embedding model.

## SQLite Tables

`chunks` columns include: `semantic_summary TEXT`, `agent_signature BLOB` (agent-supplied)
`chunks`, `chunk_history`, `documents` -- core storage
`sessions` (+ `agent_id`), `session_chunks` -- session lifecycle (SessionStorage)
`annotations`, `change_history` -- metadata (MetadataStorage)
`symbols`, `symbol_edges` -- symbol graph (SymbolStorage)
`document_conflicts` -- conflict audit log (DocumentLockStorage)
`documents` columns: `locked_by`, `locked_at`, `lock_ttl`, `doc_version`

Coordination DB (`<git-common-dir>/stele/coordination.db`):
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
- No circular imports exist in the dependency graph.

## Development

```bash
pip install -e ".[dev]"
pytest                    # 569 tests (568 pass, 1 skipped without mcp SDK)
mypy stele/
ruff check stele/
```

Entry points: `stele` (CLI), `stele-mcp` (MCP stdio server)

## Agent Workflow with Stele

When using Stele's MCP tools during refactoring:

### Before bulk edits
- `stele search "<symbol>"` to find all chunks that reference a name before removing/renaming it
- `stele find_references "<symbol>"` for definition/reference graph lookups
- `stele get_context` to read current file content from the index

### After edits
- `stele index --force-reindex` the changed files to update the cache
- `stele detect_changes` to verify what changed

### Multi-agent coordination rules
- **Atomic transformations**: Never split "remove X" and "replace X with Y" across parallel agents. Both steps must happen atomically per file.
- **No overlapping files**: Parallel agents must work on disjoint file sets.
- **Lint between batches**: Run `ruff check stele/` after each agent completes, not just at the end.
- **Reindex after passes**: After a batch of changes, reindex before the next agent reads.
