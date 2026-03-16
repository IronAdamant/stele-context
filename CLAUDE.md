# ChunkForge

Local context cache for LLM agents. 100% offline, zero required dependencies.

## Architecture

```
ChunkForge (engine.py) -- main orchestrator
  |-- Chunkers (text, code, image, pdf, audio, video)
  |     \-- BaseChunker ABC + Chunk dataclass (chunkers/base.py)
  |-- VectorIndex (HNSW, index.py) + BM25Index (bm25.py)
  |-- IndexStore (index_store.py) -- persistent index serialization
  |-- StorageBackend (storage.py, SQLite + filesystem)
  |     |-- SessionStorage (session_storage.py)
  |     |-- MetadataStorage (metadata_storage.py)
  |     \-- SymbolStorage (symbol_storage.py)
  |-- SessionManager (session.py)
  \-- SymbolExtractor (symbols.py) -- 12 language families

APIs:
  |-- CLI (cli.py + cli_metadata.py)
  |-- HTTP REST (mcp_server.py, 15 tools)
  \-- MCP stdio (mcp_stdio.py, 23 tools, JSON-RPC for Claude Desktop)

Backward compat: core.py re-exports ChunkForge + Chunk
```

## Key Design Decisions

- **Single Chunk class**: `chunkers/base.py:Chunk` is the only Chunk dataclass. `core.py` re-exports it.
- **Engine delegates session ops** to `SessionManager` (no duplicated logic).
- **Storage delegates** to 3 specialized classes: `SessionStorage`, `MetadataStorage`, `SymbolStorage`. Each owns its own SQL tables.
- **JSON only, no pickle**: Session data serialized with JSON+zlib for agent safety.
- **Zero required deps**: Core uses only stdlib. numpy/msgspec have pure-Python fallbacks in `chunkers/numpy_compat.py`.
- **`_read_and_hash(path, modality)`**: Module-level helper in `engine.py` for file reading + SHA-256. Used by `index_documents()`, `detect_changes_and_update()`, `get_context()`.
- **Hybrid search**: HNSW finds 3x candidates, BM25 re-ranks. `alpha * cosine + (1-alpha) * bm25`. Auto-tuned alpha lowers for code-like queries.
- **Per-modality thresholds**: Code merge=0.85 (preserve AST), change=0.80 (tolerate edits). Text merge=0.70, change=0.85.
- **AST-boundary merge guard**: Code chunks starting with `def`/`class`/`function` are never merged with preceding chunk.
- **Signature cache**: On re-index, `content_hash -> semantic_signature` lookup skips recomputation for unchanged chunks.
- **128-dim semantic signatures**: trigrams (0-63), word unigrams (64-79), bigrams (80-95), structural (96-103), positional (104-115), reserved (116-127). Normalized to unit vectors.
- **Token estimation**: `estimate_tokens()` in `chunkers/base.py` uses BPE merge-corrected regex (~95% accuracy).
- **HNSW persistence**: `indices/hnsw_index.json.zlib`. Staleness via SHA-256 of sorted chunk IDs. FORMAT_VERSION for forward compat.
- **BM25 persistence**: Same pattern alongside HNSW. Lazy-loaded on first search.
- **Symbol graph**: Python uses `ast.walk()`, all others use regex. Edges cleared and rebuilt after batch indexing (O(symbols), <1s for ~30K symbols).
- **Cross-language linking**: CSS-prefixed names (`.classname`, `#id`) avoid collisions. HTML attrs -> CSS selectors, JS DOM API -> CSS.
- **Staleness propagation**: BFS through symbol edges after changes. Score = `0.8^depth`. `stale_chunks(threshold)` queries it.
- **Directory indexing**: `_expand_paths()` walks dirs, filters by chunker extensions, skips `.git`/`node_modules`/`__pycache__`/hidden dirs. Configurable via `skip_dirs` param.
- **Module path resolution**: `resolve_symbols()` prefers definitions from imported module paths when multiple match.
- **Adaptive ef_search**: HNSW search width scales with index size (10 for <100, 4x for 10K+).
- **Dynamic versioning**: `__init__.__version__` is the single source. Engine, CLI, and tests all reference it.

## SQLite Tables

`chunks`, `chunk_history`, `documents` -- core storage
`sessions`, `session_chunks` -- session lifecycle (SessionStorage)
`annotations`, `change_history` -- metadata (MetadataStorage)
`symbols`, `symbol_edges` -- symbol graph (SymbolStorage)

## Module Boundaries

- `engine.py` is the only file that wires everything together. All other modules are standalone.
- `index.py` and `bm25.py` have zero internal dependencies.
- `numpy_compat.py` is the single source for `sig_to_bytes()`, `sig_from_bytes()`, `cosine_similarity()`.
- Chunker modules only import from `chunkers/base.py`.
- No circular imports exist in the dependency graph.

## Development

```bash
pip install -e ".[dev]"
pytest                    # 225 tests (224 pass, 1 skipped without mcp SDK)
mypy chunkforge/
ruff check chunkforge/
```

Entry points: `chunkforge` (CLI), `chunkforge-mcp` (MCP stdio server)
