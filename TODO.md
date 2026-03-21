# Stele TODO

## Completed (v0.9.0)

- ~~Tree-sitter for non-Python code chunking~~ — JS/TS, Java, C/C++, Go, Rust, Ruby, PHP
- ~~`.stele.toml` configuration system~~ — with minimal TOML parser fallback
- ~~Chunk history query tools~~ — `get_chunk_history()` exposed via MCP
- ~~Performance benchmarks~~ — `benchmarks/` with chunking, storage, search
- ~~CODE_OF_CONDUCT.md~~ — Contributor Covenant v2.0
- ~~Documentation polish~~ — Mermaid diagram, comparison table, FAQ, troubleshooting
- ~~Test coverage enforcement~~ — `--cov-fail-under=80` in CI

## Future (nice-to-have)

### Local sentence embeddings (advanced signatures)
- Small ONNX model for semantic embeddings
- Would be `[embeddings]` optional dependency
- Currently using 128-dim statistical signatures (trigrams, bigrams, structural)
- Useful for cross-language similarity and more precise change detection
