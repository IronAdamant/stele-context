# Grok Connectors / BYO MCP Support

This project is ready for Grok Connectors (Bring Your Own MCP) and Grok Build.

The MCP server provides persistent local context cache and indexing for LLM agents, designed to work with any MCP-compatible client, including future Grok Build local agents and remote MCP connections in Grok.

## For Grok Connectors (BYO MCP)

When Grok supports adding custom MCP servers:

1. Start the server locally (example for this project):
   ```bash
   python -m stele_context.mcp_server --port 9876
   ```
   Or use stdio mode for local agents if available.

2. For remote access from Grok, expose the HTTP endpoint over HTTPS using a secure tunnel.

3. Add to Grok Connectors / remote MCP config:
   - **server_url**: `https://your-https-tunnel/mcp`
   - **server_label**: `stele-context`
   - **server_description**: "Local context cache and semantic indexing for LLM agents. Avoid re-reading unchanged files with persistent chunk storage and vector search."

The server implements MCP tools for indexing, lookup, and context management. Grok will discover the tools automatically.

## For Grok Build (Local)

Once Grok Build is available:
- Use stele-context alongside Grok Build agents to provide fast, persistent context across long-running autonomous rebuild sessions without context window bloat.
- Agents can query the index for symbols, recent changes, and semantic matches instead of re-scanning the entire codebase.

No changes to your existing workflows are required. This project was built to be backend-agnostic and work with any LLM/IDE/CLI via MCP.

See README.md for full setup.

For questions or to contribute Grok-specific adapters, open an issue.