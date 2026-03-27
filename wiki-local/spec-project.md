# Project Specification

## Overview

Stele Context is a local context cache for LLM agents. It indexes documents through modality-specific chunkers, stores chunk data in SQLite, and provides O(log n) semantic search via an HNSW vector index. Designed for 100% offline use with zero required dependencies. As of v1.0.5, hybrid search includes weak-vector BM25 fallback; `map`/`search` accept optional `path_prefix`; `impact_radius` supports `summary_mode` for bounded blast-radius output (see CHANGELOG).

## Architecture

```
API Layer
  CLI (cli.py)              -- stele-context index / search / serve
  HTTP REST (mcp_server.py) -- unified tool registry, threaded
  MCP stdio (mcp_stdio.py)  -- unified tool registry, JSON-RPC

Engine Layer (engine.py - thin facade)
  indexing.py       -- document indexing, chunk merging
  search_engine.py  -- hybrid HNSW+BM25 search, context retrieval
  change_detection.py -- detect file changes, re-index
  engine_utils.py   -- path normalization, lock routing

Storage Layer
  storage.py        -- StorageBackend (SQLite + filesystem)
  storage_schema.py -- database init and migrations
  storage_delegates.py -- forwarding mixin for sub-storages
  session_storage.py   -- session tables
  metadata_storage.py  -- annotations, change history
  symbol_storage.py    -- symbols, edges
  document_lock_storage.py -- per-worktree locks, conflicts

Index Layer
  index.py       -- HNSWIndex (pure-Python)
  index_store.py -- HNSW/BM25 persistence
  bm25.py        -- BM25Index (keyword scoring)

Chunker Layer
  chunkers/base.py    -- Chunk dataclass, BaseChunker ABC
  chunkers/text.py    -- TextChunker
  chunkers/code.py    -- CodeChunker (AST + tree-sitter + regex)
  chunkers/image.py   -- ImageChunker (Pillow)
  chunkers/pdf.py     -- PDFChunker (pymupdf)
  chunkers/audio.py   -- AudioChunker (librosa)
  chunkers/video.py   -- VideoChunker (OpenCV)

API Layer
  tool_registry.py    -- unified tool dispatch, WRITE_TOOLS, schemas, modality flags
  mcp_tool_defs.py    -- MCP tool definitions (core)
  mcp_tool_defs_ext.py -- MCP tool definitions (extended)
  mcp_handlers.py     -- backward-compat shim

Agent docs (repo root)
  AGENTS.md            -- quick entry for LLM agents (doctor, trust, Tier 2)
  docs/philosophy.md   -- design: zero deps, Tier 1 vs Tier 2
  docs/agent-workflow.md -- index → enrich → retrieve

Cross-cutting
  agent_response.py    -- token-bounded search/map/stats, project_brief data
  config.py            -- .stele-context.toml loader
  rwlock.py            -- read-write lock
  coordination.py      -- cross-worktree coordination
  agent_registry.py    -- agent registration/heartbeat
  change_notifications.py -- change notification storage
  lock_ops.py          -- shared lock primitives
  symbols.py           -- SymbolExtractor (12 languages)
  symbol_patterns.py   -- Symbol dataclass + regex extractors
  symbol_graph.py      -- SymbolGraphManager
  stemmer.py           -- Porter stemmer, identifier splitting
  env_checks.py        -- stale bytecache, editable install detection
  protocols.py         -- typing protocols for delegation boundaries
  connection_pool.py   -- thread-local SQLite connection reuse
  core.py              -- backward-compat re-exports (Stele, Chunk)
```

## Key Constraints

- **Zero required dependencies**: Core uses only Python stdlib
- **100% offline**: No network calls, no cloud, no model downloads
- **Thread-safe**: RWLock protects all engine public methods
- **Max 500 code lines per file**: Enforced via project guidelines
- **Single Chunk class**: `chunkers/base.py:Chunk` is the only chunk dataclass
- **No circular imports**: Strict dependency DAG
- **JSON only**: No pickle for agent safety

## Data Flow

1. `index_documents(paths)` expands dirs, detects modality per file
2. Each file is read, hashed, and chunked by the appropriate chunker
3. Similar adjacent chunks are merged (respecting AST boundaries for code)
4. Chunks get 128-dim semantic signatures and are stored in SQLite
5. HNSW index is updated with chunk vectors
6. BM25 index is lazily built on first search
7. `search(query)` finds 3x candidates via HNSW, re-ranks with BM25
8. Symbol graph is rebuilt after batch indexing

## Entry Points

- `stele-context` CLI command -> `stele_context.cli:main`
- `stele-context-mcp` command -> `stele_context.mcp_stdio:main`
- Python API -> `from stele_context.engine import Stele`
- PyPI package -> `pip install stele-context`
