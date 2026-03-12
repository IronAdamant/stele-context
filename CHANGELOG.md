# Changelog

All notable changes to ChunkForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned for v0.2.0
- Vector index for fast similarity search (HNSW)
- Code-aware chunking (Python AST, JavaScript/TypeScript)
- Compression for KV-cache files (zstd)
- Chunk versioning and history
- Advanced semantic signatures (TF-IDF, local embeddings)
- Multi-document sessions
- Selective KV loading by query

### Planned for v0.1.x
- Comprehensive test suite (>90% coverage)
- Performance benchmarks
- Configuration file support (.chunkforge.toml)
- Bug fixes for edge cases (empty files, binary files, large files)
- Improved error handling and validation

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
