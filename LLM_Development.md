# LLM Development Log

Chronological record of development activity on Stele, maintained for LLM agent context.

## 2026-03-21 - v0.9.0 Release & CI Fixes

### Features added
- Config system (`.stele.toml`) with minimal TOML parser for Python 3.9-3.10
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

- Renamed from ChunkForge to Stele
- Updated all references, package name, CLI commands

## Earlier Development

See `CHANGELOG.md` for full version history (v0.6.0 through v0.7.0).
