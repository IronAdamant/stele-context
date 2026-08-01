# Glossary

| Term | Definition |
|------|-----------|
| **Chunk** | A semantically meaningful segment of a document, stored with metadata and a 128-dim signature |
| **Semantic Signature** | 128-dimensional float vector computed from trigrams, word features, and structural markers. Used for HNSW search |
| **Agent Signature** | Optional higher-quality embedding supplied by an LLM agent via `store_semantic_summary` or `store_embedding` |
| **HNSW** | Hierarchical Navigable Small World graph. O(log n) approximate nearest neighbor search |
| **BM25** | Best Matching 25. Keyword-based relevance scoring algorithm used alongside HNSW |
| **Hybrid Search** | Opt-in `search_mode=hybrid`: `alpha * cosine + (1-alpha) * bm25`. **Default search is keyword/BM25-only** |
| **Tier 1 / Tier 2** | Tier 1 = statistical 128-d signatures (always). Tier 2 = agent summaries/vectors for better ranking |
| **query (tool)** | Composite retrieval: search + symbol graph + agent_grep; returns `applied_defaults` for smart scopes |
| **working_tree** | Flag to auto-index git modified/untracked files before search |
| **lite MCP** | Default `STELE_MCP_MODE=lite` high-leverage tool subset |
| **WriterQueue** | Single-writer daemon serializing SQLite writes for resilience |
| **file_dependencies** | Path-level dependency fallback used by impact_radius when symbol edges are sparse |
| **Modality** | Content type: text, code, image, pdf, audio, video. Determines which chunker processes a file |
| **Chunker** | A class (subclass of `BaseChunker`) that splits content into `Chunk` objects. One per modality |
| **Tree-sitter** | Optional AST parser for code chunking. Supports 9 languages. Falls back to regex when not installed |
| **Session** | A named context window with KV-cache state. Supports rollback and pruning |
| **Symbol** | A named code entity (function, class, variable) extracted by `SymbolExtractor` |
| **Symbol Edge** | A reference relationship between two symbols (e.g., function A calls function B) |
| **Staleness** | Score (0-1) indicating how likely a chunk is outdated. Propagated via BFS through symbol edges |
| **Document Lock** | Per-document exclusive write access for an agent. TTL-based with auto-expiry |
| **Optimistic Locking** | `doc_version` compare-and-swap on document writes. Prevents silent overwrites |
| **Coordination** | Cross-worktree shared SQLite DB for agent registry, shared locks, and conflict log |
| **Worktree** | A git worktree - a separate working directory sharing the same `.git`. Each gets its own `.stele-context/` |
| **Project Root** | The directory containing `.git`. Used for path normalization and storage location |
| **MCP** | Model Context Protocol. JSON-RPC over stdio for Claude Desktop integration |
| **KV-Cache** | Key-value cache stored as JSON+zlib blobs on the filesystem. Used by sessions |
