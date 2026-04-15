# Changelog

All notable changes to Stele Context will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.1] - 2026-04-15

### Added
- **`WriterQueue` single-writer queue** — New zero-dependency `stele_context/storage_writer.py` serializes all SQLite write operations through a single daemon thread, eliminating "unable to open database file" errors under heavy concurrent load.
- **`sqlite_retry` decorator** — `connection_pool.py` now provides a zero-dependency retry decorator with exponential backoff for SQLite busy/locked errors.
- **Doctor DB health & search quality** — `doctor_snapshot` now includes `db_health` (WAL size, busy ratio, recommended action) and `search_quality` (Tier-2 coverage, HNSW span, advice).
- **Search result provenance** — `search` results now include `source` (`hnsw`, `bm25`, `symbol_boost`) and `tier2_present` so callers can see why each result ranked where it did.
- **`file_dependencies` fallback graph** — New table stores file-level import dependencies extracted from symbols. `impact_radius` now unions the symbol-edge graph with `file_dependencies` when the symbol graph is sparse, eliminating zero-result false negatives for heavily-imported modules.
- **Barrel-module expansion** — `extract_file_dependencies` detects re-exported symbols and creates synthetic barrel edges, enabling `impact_radius` to trace one hop through intermediate index files.
- **Time-decayed staleness** — `propagate_staleness` now stores `stale_since` timestamps. `stale_chunks` accepts `max_age_seconds` (default `None`) to ignore ancient transitive staleness.
- **`query` smart defaults** — `query` now auto-enables `working_tree` when `session_id` is provided and the git working tree is dirty. On projects with >500 files, it auto-restricts `path_prefix` to the most recently modified 25% unless the query contains global-scope keywords.
- **Operation telemetry (`operation_log`)** — Zero-dependency observability table tracks every tool invocation (success, error type, duration). Wrapped via `build_tool_map` so both HTTP and stdio MCP servers log automatically.
- **Self-healing error hints** — MCP servers now append an actionable `hint` field to error responses for known failure patterns (SQLite busy, empty impact radius, low HNSW signal).
- **`impact_radius(..., direction=...)`** — New `direction` parameter supporting `dependents` (default, incoming edges), `dependencies` (outgoing edges), and `both`. Also adds hybrid seeding for `document_path` mode: when the symbol edge graph is sparse for base classes, raw symbol references are used as a fallback so high fan-in files no longer report zero affected chunks.
- **`coupling(..., mode="co_consumers")`** — New `co_consumers` mode that detects files co-imported or co-referenced by the same consumers as the target file, catching tight coupling invisible to shared-outgoing-edge analysis.
- **`working_tree` auto-indexing** — `agent_grep`, `search_text`, `search`, and `query` now accept `working_tree=True` to auto-index modified and untracked files from the git working tree before searching.
- **`stale_chunks` guidance** — When many files are stale at the default threshold, the response now includes guidance suggesting `threshold=0.5` or `0.64` for active codebases.

### Changed
- **Storage write paths refactored** — `storage.py` and `storage_delegates.py` now route all mutations through the single-writer queue. Reads continue to use the existing connection pool/context manager, preserving SQLite WAL concurrent-reader behavior.
- **WAL auto-checkpoint tuning** — `PRAGMA wal_autocheckpoint=1000` added to writer and schema connections to reduce WAL growth under write-heavy workloads.
- **MCP Standard mode reduced to ~32 tools** — Moved `get_chunk_history`, `list_sessions`, `environment_check`, `clean_bytecache`, `prune_history`, `get_dynamic_symbols`, and `get_notifications` to Full-mode only. `query` is now the explicitly recommended universal starting point.

### Fixed
- **AttributeError in storage refactor** — Restored the `_write()` helper on `StorageBackend` to bridge legacy write-method calls to the new `WriterQueue`, fixing test regressions introduced during the partial refactor.
- **SQLite stability** — Increased connection timeout to 30 seconds in `ConnectionPool` and fallback `sqlite3.connect()` calls to reduce "unable to open database file" errors under concurrent load.
- **`agent_grep` database error handling** — Catches SQLite errors gracefully and returns actionable guidance to run `rebuild_symbols` or `detect_changes`.
- **`query()` text match integration** — Fixed broken `agent_grep` result parsing that was looking for non-existent `results` and `chunk_id` keys. Errors from sub-searches are now surfaced in `errors` instead of being silently swallowed.

## [1.1.0] - 2026-04-14

### Added
- **`query`** — Composite retrieval tool that merges semantic search, symbol graph lookups, and text grep into a single deduplicated result list with source provenance.
- **`batch`** — Multi-operation tool that executes a sequence of engine methods in one round-trip.
- **`bulk_store_embeddings`** — Batch API for storing raw embedding vectors across multiple chunks at once. Useful for large dynamic symbol meshes and Tier-2 semantic enrichment workflows.
- **`impact_radius(..., symbol=...)`** — Analyze blast radius by symbol name, enabling impact analysis for dynamic/runtime symbols that have no on-disk file (e.g. plugin hooks registered via `register_dynamic_symbols`).
- **`coupling` dynamic symbol fallback** — When a document_path has no indexed chunks, `coupling` now falls back to dynamic symbols registered for that path, allowing coupling analysis for synthetic/runtime documents.
- **Symbol `container` scoping** — The `Symbol` dataclass now carries a `container` field (e.g. `ClassName` or `ClassName.methodName`) populated by the Python AST and JS/TS regex extractors. `resolve_symbols` uses this to prefer definitions that share the reference's container, eliminating false coupling between unrelated files that happen to define the same generic name.
- **Test-to-source linking** — The symbol graph now creates `test_of` edges automatically. Test files are linked to source files via filename convention (`test_X.py` → `X.py`, `X_test.py` → `X.py`) and import-to-path analysis, making `impact_radius` and `coupling` aware of test coverage.
- **Incremental edge rebuilds (restored)** — `rebuild_edges(affected_chunk_ids=...)` is safe again. It tracks which symbol names changed in affected chunks and re-resolves edges for *all* chunks referencing those names, preserving edges from unchanged files that point into modified files. Large codebase indexing no longer forces a full O(N) rebuild on every update.
- **JS bare function call extraction** — `extract_javascript` now captures bare function calls (`validatePositiveInt(value)`) and `new ClassName()` constructor calls, enabling `find_references` to link test files and internal call sites that were previously invisible.
- **Impact radius significance thresholding** — `impact_radius(..., significance_threshold=0.1)` filters out edges driven by common stdlib/generic symbols (`push`, `has`, `addEdge`, `addNode`, etc.), preventing massive over-estimation of blast radius for new files. Optional `exclude_symbols=[...]` lets callers suppress specific symbols.
- **Coupling significance thresholding** — `coupling(..., significance_threshold=0.1)` applies the same common-symbol discounting. Results now include a `semantic_score` that penalises generic shared symbols, sorting by meaning instead of raw edge count.
- **Shadow-aware definitions** — `find_definition` now annotates multiple definitions of the same symbol in a single file with `definition_index`, `shadowed: true`, and `shadow_count`, making block-scope shadowed symbols visible.
- **Extended `_NOISE_REFS`** — Added `now`, `from`, `addNode`, `addEdge`, `removeNode`, `removeEdge`, `getNode`, `getEdge`, `hasNode`, `hasEdge`, `setNode`, `setEdge`, `updateNode`, `updateEdge`, `findNode`, `findEdge`, `queryNode`, `queryEdge` to the noise set, reducing false coupling and impact from commonly-shared generic method names.

### Changed
- **Simplified MCP surface** — Consolidated tool families to reduce LLM cognitive load:
  - `document_lock` replaces 7 separate locking tools (`acquire_document_lock`, `release_document_lock`, `refresh_document_lock`, `get_document_lock_status`, `release_agent_locks`, `get_conflicts`, `reap_expired_locks`).
  - `annotations` replaces 6 separate annotation tools (`annotate`, `get_annotations`, `delete_annotation`, `update_annotation`, `search_annotations`, `bulk_annotate`).
  - Singleton enrichment tools removed from MCP: `store_semantic_summary`, `store_embedding`, `store_chunk_agent_notes` (bulk variants and `llm_embed` remain).
  - Orientation tools removed from MCP: `stats`, `project_brief` (`doctor` and `map` remain; Python API unchanged).
  - Standard mode now registers **42 tools** (down from 56).
- **MCP Lite mode** — Set `STELE_MCP_MODE=lite` to expose only ~15 high-leverage tools for simpler agents.
- **MCP Full mode** — Set `STELE_MCP_MODE=full` to restore all deprecated singleton tools for backward compatibility.

### Fixed
- **MCP `agent_id` injection** — The MCP bridge no longer injects `agent_id` into tools that do not accept it (e.g. `llm_embed`, `store_embedding`, `bulk_store_embeddings`). Fixes `TypeError: got an unexpected keyword argument 'agent_id'` when calling embedding tools through the MCP server.
- **Regex quote corruption** — Fixed a `SyntaxError` in `symbol_patterns.py` caused by unescaped double quotes inside `r"..."` raw-string regex character classes.
- `find_references` for `const Alias = Class` patterns no longer returns `not_found` — the alias now creates an edge to the original definition.
- `coupling` for barrel modules using `module.exports = { ...require('./x') }` now shows connected files instead of returning empty results.
- `coupling` no longer reports dozens of false-positive connections through Node.js stdlib imports and ubiquitous method names like `getStats`.
- `find_references` now surfaces test-file and internal JS call-site references that were missed because bare function calls and `new ClassName()` were not extracted.

## [1.0.9] - 2026-03-31

### Added
- **`get_context` recently_searched tracking** — When `session_id` is provided, `get_context` now returns `recently_searched` (bool) and `search_pattern` (str) on unchanged and new entries, indicating if the file was found via `agent_grep`/`search_text` in this session. Enables agents to know "I already grep'd this file for the exact line I need."

## [1.0.8] - 2026-03-31

### Added
- **Grep-first indexing** — `agent_grep` and `search_text` with `session_id` now auto-index files that had matches and record search history. `get_search_history(session_id)` returns what you grep'd; `get_context` with `session_id` records fully-read files. New tools: `get_search_history`, `get_session_read_files`. MCP tool count: **55** (was 53).

### Fixed
- **`indexing.py` / `change_detection.py`** — `rebuild_edges` was called with `affected_chunk_ids=...` (incremental), silently dropping edges from unchanged files that referenced newly indexed/modified files. Temporarily changed to full rebuild (`affected_chunk_ids=None`) — O(30K symbols, <1 s). `index_documents` now also calls `propagate_staleness` after indexing, so `stale_chunks()` correctly flags dependents. Manual `rebuild_graph` no longer needed after `stele index`.

## [1.0.7] - 2026-03-27

### Fixed
- **Tests** — `test_integration_js_impact_from_html` normalizes `document_path` with `Path(...).as_posix()` so assertions match stored paths on **Windows** (`public\page.html` vs `public/page.html`).

## [1.0.6] - 2026-03-27

### Fixed
- **`index_store._save_compressed_json`** — On **Windows** (no `fcntl`), concurrent index saves now use **`msvcrt.locking`** on the sidecar lock file and **retries** for **`os.replace`** on `PermissionError` (WinError 5), matching Unix cross-process safety. Fixes `test_concurrent_saves_no_corruption` on `windows-latest` CI.

## [1.0.5] - 2026-03-27

### Added
- **Hybrid search** — BM25-only fallback when **max raw HNSW cosine** is below a fixed weak-signal threshold (~0.70), in addition to existing disagreement / flat-HNSW / clear-winner fallbacks (still **zero core deps**).
- **`Stele.search(..., path_prefix=)`** / **`get_map(..., path_prefix=)`** — optional **project-relative path prefix** to scope results when one storage index spans multiple trees (MCP + engine; CLI **`--path-prefix`** on **`search`** and **`map`**).
- **`Stele.impact_radius(..., summary_mode=, top_n_files=)`** — bounded **summary** payload: **`depth_distribution`**, top-**N** impacted files by chunk count, **`files_total`** (MCP + engine).

### Changed
- **`cli_metadata.cmd_map`** — passes **`max_annotation_chars`** through to **`get_map`**.

### Documentation
- README, AGENTS.md, STABILITY.md, CLAUDE.md, `docs/agent-workflow.md`, findings note, COMPLETE_PROJECT_DOCUMENTATION, LLM_Development — parity with 1.0.5 APIs and **reasonable stopping place** for zero-dep core scope (see STABILITY.md).

## [1.0.4] - 2026-03-27

### Added
- **`AGENTS.md`**, **[`docs/philosophy.md`](docs/philosophy.md)**, **[`docs/agent-workflow.md`](docs/agent-workflow.md)** — agent-oriented design, session vs project index, Tier 2 bootstrap, tool choice
- **`stele_context/agent_response.py`** — token-bounded `search`/`map`/`stats` helpers, `project_brief` builder, chunk content trimming
- **`Stele.doctor_snapshot()`** / MCP+CLI **`doctor`** — one-screen health: version, Python, storage, counts, `index_health`, `environment_check`, compact map preview
- **`Stele.get_project_brief()`** / MCP+CLI **`project-brief`** — largest files by tokens, extension histogram, totals
- **`Stele.search(...)`** — `max_result_tokens`, `compact`, `return_response_meta` for bounded agent context
- **`Stele.get_map` / `get_stats`** — `compact` and map limits (`max_documents`, `max_annotation_chars`)
- **`Stele.get_context`** — `include_trust`, `max_chunk_content_tokens`; **trust** hints (mtime vs index, staleness); **`agent_notes`** on chunks (JSON or text)
- **SQLite `chunks.agent_notes`** — `store_chunk_agent_notes`, `bulk_store_chunk_agent_notes` (MCP + engine)
- **CLI**: `doctor`, `project-brief`, `search --compact|--max-result-tokens|--meta`, `stats --compact`, `map --compact|--max-documents`
- **MCP tools** — `doctor`, `project_brief`, chunk-notes tools; extended schemas for `search`, `map`, `stats`, `get_context` (53 tools total)
- **Tests** — `tests/test_agent_response.py`

### Documentation
- README, COMPLETE_PROJECT_DOCUMENTATION, wiki, CLAUDE.md aligned with agent UX and current tool/registry counts

## [1.0.3] - 2026-03-27

### Added
- **`index_health` module** (`index_health.py`) — `compute_index_health_snapshot()` for `map`/`stats`: `alerts`, `symbol_graph_status`, `chunk_store_status`, `seconds_since_last_index`, `storage_dir`; actionable hints for empty index, missing symbols with chunks present, and index older than ~7 days
- **`Stele.search(..., search_mode=)`** — `hybrid` (default) or `keyword` (BM25-only, no HNSW); CLI `--search-mode`
- **`map` / `get_stats`** — `index_health`, **`project_root`** (detected repo root or `null`)
- **CI** — `search-regression` job: `pip install -e .` + pytest only, runs `pytest -m search_regression`
- **Tests** — `test_index_health.py`, `test_search_regression.py`, `@pytest.mark.search_regression`

### Changed
- **Hybrid search** — Stronger BM25 weight for natural-language queries, flat-HNSW detection, proximity terms from words ≥4 chars; default `search_alpha` **0.42**; rank-disagreement threshold **0.5**
- **`find_references` / `find_definition`** — `symbol_index` + `guidance` when there are no hits (empty graph vs missing symbol)
- **MCP tool descriptions** — Search positioned as secondary to `agent_grep` / `search_text` / symbol tools; `detect_changes` documents `scan_new` default
- **`pyproject.toml` version** — Synced with `__version__` (was 1.0.1 vs 1.0.2)

### Fixed
- Documentation and packaging version alignment for PyPI sdist/wheel metadata

## [1.0.1] - 2026-03-22

### Fixed
- **text.py**: Initialize `metadata` before paragraph loop to prevent potential `UnboundLocalError` on empty input
- **image.py**: Guard `_color_histogram()` against division-by-zero when histogram length < bins (`step = max(1, ...)`)
- **protocols.py**: Fix `propagate_staleness` return type (`None` → `int`) to match actual implementation in `symbol_graph.py`
- **mcp_stdio.py**: Remove duplicate `main = run` assignment
- **mcp_server.py**: Join `_heartbeat_thread` in `stop()` for clean daemon shutdown
- **lock_ops.py**: Save/restore `row_factory` in all four functions to prevent state leakage through connection pool
- **storage_schema.py**: Reset `row_factory = None` in fallback (non-pooled) connection path for consistency
- **test_worktree_safety.py**: Fix `test_resolve_absolute_passthrough` for Windows — use native `C:\` path on `os.name == "nt"`
- **change_notifications.py**: Keep `Optional[str]` in `Callable` type alias (PEP 604 `str | None` in runtime type aliases fails on Python 3.9)

### Changed
- **indexing.py**: Narrow `except Exception` to `(OSError, UnicodeDecodeError, ValueError)` in per-document processing
- **search_engine.py**: Narrow `except Exception` to specific types in index rebuild (`TypeError, ValueError, KeyError, struct.error`) and file read (`OSError, UnicodeDecodeError, ValueError`)
- **change_detection.py**: Narrow `except Exception` to `(OSError, UnicodeDecodeError, ValueError)` in file read path
- **symbol_graph.py**: Complete generic type annotations (`set[str]`, `dict[str, list[dict[str, Any]]]`)
- **audio.py / video.py**: Move `os` and `tempfile` imports to module level (were re-imported inside methods)
- **tool_registry.py**: Extract `get_modality_flags()` helper to deduplicate modality flag construction from both MCP servers
- **mcp_server.py / mcp_stdio.py**: Use shared `get_modality_flags()` instead of inline flag dicts
- **text.py**: Remove stale comment claiming methods were inlined

### Added
- **tests/conftest.py**: Shared pytest fixtures (`stele_engine`, `stele_engine_with_file`, `stele_engine_with_data`) replacing duplicated helper functions across test files
- **test_metadata.py, test_cli.py, test_concurrency.py**: Migrated to shared conftest fixtures

## [1.0.0] - 2026-03-22

### Changed
- **Production/Stable release** — classifier updated to "5 - Production/Stable"
- **API frozen** — all public methods on `Stele`, `Chunk`, `StorageBackend`, `SessionManager` are stable per STABILITY.md

### Removed
- **`Stele.save_state()` alias** — use `Stele.save_kv_state()` (the canonical name since 0.10.0)

### Summary
Local context cache for LLM agents. 100% offline, zero required dependencies.
49 source files, ~13,000 LOC. 739 tests across 28 test files. CI green on Python
3.9-3.13 across Linux, macOS, and Windows. 42 MCP tools exposed via HTTP and
stdio servers. Pure-Python HNSW + BM25 hybrid search. Multi-agent coordination
with document locks, optimistic versioning, and cross-worktree shared state.

## [0.11.0] - 2026-03-22

### Changed
- **Development Status: Beta** — classifier updated from "3 - Alpha" to "4 - Beta" in pyproject.toml
- **Version bump** to 0.11.0 to mark the Beta milestone

### Added
- **STABILITY.md** — API stability guarantees, public vs internal method documentation, deprecation policy, and typing protocol reference
- **`__all__` exports** on `engine.py`, `storage.py`, `session.py`, `chunkers/base.py` — defines the public API boundary for each module

## [0.10.6] - 2026-03-22

### Added
- **SECURITY.md**: Vulnerability reporting process, scope definitions, security design principles
- **Windows/macOS CI**: New `test-platform` job runs tests on `macos-latest` and `windows-latest` with Python 3.12
- **Typing protocols**: `protocols.py` defines `StorageProto`, `VectorIndexProto`, `SymbolManagerProto`, `CoordinationProto` for type-safe delegation boundaries (TYPE_CHECKING-only, no runtime overhead)
- **WAL checkpoint on close**: `StorageBackend.close()` runs `PRAGMA wal_checkpoint(TRUNCATE)` to prevent unbounded WAL growth
- **Media chunker tests**: `test_media_chunkers.py` covers extensions, HAS_* flags, and modality detection (works without optional deps)
- **Signature cache test**: Verifies unchanged content re-uses cached semantic signatures during re-indexing
- **Git tags**: Created tags v0.9.0 through v0.10.5 for all previously untagged releases

## [0.10.5] - 2026-03-22

### Fixed
- **CI failure on Python 3.9**: `str | None` type union in `change_notifications.py` module-level alias failed at import time on Python <3.10 — replaced with `Optional[str]`
- **Missing `main` alias in `mcp_stdio.py`**: `main = run` was never committed, causing `AttributeError` in tests and breaking the `stele-context-mcp` entry point
- **Stale test comment**: "Must contain all 15 tools" updated to 42 in `test_mcp_server.py`
- **`tempfile.mkdtemp()` leaks in `test_symbols.py`**: 6 test classes created temp directories without cleanup — added `teardown_method` with `shutil.rmtree`

### Added
- **New test files**: `test_stemmer.py` (Porter stemmer), `test_cli.py` (CLI commands), `test_search_engine.py` (search ranking), `test_connection_pool.py` (connection pool + search_text edge cases)
- **Python 3.13 in CI matrix**: Added 3.13 to test workflow (matches pyproject.toml classifiers)
- **`fail-fast: false`** in CI: All Python versions now run to completion even if one fails

### Changed
- **CONTRIBUTING.md**: Fixed Black/isort references to ruff (matches actual CI tooling)
- **CI actions**: Updated test matrix to include Python 3.13

## [0.10.3] - 2026-03-21

### Fixed
- Position tracking bug in `CodeChunker._boundaries_to_chunks()`
- Unbounded SQL query in `metadata_storage.get_change_history()` with document_path filter
- agent_id injection inconsistency between stdio and HTTP servers
- Added lock tools to `WRITE_TOOLS` for proper auto agent_id injection
- Synced pyproject.toml version with __init__.py

### Removed
- 35 redundant `conn.commit()` calls across storage modules
- Unnecessary `isinstance(d, dict)` check in change_detection.py
- Production `assert` in image.py

### Added
- `Chunk` to `stele_context/__init__.py` exports

## [0.10.4] - 2026-03-22

### Removed
- Dead functions `_replace_suffix()` and `stem_tokens()` from stemmer.py
- 15+ redundant `conn.commit()` calls in coordination modules
- Redundant `is not None` checks, unreachable guards, stale tracking variables
- Duplicate `clear_chunk_edges()` call in `storage.remove_document()`

### Changed
- Inlined `mcp_handlers.py` logic into `mcp_server.py` (handlers reduced to re-export shim)
- Simplified `coordination._record_conflict()` from 35 lines to 12
- Moved `logging.basicConfig()` from module-level to `MCPServer.start()`
- Updated branding to "Stele Context" in `__init__.py`

### Fixed
- Missing `"type": "object"` on `kv_data` schema in `mcp_tool_defs.py`

## [0.9.0] - 2026-03-21

### Added
- **`.stele-context.toml` configuration** — Project-level config file loaded from `<project_root>/.stele-context.toml`. Supports `storage_dir`, `chunk_size`, `max_chunk_size`, `merge_threshold`, `change_threshold`, `search_alpha`, `skip_dirs` under a `[stele-context]` section. Uses stdlib `tomllib` (Python 3.11+) with a minimal fallback parser for 3.9-3.10. Explicit constructor params override config values.
- **Tree-sitter code chunking** — `CodeChunker` now uses tree-sitter AST parsing for JavaScript, TypeScript, Java, C, C++, Go, Rust, Ruby, and PHP when installed (`pip install stele-context[tree-sitter]`). Falls back to regex patterns if tree-sitter is not available. Grammar packages are lazy-loaded and cached.
- **Chunk history query tools** — `get_chunk_history(chunk_id=, document_path=, limit=)` method on engine, exposed as MCP tools on both HTTP (28 tools total) and stdio (30 tools total) servers. Queries the `chunk_history` table for chunk version history.
- **Performance benchmarks** — `benchmarks/` directory with `bench_chunking.py`, `bench_storage.py`, `bench_search.py`, and `run_all.py` runner. Zero external dependencies, standalone-runnable, `--quick` mode for CI.
- **`[tree-sitter]` optional dependency group** — `pip install stele-context[tree-sitter]` installs tree-sitter + 9 language grammar packages.
- **`tests/test_config.py`** (18 tests) — TOML parser, config loading, config merging, engine integration.
- **`tests/test_chunk_history.py`** (8 tests) — Storage and engine chunk history queries.
- **`tests/test_tree_sitter.py`** (13 tests) — Tree-sitter chunking for JS, TS, Go, Rust, Java, C, plus large files and edge cases.
- **CODE_OF_CONDUCT.md** — Contributor Covenant v2.0.
- **README.md overhaul** — Mermaid architecture diagram, comparison table vs alternatives, FAQ section, troubleshooting guide, updated tool counts and benchmark results.
- **CI coverage enforcement** — `--cov-fail-under=80` added to test workflow.
- **Agent-supplied semantic embeddings** — Two-tier signature system. `store_semantic_summary(chunk_id, summary)` accepts agent's natural language description, computes 128-dim signature from it, updates HNSW index. `store_embedding(chunk_id, vector)` stores raw vectors. HNSW prefers agent signatures for search, falls back to statistical. Zero new dependencies — the calling LLM agent IS the embedding model. ~9% search quality improvement on semantic match benchmarks.
- **`tests/test_agent_embeddings.py`** (12 tests) — Storage, engine, and index rebuild tests for agent-supplied embeddings.

### Changed
- **MCP tools**: HTTP 30 (was 27), stdio 32 (was 29)
- **Engine constructor** — Now accepts `Optional[int]`/`Optional[float]` for numeric params (was fixed defaults), enabling config file values to slot in between.
- **Python AST chunking** — Refactored to share `_boundaries_to_chunks()` with tree-sitter path.
- **`chunks` table** — New columns: `semantic_summary TEXT`, `agent_signature BLOB` (auto-migrated).
- **HNSW index rebuild** — Uses `agent_signature` when available, falls back to `semantic_signature`.
- **412 tests** (was 355), 1 skipped (MCP SDK not installed)

## [0.8.0] - 2026-03-16

### Added
- **Multi-agent support** — Multiple LLM agents can safely share one Stele Context instance (via HTTP) or use separate MCP stdio processes without data corruption or semantic conflicts.
- **Read-write lock** (`rwlock.py`) — `RWLock` protects all engine public methods. Read operations (search, get_context, get_stats, etc.) allow concurrent access. Write operations (index_documents, detect_changes, annotate, etc.) get exclusive access. Zero external dependencies (stdlib `threading` only).
- **Threaded HTTP server** — `ThreadedHTTPServer(ThreadingMixIn, HTTPServer)` handles each request in a new thread. Safe because RWLock protects engine state.
- **Agent-aware sessions** — `sessions` table gains `agent_id TEXT` column (added via migration). `create_session(id, agent_id=)`, `list_sessions(agent_id=)` for multi-agent tracking. All existing APIs backward-compatible (agent_id defaults to None).
- **Per-document ownership** — `acquire_document_lock(path, agent_id, ttl=300)` gives exclusive write access. Other agents can read but writes are rejected with `PermissionError`. Locks auto-expire after TTL. `force=True` steals lock and logs a conflict. `release_agent_locks(agent_id)` for bulk cleanup.
- **Optimistic locking** — `doc_version INTEGER` on documents table, auto-incremented on each write. `index_documents(expected_versions={path: N})` rejects files whose version changed since the caller last read them. Prevents silent overwrites between agents.
- **Conflict log** — `document_conflicts` table records ownership violations, version conflicts, and lock steals with full audit trail. `get_conflicts(document_path=, agent_id=, limit=)` MCP tool for querying history.
- **`document_lock_storage.py`** (~270 LOC) — `DocumentLockStorage` delegate following the same pattern as `SessionStorage`, `MetadataStorage`, `SymbolStorage`. Owns lock columns on `documents` and the `document_conflicts` table.
- **5 new MCP tools** — `acquire_document_lock`, `release_document_lock`, `get_document_lock_status`, `release_agent_locks`, `get_conflicts` (both HTTP and stdio).
- **`list_sessions` tool** — New MCP tool (HTTP + stdio) to query sessions, optionally filtered by agent_id.
- **`agent_id` parameter** — Added to `index_documents()`, `detect_changes_and_update()`, `save_kv_state()`, and `remove_document()` for ownership checking.
- **`expected_versions` parameter** — Added to `index_documents()` for optimistic locking.
- **Cross-process file locking** — `index_store.py` uses `fcntl.flock()` on `.lock` sidecar files to prevent index corruption when multiple MCP stdio processes share `~/.stele-context/`. LOCK_EX for writes, LOCK_SH for reads. No-op fallback on Windows.
- **BM25 double-checked locking** — `_ensure_bm25()` uses a separate `threading.Lock` so concurrent readers don't race during lazy initialization.
- **`tests/test_concurrency.py`** (12 tests) — RWLock semantics, concurrent searches, write-blocks-read, BM25 lazy-init thread safety, agent_id roundtrip, backward compat, cross-process file locking.
- **`tests/test_conflicts.py`** (31 tests) — Per-document ownership (acquire, release, expiry, force-steal, ownership blocking), optimistic locking (version increment, stale rejection, CAS), conflict resolution (logging, filtering, pruning), backward compatibility, concurrent races.

### Changed
- **MCP tools**: HTTP 21 (was 15), stdio 29 (was 23)
- **`store_document()`** uses `INSERT ... ON CONFLICT DO UPDATE` instead of `INSERT OR REPLACE` to preserve `locked_by`/`doc_version` columns
- **Version**: 0.7.0 → 0.8.0

### Fixed
- **Deadlock in `detect_changes_and_update`** — `_detect_changes_unlocked()` called `self.remove_document()` which tried to re-acquire the non-reentrant write lock. Now inlines the removal logic.

### Tests
- **286 tests passing** (was 243), 1 skipped (MCP SDK not installed)

## [0.7.0] - 2026-03-16

### Added
- **Symbol graph** — Cross-file reference tracking with `find_references`, `find_definition`, `impact_radius`, and `rebuild_symbols` MCP tools. Extracts definitions and references from 12 language families (Python via AST, JS/TS/HTML/CSS/Java/Go/Rust/C/Ruby/PHP via regex). Zero new dependencies.
- **Cross-language linking** — HTML `class="btn"` → CSS `.btn {}`, JS `querySelector('.btn')` → CSS `.btn {}`, HTML `onclick="fn()"` → JS `function fn()`, JS `getElementById('app')` → HTML `id="app"`.
- **Directory indexing** — `index_documents()` now accepts directories. Recursively walks with `Path.rglob()`, filters by supported extensions, skips `.git`, `node_modules`, `__pycache__`, `.venv`, hidden dirs.
- **Configurable skip-dirs** — `Stele(skip_dirs={"vendor", "generated"})` to add project-specific directories to skip during directory indexing. Merges with defaults.
- **Staleness propagation** — When `detect_changes_and_update()` finds modified files, propagates staleness scores through symbol edges to dependents. Score = `0.8^depth` (direct dep = 0.8, transitive = 0.64). New `stale_chunks(threshold)` MCP tool.
- **Search with edges** — `search()` results now include `edges.depends_on` and `edges.depended_on_by` for each chunk, showing symbol connections without extra round-trips.
- **Module path resolution** — `from pkg.utils import helper` now prefers `helper` defined in `pkg/utils.py` over an unrelated `helper` in another file. Reduces false edges in multi-project indexes.
- **Symbol re-extraction on change** — `detect_changes_and_update()` re-extracts symbols and rebuilds edges for modified documents, keeping the graph in sync.
- **`stele_context/symbols.py`** (~530 LOC) — `SymbolExtractor` class, `resolve_symbols()` with module path hints, `_module_matches_path()` helper.
- **`stele_context/symbol_storage.py`** (~200 LOC) — `SymbolStorage` delegate; `symbols` and `symbol_edges` SQLite tables with indexed queries.
- **`tests/test_symbols.py`** (63 tests) — Symbol extraction, cross-language resolution, storage, engine integration, directory indexing, staleness, search-with-edges, skip-dirs, module path resolution.
- `staleness_score REAL DEFAULT 0.0` column on chunks table (added via migration).
- **Noise filter** — `_NOISE_REFS` frozenset (~60 entries) filters Python builtins, dunder methods, JS globals, and ambiguous method names (get, set, push, etc.) from symbol resolution. Reduces false edges by ~14% without losing real cross-file connections.
- **Better JS/TS extraction** — Destructured require (`const { a, b } = require('pkg')`), class method definitions (`methodName() {` inside class bodies), control-flow guard (if/for/while not detected as methods).
- **Symbol-boosted search** — `search()` extracts identifier-like tokens from queries and matches them against symbol definition names, surfacing chunks that define matching symbols even when HNSW+BM25 missed them.
- **Incremental edge rebuild** — `_rebuild_edges()` now accepts `affected_chunk_ids` to scope DB operations. Only edges involving indexed/modified chunks are cleared and re-created; unrelated edges preserved.

### Changed
- **MCP tools**: 23 total (was 18) — added `find_references`, `find_definition`, `impact_radius`, `rebuild_symbols`, `stale_chunks`
- **`storage.py`**: Delegates symbol operations to `SymbolStorage`; cleanup in `delete_chunks`/`remove_document`/`clear_all`; symbol stats in `get_storage_stats()`
- **Version**: 0.6.0 → 0.7.0

### Tests
- **229 tests passing** (was 152), 1 skipped (MCP SDK not installed)

## [0.6.0] - 2026-03-15

### Added
- **Hybrid search (BM25 + HNSW)** — `search()` widens HNSW to 3x candidates and re-ranks with BM25 keyword scores. Blend controlled by `search_alpha` (default 0.7). BM25 index lazily initialized on first search, persisted alongside HNSW.
- **`stele_context/bm25.py`** — Pure-Python Okapi BM25 keyword index with zero dependencies. Includes `to_dict()`/`from_dict()` for persistence.
- **BM25 persistence** — BM25 index serialized to `indices/bm25_index.json.zlib` with same staleness detection as HNSW. Loaded from disk on first search instead of rebuilding from SQLite.
- **Search alpha auto-tuning** — `_compute_search_alpha()` detects code-like queries (identifiers, brackets, keywords) and lowers alpha to weight keyword matching more heavily.
- **Per-modality thresholds** — `MODALITY_THRESHOLDS` dict: code uses merge=0.85 (preserves AST boundaries) and change=0.80 (tolerates incremental edits); text and PDF keep existing defaults
- **AST-boundary merge guard** — Code chunks starting with `def`, `class`, `function`, etc. are never merged with the preceding chunk, regardless of similarity
- **Adaptive ef_search** — HNSW search width auto-scales based on index size: 10 for <100 nodes, ef_search for <1K, 2x for <10K, 4x for 10K+
- **BPE merge-aware token estimation** — `estimate_tokens()` now applies space-word and punctuation-pair merge corrections, achieving ~95% accuracy vs actual BPE (was ~85-90%)
- **Binary file handling** — `index_documents()` and `detect_changes_and_update()` now read binary modalities (image, audio, video) as bytes instead of forcing UTF-8 text decode
- **Signature cache for incremental indexing** — On re-index, unchanged chunks reuse cached semantic signatures instead of recomputing
- **`estimate_tokens()` function** — Exported from `chunkers/base.py` as the single source of truth for token estimation
- **Document removal** — `remove` MCP tool and CLI command to unindex a document and clean up all its chunks, annotations, and index entries
- **Stale chunk cleanup** — Re-indexing and change detection now automatically delete old chunks that no longer exist in the new chunking
- `delete_chunks()` and `remove_document()` storage methods
- Schema migration for `version` column on older databases

### Changed
- **Single-pass merge** — `_merge_similar_chunks()` replaced O(n^2) `while changed` loop with single left-to-right pass
- **HNSW distance metric** — Replaced Euclidean distance with `1 - dot_product` for normalized vectors (avoids per-comparison sqrt)
- **HNSW remove() repairs graph** — Removing a node now reconnects orphaned neighbours to maintain graph connectivity
- **Vector storage deduplicated** — Removed redundant `chunk_vectors` dict from `VectorIndex`; vectors live only in HNSW nodes (~40% index memory reduction)
- **Token estimation consistency** — Replaced all `len(text) // 4` shortcuts with `estimate_tokens()` BPE-corrected tokenizer

### Fixed
- **Binary files read as UTF-8** — Image/audio/video files no longer forced through `read_text(errors="replace")`; now properly read as bytes
- **Schema migration gap** — Databases created before v0.4.0 lacked the `version` column on chunks table, causing index errors

## [0.5.5] - 2026-03-14

### Added
- **Annotation system** — `annotate`, `get_annotations`, `delete_annotation`, `update_annotation` MCP tools for attaching metadata to documents and chunks
- **Update annotations** — `update_annotation` MCP tool and CLI command to modify content/tags of existing annotations
- **Annotation search** — `search_annotations` MCP tool for substring search across annotation content
- **Bulk annotate** — `bulk_annotate` MCP tool to annotate multiple targets in one call
- **Project map** — `map` MCP tool and CLI command for project overview with chunk counts, tokens, and annotations
- **Change history** — `history` MCP tool and CLI command for change detection audit trail
- **History pruning** — `prune_history` MCP tool to clean up old entries by age or max count
- **`detect_changes` reason param** — optional `reason` string stored in change history
- **CLI commands** — `annotate`, `get-annotations`, `delete-annotation`, `update-annotation`, `map`, `history`
- New `annotations` and `change_history` SQLite tables
- `MetadataStorage` delegate class following `SessionStorage` pattern
- `cli_metadata.py` — CLI handler functions for metadata commands
- 33 new tests; total: 135

## [0.5.4] - 2026-03-13

### Fixed
- **MCP server crash on startup** — `Server.run()` in MCP SDK v1.26+ requires `InitializationOptions` with server name, version, and capabilities. Added proper initialization.
- **MCP tools not discoverable by clients** — `ServerCapabilities` was `tools=None, resources=None`, so clients like Claude Code never requested tool listings. Now advertises `ToolsCapability()` and `ResourcesCapability()`.
- **CLI `--version` stale** — was hardcoded `"0.5.0"`, now uses `__version__` dynamically
- **`engine.py` version hardcoded** — `get_stats()` now uses `__version__` via lazy import
- **Sliding window infinite loop risk** — overlap could equal chunk size causing zero forward progress; now caps overlap to ensure at least 1 sentence advance
- **Last chunk missing metadata** — `_chunk_by_paragraphs` final chunk now includes adaptive density/size metadata
- **Version tests fragile** — 3 test files now use `__version__` instead of hardcoded strings

### Removed
- **Dead `IndexNode` methods** — `distance()` and `cosine_similarity()` never called; `HNSWIndex` has its own
- **Dead `core.py` re-exports** — `np`, `HAS_NUMPY`, `_cosine_similarity` imported but nothing used them from core
- **Redundant engine imports** — module-level conditional imports for optional chunkers; now lazy-imported in `_init_chunkers()`
- **Unused `numpy` import** in `video.py`
- **Unused `HAS_NUMPY` import** in `base.py` (normalization simplified)
- **Empty `conftest.py`** — contained only a docstring with no fixtures

### Changed
- **`get_document_chunks` delegates** to `search_chunks()` instead of duplicating the SQL query
- **Regex patterns deduplicated** in `code.py` — js/jsx/mjs/cjs, ts/tsx, sh/bash/zsh share patterns
- **Signature normalization simplified** — removed HAS_NUMPY branch; list comprehension works for both paths
- **`compute_chunk_ids_hash` optimized** — uses `SELECT chunk_id` instead of `SELECT *` (avoids fetching content + BLOBs)

### Tests
- All 102 tests passing, 1 skipped (MCP SDK not installed)

## [0.5.3] - 2026-03-13

### Changed
- **Better token estimation** — replaced `len(content) // 4` heuristic with regex-based tokenizer that splits on camelCase, snake_case, punctuation, and numbers. Within ~10-15% of actual BPE token counts.
- **Enhanced semantic signatures** — added word bigrams (dims 80-95), positional features (dims 104-115: first-line keywords, indentation depth/variance, content density, lexical diversity, average word length, punctuation/numeric density). Signatures now use all 128 dimensions.
- **HNSW vector performance** — vectors stored as `array.array('f')` instead of `List[float]` for better memory layout and cache locality. Vector norms cached on `IndexNode` to avoid redundant sqrt computations during search. Query norm pre-computed once per search call.

### Tests
- All 102 tests passing, 1 skipped (MCP SDK not installed)

## [0.5.2] - 2026-03-13

### Added
- **Persistent HNSW index serialization** — index saved to `~/.stele-context/indices/hnsw_index.json.zlib` after indexing or change detection. Loaded on startup if fresh (chunk IDs hash matches), otherwise rebuilt from SQLite. Eliminates redundant O(n) rebuild on every startup.
- **`to_dict()`/`from_dict()` on `HNSWIndex` and `VectorIndex`** — serialization methods for round-tripping the full graph structure
- **`index_store.py` module** — `save_index()`, `load_index()`, `load_if_fresh()`, `compute_chunk_ids_hash()` functions for persistent index management
- **14 new tests** in `test_index_store.py` — round-trip serialization, staleness detection, corrupt file handling, search-after-reload integration

## [0.5.1] - 2026-03-13

### Fixed
- **`detect_changes_and_update` now persists updated chunks** — previously re-chunked content was computed but never stored, leaving stale data in SQLite
- **`store_chunk` preserves `access_count`** — INSERT OR REPLACE was resetting access_count to 0; now uses UPDATE for existing chunks
- **`prune_chunks` updates `total_tokens`** — sessions table now reflects actual token count after pruning
- **Binary semantic signatures padded to 128-dim** — hash-based signatures for binary content were 64-dim, causing dimension mismatch with text signatures
- **Consistent `results["new"]` types** — `detect_changes_and_update` now always returns dicts (was mixing strings and dicts)
- **`store_kv_state` consistent compression** — msgspec fallback path now applies zlib compression like the JSON path
- **`get_session()` hoisted out of per-chunk loop** — was called N times per unchanged document instead of once

### Removed
- **Pickle fallback** in session_storage.py — removed insecure pickle deserialization; only JSON+zlib and msgspec JSON are supported
- **Unused imports** across 12 source and test files (~20 imports removed)
- **All 4 unused conftest.py fixtures** — sample file fixtures that were never referenced by any test
- **Unused `current_start` variable** in code.py regex chunker

### Changed
- **Engine delegates session ops to SessionManager** — `save_kv_state`, `rollback`, `prune_chunks`, `get_relevant_kv` now delegate to `SessionManager` instead of duplicating logic
- **`storage.py` uses `sig_to_bytes()` from numpy_compat** — replaces duplicated numpy/struct conversion code
- **Raw SQL replaced with StorageBackend API** — `_rebuild_index`, `detect_changes_and_update`, and `mcp_stdio.read_resource` now use proper storage methods
- **`_extract_words` simplified** — one-liner with Counter comprehension
- **`_estimate_token_count` collapsed** — merged identical str/bytes branches

### Tests
- All 88 tests passing, 1 skipped (MCP SDK not installed)

## [0.5.0] - 2026-03-13

### Added
- **New engine module** (`engine.py`): Main Stele class now routes documents through modality-specific chunkers and wires in the HNSW vector index
- **Semantic search API** (`search(query, top_k)`): Returns chunk content + metadata ranked by HNSW similarity
- **Context cache API** (`get_context(document_paths)`): Returns cached chunk content for unchanged files, flags changed/new docs
- **Real MCP server** (`mcp_stdio.py`): JSON-RPC over stdio, compatible with Claude Desktop and MCP clients
- **`serve-mcp` CLI command**: Start stdio MCP server
- **`search` CLI command**: `stele search "query" --top-k 10`
- **SessionManager** (`session.py`): High-level session operations with HNSW-accelerated retrieval
- **SessionStorage** (`session_storage.py`): Extracted session operations from storage.py
- **numpy_compat module** (`chunkers/numpy_compat.py`): Shared numpy fallback + `cosine_similarity()` helper
- **Chunk content storage**: SQLite `chunks` table now has `content TEXT` column; chunk text retrievable without re-reading files
- **`get_chunk_content()`** and **`search_chunks()`** methods on StorageBackend
- **`save_state`/`load_state` aliases** on Stele (clearer naming alongside existing `save_kv_state`)
- **MCP SDK optional dependency**: `pip install stele-context[mcp]`
- **`stele-context-mcp` entry point**: Direct entry point for MCP stdio server
- **4 new test files**: `test_engine.py`, `test_session.py`, `test_mcp_stdio.py`, `test_storage_migration.py`

### Changed
- **Chunker routing in engine**: `index_documents()` now routes through CodeChunker for `.py`/`.js` etc., TextChunker for `.txt`/`.md`, instead of reimplementing paragraph splitting inline
- **HNSW index wired in**: Vector index populated on startup from SQLite, used for `search()`, `get_relevant_kv()`, and change detection
- **Unified Chunk class**: Single `Chunk` from `chunkers.base` with rich 128-dim semantic signatures (trigrams, word frequencies, structural features) — replaces the two incompatible Chunk classes
- **core.py is now a shim**: Re-exports `Stele` from `engine` and `Chunk` from `chunkers.base` for backward compat
- **JSON replaces pickle**: Session storage uses `json.dumps()` + `zlib.compress()` instead of pickle (security improvement for agent-facing tools)
- **Storage migration**: `_migrate_database()` adds `content` column via `ALTER TABLE ADD COLUMN` (preserves existing data)
- **Terminology**: Docstrings and module descriptions reframed as "context cache" not "KV-cache tensors"
- **Version**: 0.4.1 -> 0.5.0
- **Description**: "Local context cache for LLM agents with semantic chunking and vector search"
- **Keywords**: `kv-cache` -> `context-cache`, `vector-search`, `semantic-search`
- **HTTP MCP server**: Added `search` and `get_context` tools to HTTP API
- **CLI stats**: Now shows vector index statistics

### Security
- **Pickle removed**: KV-cache serialization uses JSON+zlib. Legacy pickle files still loadable during migration via restricted unpickler.

### Tests
- **88 tests passing** (was 49), 1 skipped (MCP SDK not installed)
- New test coverage: engine routing, HNSW integration, search API, content storage, schema migration, JSON serialization, session manager

## [0.4.1] - 2026-03-13

### Fixed
- **Optional dependency detection** - `HAS_IMAGE_CHUNKER`, `HAS_PDF_CHUNKER`, `HAS_AUDIO_CHUNKER`, `HAS_VIDEO_CHUNKER` flags now correctly check whether the underlying library (Pillow, pymupdf, librosa, opencv) is installed, not just whether the chunker module imported. Previously, `Stele()` would crash with `ImportError` when optional deps were missing.
- **msgspec guard in storage** - `store_kv_state()` and `load_kv_state()` now check `HAS_MSGSPEC` before calling `msgspec` methods. Previously crashed with `AttributeError` when msgspec was not installed.
- **Test version assertion** - `test_get_stats` expected version `"0.1.0"` instead of `"0.4.0"`.
- **README line count** - Updated codebase size from ~2,000 to ~4,800 lines.

### Removed
- **Dead chunk subclasses** - Removed `TextChunk`, `PDFChunk`, `ImageChunk`, `AudioChunk`, `VideoChunk` classes (never imported or instantiated anywhere).
- **Unused methods** - Removed `Stele.get_chunker()` and `BaseChunker.read_file()` (defined but never called).
- **Unused imports** - Removed `import io` from video.py, unused `Optional`, `Counter`, `Tuple` type imports across chunker modules.

### Changed
- **Deduplicated paragraph chunking** - Merged `TextChunker._chunk_paragraphs()` and `_chunk_adaptive()` (90%+ identical) into a single `_chunk_by_paragraphs(adaptive)` method.
- **Extracted cosine similarity helper** - Duplicated cosine similarity computation in `detect_changes_and_update()` and `get_relevant_kv()` extracted to shared `_cosine_similarity()` function.

## [0.4.0] - 2026-03-12

### Added
- **Vector index** (HNSW) for fast approximate nearest neighbor search
  - Pure Python implementation with zero dependencies
  - O(log n) similarity search instead of O(n) scan
  - Configurable M, ef_construction, ef_search parameters
- **VectorIndex** high-level wrapper for chunk-specific functionality
- **Enhanced semantic signatures** with better feature extraction
  - TF-IDF weighting for term importance
  - Structural features (code density, comment ratio, etc.)
  - Normalized unit vectors for consistent similarity
- **Compression for KV-cache files** using zlib (stdlib)
  - 50-80% space savings on typical KV data
  - Transparent compression/decompression
  - Configurable compression level
- **Chunk versioning and history**
  - Track changes to chunks over time
  - Rollback to any previous version
  - Version metadata (timestamp, content hash)
- **Smarter chunking**
  - Adaptive chunk sizing based on content density
  - Sliding window option for overlapping chunks
  - Code-aware chunking with AST parsing for Python
- **Dataclass refactoring**
  - Consistent use of dataclasses throughout
  - Type hints for all public APIs
  - Immutable where appropriate

### Changed
- Storage backend now supports compression
- Chunk metadata includes version information
- Similarity search uses vector index for performance
- Semantic signatures are more discriminative

### Performance
- Similarity search: O(n) → O(log n) with HNSW index
- Storage: 50-80% reduction with compression
- Chunking: Adaptive sizing reduces chunk count by 20-30%

## [0.3.0] - 2026-03-12

### Added
- **Multi-modal support** with modular chunker architecture
- **ImageChunker**: Image indexing with perceptual hashing and color histograms (requires Pillow)
- **PDFChunker**: PDF text extraction by page with metadata (requires pymupdf)
- **AudioChunker**: Audio segmentation with MFCC and spectral features (requires librosa)
- **VideoChunker**: Video keyframe extraction with frame hashing (requires opencv-python)
- **CodeChunker**: Code-aware chunking with AST parsing for Python and regex for other languages
- **TextChunker**: Refactored text chunker with enhanced semantic signatures
- **BaseChunker**: Abstract base class for all chunkers
- New MCP tools: `detect_modality()` and `get_supported_formats()`
- Optional dependency extras: `[image]`, `[pdf]`, `[audio]`, `[video]`, `[all]`
- Lazy imports for optional dependencies (graceful fallback if not installed)

### Changed
- Core Stele Context class now initializes and manages multiple chunkers
- `index_documents()` automatically selects appropriate chunker based on file type
- `detect_modality()` method to identify file type
- README updated with multi-modal documentation and supported formats

### Security
- All optional dependencies are 100% offline (no network access)
- Zero required dependencies maintained for core functionality

## [0.2.0] - 2026-03-12

### Added
- Comprehensive test suite (14 passing tests)
- GitHub Actions CI/CD (test on Python 3.9-3.12)
- GitHub Actions publish workflow (PyPI)
- Issue templates (bug report, feature request, question)
- Pull request template
- CONTRIBUTING.md with detailed guidelines
- CHANGELOG.md
- pytest.ini configuration
- V0.2 roadmap and release checklist

### Changed
- Made msgspec and numpy optional dependencies (zero required dependencies)
- Added `[performance]` extra for optional msgspec/numpy
- Added mypy and ruff to dev dependencies
- README updated with security section and badges

## [0.1.0] - 2026-03-12

### Added
- Initial release of Stele Context
- Dynamic semantic chunking with intelligent merging
- Hybrid indexing (SHA-256 hashes + semantic signatures)
- Change detection with lazy double-check
- Persistent KV-cache storage (SQLite + filesystem)
- Session management with full rollback support
- Automatic pruning of low-relevance chunks
- Built-in MCP server (localhost:9876)
- CLI interface with commands: serve, index, detect, stats, clear
- Pure-Python fallbacks for numpy and msgspec
- Comprehensive documentation and examples

### Features
- **Chunking**: Splits documents into ~256-token chunks, merges similar chunks up to 4096 tokens
- **Indexing**: SHA-256 content hashes + 128-dimensional semantic signatures
- **Change Detection**: Three-tier detection (hash → semantic similarity → reprocess)
- **KV Persistence**: SQLite metadata + filesystem blob storage
- **Sessions**: Independent sessions with rollback to any previous turn
- **Pruning**: Remove low-relevance chunks to stay under token limits
- **MCP Server**: HTTP/JSON server with 6 discoverable tools
- **CLI**: Full command-line interface for all operations

### Technical Details
- 100% offline and local-only operation
- Minimal dependencies: msgspec (optional), numpy (optional)
- CPU-only, runs on standard laptop hardware
- Type hints throughout
- Comprehensive error handling
- Well-documented with extensive comments

### Supported Operations
- `index_documents(paths)` - Index documents with semantic chunking
- `detect_changes_and_update(session_id)` - Detect changes and update KV-cache
- `get_relevant_kv(session_id, query)` - Get KV for relevant chunks
- `save_kv_state(session_id, kv_data)` - Save KV state for rollback
- `rollback(session_id, target_turn)` - Rollback to previous turn
- `prune_chunks(session_id, max_tokens)` - Prune low-relevance chunks

### Storage
- Location: `~/.stele-context/` (configurable)
- Database: SQLite with WAL mode
- KV-cache: Serialized tensors in session directories
- Metadata: Chunks, documents, sessions, session-chunks

### Performance
- Instant KV restoration for unchanged documents (100% token savings)
- Lazy double-check for minor edits (90%+ token savings)
- Selective reprocessing for significant changes
- O(n) chunk similarity search (will be improved in v0.2.0)

### Known Limitations
- No vector index (O(n) similarity search)
- Paragraph-based chunking only (no code-aware splitting)
- No compression for KV-cache files
- No chunk versioning/history
- Basic semantic signatures (TF-style features)

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 1.0.3 | 2026-03-27 | Index health + alerts, search `keyword` mode, hybrid tuning, symbol diagnostics, CI search-regression |
| 0.8.0 | 2026-03-16 | Multi-agent support: RWLock, threaded HTTP, document ownership, optimistic locking, conflict resolution |
| 0.7.0 | 2026-03-16 | Symbol graph, cross-file references, directory indexing, staleness detection, search-with-edges |
| 0.6.0 | 2026-03-15 | Hybrid search, BPE tokens, adaptive HNSW, per-modality thresholds, binary handling |
| 0.5.4 | 2026-03-13 | Codebase audit: bug fixes, dead code removal, deduplication, dynamic versioning |
| 0.5.3 | 2026-03-13 | Better signatures (bigrams, positional features), regex tokenizer, HNSW performance (array.array, cached norms) |
| 0.5.2 | 2026-03-13 | Persistent HNSW index serialization — skip rebuild on startup |
| 0.5.1 | 2026-03-13 | Codebase audit: bug fixes, dead code removal, deduplication, engine delegates to SessionManager |
| 0.5.0 | 2026-03-13 | Context cache overhaul: unified chunks, HNSW wired in, search API, real MCP, JSON storage |
| 0.4.1 | 2026-03-13 | Bug fixes, dead code removal, code simplification |
| 0.4.0 | 2026-03-12 | Vector index, compression, adaptive chunking |
| 0.3.0 | 2026-03-12 | Multi-modal support |
| 0.2.0 | 2026-03-12 | Test suite, CI/CD, optional dependencies |
| 0.1.0 | 2026-03-12 | Initial release |

---

## Upgrade Guide

### From 0.0.x to 0.1.0

This is the initial release, no upgrade needed.

---

## Deprecation Policy

- Features will be deprecated for at least one minor version before removal
- Deprecation warnings will be added to affected functions
- Migration guides will be provided for breaking changes

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for information on how to contribute to Stele Context.

---

## License

Stele Context is released under the MIT License. See [LICENSE](LICENSE) for details.
