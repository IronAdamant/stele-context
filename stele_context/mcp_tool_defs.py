"""
Tool definitions for the Stele MCP stdio server (core tools).

Part 1: index, search, context, annotations, history, stats.
Each entry is a dict with name, description, and inputSchema.
These are converted to MCP Tool objects at server startup.
Depends only on mcp_tool_defs_ext (sibling data module).

Extended tools (symbols, locking, agents, etc.) are in mcp_tool_defs_ext.py.
The combined list is available as TOOL_DEFINITIONS.
"""

from __future__ import annotations

from typing import Any

from stele_context.mcp_tool_defs_ext import TOOL_DEFINITIONS_EXT

_TOOL_DEFINITIONS_CORE: list[dict[str, Any]] = [
    # -- Primary: Agent Search (exact + structured) ---------------------------
    {
        "name": "agent_grep",
        "description": "Primary search tool for LLM agents — like grep but with "
        "scope annotation (enclosing function/class), syntactic classification "
        "(comment/import/definition/string/code), deduplication of identical "
        "lines, and token budgeting to prevent context overflow. "
        "USE WHEN: auditing symbol usage, verifying dead code, understanding "
        "how a pattern is used across the codebase, or any search needing "
        "structured context-aware results. Preferred over search_text and "
        "search for all verification and audit workflows.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for",
                },
                "regex": {
                    "type": "boolean",
                    "description": "Treat pattern as a regex (default: false)",
                    "default": False,
                },
                "document_path": {
                    "type": "string",
                    "description": "Scope search to a specific file",
                },
                "classify": {
                    "type": "boolean",
                    "description": "Tag each match: comment/import/definition/string/code (default: true)",
                    "default": True,
                },
                "include_scope": {
                    "type": "boolean",
                    "description": "Annotate each match with enclosing function/class (default: true)",
                    "default": True,
                },
                "group_by": {
                    "type": "string",
                    "enum": ["file", "scope", "classification"],
                    "description": "How to group results (default: file)",
                    "default": "file",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Token budget for results — matches added until budget reached (default: 4000)",
                    "default": 4000,
                },
                "deduplicate": {
                    "type": "boolean",
                    "description": "Collapse structurally identical match lines (default: true)",
                    "default": True,
                },
                "context_lines": {
                    "type": "integer",
                    "description": "Lines of context above/below each match (default: 0)",
                    "default": 0,
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session ID. When provided, records this search "
                    "in session history and auto-indexes files with matches so they "
                    "are cached for get_context — no separate index call needed.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "search_text",
        "description": "Exact substring or regex search with perfect recall — "
        "guaranteed to find every occurrence across all indexed chunks. "
        "USE WHEN: need guaranteed completeness for simple patterns without "
        "enrichment. For structured results with scope/classification, "
        "prefer agent_grep.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text pattern to search for",
                },
                "regex": {
                    "type": "boolean",
                    "description": "Treat pattern as a regex (default: false)",
                    "default": False,
                },
                "document_path": {
                    "type": "string",
                    "description": "Limit search to a specific document",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max chunks to return (default: 50)",
                    "default": 50,
                },
            },
            "required": ["pattern"],
        },
    },
    # -- Primary: Search & Exploration ----------------------------------------
    {
        "name": "search",
        "description": "Secondary exploration tool — HNSW (statistical vectors) plus "
        "BM25 keywords with automatic fallback to keyword ranking when vector "
        "and keyword results disagree, when HNSW top scores are weak, or when "
        "scores are flat. NOT primary retrieval: results can "
        "favor structural/code boilerplate over query intent. "
        "USE WHEN: broad exploration after indexing; prefer agent_grep or "
        "search_text for verifying identifiers, renames, or exact occurrences. "
        "Prefer find_references / find_definition for symbol navigation. "
        "Use search_mode=keyword for deterministic BM25-only ranking (no vectors).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language or keyword query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results",
                    "default": 10,
                },
                "search_mode": {
                    "type": "string",
                    "enum": ["hybrid", "keyword"],
                    "description": "hybrid = HNSW+BM25 (default); keyword = BM25 only",
                    "default": "hybrid",
                },
                "max_result_tokens": {
                    "type": "integer",
                    "description": "Optional cap on total estimated tokens across "
                    "result bodies (reduces context overflow).",
                },
                "compact": {
                    "type": "boolean",
                    "description": "If true, replace full content with short previews.",
                    "default": False,
                },
                "return_response_meta": {
                    "type": "boolean",
                    "description": "If true, return {results, meta} with truncation info.",
                    "default": False,
                },
                "path_prefix": {
                    "type": "string",
                    "description": "If set, only return chunks whose document path starts with this prefix (project-relative path filter; reduces multi-repo noise).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "map",
        "description": "Project overview: all indexed documents with chunk counts, "
        "token totals, annotations, index_health (documents/chunks/symbol_rows, "
        "storage_dir, latest_indexed_at, seconds_since_last_index, "
        "symbol_graph_status, chunk_store_status, alerts), and project_root. "
        "Optional path_prefix limits to documents under that path prefix. "
        "USE WHEN: starting work on a project, understanding what's indexed, "
        "checking project scope and size.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "compact": {
                    "type": "boolean",
                    "description": "Sort by token count, cap document list, shorten annotations.",
                    "default": False,
                },
                "max_documents": {
                    "type": "integer",
                    "description": "With compact=true, max documents to return.",
                },
                "max_annotation_chars": {
                    "type": "integer",
                    "description": "Truncate each annotation content to this many chars.",
                    "default": 200,
                },
                "path_prefix": {
                    "type": "string",
                    "description": "If set, only include documents whose path starts with this prefix (project-relative; isolates one repo in a shared index).",
                },
            },
        },
    },
    {
        "name": "doctor",
        "description": "One-screen health snapshot: version, Python, storage paths, "
        "counts, index_health, environment_check issues, compact map preview. "
        "USE WHEN: orienting at session start or debugging index/storage.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_context",
        "description": "Check cached document state — returns unchanged/changed/new "
        "categorization per file. Unchanged entries may include trust (mtime vs "
        "index, staleness) and per-chunk agent_notes. When session_id is provided, "
        "also includes recently_searched (bool) and search_pattern (str) indicating "
        "if this file was found via agent_grep/search_text in this session. "
        "USE WHEN: checking if files need re-indexing before starting work, "
        "reading cached chunk content without re-reading disk.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Document paths to check",
                },
                "session_id": {
                    "type": "string",
                    "description": "Optional session ID. Records each returned file "
                    "as read and adds recently_searched/search_pattern to unchanged entries.",
                },
                "include_trust": {
                    "type": "boolean",
                    "description": "Include trust/staleness hints (default true).",
                    "default": True,
                },
                "max_chunk_content_tokens": {
                    "type": "integer",
                    "description": "Optional per-chunk content trim (estimated tokens).",
                },
            },
            "required": ["document_paths"],
        },
    },
    {
        "name": "get_search_history",
        "description": "Return all grep/search_text runs recorded for a session — "
        'the "post-it note" showing which files were already searched. '
        "USE WHEN: checking what you already grep'd before re-running a search, "
        "or verifying which files were auto-indexed after a grep.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to get search history for",
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "get_session_read_files",
        "description": "Return all files fully read via get_context in a session — "
        "distinct from searched files. Shows what content was retrieved from cache. "
        "USE WHEN: checking what you already fully read before re-calling get_context, "
        "to avoid re-fetching files you already have.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID to get read file list for",
                },
            },
            "required": ["session_id"],
        },
    },
    # -- Primary: Indexing & Change Detection ---------------------------------
    {
        "name": "index",
        "description": "Index files into the semantic cache with automatic chunking. "
        "Optionally pass per-file summaries to improve search quality "
        "(Tier 2 agent signatures). "
        "USE WHEN: after editing files to keep the index current, when adding "
        "new files to the project. Run on modified files after every batch of changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File or directory paths to index",
                },
                "force_reindex": {
                    "type": "boolean",
                    "description": "Force re-indexing even if unchanged",
                    "default": False,
                },
                "summaries": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Optional mapping of file path to semantic summary. "
                    "All chunks from a file receive the summary as an agent signature, "
                    "improving search relevance. Example: "
                    '{"src/auth.py": "JWT middleware that validates tokens"}',
                },
            },
            "required": ["paths"],
        },
    },
    {
        "name": "detect_changes",
        "description": "Detect and re-index changed documents since last indexing. "
        "By default (scan_new=true) also scans the project root for new files "
        "with chunker extensions not yet in the index — they appear under new "
        "with reason New file (scan). "
        "USE WHEN: after external edits, between agent passes, discovering "
        "new files, verifying what changed before proceeding.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "document_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths to check (default: all indexed)",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for the change detection (stored in history)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent identifier for multi-agent tracking",
                },
                "scan_new": {
                    "type": "boolean",
                    "description": "When true and document_paths is omitted, scan project root for new files matching chunker extensions not yet in the index; reported under new with reason New file (scan) (default: true)",
                    "default": True,
                },
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "remove",
        "description": "Remove a document and all its chunks, annotations, symbols, "
        "and index entries. "
        "USE WHEN: a file has been deleted from the project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Path of the document to remove",
                },
            },
            "required": ["document_path"],
        },
    },
    # -- Secondary: Annotations -----------------------------------------------
    {
        "name": "annotations",
        "description": "Unified annotation lifecycle tool. "
        "Actions: create, get, delete, update, search, bulk_create. "
        "USE WHEN: tagging code, recording audit findings, or querying notes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create",
                        "get",
                        "delete",
                        "update",
                        "search",
                        "bulk_create",
                    ],
                    "description": "Annotation operation to perform",
                },
                "target": {
                    "type": "string",
                    "description": "Document path or chunk ID (create/get)",
                },
                "target_type": {
                    "type": "string",
                    "enum": ["document", "chunk"],
                    "description": "Whether target is a document or chunk",
                },
                "content": {
                    "type": "string",
                    "description": "Annotation text (create/update)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization",
                },
                "annotation_id": {
                    "type": "integer",
                    "description": "Annotation ID (delete/update)",
                },
                "query": {
                    "type": "string",
                    "description": "Search query (search action)",
                },
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target": {"type": "string"},
                            "target_type": {
                                "type": "string",
                                "enum": ["document", "chunk"],
                            },
                            "content": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["target", "target_type", "content"],
                    },
                    "description": "List of annotations for bulk_create",
                },
            },
            "required": ["action"],
        },
    },
    # -- Secondary: History & Stats -------------------------------------------
    {
        "name": "history",
        "description": "Get chronological indexing history for documents",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return",
                    "default": 20,
                },
                "document_path": {
                    "type": "string",
                    "description": "Filter by document path",
                },
            },
        },
    },
    {
        "name": "prune_history",
        "description": "Prune old change history entries by age or count",
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_age_seconds": {
                    "type": "number",
                    "description": "Delete entries older than this many seconds",
                },
                "max_entries": {
                    "type": "integer",
                    "description": "Keep only this many newest entries",
                },
            },
        },
    },
    {
        "name": "query",
        "description": "Composite retrieval that merges semantic search, symbol graph lookups, "
        "and text grep into one result list. Returns deduplicated chunks with source provenance. "
        "USE WHEN: you want the broadest possible coverage for a natural-language question.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Max results to return (default: 10)",
                    "default": 10,
                },
                "path_prefix": {
                    "type": "string",
                    "description": "Optional project-relative path prefix filter",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "batch",
        "description": "Execute multiple engine operations in sequence under a single write lock. "
        "Each operation is {'method': '...', 'params': {...}}. Unknown methods or errors are captured "
        "and execution continues. USE WHEN: chaining index + annotate + embed in one round-trip.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "method": {"type": "string"},
                            "params": {"type": "object"},
                        },
                        "required": ["method"],
                    },
                    "description": "List of operations to execute",
                },
            },
            "required": ["operations"],
        },
    },
    # -- Secondary: Session & KV Cache ----------------------------------------
    {
        "name": "get_relevant_kv",
        "description": "Retrieve cached KV state for a session, matched by query",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID",
                },
                "query": {
                    "type": "string",
                    "description": "Query to match against cached chunks",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 5,
                },
            },
            "required": ["session_id", "query"],
        },
    },
    {
        "name": "save_kv_state",
        "description": "Save KV-cache state for a session chunk",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID",
                },
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID",
                },
                "kv_data": {
                    "type": "object",
                    "description": "KV-cache data to save",
                },
            },
            "required": ["session_id", "chunk_id", "kv_data"],
        },
    },
    {
        "name": "rollback",
        "description": "Rollback a session to a previous turn",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID",
                },
                "target_turn": {
                    "type": "integer",
                    "description": "Turn number to rollback to",
                },
            },
            "required": ["session_id", "target_turn"],
        },
    },
    {
        "name": "prune_chunks",
        "description": "Prune least-relevant chunks from a session to stay within token budget",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum total tokens to keep",
                },
            },
            "required": ["session_id", "max_tokens"],
        },
    },
    # -- Utility: Modality Detection ------------------------------------------
    {
        "name": "detect_modality",
        "description": "Detect file type (code/text/pdf/image/audio/video) from extension",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to detect modality for",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_supported_formats",
        "description": "List supported file extensions by modality",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

# Combined list: core + extended (symbols, locking, agents, env, embeddings)
TOOL_DEFINITIONS: list[dict[str, Any]] = _TOOL_DEFINITIONS_CORE + TOOL_DEFINITIONS_EXT
