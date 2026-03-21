"""
Tool definitions for the Stele MCP stdio server (core tools).

Part 1: index, search, context, annotations, history, stats.
Each entry is a dict with name, description, and inputSchema.
These are converted to MCP Tool objects at server startup.
Standalone module with zero internal dependencies.

Extended tools (symbols, locking, agents, etc.) are in mcp_tool_defs_ext.py.
The combined list is available as TOOL_DEFINITIONS.
"""

from typing import Any, Dict, List

from stele.mcp_tool_defs_ext import TOOL_DEFINITIONS_EXT

_TOOL_DEFINITIONS_CORE: List[Dict[str, Any]] = [
    {
        "name": "index",
        "description": "Index documents for semantic chunking and caching",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths to index",
                },
                "force_reindex": {
                    "type": "boolean",
                    "description": "Force re-indexing even if unchanged",
                    "default": False,
                },
            },
            "required": ["paths"],
        },
    },
    {
        "name": "remove",
        "description": "Remove a document and all its chunks, annotations, and index entries",
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
    {
        "name": "search",
        "description": "Semantic search across indexed chunks, returns content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query text",
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
        "name": "get_context",
        "description": "Get cached context for documents (unchanged/changed/new)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Document paths to get context for",
                },
            },
            "required": ["document_paths"],
        },
    },
    {
        "name": "detect_changes",
        "description": "Detect changes in indexed documents",
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
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "annotate",
        "description": "Add an annotation to a document or chunk for LLM context",
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
        "name": "map",
        "description": "Get project overview: all documents with chunk counts, tokens, and annotations",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "history",
        "description": "Get change history for indexed documents",
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
        "name": "stats",
        "description": "Get Stele statistics",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]

# Combined list: core + extended (symbols, locking, agents, env, embeddings)
TOOL_DEFINITIONS: List[Dict[str, Any]] = _TOOL_DEFINITIONS_CORE + TOOL_DEFINITIONS_EXT
