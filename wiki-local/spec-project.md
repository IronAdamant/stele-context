# Project Specification

## Overview

Stele Context is a local context cache for LLM agents. It indexes documents through modality-specific chunkers, stores chunk data in SQLite, and provides O(log n) semantic search via an HNSW vector index. Designed for 100% offline use with zero required dependencies. **Default search is keyword/BM25**; hybrid (HNSW+BM25) is opt-in. `map`/`search` accept optional `path_prefix`; `impact_radius` supports `summary_mode`. As of **v1.5.x**: MCP **lite** default, `stele-context init`, `query` composite retrieval + `applied_defaults`, `enrichment_plan`, doctor guidance, working_tree auto-index. v1.4.x also: `diff_since_cache`, `.gitignore`-aware indexing, bounded history, stale egg-info detection (see CHANGELOG).

## Architecture

```
API Layer
  CLI (cli.py)              -- index / search / query / find-* / impact-radius / detect / serve-mcp
  HTTP REST (mcp_server.py) -- unified tool registry, threaded; envelope {success,result}
  MCP stdio (mcp_stdio.py)  -- bare JSON results; STELE_MCP_MODE=lite|standard|full

Engine Layer (engine.py facade + five mixins)
  engine_index_mixin.py  -- index, detect_changes, annotations, Tier-2 writes
  engine_search_mixin.py -- search, agent_grep, search_text, get_context, working_tree
  engine_symbol_mixin.py -- find_*, impact, coupling, dynamic symbols
  engine_info_mixin.py   -- map, doctor, enrichment_plan, stats
  engine_lock_mixin.py   -- document_lock, agents, notifications
  indexing.py / search_engine.py / change_detection.py / agent_grep.py
  agent_guidance.py / engine_utils.py (read_and_hash) / context_diff.py / gitignore.py

Storage Layer
  storage.py + storage_writer.py (WriterQueue) + storage_schema.py + connection_pool.py
  session_storage / metadata_storage / symbol_storage / document_lock_storage

Index Layer: index.py (HNSW), bm25.py, index_store.py, index_health.py
Chunkers: base, text, code (+ patterns), optional media
MCP: tool_registry, mcp_tools_primary, mcp_tools_symbols
```

## Key Constraints

- **Zero required dependencies**: Core uses only Python stdlib
- **100% offline**: No network calls, no cloud, no model downloads
- **Thread-safe**: RWLock protects all engine public methods
- **Soft ~500 LOC guideline**: some modules are **accepted residual** size (see COMPLETE D5 note)
- **Single Chunk class**: `chunkers/base.py:Chunk` is the only chunk dataclass
- **No circular imports**: Strict dependency DAG
- **Design ceilings**: static name graph only — no polymorphic lattice / synthetic dynamic edges (STABILITY)
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
