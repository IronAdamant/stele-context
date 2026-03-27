# API Stability

Stele Context follows [Semantic Versioning](https://semver.org/). This document defines what is considered public API and what stability guarantees apply.

## Public API

The public API consists of:

### Classes

| Class | Module | Stability |
|-------|--------|-----------|
| `Stele` | `stele_context.engine` | **Stable** — all 42 public methods |
| `Chunk` | `stele_context.chunkers.base` | **Stable** — dataclass fields + properties |
| `StorageBackend` | `stele_context.storage` | **Semi-stable** — see below |
| `SessionManager` | `stele_context.session` | **Stable** |
| `MCPServer` | `stele_context.mcp_server` | **Stable** |

### Functions

| Function | Module | Stability |
|----------|--------|-----------|
| `estimate_tokens` | `stele_context.chunkers.base` | **Stable** |

### Package Exports

Everything in `stele_context.__all__` is public and stable:
- `Stele`, `Chunk`, `StorageBackend`, `SessionManager`, `MCPServer`, `__version__`

## What "Stable" Means

- Method signatures will not change in backward-incompatible ways within a major version.
- New optional parameters may be added to existing methods.
- New methods may be added to existing classes.
- Return dict keys will not be removed, but new keys may be added.

## StorageBackend: Public vs Internal Methods

`StorageBackend` exposes ~57 methods through its delegate mixin. Only a subset are intended for direct external use:

**Public (user-facing):**
- `close()`, `get_storage_stats()`, `clear_all()`
- `get_document()`, `get_all_documents()`, `get_document_chunks()`, `search_chunks()`
- `search_text()`, `get_chunk()`, `get_chunk_content()`, `get_chunk_history()`
- `get_stale_chunks()`, `remove_document()`, `delete_chunks()`
- `store_semantic_summary()`, `store_agent_signature()`, `get_agent_signature()`
- All annotation methods: `store_annotation`, `get_annotations`, `delete_annotation`, `update_annotation`, `search_annotations`
- All lock methods: `acquire_document_lock`, `release_document_lock`, `refresh_document_lock`, `get_document_lock_status`, `release_agent_locks`, `reap_expired_locks`
- Session methods: `create_session`, `get_session`, `list_sessions`
- `get_conflicts()`, `get_change_history()`, `prune_history()`, `prune_conflicts()`

**Internal (used by engine modules, not for direct external use):**
- `store_chunk()`, `store_document()`
- All symbol mutators: `store_symbols`, `store_edges`, `clear_*_symbols`, `clear_*_edges`
- Version methods: `increment_doc_version`, `check_and_increment_doc_version`, `get_document_version`
- Staleness setters: `set_staleness`, `set_staleness_batch`, `clear_staleness`
- `record_change()`, `record_conflict()`
- Raw graph accessors: `get_all_symbols`, `find_definitions`, `find_references_by_name`, `get_edges_for_chunk`, `get_incoming_edges`, `get_outgoing_edges`, `search_symbol_names`

Internal methods may change without notice. If you need functionality from an internal method, use the corresponding `Stele` engine method instead (e.g., `stele.find_references()` instead of `storage.find_references_by_name()`).

## Typing Protocols

`stele_context.protocols` defines structural Protocol types (`StorageProto`, `VectorIndexProto`, `SymbolManagerProto`, `CoordinationProto`) that document the exact interface contracts. These are for reference and testing — they are not enforced at runtime.

## Deprecation Policy

Before removing or renaming a public API element:
1. The old name will be kept as an alias for at least one minor version.
2. A deprecation warning will be emitted when the old name is used.
3. The CHANGELOG will document the deprecation.

## Currently Deprecated

None. All deprecated aliases were removed in 1.0.0.

## Product scope (zero-dep core)

As of **v1.0.5**, the **core package** (no required third-party runtime dependencies) is in a **reasonable stopping place** for RecipeLab-style validation: hybrid search includes weak-vector and multi-signal BM25 fallbacks; **`map`** / **`search`** support optional **`path_prefix`** scoping; **`impact_radius`** supports **`summary_mode`** for bounded outputs. Further improvements that do not change the “no bundled embedding model” story are expected to be **incremental** (heuristics, UX, docs) unless the project explicitly adopts optional heavy deps or new major features.
