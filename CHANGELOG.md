# Changelog

All notable changes to ChunkForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Hybrid search (BM25 + HNSW)** — `search()` now widens HNSW to 3x candidates and re-ranks with BM25 keyword scores. Blend controlled by `search_alpha` (default 0.7). BM25 index lazily initialized on first search, maintained incrementally.
- **`chunkforge/bm25.py`** — Pure-Python Okapi BM25 keyword index with zero dependencies
- **Per-modality thresholds** — `MODALITY_THRESHOLDS` dict: code uses merge=0.85 (preserves AST boundaries) and change=0.80 (tolerates incremental edits); text and PDF keep existing defaults
- **Signature cache for incremental indexing** — On re-index, unchanged chunks reuse cached semantic signatures instead of recomputing. Applied in both `index_documents()` and `detect_changes_and_update()`
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
- **Token estimation consistency** — Replaced all 5 instances of `len(text) // 4` in `text.py` and `code.py` with `estimate_tokens()` regex tokenizer. Incremental tracking in hot loops avoids O(n^2).

### Fixed
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
- **Persistent HNSW index serialization** — index saved to `~/.chunkforge/indices/hnsw_index.json.zlib` after indexing or change detection. Loaded on startup if fresh (chunk IDs hash matches), otherwise rebuilt from SQLite. Eliminates redundant O(n) rebuild on every startup.
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
- **New engine module** (`engine.py`): Main ChunkForge class now routes documents through modality-specific chunkers and wires in the HNSW vector index
- **Semantic search API** (`search(query, top_k)`): Returns chunk content + metadata ranked by HNSW similarity
- **Context cache API** (`get_context(document_paths)`): Returns cached chunk content for unchanged files, flags changed/new docs
- **Real MCP server** (`mcp_stdio.py`): JSON-RPC over stdio, compatible with Claude Desktop and MCP clients
- **`serve-mcp` CLI command**: Start stdio MCP server
- **`search` CLI command**: `chunkforge search "query" --top-k 10`
- **SessionManager** (`session.py`): High-level session operations with HNSW-accelerated retrieval
- **SessionStorage** (`session_storage.py`): Extracted session operations from storage.py
- **numpy_compat module** (`chunkers/numpy_compat.py`): Shared numpy fallback + `cosine_similarity()` helper
- **Chunk content storage**: SQLite `chunks` table now has `content TEXT` column; chunk text retrievable without re-reading files
- **`get_chunk_content()`** and **`search_chunks()`** methods on StorageBackend
- **`save_state`/`load_state` aliases** on ChunkForge (clearer naming alongside existing `save_kv_state`)
- **MCP SDK optional dependency**: `pip install chunkforge[mcp]`
- **`chunkforge-mcp` entry point**: Direct entry point for MCP stdio server
- **4 new test files**: `test_engine.py`, `test_session.py`, `test_mcp_stdio.py`, `test_storage_migration.py`

### Changed
- **Chunker routing in engine**: `index_documents()` now routes through CodeChunker for `.py`/`.js` etc., TextChunker for `.txt`/`.md`, instead of reimplementing paragraph splitting inline
- **HNSW index wired in**: Vector index populated on startup from SQLite, used for `search()`, `get_relevant_kv()`, and change detection
- **Unified Chunk class**: Single `Chunk` from `chunkers.base` with rich 128-dim semantic signatures (trigrams, word frequencies, structural features) — replaces the two incompatible Chunk classes
- **core.py is now a shim**: Re-exports `ChunkForge` from `engine` and `Chunk` from `chunkers.base` for backward compat
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
- **Optional dependency detection** - `HAS_IMAGE_CHUNKER`, `HAS_PDF_CHUNKER`, `HAS_AUDIO_CHUNKER`, `HAS_VIDEO_CHUNKER` flags now correctly check whether the underlying library (Pillow, pymupdf, librosa, opencv) is installed, not just whether the chunker module imported. Previously, `ChunkForge()` would crash with `ImportError` when optional deps were missing.
- **msgspec guard in storage** - `store_kv_state()` and `load_kv_state()` now check `HAS_MSGSPEC` before calling `msgspec` methods. Previously crashed with `AttributeError` when msgspec was not installed.
- **Test version assertion** - `test_get_stats` expected version `"0.1.0"` instead of `"0.4.0"`.
- **README line count** - Updated codebase size from ~2,000 to ~4,800 lines.

### Removed
- **Dead chunk subclasses** - Removed `TextChunk`, `PDFChunk`, `ImageChunk`, `AudioChunk`, `VideoChunk` classes (never imported or instantiated anywhere).
- **Unused methods** - Removed `ChunkForge.get_chunker()` and `BaseChunker.read_file()` (defined but never called).
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
- Core ChunkForge class now initializes and manages multiple chunkers
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
- Initial release of ChunkForge
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
- Location: `~/.chunkforge/` (configurable)
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

See [CONTRIBUTING.md](CONTRIBUTING.md) for information on how to contribute to ChunkForge.

---

## License

ChunkForge is released under the MIT License. See [LICENSE](LICENSE) for details.
