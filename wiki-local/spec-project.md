# Project Specification

## Overview

Stele is a local context cache for LLM agents. It indexes documents through modality-specific chunkers, stores chunk data in SQLite, and provides O(log n) semantic search via an HNSW vector index. Designed for 100% offline use with zero required dependencies.

## Architecture

```
API Layer
  CLI (cli.py)              -- stele index / search / serve
  HTTP REST (mcp_server.py) -- 30 tools, threaded
  MCP stdio (mcp_stdio.py)  -- 32 tools, JSON-RPC

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

Cross-cutting
  config.py        -- .stele.toml loader
  rwlock.py        -- read-write lock
  coordination.py  -- cross-worktree coordination
  agent_registry.py -- agent registration/heartbeat
  symbols.py       -- SymbolExtractor (12 languages)
  symbol_patterns.py -- Symbol dataclass + regex extractors
  symbol_graph.py  -- SymbolGraphManager
  env_checks.py    -- stale bytecache, editable install detection
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

- `stele` CLI command -> `stele.cli:main`
- `stele-mcp` command -> `stele.mcp_stdio:main`
- Python API -> `from stele.engine import Stele`
- PyPI package -> `pip install stele-context`
