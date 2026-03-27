# Stele Context - Complete Project Documentation

**Last updated:** 2026-03-27 · **Release:** v1.0.7

## Documentation (root)

| Path | Purpose |
|------|---------|
| `AGENTS.md` | Short agent entry: orientation tools, session vs index, trust, Tier 2 vs chunk notes |
| `docs/philosophy.md` | Design philosophy: zero deps, Tier 1 vs Tier 2, LLM-as-embedder, cross-session retrieval, comparison by design |
| `docs/agent-workflow.md` | Agent-oriented workflow: index → enrich → retrieve, tool choice, Tier 2 APIs, sessions |
| `stele_context/agent_response.py` | Token-bounded search/map/stats helpers, `project_brief` builder, chunk content trim |

## File Table

| Path | Purpose | Internal Deps | Tests |
|------|---------|---------------|-------|
| `stele_context/__init__.py` | Package root, exports `__version__`, `Stele`, `Chunk` | None | test_core.py |
| `stele_context/core.py` | Backward-compat re-exports (`Stele`, `Chunk`) | engine, chunkers.base | test_core.py |
| `stele_context/engine.py` | Main orchestrator, thin `Stele` facade class | indexing, search_engine, change_detection, engine_utils, config, rwlock, session, storage, symbol_graph | test_engine.py |
| `stele_context/engine_utils.py` | Path normalization, lock routing, env checks | coordination, env_checks | (via test_engine.py, test_worktree_safety.py) |
| `stele_context/indexing.py` | Document indexing: chunk, store, merge, expand | chunkers.base, chunkers.numpy_compat | test_engine.py |
| `stele_context/search_engine.py` | Hybrid search (HNSW+BM25, weak-cosine BM25 fallback, path_prefix), get_context, map/stats, project_brief, search bounds | bm25, index, index_store, agent_response, chunkers | test_engine.py, test_search_engine.py |
| `stele_context/index_health.py` | `compute_index_health_snapshot()` — alerts, staleness for map/stats | None | test_index_health.py |
| `stele_context/change_detection.py` | Detect file changes, re-index modified chunks | chunkers.base, chunkers.numpy_compat | test_engine.py |
| `stele_context/config.py` | `.stele-context.toml` loader with minimal TOML parser | None | test_config.py |
| `stele_context/storage.py` | `StorageBackend` - SQLite + filesystem persistence | storage_schema, storage_delegates, all sub-storages | test_engine.py, test_storage_migration.py |
| `stele_context/storage_schema.py` | Database init and migration SQL | connection_pool | test_storage_migration.py |
| `stele_context/connection_pool.py` | Thread-local SQLite connection reuse | None | (via test_engine.py) |
| `stele_context/storage_delegates.py` | `StorageDelegatesMixin` - forwarding methods | None (mixin) | (via test_engine.py) |
| `stele_context/session_storage.py` | Session table operations, KV-cache | None | test_session.py |
| `stele_context/metadata_storage.py` | Annotations and change history tables | None | test_metadata.py |
| `stele_context/symbol_storage.py` | Symbols and symbol edges tables | None | test_symbol_storage.py |
| `stele_context/lock_ops.py` | Shared lock primitives (refresh, conflict, reap) | None | test_conflicts.py, test_worktree_safety.py |
| `stele_context/document_lock_storage.py` | Per-worktree document locks and conflicts | lock_ops | test_conflicts.py |
| `stele_context/session.py` | `SessionManager` - session lifecycle | storage | test_session.py |
| `stele_context/index.py` | `HNSWIndex` - pure-Python HNSW vector index | chunkers.numpy_compat | test_index.py |
| `stele_context/index_store.py` | HNSW/BM25 persistence (JSON+zlib) | None | test_index_store.py |
| `stele_context/bm25.py` | `BM25Index` - keyword scoring | None | test_bm25.py |
| `stele_context/rwlock.py` | Read-write lock for thread safety | None | test_concurrency.py |
| `stele_context/symbols.py` | `SymbolExtractor` - dispatcher + Python AST | symbol_patterns | test_symbols.py |
| `stele_context/symbol_patterns.py` | `Symbol` dataclass + 10 language regex extractors | None | test_symbols.py |
| `stele_context/symbol_graph.py` | `SymbolGraphManager` - edges, staleness, queries, `impact_radius` summary_mode | symbols, storage | test_symbols.py |
| `stele_context/coordination.py` | `CoordinationBackend` - cross-worktree shared DB | agent_registry, change_notifications, lock_ops | test_worktree_safety.py |
| `stele_context/change_notifications.py` | Change notification storage for coordination DB | None | test_worktree_safety.py |
| `stele_context/agent_registry.py` | Agent registration, heartbeat, reaping | None | test_worktree_safety.py |
| `stele_context/env_checks.py` | Stale bytecache + editable install detection | None | test_env_checks.py |
| `stele_context/protocols.py` | Typing protocols for delegation boundaries | None | (static analysis only) |
| `stele_context/stemmer.py` | Pure-Python Porter stemmer, identifier splitting | None | test_stemmer.py |
| `stele_context/chunkers/__init__.py` | Chunker registry, auto-detection | all chunkers | test_chunkers.py |
| `stele_context/chunkers/base.py` | `Chunk` dataclass, `BaseChunker` ABC, `estimate_tokens()` | None | test_base_chunker.py |
| `stele_context/chunkers/numpy_compat.py` | Pure-Python `sig_to_bytes`, `cosine_similarity` | None | test_numpy_compat.py |
| `stele_context/chunkers/text.py` | `TextChunker` - sentence/paragraph splitting | chunkers.base | test_chunkers.py |
| `stele_context/chunkers/code.py` | `CodeChunker` - AST/tree-sitter/regex chunking | chunkers.base, chunkers.code_patterns | test_chunkers.py, test_tree_sitter.py |
| `stele_context/chunkers/code_patterns.py` | Tree-sitter node types, regex patterns per language | None | (via test_chunkers.py) |
| `stele_context/chunkers/image.py` | `ImageChunker` - Pillow-based (optional) | chunkers.base | (requires Pillow) |
| `stele_context/chunkers/pdf.py` | `PDFChunker` - pymupdf-based (optional) | chunkers.base | (requires pymupdf) |
| `stele_context/chunkers/audio.py` | `AudioChunker` - librosa-based (optional) | chunkers.base | (requires librosa) |
| `stele_context/chunkers/video.py` | `VideoChunker` - OpenCV-based (optional) | chunkers.base | (requires opencv) |
| `stele_context/cli.py` | CLI entry point (`stele-context` command) | engine | (manual testing) |
| `stele_context/cli_metadata.py` | CLI metadata/annotation subcommands | engine | (manual testing) |
| `stele_context/mcp_server.py` | HTTP REST server (unified tool registry, threaded) + tool dispatch | tool_registry | test_mcp_server.py |
| `stele_context/mcp_handlers.py` | Backward-compat shim (re-exports from mcp_server) | mcp_server, tool_registry | test_mcp_server.py |
| `stele_context/tool_registry.py` | Unified tool dispatch, WRITE_TOOLS, HTTP schemas, modality flags | mcp_tool_defs | (via test_mcp_server.py, test_mcp_stdio.py) |
| `stele_context/mcp_stdio.py` | MCP stdio server (JSON-RPC for Claude Desktop) | mcp_tool_defs, tool_registry | test_mcp_stdio.py |
| `stele_context/mcp_tool_defs.py` | MCP tool definitions (core; combined with ext = 53 tools) | mcp_tool_defs_ext | (via test_mcp_stdio.py) |
| `stele_context/mcp_tool_defs_ext.py` | MCP tool definitions (extended) | None | (via test_mcp_stdio.py) |

## Test Files

| Path | Covers | Count |
|------|--------|-------|
| `tests/conftest.py` | Shared fixtures: `stele_engine`, `stele_engine_with_file`, `stele_engine_with_data` | — |
| `tests/test_core.py` | core.py re-exports | ~5 |
| `tests/test_engine.py` | Engine integration | ~80 |
| `tests/test_base_chunker.py` | Chunk dataclass, BaseChunker, estimate_tokens | ~64 |
| `tests/test_numpy_compat.py` | sig_to_bytes, cosine_similarity round-trips | ~29 |
| `tests/test_chunkers.py` | Text and code chunking | ~30 |
| `tests/test_tree_sitter.py` | Tree-sitter code chunking (optional) | ~10 |
| `tests/test_bm25.py` | BM25 indexing and scoring | ~15 |
| `tests/test_index.py` | HNSW insert, search, delete | ~20 |
| `tests/test_index_store.py` | Index persistence | ~10 |
| `tests/test_session.py` | Session lifecycle, rollback | ~15 |
| `tests/test_metadata.py` | Annotations, change history | ~20 |
| `tests/test_symbols.py` | Symbol extraction, graph, staleness | ~30 |
| `tests/test_symbol_storage.py` | Symbol and edge CRUD | ~32 |
| `tests/test_config.py` | TOML parsing, config merging | ~15 |
| `tests/test_conflicts.py` | Document locking, conflicts | ~20 |
| `tests/test_concurrency.py` | RWLock, thread safety | ~15 |
| `tests/test_worktree_safety.py` | Path normalization, coordination, agents | ~60 |
| `tests/test_mcp_server.py` | HTTP server tools | ~19 |
| `tests/test_mcp_stdio.py` | MCP stdio tools | ~18 |
| `tests/test_storage_migration.py` | Schema migrations | ~8 |
| `tests/test_agent_embeddings.py` | Agent-supplied embeddings | ~15 |
| `tests/test_chunk_history.py` | Chunk version history | ~10 |
| `tests/test_env_checks.py` | Pycache scanning, editable installs | ~32 |

| `tests/test_stemmer.py` | Porter stemmer: stem(), split_identifier() | ~25 |
| `tests/test_cli.py` | CLI commands, argument parsing, JSON output | ~30 |
| `tests/test_search_engine.py` | Search alpha tuning, identifier extraction, signatures | ~30 |
| `tests/test_index_health.py` | Index health snapshot, alerts | ~4 |
| `tests/test_search_regression.py` | Keyword/hybrid regression (`@search_regression`) | ~4 |
| `tests/test_agent_response.py` | Token bounds, compact map, project_brief helper | ~4 |
| `tests/test_connection_pool.py` | Thread-local pool, connect() context manager, search_text edges | ~40 |
| `tests/test_media_chunkers.py` | Media chunker extensions, HAS_* flags, modality detection | ~30 |

**Total: 862+ tests (1 skipped without MCP SDK)**
