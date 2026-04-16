# Stele Context

Local context cache for LLM agents. 100% offline, zero required dependencies.

## Purpose

Cache files an LLM has read so it doesn't re-read unchanged content. Zero runtime deps because every added dependency is a supply-chain attack surface.

## Architecture

```
Stele Context (engine.py) -- thin facade orchestrator (inherits engine_*_mixin.py)
  |-- indexing.py -- document indexing, chunk merging, expand paths
  |-- search_engine.py -- hybrid HNSW+BM25 search, context, stats, project_brief
  |-- agent_response.py -- token-bounded responses for agents (optional helper)
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
  \-- Tool definitions (mcp_tools_primary.py + mcp_tools_symbols.py)

Concurrency:
  |-- RWLock (rwlock.py) -- read-write lock for engine thread safety
  |-- ThreadedHTTPServer (mcp_server.py) -- one thread per HTTP request
  \-- fcntl file locking (index_store.py) -- cross-process index safety

Conflict prevention:
  |-- LockOps (lock_ops.py) -- shared lock primitives (refresh, conflict, reap)
  |-- DocumentLockStorage (document_lock_storage.py) -- per-worktree locks
  |-- CoordinationBackend (coordination.py) + agent_registry.py + change_notifications.py
  \-- Per-document locks, optimistic locking, conflict log, project-root detection

Environment safety:
  \-- env_checks.py -- stale bytecache detection, editable install hijack detection

Backward compat: core.py re-exports Stele + Chunk
```

## Load-bearing invariants

Changes that violate any of these break the project's basis. Do not relax without explicit discussion.

- **Zero required deps in core.** stdlib only. Optional deps (numpy, msgspec, tree-sitter, librosa, PIL, pymupdf, cv2) must be imported lazily and have pure-Python fallbacks. Every new dependency is a supply-chain attack vector.
- **JSON only, no pickle.** Session data and index state serialize with JSON+zlib. Pickle deserialization is a supply-chain vector; do not introduce it anywhere.
- **Thread safety via RWLock.** `self._lock.read_lock()` wraps read methods; `self._lock.write_lock()` wraps write methods. Never add a public method without the right wrapper.
- **mtime+size fast-path for unchanged files.** `file_unchanged(abs_path, stored_doc)` in `engine_utils.py` must be checked before full read — this is the core memory feature. `index_documents()`, `detect_changes_and_update()`, and `get_context()` all rely on it.
- **Single `Chunk` class.** Defined in `chunkers/base.py`; re-exported by `core.py`. Do not create a second Chunk dataclass.
- **Path normalization at every engine public-method boundary.** Every incoming path goes through `_normalize_path` so storage is worktree-consistent and idempotent.
- **`agent_id` propagation.** When `agent_id` is set on a write method, locks auto-acquire and conflicts get logged. Do not bypass this.
- **No circular imports.** Enforced by the delegate/mixin pattern. `engine.py` imports from mixins and delegates; mixins/delegates never import from `engine.py`.

## Design decisions

Full log of architectural decisions (why we picked option A over B, historical trade-offs, parser quirks, ranking heuristics) lives in [`docs/architecture.md`](docs/architecture.md#design-decisions-log). Consult it before altering behavior in an area — these are decisions taken with context.

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

For the full module table, see [`COMPLETE_PROJECT_DOCUMENTATION.md`](COMPLETE_PROJECT_DOCUMENTATION.md).

## Development

```bash
pip install -e ".[dev]"
pytest                    # 891 pass, 1 skipped (mcp SDK optional)
mypy stele_context/
ruff check stele_context/
```

Entry points: `stele-context` (CLI), `stele-context-mcp` (MCP stdio server)

## Agent Workflow with Stele Context

Stele's MCP tools **complement** native tools (Grep, Glob, Read) — they do not replace them. Pick the right tool for the job.

### Grep-first workflow

The core principle: **grep to find, grep to cache**. Every `agent_grep` call automatically indexes the files it searches, so searching and caching happen in one step. `get_search_history` tells you what you already searched this session.

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
| `agent_grep` | Finding usages, auditing code — scope-aware grep with token budget |
| `find_references` | Looking up a specific symbol's definition and all callers |
| `find_definition` | Finding where a symbol is defined |
| `get_context` | Getting full file content from cache (records read in session) |
| `get_search_history` | Checking what you already grep'd this session |
| `get_session_read_files` | Checking what you already fully read this session |
| `search` (semantic) | Open-ended exploration only — only useful with Tier-2 summaries populated |
| `search_text` | Guaranteed complete substring/regex search |

### After edits
- `stele-context index --force-reindex` the changed files to update the cache
- `stele-context detect_changes` to verify what changed

### Multi-agent coordination rules
- **Atomic transformations**: Never split "remove X" and "replace X with Y" across parallel agents. Both steps must happen atomically per file.
- **No overlapping files**: Parallel agents must work on disjoint file sets.
- **Lint between batches**: Run `ruff check stele_context/` after each agent completes, not just at the end.
- **Reindex after passes**: After a batch of changes, reindex before the next agent reads.
