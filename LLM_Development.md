# LLM Development Log

Chronological record of development activity on Stele Context, maintained for LLM agent context.

## 2026-03-27 - v1.0.3 Agent UX and index diagnostics

- **index_health** module, `map`/`stats` alerts and `project_root`; hybrid search tuning; `search_mode=keyword`; symbol `guidance`; CI `search-regression` job; `pyproject.toml` version synced with `__version__` for PyPI.

## 2026-03-22 - v1.0.1 Codebase Cleanup

### Bug fixes
- Fixed potential `UnboundLocalError` in `TextChunker._chunk_by_paragraphs()` for empty input
- Fixed division-by-zero guard in `ImageChunker._color_histogram()` when bins > histogram length
- Fixed `SymbolManagerProto.propagate_staleness` return type mismatch (`None` → `int`)
- Removed duplicate `main = run` in `mcp_stdio.py`
- Added `_heartbeat_thread.join()` in `MCPServer.stop()` for clean shutdown
- Fixed `row_factory` state leakage in all four `lock_ops.py` functions (save/restore pattern)
- Reset `row_factory` in non-pooled connection path in `storage_schema.py`
- Fixed `test_resolve_absolute_passthrough` for Windows — use `C:\` path on `nt`
- Kept `Optional[str]` in `change_notifications.py` `Callable` type alias — PEP 604 `str | None` in runtime type aliases (outside function annotations) is not supported on Python 3.9

### Code quality
- Narrowed broad `except Exception` to specific exception types in `indexing.py`, `search_engine.py`, and `change_detection.py`
- Created `tests/conftest.py` with shared fixtures (`stele_engine`, `stele_engine_with_file`, `stele_engine_with_data`)
- Migrated `test_metadata.py`, `test_cli.py`, `test_concurrency.py` to use shared conftest fixtures
- Completed generic type annotations in `symbol_graph.py`
- Moved `os`/`tempfile` imports to module level in `audio.py` and `video.py`
- Extracted `get_modality_flags()` helper in `tool_registry.py`, used by both MCP servers
- Removed stale comment in `text.py`

## 2026-03-22 - v1.0.0 Stable Release

### Breaking changes
- Removed `Stele.save_state()` alias (use `save_kv_state()`)

### Status
- Classifier: "Development Status :: 5 - Production/Stable"
- 49 source files, ~13,000 LOC implementation
- 739 tests across 28 test files
- CI: Python 3.9-3.13, Linux/macOS/Windows
- Zero required dependencies

## 2026-03-22 - v0.11.0 Beta Release

### Status upgrade
- Changed pyproject.toml classifier from "3 - Alpha" to "4 - Beta"
- Bumped version to 0.11.0 to mark the milestone

### API surface definition
- Added `__all__` to `engine.py` (`["Stele"]`), `storage.py` (`["StorageBackend"]`), `session.py` (`["SessionManager"]`), `chunkers/base.py` (`["Chunk", "BaseChunker", "estimate_tokens"]`)
- Created STABILITY.md with full API stability guarantees, public vs internal method catalog, deprecation policy, and deprecation schedule

## 2026-03-22 - v0.10.6 1.0 Readiness Items

### Infrastructure
- Added SECURITY.md with vulnerability reporting process and security design documentation
- Added Windows and macOS CI runners (`test-platform` job with Python 3.12)
- Created git tags for all untagged versions: v0.9.0, v0.9.1, v0.9.2, v0.9.3, v0.10.0–v0.10.5
- Updated CHANGELOG.md through v0.10.6

### Type safety
- Created `protocols.py` with structural Protocol types for delegation boundaries: `StorageProto`, `VectorIndexProto`, `SymbolManagerProto`, `CoordinationProto`
- Protocols are `TYPE_CHECKING`-only — delegation functions keep `Any` at runtime to avoid import cycles, but IDEs/developers can reference protocols for the exact expected interface

### Bug fixes
- Added `PRAGMA wal_checkpoint(TRUNCATE)` to `StorageBackend.close()` — prevents unbounded WAL file growth for long-running servers

### New tests (739 total, was 708)
- `test_media_chunkers.py`: Media chunker extensions, HAS_* flags, modality detection routing (30 tests)
- Signature cache test appended to `test_engine.py`: verifies unchanged content re-uses cached signatures during re-indexing

## 2026-03-22 - v0.10.5 CI Fix + Test Coverage Expansion

### CI fixes
- Fixed Python 3.9 import failure: `str | None` in module-level type alias `GetWorktreeFn` in `change_notifications.py` — replaced with `Optional[str]` (PEP 604 union syntax not available at runtime on Python <3.10)
- Fixed missing `main = run` alias in `mcp_stdio.py` — was never committed, causing `AttributeError` in CI tests and broken `stele-context-mcp` entry point
- Added `fail-fast: false` to CI matrix so all Python versions run even if one fails
- Added Python 3.13 to CI test matrix (was in pyproject.toml classifiers but not tested)

### New test files (129 new tests, total 708)
- `test_stemmer.py`: Porter stemmer `stem()` and `split_identifier()` with known test vectors
- `test_cli.py`: CLI argument parsing, all subcommands, JSON output mode, error cases
- `test_search_engine.py`: `compute_search_alpha()`, `extract_query_identifiers()`, `_text_signature()`, `init_chunkers()`
- `test_connection_pool.py`: Thread-local connection reuse, cross-thread isolation, `connect()` context manager commit/rollback, `search_text()` edge cases (invalid regex, empty pattern)

### Test fixes
- Fixed `tempfile.mkdtemp()` leaks in `test_symbols.py` — added `teardown_method` with `shutil.rmtree` to all 6 test classes
- Fixed CONTRIBUTING.md: replaced Black/isort references with ruff (matches actual CI tooling)

### Documentation
- Updated CHANGELOG.md to cover versions 0.10.3 through 0.10.5 (was stale at 0.9.0)
- Updated COMPLETE_PROJECT_DOCUMENTATION.md with new test file entries

## 2026-03-22 - v0.10.4 Comprehensive Codebase Cleanup

### Dead code removed
- Removed `_replace_suffix()` from `stemmer.py` (defined but never called)
- Removed `stem_tokens()` from `stemmer.py` (defined but never imported/called)
- Removed redundant `changed` tracking variable from `storage_schema.migrate_database()`
- Removed duplicate `clear_chunk_edges()` call in `storage.remove_document()` (already handled by `delete_chunks()`)
- Removed unreachable `if not lines` guard in `text_chunker._calculate_density()` (`str.split()` never returns empty list)
- Removed unnecessary `hasattr(node, "end_lineno")` guard in `code_chunker.py` (Python 3.9+ guarantees the attribute)

### Redundant code eliminated
- Removed 15+ redundant `conn.commit()` calls inside `with connect() as conn:` / `with self._connect() as conn:` context manager blocks across coordination.py, agent_registry.py, change_notifications.py, storage_schema.py (context manager auto-commits on success)
- Removed 4 redundant `is not None` checks in `search_engine.init_chunkers()` (`HAS_*_CHUNKER` flags already imply non-None)
- Removed unnecessary `content: Any` / `file_content: Any` forward-declarations that were immediately assigned on the next line (indexing.py, change_detection.py)
- Inlined two thin one-line wrapper methods (`_chunk_paragraphs`, `_chunk_adaptive`) in `text_chunker.py` — callers now call `_chunk_by_paragraphs()` directly
- Consolidated duplicate `from stele_context.storage_schema import ...` lines in `storage.py` into single import

### Code simplification
- Simplified `coordination._record_conflict()`: collapsed the `conn_or_none` pattern (only ever called with None) into a direct `with self._connect() as conn:` block, removing 20+ lines of boilerplate
- Merged two separate `child.relative_to(p).parts` iterations in `indexing.expand_paths()` into a single pass
- Inlined `mcp_handlers.py` logic into `mcp_server.py` (module was 56 lines, mcp_server was 262 — combined 300, well under 500 LOC limit). `mcp_handlers.py` reduced to backward-compat re-export shim
- Moved deferred `import re` in `storage.search_text()` to module-level (was re-importing on every call)

### Bug fixes
- Added missing `"type": "object"` to `kv_data` property in `mcp_tool_defs.py` `save_kv_state` schema (was the only property without a type)
- Fixed stale comment in `test_mcp_server.py` ("Must contain all 15 tools" updated to 42)
- Moved `logging.basicConfig()` from module-level import side-effect in `mcp_server.py` to `MCPServer.start()` (prevents global logger reconfiguration on any import of the module)

### Modernization
- Added `from __future__ import annotations` to `cli_metadata.py` (was the only module missing it)
- Updated `__author__` from "Stele Contributors" to "Stele Context Contributors" to match pyproject.toml
- Updated `__init__.py` module docstring to "Stele Context" (rebranding was incomplete)

### Version
- Bumped to 0.10.4 in pyproject.toml and __init__.py
- Updated COMPLETE_PROJECT_DOCUMENTATION.md test count to 579

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
