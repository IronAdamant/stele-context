# LLM Development Log

Chronological record of development activity on Stele Context, maintained for LLM agent context.

## 2026-03-21 - v0.10.3 Codebase Audit & Cleanup

### Bug fixes
- Fixed position tracking bug in `CodeChunker._boundaries_to_chunks()`: `end_pos` used stripped length but `current_start` advanced by unstripped length, causing misaligned chunk positions
- Fixed unbounded SQL query in `metadata_storage.get_change_history()` when filtering by `document_path` — added `LIMIT` to prevent loading entire table
- Fixed agent_id injection inconsistency: `mcp_stdio.py` injected unconditionally while `mcp_handlers.py` checked for truthy `server_agent_id` — now both match
- Added lock-related tools to `WRITE_TOOLS` (`acquire_document_lock`, `release_document_lock`, `refresh_document_lock`, `release_agent_locks`) for proper auto agent_id injection
- Synced `pyproject.toml` version with `__init__.py` (was stuck at 0.10.0)

### Dead code removed
- Removed 35 redundant `conn.commit()` calls inside `connect()` context manager blocks (storage.py: 12, symbol_storage.py: 8, session_storage.py: 5, metadata_storage.py: 5, document_lock_storage.py: 5) — context manager auto-commits
- Removed unnecessary `isinstance(d, dict)` check in `change_detection.py` (all entries guaranteed to be dicts)
- Removed production `assert` in `image.py` (replaced with type annotation)

### Improvements
- Added `Chunk` to `stele_context/__init__.py` exports — key public type was missing from package root

### Documentation
- Updated README MCP tools section: accurate 42-tool count for both servers, complete tool listing
- Updated README architecture diagram tool counts (30/32 → 42/42)
- Updated COMPLETE_PROJECT_DOCUMENTATION.md with `Chunk` export
- Total: 573 pass, 1 skipped

## 2026-03-21 - v0.10.2 Lock Deduplication & Storage Improvements

### Refactoring
- Extracted `lock_ops.py` (shared lock primitives): `refresh_lock`, `record_conflict`, `query_conflicts`, `release_agent_locks`, `reap_expired_locks`, `hydrate_conflicts`
- Both `DocumentLockStorage` and `CoordinationBackend` now delegate to `lock_ops` for shared operations
- `delete` parameter controls UPDATE-NULL (documents table) vs DELETE (shared_locks table)
- `storage_delegates.py`: compressed from 309 to 140 lines — removed redundant docstrings, kept full type signatures for mypy/IDE safety
- `metadata_storage.get_change_history()`: added SQL `LIKE` pre-filter when filtering by `document_path` — avoids loading entire change_history table into memory. Python filter still handles exact structural matching.

### Impact
- Eliminated ~120 lines of duplicated lock SQL between document_lock_storage.py and coordination.py
- Future bug fixes to lock refresh/conflict/reap only need to happen in lock_ops.py
- Change history queries with document_path filter now O(matches) instead of O(total)
- Total: 573 pass, 1 skipped

## 2026-03-21 - v0.10.1 Codebase Cleanup

### Bug fixes
- Fixed MCP stdio server not registering `detect_modality`/`get_supported_formats` tools (modality_flags not passed to `build_tool_map`)
- Fixed `get_chunk()` in storage.py: SELECT now before UPDATE (was incrementing access_count on nonexistent chunks)
- Fixed lock file resource leak in `index_store.py`: write path now uses `with` statement, read path uses nested try/finally for guaranteed cleanup

### Dead code removed
- Removed `load_index()` wrapper from `index_store.py` (was only used by `load_if_fresh()`, inlined to direct `_load_compressed_json` call)
- Updated test_index_store.py to use `_load_compressed_json` directly

### Code quality improvements
- Extracted `_hydrate_conflicts()` shared helper in `document_lock_storage.py`, used by both `DocumentLockStorage` and `CoordinationBackend` (eliminated duplicate JSON hydration)
- Fixed tuple indexing inconsistency in `DocumentLockStorage`: `refresh_lock()` and `release_lock()` now use `sqlite3.Row` named access instead of `row[0]`/`row[1]`
- Simplified `get_lock_stats()` by inlining single-use row variables
- Extracted `_print_detect_section()` helper in `cli.py` to deduplicate 4 near-identical printing blocks in `cmd_detect()`
- Added error handling to `cmd_clear()` for filesystem operations
- Defined `DEFAULT_MCP_PORT` and `HEARTBEAT_INTERVAL` constants in `mcp_server.py`, reused by `cli.py`
- Added `idx_chunks_staleness` index on `chunks(staleness_score)` during migration for fast stale-chunk queries

### Documentation
- Updated README test badge: 412 → 573
- Updated CLAUDE.md with new design decisions
- Updated COMPLETE_PROJECT_DOCUMENTATION.md file table
- Total: 573 pass, 1 skipped (MCP SDK)

## 2026-03-21 - v0.9.0 Release & CI Fixes

### Features added
- Config system (`.stele-context.toml`) with minimal TOML parser for Python 3.9-3.10
- Tree-sitter code chunking for 9 languages (JS/TS, Java, C/C++, Go, Rust, Ruby, PHP)
- Chunk history query tools (`get_chunk_history`)
- Agent-supplied semantic embeddings (`store_semantic_summary`, `store_embedding`)
- Performance benchmarks (`benchmarks/` directory)

### CI/CD
- Fixed all ruff lint errors (unused imports, ambiguous variables, formatting)
- Fixed all mypy type errors (Optional return types, null guards, parameter types)
- Added coverage config excluding CLI and optional media chunkers
- Published to PyPI as `stele-context` (name `stele` was taken)
- Added PyPI badge and install instructions to README

### Refactoring (500 LOC limit compliance)
- `engine.py` (1735 -> 594): extracted `indexing.py`, `search_engine.py`, `change_detection.py`, `engine_utils.py`
- `storage.py` (1040 -> 524): extracted `storage_delegates.py`, `storage_schema.py`
- `mcp_stdio.py` (969 -> 384): extracted `mcp_tool_defs.py`, `mcp_tool_defs_ext.py`
- `mcp_server.py` (842 -> 257): extracted `mcp_handlers.py`, `mcp_schemas.py`
- `symbols.py` (885 -> 427): extracted `symbol_patterns.py`
- `coordination.py` (695 -> 569): extracted `agent_registry.py`
- `chunkers/code.py` (613 -> 471): extracted `chunkers/code_patterns.py`

### Tests added
- `test_base_chunker.py` (64 tests) - Chunk dataclass, BaseChunker ABC, estimate_tokens
- `test_env_checks.py` (32 tests) - Stale pycache detection, editable installs
- `test_numpy_compat.py` (29 tests) - Signature encoding, cosine similarity
- `test_symbol_storage.py` (32 tests) - Symbol and edge CRUD operations
- Total: 412 -> 569 tests

### Documentation
- Created `COMPLETE_PROJECT_DOCUMENTATION.md` (file table)
- Created `LLM_Development.md` (this file)
- Created `wiki-local/` directory with spec, glossary, index

## 2026-03-19 - v0.8.0 Worktree Safety

### Features
- Per-worktree storage isolation
- Project-root detection (`.git` file/dir walking)
- Path normalization (absolute -> project-relative)
- Auto-lock acquisition when `agent_id` is set
- Cross-worktree coordination (`coordination.py`)
- Agent registry with heartbeat
- Change notifications
- Document conflict logging

## 2026-03-16 - Project Rename

- Renamed from ChunkForge to Stele Context
- Updated all references, package name, CLI commands

## Earlier Development

See `CHANGELOG.md` for full version history (v0.6.0 through v0.7.0).
