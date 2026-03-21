# Stele - Complete Project Documentation

## File Table

| Path | Purpose | Internal Deps | Tests |
|------|---------|---------------|-------|
| `stele/__init__.py` | Package root, exports `__version__` | None | test_core.py |
| `stele/core.py` | Backward-compat re-exports (`Stele`, `Chunk`) | engine, chunkers.base | test_core.py |
| `stele/engine.py` | Main orchestrator, thin `Stele` facade class | indexing, search_engine, change_detection, engine_utils, config, rwlock, session, storage, symbol_graph | test_engine.py |
| `stele/engine_utils.py` | Path normalization, lock routing, env checks | coordination, env_checks | (via test_engine.py, test_worktree_safety.py) |
| `stele/indexing.py` | Document indexing: chunk, store, merge, expand | chunkers.base, chunkers.numpy_compat | test_engine.py |
| `stele/search_engine.py` | Hybrid search (HNSW+BM25), get_context, stats | bm25, index, index_store, chunkers | test_engine.py |
| `stele/change_detection.py` | Detect file changes, re-index modified chunks | chunkers.base, chunkers.numpy_compat | test_engine.py |
| `stele/config.py` | `.stele.toml` loader with minimal TOML parser | None | test_config.py |
| `stele/storage.py` | `StorageBackend` - SQLite + filesystem persistence | storage_schema, storage_delegates, all sub-storages | test_engine.py, test_storage_migration.py |
| `stele/storage_schema.py` | Database init and migration SQL | None | test_storage_migration.py |
| `stele/storage_delegates.py` | `StorageDelegatesMixin` - forwarding methods | None (mixin) | (via test_engine.py) |
| `stele/session_storage.py` | Session table operations, KV-cache | None | test_session.py |
| `stele/metadata_storage.py` | Annotations and change history tables | None | test_metadata.py |
| `stele/symbol_storage.py` | Symbols and symbol edges tables | None | test_symbol_storage.py |
| `stele/document_lock_storage.py` | Per-worktree document locks and conflicts | None | test_conflicts.py |
| `stele/session.py` | `SessionManager` - session lifecycle | storage | test_session.py |
| `stele/index.py` | `HNSWIndex` - pure-Python HNSW vector index | chunkers.numpy_compat | test_index.py |
| `stele/index_store.py` | HNSW/BM25 persistence (JSON+zlib) | None | test_index_store.py |
| `stele/bm25.py` | `BM25Index` - keyword scoring | None | test_bm25.py |
| `stele/rwlock.py` | Read-write lock for thread safety | None | test_concurrency.py |
| `stele/symbols.py` | `SymbolExtractor` - dispatcher + Python AST | symbol_patterns | test_symbols.py |
| `stele/symbol_patterns.py` | `Symbol` dataclass + 10 language regex extractors | None | test_symbols.py |
| `stele/symbol_graph.py` | `SymbolGraphManager` - edges, staleness, queries | symbols, storage | test_symbols.py |
| `stele/coordination.py` | `CoordinationBackend` - cross-worktree shared DB | agent_registry | test_worktree_safety.py |
| `stele/agent_registry.py` | Agent registration, heartbeat, reaping | None | test_worktree_safety.py |
| `stele/env_checks.py` | Stale bytecache + editable install detection | None | test_env_checks.py |
| `stele/chunkers/__init__.py` | Chunker registry, auto-detection | all chunkers | test_chunkers.py |
| `stele/chunkers/base.py` | `Chunk` dataclass, `BaseChunker` ABC, `estimate_tokens()` | None | test_base_chunker.py |
| `stele/chunkers/numpy_compat.py` | Pure-Python `sig_to_bytes`, `cosine_similarity` | None | test_numpy_compat.py |
| `stele/chunkers/text.py` | `TextChunker` - sentence/paragraph splitting | chunkers.base | test_chunkers.py |
| `stele/chunkers/code.py` | `CodeChunker` - AST/tree-sitter/regex chunking | chunkers.base, chunkers.code_patterns | test_chunkers.py, test_tree_sitter.py |
| `stele/chunkers/code_patterns.py` | Tree-sitter node types, regex patterns per language | None | (via test_chunkers.py) |
| `stele/chunkers/image.py` | `ImageChunker` - Pillow-based (optional) | chunkers.base | (requires Pillow) |
| `stele/chunkers/pdf.py` | `PDFChunker` - pymupdf-based (optional) | chunkers.base | (requires pymupdf) |
| `stele/chunkers/audio.py` | `AudioChunker` - librosa-based (optional) | chunkers.base | (requires librosa) |
| `stele/chunkers/video.py` | `VideoChunker` - OpenCV-based (optional) | chunkers.base | (requires opencv) |
| `stele/cli.py` | CLI entry point (`stele` command) | engine | (manual testing) |
| `stele/cli_metadata.py` | CLI metadata/annotation subcommands | engine | (manual testing) |
| `stele/mcp_server.py` | HTTP REST server (30 tools, threaded) | mcp_handlers, mcp_schemas | test_mcp_server.py |
| `stele/mcp_handlers.py` | HTTP tool dispatch and agent_id injection | mcp_schemas | test_mcp_server.py |
| `stele/mcp_schemas.py` | HTTP tool schema definitions (pure data) | None | (via test_mcp_server.py) |
| `stele/mcp_stdio.py` | MCP stdio server (JSON-RPC for Claude Desktop) | mcp_tool_defs | test_mcp_stdio.py |
| `stele/mcp_tool_defs.py` | MCP stdio tool definitions (core, 15 tools) | mcp_tool_defs_ext | (via test_mcp_stdio.py) |
| `stele/mcp_tool_defs_ext.py` | MCP stdio tool definitions (extended, 20 tools) | None | (via test_mcp_stdio.py) |

## Test Files

| Path | Covers | Count |
|------|--------|-------|
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

**Total: 569 tests (1 skipped without MCP SDK)**
