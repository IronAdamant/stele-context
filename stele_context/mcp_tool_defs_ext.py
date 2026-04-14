"""
Tool definitions for the Stele MCP stdio server (extended tools).

Part 2: search (agent-optimized), symbols, sessions, document locking,
agents, environment, embeddings, chunk history, and notifications.
Each entry is a dict with name, description, and inputSchema.
Standalone module with zero internal dependencies.
"""

from __future__ import annotations

from typing import Any

TOOL_DEFINITIONS_EXT: list[dict[str, Any]] = [
    # -- Primary: Symbol Graph ------------------------------------------------
    {
        "name": "find_references",
        "description": "Find all definitions and usages of a symbol across the "
        "codebase (LSP-style). Returns verdict "
        "(unreferenced/referenced/external/not_found), symbol_index (empty vs ready), "
        "and guidance when there are no hits — so empty results are never silent: "
        "distinguishes an unpopulated symbol table from a name absent in the graph. "
        "USE WHEN: verifying dead code, callers before refactors, dependencies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Symbol name (function, class, CSS class like '.btn', CSS ID like '#app')",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "find_definition",
        "description": "Jump to where a symbol is defined, with full chunk content. "
        "Includes symbol_index and guidance when count is zero (empty index vs "
        "symbol not in graph). "
        "USE WHEN: reading implementations, verifying signatures.",
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
        "description": "Find chunks affected by changing a chunk or file "
        "(transitive dependents via symbol graph). "
        "Accepts chunk_id, document_path, or symbol (at least one required). "
        "Default compact=true: per-file summaries (recommended for agents); "
        "set compact=false only for debugging (full chunk list). "
        "summary_mode=true returns bounded output: depth_distribution plus "
        "top_n_files impacted paths (by chunk count), similar to fan-in summaries. "
        "significance_threshold>0 filters out low-significance edges driven by "
        "common stdlib/generic symbols (e.g. push, has, addEdge), reducing "
        "false-positive blast radius for new files. "
        "USE WHEN: blast-radius checks before edits, downstream impact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {
                    "type": "string",
                    "description": "Chunk ID to analyze impact for",
                },
                "document_path": {
                    "type": "string",
                    "description": "File path to analyze impact for (all chunks in file used as seeds)",
                },
                "symbol": {
                    "type": "string",
                    "description": "Symbol name to analyze impact for (all definition chunks used as seeds). Useful for dynamic symbols.",
                },
                "depth": {
                    "type": "integer",
                    "description": "Max hops through dependency graph (default: 2)",
                    "default": 2,
                },
                "compact": {
                    "type": "boolean",
                    "description": "If true, return per-file summaries (path, chunk_count, depth range) instead of full chunk list (default: true)",
                    "default": True,
                },
                "include_content": {
                    "type": "boolean",
                    "description": "If false, omit chunk content from each record (default true)",
                    "default": True,
                },
                "path_filter": {
                    "type": "string",
                    "description": "Only include results whose document path contains this substring (e.g. 'src/' to exclude tests)",
                },
                "summary_mode": {
                    "type": "boolean",
                    "description": "If true, return per-depth chunk counts and only top_n_files most-impacted paths (bounded payload; implies compact, no content)",
                    "default": False,
                },
                "top_n_files": {
                    "type": "integer",
                    "description": "With summary_mode, max files listed in files (default 25)",
                    "default": 25,
                },
                "significance_threshold": {
                    "type": "number",
                    "description": "If > 0, skip edges driven by common stdlib/generic symbols (e.g. 0.1 filters push/has/addEdge). Default 0.0 = no filtering.",
                    "default": 0.0,
                },
                "exclude_symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of symbol names to ignore when traversing edges.",
                },
            },
        },
    },
    {
        "name": "coupling",
        "description": "Find files semantically coupled to a given file via shared "
        "symbol dependencies. Returns coupled files sorted by strength with "
        "direction (depends_on / depended_on_by / bidirectional), shared "
        "symbol names, and a semantic_score that discounts generic symbols. "
        "significance_threshold>0 filters out low-significance common symbols. "
        "USE WHEN: assessing which files need co-modification, understanding "
        "tight coupling for refactoring, identifying related files to review "
        "together.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_path": {
                    "type": "string",
                    "description": "File path to find coupled files for",
                },
                "significance_threshold": {
                    "type": "number",
                    "description": "If > 0, skip edges driven by common stdlib/generic symbols (e.g. 0.1 filters push/has/addEdge). Default 0.0 = no filtering.",
                    "default": 0.0,
                },
                "exclude_symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of symbol names to ignore when computing coupling.",
                },
            },
            "required": ["document_path"],
        },
    },
    {
        "name": "stale_chunks",
        "description": "Find chunks whose dependencies changed — detects context "
        "rot through the symbol graph. Staleness score: 0.8 = direct "
        "dependency changed, 0.64 = transitive. "
        "USE WHEN: checking if cached context is still valid after edits, "
        "identifying stale files that need re-review after upstream changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "Minimum staleness score (0.0-1.0, default 0.3)",
                    "default": 0.3,
                },
            },
        },
    },
    {
        "name": "rebuild_symbols",
        "description": "Rebuild the entire symbol graph from stored chunks. "
        "USE WHEN: after upgrade, to repair a corrupted graph, or after "
        "bulk re-indexing.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    # -- Dynamic Symbol Tracking (Runtime Symbols) -----------------------------
    {
        "name": "register_dynamic_symbols",
        "description": "Register runtime/dynamic symbols that don't correspond to "
        "indexed file chunks — plugin hooks, runtime callbacks, dynamically "
        "registered handlers. These appear in find_references, coupling, and "
        "impact_radius just like statically-extracted symbols, enabling the "
        "symbol graph to model dynamic registration patterns invisible to "
        "static analysis. "
        "USE WHEN: a plugin or runtime system registers symbols by name "
        "(e.g., hook registration API), and you want those symbols to be "
        "visible to find_references and impact_radius for change analysis.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "description": "Symbol name (e.g., 'on_recipe_validated')",
                            },
                            "kind": {
                                "type": "string",
                                "description": "Symbol kind: function, class, variable, module (default: function)",
                                "default": "function",
                            },
                            "role": {
                                "type": "string",
                                "description": "'definition' (symbol is defined here) or 'reference' (symbol is used here, default: definition)",
                                "default": "definition",
                            },
                            "document_path": {
                                "type": "string",
                                "description": "File path this symbol belongs to (e.g., 'src/plugins/hooks.js')",
                            },
                            "line_number": {
                                "type": "integer",
                                "description": "Optional line number where the symbol appears",
                            },
                        },
                        "required": ["name"],
                    },
                    "description": "List of runtime symbols to register",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier — symbols are namespaced by agent for later removal",
                },
            },
            "required": ["symbols", "agent_id"],
        },
    },
    {
        "name": "remove_dynamic_symbols",
        "description": "Remove all runtime/dynamic symbols previously registered by "
        "an agent. Use after an agent finishes work so stale runtime symbols "
        "don't pollute future queries. "
        "USE WHEN: cleaning up after an agent session completes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent whose symbols to remove",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "get_dynamic_symbols",
        "description": "List all registered runtime/dynamic symbols, "
        "optionally filtered by agent. Shows what dynamic symbols are currently "
        "in the symbol graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Filter by agent (optional — omit to get all)",
                },
            },
        },
    },
    # -- Secondary: Sessions --------------------------------------------------
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
    # -- Infrastructure: Document Locking -------------------------------------
    {
        "name": "document_lock",
        "description": "Unified document lock lifecycle tool. "
        "Actions: acquire, release, refresh, status, release_all, reap, conflicts. "
        "USE WHEN: claiming a file before editing, checking ownership, or cleaning up locks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "acquire",
                        "release",
                        "refresh",
                        "status",
                        "release_all",
                        "reap",
                        "conflicts",
                    ],
                    "description": "Lock operation to perform",
                },
                "document_path": {
                    "type": "string",
                    "description": "Document path (required for acquire/release/refresh/status)",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Agent identifier (required for acquire/release/refresh/release_all)",
                },
                "ttl": {
                    "type": "number",
                    "description": "Lock TTL in seconds (default: 300)",
                    "default": 300,
                },
                "force": {
                    "type": "boolean",
                    "description": "Force-steal lock from another agent (acquire only)",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries for conflicts action (default: 20)",
                    "default": 20,
                },
            },
            "required": ["action"],
        },
    },
    # -- Infrastructure: Agent Coordination -----------------------------------
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
        "name": "get_notifications",
        "description": "Get change notifications from other agents — what files "
        "changed since your last check. "
        "USE WHEN: checking for concurrent edits in multi-agent workflows.",
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
    # -- Infrastructure: Environment ------------------------------------------
    {
        "name": "environment_check",
        "description": "Check for stale __pycache__, editable install mismatches, "
        "and other environment issues",
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
    # -- Secondary: Semantic Enrichment ---------------------------------------
    {
        "name": "bulk_store_summaries",
        "description": "Batch-store per-chunk semantic summaries. Each chunk gets "
        "its own agent signature computed from its summary. "
        "USE WHEN: after indexing, when you have per-chunk descriptions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "summaries": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Mapping of chunk_id to semantic summary text. "
                    'Example: {"chunk_abc123": "Database connection pool with retry logic"}',
                },
            },
            "required": ["summaries"],
        },
    },
    {
        "name": "bulk_store_chunk_agent_notes",
        "description": "Batch-set agent_notes for many chunk IDs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "chunk_id -> notes string",
                },
            },
            "required": ["notes"],
        },
    },
    {
        "name": "bulk_store_embeddings",
        "description": "Batch-store raw embedding vectors for multiple chunks. "
        "Use WHEN: you have many vectors to store at once (e.g. large dynamic symbol meshes).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "embeddings": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "array",
                        "items": {"type": "number"},
                    },
                    "description": "Mapping of chunk_id to embedding vector. "
                    'Example: {"chunk_abc": [0.1, -0.2, ...], "chunk_def": [0.3, ...]}',
                },
            },
            "required": ["embeddings"],
        },
    },
    {
        "name": "llm_embed",
        "description": "Generate and store a semantic embedding using LLM reasoning. "
        "The LLM analyzes text and produces a 128-dim semantic fingerprint which is "
        "converted to a unit vector and stored, updating the HNSW index for Tier 2 "
        "semantic search. "
        "USE WHEN: storing important context, session state, or project knowledge "
        "for later retrieval. The stored embedding enables semantic search that "
        "actually understands content — not just statistical patterns. "
        "The LLM (you) should reason about the text and produce a fingerprint dict "
        "with 32 semantic dimension scores (each -1.0 to 1.0), then pass it as "
        "fingerprint_values so the server can convert to a vector.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text content to embed (first 4000 chars used)",
                },
                "chunk_id": {
                    "type": "string",
                    "description": "Unique chunk ID for this content (e.g. 'mem:<uuid>')",
                },
                "fingerprint_values": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Optional: 32 fingerprint values (one per semantic "
                    "dimension, -1.0 to 1.0). If omitted, statistical fallback is used. "
                    "For best results, the LLM should reason about the text and provide "
                    "these values.",
                },
            },
            "required": ["text", "chunk_id"],
        },
    },
    # -- Secondary: Chunk History ---------------------------------------------
    {
        "name": "get_chunk_history",
        "description": "Get chunk version history — shows how chunks changed over "
        "indexing cycles",
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
]
