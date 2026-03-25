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
        "description": "Semantic + keyword hybrid search across indexed chunks. "
        "Finds code by meaning, not just exact text — ranks results by "
        "combined vector similarity and keyword relevance. "
        "USE WHEN: exploring concepts ('how does auth work?'), finding "
        "related code by meaning, discovering relevant files for a new task. "
        "For exact pattern matching or verification, use agent_grep instead.",
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
            },
            "required": ["query"],
        },
    },
    {
        "name": "map",
        "description": "Project overview: all indexed documents with chunk counts, "
        "token totals, and annotations. "
        "USE WHEN: starting work on a project, understanding what's indexed, "
        "checking project scope and size.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_context",
        "description": "Check cached document state — returns unchanged/changed/new "
        "categorization per file. "
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
            },
            "required": ["document_paths"],
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
        "USE WHEN: after external edits, between agent passes, verifying "
        "what changed before proceeding.",
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
                    "description": "When true and document_paths is omitted, scan project root for new files matching chunker extensions not yet in the index; reported under new with reason New file (scan)",
                    "default": False,
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
        "name": "annotate",
        "description": "Add a metadata annotation to a document or chunk. "
        "USE WHEN: tagging code for later retrieval (TODO, deprecated, "
        "needs-review), recording audit findings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Document path or chunk ID to annotate",
                },
                "target_type": {
                    "type": "string",
                    "enum": ["document", "chunk"],
                    "description": "Whether target is a document or chunk",
                },
                "content": {
                    "type": "string",
                    "description": "Annotation text",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for categorization",
                },
            },
            "required": ["target", "target_type", "content"],
        },
    },
    {
        "name": "get_annotations",
        "description": "Retrieve annotations, optionally filtered by target, type, or tags",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Filter by document path or chunk ID",
                },
                "target_type": {
                    "type": "string",
                    "enum": ["document", "chunk"],
                    "description": "Filter by target type",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by tags (any match)",
                },
            },
        },
    },
    {
        "name": "delete_annotation",
        "description": "Delete an annotation by ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "annotation_id": {
                    "type": "integer",
                    "description": "Annotation ID to delete",
                },
            },
            "required": ["annotation_id"],
        },
    },
    {
        "name": "update_annotation",
        "description": "Update an existing annotation's content and/or tags",
        "inputSchema": {
            "type": "object",
            "properties": {
                "annotation_id": {
                    "type": "integer",
                    "description": "Annotation ID to update",
                },
                "content": {
                    "type": "string",
                    "description": "New annotation text",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "New tags (replaces existing)",
                },
            },
            "required": ["annotation_id"],
        },
    },
    {
        "name": "search_annotations",
        "description": "Search annotation content text (substring match)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in annotation content",
                },
                "target_type": {
                    "type": "string",
                    "enum": ["document", "chunk"],
                    "description": "Filter by target type",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "bulk_annotate",
        "description": "Annotate multiple targets in one call",
        "inputSchema": {
            "type": "object",
            "properties": {
                "annotations": {
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
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["target", "target_type", "content"],
                    },
                    "description": "List of annotations to create",
                },
            },
            "required": ["annotations"],
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
        "name": "stats",
        "description": "Get Stele statistics: storage counts, index health, config",
        "inputSchema": {
            "type": "object",
            "properties": {},
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
