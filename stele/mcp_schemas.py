"""
MCP tool schema definitions for Stele.

Single source of truth for tool discovery. Every tool in
``mcp_handlers.build_tool_map()`` MUST have a matching entry here.
Tools added to the map but missing from schemas get a minimal
auto-generated entry so they never silently disappear from /tools.

This module is pure data -- no logic, no internal imports.
"""

from typing import Any, Dict

_TOOL_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "index_documents": {
        "description": "Index one or more documents for KV-cache management. Supports text, code, images, PDFs, audio, and video.",
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of document paths to index",
                },
                "force_reindex": {
                    "type": "boolean",
                    "description": "Force re-indexing even if document hasn't changed",
                    "default": False,
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier for ownership checking",
                },
                "expected_versions": {
                    "type": "object",
                    "description": "Map of path -> expected version for optimistic locking",
                },
            },
            "required": ["paths"],
        },
    },
    "detect_modality": {
        "description": "Detect the modality of a file (text, code, image, pdf, audio, video).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to file",
                },
            },
            "required": ["path"],
        },
    },
    "get_supported_formats": {
        "description": "Get list of supported file formats by modality.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "detect_changes_and_update": {
        "description": "Detect changes in documents and update KV-cache accordingly.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "document_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of paths to check (defaults to all indexed)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent identifier for multi-agent tracking",
                },
            },
            "required": ["session_id"],
        },
    },
    "get_relevant_kv": {
        "description": "Get KV-cache for chunks most relevant to a query.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "query": {
                    "type": "string",
                    "description": "Query text to find relevant chunks for",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of top chunks to return",
                    "default": 10,
                },
            },
            "required": ["session_id", "query"],
        },
    },
    "save_kv_state": {
        "description": "Save KV-cache state for a session.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "kv_data": {
                    "type": "object",
                    "description": "Dictionary mapping chunk_id to KV data",
                },
                "chunk_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of chunk IDs to save (defaults to all)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent identifier for multi-agent tracking",
                },
            },
            "required": ["session_id", "kv_data"],
        },
    },
    "rollback": {
        "description": "Rollback session to a previous turn.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "target_turn": {
                    "type": "integer",
                    "description": "Target turn number to rollback to",
                },
            },
            "required": ["session_id", "target_turn"],
        },
    },
    "prune_chunks": {
        "description": "Prune low-relevance chunks to stay under token limit.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session identifier",
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Maximum total tokens to keep",
                },
            },
            "required": ["session_id", "max_tokens"],
        },
    },
    "search": {
        "description": "Semantic search across indexed chunks. Returns content ranked by relevance.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query text",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    "get_context": {
        "description": "Get cached context for documents. Returns unchanged chunks, flags changed/new.",
        "parameters": {
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
    "find_references": {
        "description": "Find all definitions and references for a symbol name across indexed documents.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to search for",
                },
            },
            "required": ["symbol"],
        },
    },
    "find_definition": {
        "description": "Find the definition location of a symbol.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to find",
                },
            },
            "required": ["symbol"],
        },
    },
    "impact_radius": {
        "description": "Find all chunks potentially affected by a change to a given chunk.",
        "parameters": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID to analyze",
                },
                "depth": {
                    "type": "integer",
                    "description": "Maximum hops to traverse (default: 2)",
                    "default": 2,
                },
            },
            "required": ["chunk_id"],
        },
    },
    "rebuild_symbol_graph": {
        "description": "Rebuild the symbol graph for all indexed documents.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "stale_chunks": {
        "description": "Find chunks with staleness scores above a threshold, grouped by document.",
        "parameters": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "Minimum staleness score (default: 0.3)",
                    "default": 0.3,
                },
            },
            "required": [],
        },
    },
    "list_sessions": {
        "description": "List sessions, optionally filtered by agent ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter sessions by agent identifier",
                },
            },
            "required": [],
        },
    },
    "acquire_document_lock": {
        "description": "Acquire exclusive write lock on a document. Other agents can read but not write.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Document path to lock",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent claiming ownership",
                },
                "ttl": {
                    "type": "number",
                    "description": "Lock TTL in seconds (default: 300)",
                    "default": 300,
                },
                "force": {
                    "type": "boolean",
                    "description": "Force-steal lock from another agent",
                    "default": False,
                },
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "refresh_document_lock": {
        "description": "Refresh lock TTL without releasing. Prevents expiry during long operations.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Document path whose lock to refresh",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent that holds the lock",
                },
                "ttl": {
                    "type": "number",
                    "description": "New TTL in seconds (default: keep current)",
                },
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "release_document_lock": {
        "description": "Release write lock on a document.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Document path to unlock",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent releasing ownership",
                },
            },
            "required": ["document_path", "agent_id"],
        },
    },
    "get_document_lock_status": {
        "description": "Check if a document is locked and by which agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Document path to check",
                },
            },
            "required": ["document_path"],
        },
    },
    "release_agent_locks": {
        "description": "Release all document locks held by an agent (cleanup).",
        "parameters": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent whose locks to release",
                },
            },
            "required": ["agent_id"],
        },
    },
    "get_conflicts": {
        "description": "Get conflict history for documents or agents.",
        "parameters": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "Filter by document path",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Filter by agent ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
    "reap_expired_locks": {
        "description": "Clear all expired document locks and return what was reaped.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "list_agents": {
        "description": "List agents registered across all worktrees with heartbeat status.",
        "parameters": {
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "Only show agents active (default: true)",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    "environment_check": {
        "description": "Check for environment issues: stale __pycache__, editable install mismatches.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "clean_bytecache": {
        "description": "Remove orphaned .pyc files from stale __pycache__ directories.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "store_semantic_summary": {
        "description": "Store an agent-supplied semantic summary for a chunk. Improves search by using the agent's understanding.",
        "parameters": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID to annotate with semantic summary",
                },
                "summary": {
                    "type": "string",
                    "description": "Agent's semantic description of the chunk (e.g. 'JWT middleware that validates tokens')",
                },
            },
            "required": ["chunk_id", "summary"],
        },
    },
    "store_embedding": {
        "description": "Store a raw embedding vector for a chunk. For agents with access to embedding APIs.",
        "parameters": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID to update",
                },
                "vector": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Embedding vector (will be normalized to unit length)",
                },
            },
            "required": ["chunk_id", "vector"],
        },
    },
    "get_chunk_history": {
        "description": "Get chunk version history. Shows how chunks changed over time.",
        "parameters": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Filter by specific chunk ID",
                },
                "document_path": {
                    "type": "string",
                    "description": "Filter by document path",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default: 50)",
                    "default": 50,
                },
            },
            "required": [],
        },
    },
    "get_notifications": {
        "description": "Get change notifications from other agents (what files changed since last check).",
        "parameters": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "number",
                    "description": "Unix timestamp; only notifications after this time",
                },
                "exclude_self": {
                    "type": "string",
                    "description": "Agent ID to exclude (skip your own changes)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max notifications to return (default: 100)",
                    "default": 100,
                },
            },
            "required": [],
        },
    },
}
