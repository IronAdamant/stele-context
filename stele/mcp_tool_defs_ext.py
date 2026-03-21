"""
Tool definitions for the Stele MCP stdio server (extended tools).

Part 2: symbols, sessions, document locking, agents, environment, embeddings,
chunk history, and notifications.
Each entry is a dict with name, description, and inputSchema.
Standalone module with zero internal dependencies.
"""

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS_EXT: list[dict[str, Any]] = [
    {
        "name": "find_references",
        "description": "Find all definitions and references of a symbol across the codebase (LSP-style)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to search for (function, class, CSS class like '.btn', CSS ID like '#app')",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "find_definition",
        "description": "Find where a symbol is defined, with full chunk content",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to find definition for",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "impact_radius",
        "description": "Find all chunks affected by changing a chunk (transitive dependents via symbol graph)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID to analyze impact for",
                },
                "depth": {
                    "type": "integer",
                    "description": "Max hops through dependency graph (default: 2)",
                    "default": 2,
                },
            },
            "required": ["chunk_id"],
        },
    },
    {
        "name": "rebuild_symbols",
        "description": "Rebuild the entire symbol graph from stored chunks (use after upgrade or to repair)",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "stale_chunks",
        "description": "Get chunks whose dependencies changed -- detects context rot through the symbol graph",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "Minimum staleness score (0.0-1.0, default 0.3). 0.8 = direct dep changed, 0.64 = transitive",
                    "default": 0.3,
                },
            },
        },
    },
    {
        "name": "list_sessions",
        "description": "List sessions, optionally filtered by agent ID",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter sessions by agent identifier",
                },
            },
        },
    },
    {
        "name": "acquire_document_lock",
        "description": "Acquire exclusive write lock on a document for multi-agent ownership",
        "inputSchema": {
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
    {
        "name": "refresh_document_lock",
        "description": "Refresh lock TTL without releasing -- prevents expiry during long operations",
        "inputSchema": {
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
    {
        "name": "release_document_lock",
        "description": "Release write lock on a document",
        "inputSchema": {
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
    {
        "name": "get_document_lock_status",
        "description": "Check if a document is locked and by which agent",
        "inputSchema": {
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
    {
        "name": "release_agent_locks",
        "description": "Release all document locks held by an agent (cleanup on disconnect)",
        "inputSchema": {
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
    {
        "name": "get_conflicts",
        "description": "Get conflict history for documents or agents",
        "inputSchema": {
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
        },
    },
    {
        "name": "reap_expired_locks",
        "description": "Clear all expired document locks and return what was reaped",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_agents",
        "description": "List agents registered across all worktrees with heartbeat status",
        "inputSchema": {
            "type": "object",
            "properties": {
                "active_only": {
                    "type": "boolean",
                    "description": "Only show active agents (default: true)",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "environment_check",
        "description": "Check for stale __pycache__, editable install mismatches, and other issues",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "clean_bytecache",
        "description": "Remove orphaned .pyc files from stale __pycache__ directories",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "store_semantic_summary",
        "description": "Store agent's semantic summary for a chunk -- improves search using agent's understanding",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID to annotate",
                },
                "summary": {
                    "type": "string",
                    "description": "Semantic description (e.g. 'JWT middleware that validates tokens')",
                },
            },
            "required": ["chunk_id", "summary"],
        },
    },
    {
        "name": "store_embedding",
        "description": "Store a raw embedding vector for a chunk -- for agents with embedding API access",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID to update",
                },
                "vector": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Embedding vector (normalized to unit length)",
                },
            },
            "required": ["chunk_id", "vector"],
        },
    },
    {
        "name": "get_chunk_history",
        "description": "Get chunk version history -- shows how chunks changed over time",
        "inputSchema": {
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
        },
    },
    {
        "name": "get_notifications",
        "description": "Get change notifications from other agents (what files changed since last check)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "since": {
                    "type": "number",
                    "description": "Unix timestamp; only show notifications after this",
                },
                "exclude_self": {
                    "type": "string",
                    "description": "Agent ID to exclude from results",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max notifications (default: 100)",
                    "default": 100,
                },
            },
        },
    },
    {
        "name": "search_text",
        "description": "Search chunk content by exact substring or regex pattern. "
        "Perfect recall for literal patterns — finds every occurrence across all "
        "indexed chunks. Use before renaming/removing symbols to find all usages.",
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
]
