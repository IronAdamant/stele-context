# ChunkForge

**Local context cache for LLM agents with semantic chunking and vector search.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)](https://github.com/IronAdamant/ChunkForge)

ChunkForge helps LLM agents avoid re-reading unchanged files by caching chunk data with semantic search. Documents are routed through modality-specific chunkers, chunk content is stored in SQLite, and an HNSW vector index enables fast O(log n) retrieval. Only modified chunks trigger reprocessing.

## Key Features

- **100% Offline & Local-Only**: No internet access, no external API calls, no cloud components
- **Zero Required Dependencies**: Runs on Python stdlib alone—no supply chain risks
- **Multi-Modal Support**: Text, code, images, PDFs, audio, and video (optional dependencies)
- **HNSW Vector Index**: O(log n) semantic search across all indexed chunks
- **Semantic Search API**: `search(query)` returns chunk content ranked by relevance
- **Context Cache API**: `get_context(paths)` returns cached chunks for unchanged files
- **Modality-Specific Chunking**: Routes .py through AST-aware CodeChunker, .txt through TextChunker, etc.
- **Chunk Content Storage**: Chunk text persisted in SQLite — no need to re-read source files
- **Real MCP Server**: JSON-RPC over stdio (`serve-mcp`) for Claude Desktop integration
- **Dynamic Semantic Chunking**: ~256-token initial chunks, intelligently merged by semantic similarity
- **Adaptive Chunking**: Adjusts chunk size based on content density (code vs prose)
- **Hybrid Indexing**: SHA-256 content hashes + 128-dim semantic signatures
- **Change Detection**: Unchanged = instant cache hit; similar = lightweight double-check; different = reprocess
- **JSON Serialization**: KV-cache stored as JSON+zlib (no pickle, safe for agent-facing tools)
- **Session Management**: Sessions with rollback support and automatic pruning
- **Persistent Storage**: SQLite metadata + filesystem cache with full rollback support
- **HTTP REST Server**: `serve` command for HTTP API integration
- **Optional Performance**: `msgspec` and `numpy` for speed (with stdlib fallbacks)

## Installation

### From Source

```bash
# Clone the repository
git clone https://github.com/chunkforge/chunkforge.git
cd chunkforge

# Install in development mode
pip install -e .

# Or install with dev dependencies
pip install -e ".[dev]"
```

### Requirements

- Python 3.9+
- **Zero required dependencies**

Optional (all 100% offline, no network):

| Extra | Packages | Use Case |
|-------|----------|----------|
| `performance` | msgspec, numpy | Faster serialization & vector math |
| `image` | Pillow | Image indexing & similarity |
| `pdf` | pymupdf | PDF text extraction |
| `audio` | librosa, numpy | Audio segmentation & features |
| `video` | opencv-python, numpy | Video keyframe extraction |
| `mcp` | mcp | Real MCP server (stdio transport) |
| `all` | All of the above | Everything |

```bash
# Install with specific modalities
pip install chunkforge[image,pdf]
pip install chunkforge[all]
```

All features work with Python standard library alone (text/code).

## Security & Supply Chain

ChunkForge is designed with security in mind:

- **Zero required dependencies** - No supply chain attack surface for core functionality
- **No model downloads** - Semantic signatures use simple TF-style features, not ML models
- **No API calls** - Everything runs locally, no data leaves your machine
- **Optional deps are safe** - `msgspec` and `numpy` are pure computation libraries with no network access
- **No pickle** - Session data serialized with JSON+zlib, safe for agent-facing tools
- **Minimal codebase** - ~5,000 lines of Python, easy to audit

For maximum security:
```bash
# Install with zero dependencies
pip install chunkforge --no-deps
```

## Supported Formats

### Text & Code (Zero Dependencies)
- `.txt`, `.md`, `.rst`, `.csv`, `.log`
- `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.cpp`, `.c`, `.h`
- `.go`, `.rs`, `.rb`, `.php`, `.swift`, `.sh`, `.bash`
- `.json`, `.yaml`, `.yml`, `.toml`, `.xml`, `.html`, `.css`, `.sql`

### Images (requires Pillow)
- `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.bmp`, `.tiff`, `.ico`

### PDFs (requires pymupdf)
- `.pdf`

### Audio (requires librosa)
- `.mp3`, `.wav`, `.ogg`, `.flac`, `.m4a`, `.aac`, `.wma`

### Video (requires opencv-python)
- `.mp4`, `.avi`, `.mov`, `.mkv`, `.webm`, `.flv`, `.wmv`

## Performance

### Similarity Search
- **v0.3.0**: O(n) linear scan
- **v0.4.0**: O(log n) with HNSW vector index
- **Speedup**: 10-100x for large chunk collections

### Storage
- **v0.3.0**: Uncompressed KV-cache files
- **v0.4.0**: zlib compression (level 6)
- **Space savings**: 50-80% on typical KV data

### Chunking
- **v0.3.0**: Fixed-size chunks
- **v0.4.0**: Adaptive sizing + sliding window
- **Improvement**: 20-30% fewer chunks, better context

## Quick Start

### 1. Index Documents

```bash
# Index files (auto-detects modality)
chunkforge index src/*.py docs/*.md

# Force re-indexing
chunkforge index --force document.py
```

### 2. Semantic Search

```bash
# Search across all indexed chunks
chunkforge search "authentication logic" --top-k 5

# JSON output
chunkforge search "error handling" --json
```

### 3. MCP Server (for Claude Code / Claude Desktop)

```bash
# Install MCP dependency
pip install chunkforge[mcp]

# Start stdio MCP server
chunkforge serve-mcp
```

**Claude Code** (`~/.claude/settings.json`):
```json
{
  "mcpServers": {
    "chunkforge": {
      "command": "chunkforge",
      "args": ["serve-mcp"]
    }
  }
}
```

**Claude Desktop** (`~/.config/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "chunkforge": {
      "command": "chunkforge",
      "args": ["serve-mcp"]
    }
  }
}
```

> **Tip:** If installed in a virtualenv, use the full path to the `chunkforge` binary (e.g., `/path/to/.venv/bin/chunkforge`).

### 4. HTTP REST Server

```bash
# Start HTTP server on default port (9876)
chunkforge serve --port 9876
```

### 5. Detect Changes & View Stats

```bash
chunkforge detect --session my-session
chunkforge stats
```

## Python API Usage

### Basic Usage

```python
from chunkforge import ChunkForge

cf = ChunkForge(storage_dir="~/.chunkforge")

# Index documents (routes through modality-specific chunkers)
result = cf.index_documents(["src/main.py", "README.md"])
print(f"Indexed {result['total_chunks']} chunks")

# Semantic search — returns chunk content + metadata
results = cf.search("authentication logic", top_k=5)
for r in results:
    print(f"[{r['relevance_score']:.3f}] {r['document_path']}")
    print(f"  {r['content'][:100]}...")

# Get cached context for documents
context = cf.get_context(["src/main.py", "src/utils.py"])
for doc in context["unchanged"]:
    print(f"{doc['path']}: {len(doc['chunks'])} cached chunks")
for doc in context["changed"]:
    print(f"{doc['path']}: needs re-indexing")

# Detect changes
changes = cf.detect_changes_and_update(session_id="my-session")
print(f"Restored {changes['kv_restored']}, reprocessed {changes['kv_reprocessed']}")

# Session management
cf.save_kv_state("my-session", {"chunk_id": {"key": "value"}})
cf.rollback("my-session", target_turn=2)
cf.prune_chunks("my-session", max_tokens=100000)
```

### Advanced Configuration

```python
cf = ChunkForge(
    storage_dir="~/.chunkforge",
    chunk_size=256,           # Target tokens per initial chunk
    max_chunk_size=4096,      # Maximum tokens per merged chunk
    merge_threshold=0.7,      # Similarity threshold for merging
    change_threshold=0.85,    # Similarity threshold for "unchanged"
)
```

## MCP Server API

The MCP server exposes the following endpoints:

### GET /tools

Discover available tools.

```bash
curl http://localhost:9876/tools
```

Response:
```json
{
  "tools": [
    {
      "name": "index_documents",
      "description": "Index one or more documents...",
      "parameters": { ... }
    },
    ...
  ]
}
```

### POST /call

Execute a tool.

```bash
curl -X POST http://localhost:9876/call \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "index_documents",
    "parameters": {
      "paths": ["document.py"]
    }
  }'
```

### GET /health

Health check and statistics.

```bash
curl http://localhost:9876/health
```

## Agent Integration Examples

### Example 1: Index Codebase

```python
import requests

# Index all Python files
response = requests.post("http://localhost:9876/call", json={
    "tool": "index_documents",
    "parameters": {
        "paths": ["src/main.py", "src/utils.py", "tests/test_main.py"]
    }
})
print(response.json())
```

### Example 2: Detect Changes After Edit

```python
# After editing a file, check what changed
response = requests.post("http://localhost:9876/call", json={
    "tool": "detect_changes_and_update",
    "parameters": {
        "session_id": "coding-session-1"
    }
})
result = response.json()["result"]
print(f"Restored {result['kv_restored']} KV states")
print(f"Need to reprocess {result['kv_reprocessed']} chunks")
```

### Example 3: Get Relevant Context

```python
# Get KV-cache for relevant chunks
response = requests.post("http://localhost:9876/call", json={
    "tool": "get_relevant_kv",
    "parameters": {
        "session_id": "coding-session-1",
        "query": "How does user authentication work?",
        "top_k": 10
    }
})
relevant_chunks = response.json()["result"]["chunks"]
```

### Example 4: Save and Rollback

```python
# Save current KV state
requests.post("http://localhost:9876/call", json={
    "tool": "save_kv_state",
    "parameters": {
        "session_id": "coding-session-1",
        "kv_data": {"chunk_1": {"key": "value"}}
    }
})

# Later, rollback to previous state
requests.post("http://localhost:9876/call", json={
    "tool": "rollback",
    "parameters": {
        "session_id": "coding-session-1",
        "target_turn": 2
    }
})
```

## How It Works

### Token-Saving Mechanism

ChunkForge dramatically reduces token usage through three mechanisms:

1. **Instant KV Restoration**: When a document hasn't changed, all its chunks' KV-cache states are loaded instantly. The LLM never needs to re-process these chunks, saving tokens equal to the chunk size × number of unchanged chunks.

2. **Lazy Double-Check**: When a document has changed, ChunkForge compares semantic signatures of each chunk. If a chunk's content changed but its semantic meaning is similar (cosine similarity > 0.85), it's considered "unchanged" and its KV state is restored without LLM re-processing.

3. **Selective Reprocessing**: Only chunks with significant semantic changes are marked for reprocessing. This means editing a comment or fixing a typo doesn't trigger reprocessing of the entire file.

### Change Detection Logic

```
For each chunk in document:
  1. Compute SHA-256 hash of content
  2. If hash matches stored hash:
     → Content unchanged → Load pre-saved KV (instant)
  
  3. If hash differs:
     → Compute semantic signature (TF-style features)
     → Compare with stored signature (cosine similarity)
     
     4. If similarity > change_threshold (0.85):
        → Semantically similar → Load pre-saved KV (lazy double-check)
     
     5. If similarity ≤ change_threshold:
        → Significant change → Mark for reprocessing
```

### Dynamic Semantic Chunking

The chunking algorithm works in two phases:

**Phase 1: Initial Chunking**
- Split document on paragraph boundaries
- Target ~256 tokens per chunk
- Preserve semantic coherence

**Phase 2: Intelligent Merging**
- Compute semantic signatures for all chunks
- Iteratively merge adjacent chunks with high similarity (> 0.7)
- Stop when chunks reach max_chunk_size (4096 tokens) or similarity drops

This creates chunks that are:
- Semantically coherent (related content stays together)
- Optimally sized (not too small, not too large)
- Stable across edits (minor changes don't affect chunk boundaries)

### KV-Cache Persistence

KV-cache states are stored as:
- **Metadata**: SQLite database with chunk info, hashes, signatures
- **KV Data**: Serialized tensors in `~/.chunkforge/kv_cache/`
- **Sessions**: Track multiple independent contexts with rollback support

Storage format:
```
~/.chunkforge/
├── chunkforge.db          # SQLite metadata + chunk content
├── kv_cache/
│   └── {session_id}/
│       ├── {chunk_id}_turn0.kv   # JSON + zlib compressed
│       └── ...
└── indices/               # Reserved for persistent indices
```

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│               ChunkForge Engine (engine.py)               │
├──────────────────────────────────────────────────────────┤
│  index_documents()  │  search()    │  get_context()      │
│  detect_changes()   │  rollback()  │  save/load state    │
└──────────────────────────────────────────────────────────┘
          │                  │                   │
  ┌───────▼──────┐   ┌──────▼──────┐   ┌────────▼────────┐
  │  Chunkers    │   │ VectorIndex │   │  StorageBackend  │
  │  text, code  │   │   (HNSW)    │   │  + SessionStore  │
  │  image, pdf  │   │  index.py   │   │  storage.py      │
  │  audio,video │   └─────────────┘   └─────────────────┘
  └──────────────┘                            │
                                     ┌────────▼────────┐
  ┌──────────────┐                   │  SQLite + JSON   │
  │  MCP Servers │                   │  (chunk content  │
  │  stdio + HTTP│                   │   + metadata)    │
  └──────────────┘                   └─────────────────┘
```

## Configuration

### Environment Variables

- `CHUNKFORGE_STORAGE_DIR`: Override default storage directory
- `CHUNKFORGE_LOG_LEVEL`: Set logging level (DEBUG, INFO, WARNING, ERROR)

### Default Values

| Parameter | Default | Description |
|-----------|---------|-------------|
| `chunk_size` | 256 | Target tokens per initial chunk |
| `max_chunk_size` | 4096 | Maximum tokens per merged chunk |
| `merge_threshold` | 0.7 | Similarity threshold for merging chunks |
| `change_threshold` | 0.85 | Similarity threshold for "unchanged" |
| `host` | localhost | MCP server host |
| `port` | 9876 | MCP server port |

## Performance

### Token Savings

Typical token savings with ChunkForge:

| Scenario | Without ChunkForge | With ChunkForge | Savings |
|----------|-------------------|-----------------|---------|
| Unchanged document | 10,000 tokens | 0 tokens | 100% |
| Minor edit (typo) | 10,000 tokens | ~100 tokens | 99% |
| Moderate edit | 10,000 tokens | ~1,000 tokens | 90% |
| Major rewrite | 10,000 tokens | 10,000 tokens | 0% |

### Storage Overhead

- Metadata: ~1KB per chunk
- KV-cache: ~10-100KB per chunk (depends on model size)
- Total: Typically 1-10% of original document size

## Limitations

- **Semantic signatures are approximate**: The feature-based signatures (trigrams, bigrams, positional features) provide good but not perfect semantic similarity
- **No GPU acceleration**: All operations run on CPU
- **Single-machine only**: No distributed storage or multi-node support
- **KV-cache format**: Assumes JSON-serializable KV data (falls back to msgspec for complex types)

## Contributing

Contributions are welcome! Please ensure:

1. All code is 100% offline and local-only
2. Minimal external dependencies (prefer stdlib)
3. Comprehensive documentation and type hints
4. Tests for new features

## License

MIT License - see LICENSE file for details.

## Acknowledgments

ChunkForge is inspired by the need for efficient long-context LLM interactions in coding agents and development workflows.
