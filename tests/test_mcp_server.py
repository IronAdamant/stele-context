"""Integration tests for the HTTP MCP server.

Starts a real HTTP server in a background thread and exercises the
full request/response cycle for tool discovery, health checks,
tool execution, and error handling.
"""

import json
import os
import urllib.request
import urllib.error

from stele_context.engine import Stele
from stele_context.mcp_server import MCPServer, _TOOL_SCHEMAS


class TestHTTPServer:
    """End-to-end tests for the MCP HTTP server."""

    def _start_server(self, tmp_path, port=0, mode: str = "standard"):
        """Start a server on an ephemeral port, return (server, base_url, engine).

        Default mode is ``standard`` so integration tests cover the broader
        tool surface. Production default is ``lite`` (see STELE_MCP_MODE).

        Tool maps are built per-request from ``STELE_MCP_MODE``, so the env
        var stays set until the test calls ``_stop_server`` and restores it.
        """
        self._prev_mcp_mode = os.environ.get("STELE_MCP_MODE")
        os.environ["STELE_MCP_MODE"] = mode
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        server = MCPServer(stele=cf, host="127.0.0.1", port=port)
        server.start(blocking=False)
        # Get the actual port assigned by the OS
        actual_port = server.server.server_address[1]
        base_url = f"http://127.0.0.1:{actual_port}"
        return server, base_url, cf

    def _stop_server(self, server):
        """Stop server and restore STELE_MCP_MODE."""
        try:
            server.stop()
        finally:
            prev = getattr(self, "_prev_mcp_mode", None)
            if prev is None:
                os.environ.pop("STELE_MCP_MODE", None)
            else:
                os.environ["STELE_MCP_MODE"] = prev

    def _get(self, url):
        """GET request, return (status, parsed JSON)."""
        try:
            resp = urllib.request.urlopen(url)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _post(self, url, data):
        """POST JSON request, return (status, parsed JSON)."""
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            resp = urllib.request.urlopen(req)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    # -- Discovery --

    def test_tools_discovery_returns_all_tools(self, tmp_path):
        """GET /tools returns every tool from _get_tool_map()."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._get(f"{url}/tools")
            assert status == 200
            tool_names = {t["name"] for t in data["tools"]}
            # Must contain all tools
            assert "index" in tool_names
            assert "search" in tool_names
            assert "find_references" in tool_names
            assert "find_definition" in tool_names
            assert "impact_radius" in tool_names
            assert "coupling" in tool_names
            assert "rebuild_symbols" in tool_names
            assert "stale_chunks" in tool_names
            assert "detect_modality" in tool_names
            assert "get_supported_formats" in tool_names
            assert "list_sessions" in tool_names
            assert "search_text" in tool_names
            assert "register_dynamic_symbols" in tool_names
            assert "remove_dynamic_symbols" in tool_names
            assert "get_dynamic_symbols" in tool_names
            # standard mode (this test): broader surface including batch/query
            assert len(tool_names) >= 40
        finally:
            self._stop_server(server)

    def test_default_mode_is_lite_high_leverage(self, tmp_path):
        """Production default (no STELE_MCP_MODE) exposes lite tools only."""
        prev = os.environ.pop("STELE_MCP_MODE", None)
        try:
            server, url, _ = self._start_server(tmp_path, mode="lite")
            try:
                status, data = self._get(f"{url}/tools")
                assert status == 200
                tool_names = {t["name"] for t in data["tools"]}
                assert "doctor" in tool_names
                assert "query" in tool_names
                assert "enrichment_plan" in tool_names
                assert "stats" not in tool_names
                assert "rebuild_symbols" not in tool_names
            finally:
                self._stop_server(server)
        finally:
            if prev is not None:
                os.environ["STELE_MCP_MODE"] = prev

    def test_tools_have_descriptions_and_parameters(self, tmp_path):
        """Every discovered tool has description and parameters."""
        server, url, _ = self._start_server(tmp_path)
        try:
            _, data = self._get(f"{url}/tools")
            for tool in data["tools"]:
                assert "name" in tool
                assert "description" in tool
                assert "parameters" in tool
                assert tool["parameters"]["type"] == "object"
        finally:
            self._stop_server(server)

    def test_tool_schemas_match_tool_map(self, tmp_path):
        """Every discovered tool has a schema; definitions may include full-mode extras."""
        server, url, _ = self._start_server(tmp_path)
        try:
            _, data = self._get(f"{url}/tools")
            discovered = {t["name"] for t in data["tools"]}
            schema_names = set(_TOOL_SCHEMAS.keys())
            # Discovery is mode-filtered; TOOL_DEFINITIONS includes full-mode names too.
            # Every live tool must have a schema entry (no empty fallback required).
            assert discovered.issubset(schema_names), (
                f"tools missing schemas: {discovered - schema_names}"
            )
            assert discovered  # lite (or server mode) is non-empty
        finally:
            self._stop_server(server)

    # -- Health --

    def test_health_endpoint(self, tmp_path):
        """GET /health returns status and version."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._get(f"{url}/health")
            assert status == 200
            assert data["status"] == "healthy"
            assert "version" in data
            assert "storage" in data
        finally:
            self._stop_server(server)

    # -- Tool execution --

    def test_index_and_search(self, tmp_path):
        """Index a file via HTTP, then search for it."""
        server, url, _ = self._start_server(tmp_path)
        try:
            # Create a test file
            test_file = tmp_path / "hello.py"
            test_file.write_text("def greet(name):\n    return f'Hello {name}'\n")

            # Index it
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "index",
                    "parameters": {"paths": [str(test_file)]},
                },
            )
            assert status == 200
            assert data["success"] is True
            assert data["result"]["total_chunks"] >= 1

            # Search for it
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "search",
                    "parameters": {"query": "greet", "top_k": 5},
                },
            )
            assert status == 200
            assert data["success"] is True
            assert len(data["result"]) >= 1
            assert "greet" in data["result"][0]["content"]
        finally:
            self._stop_server(server)

    def test_get_context(self, tmp_path):
        """Index then get_context for a file."""
        server, url, cf = self._start_server(tmp_path)
        try:
            test_file = tmp_path / "ctx.py"
            test_file.write_text("x = 42\n")

            # Index via engine directly (already tested HTTP index above)
            cf.index_documents([str(test_file)])

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "get_context",
                    "parameters": {"document_paths": [str(test_file)]},
                },
            )
            assert status == 200
            assert data["success"] is True
            assert len(data["result"]["unchanged"]) == 1
        finally:
            self._stop_server(server)

    def test_detect_changes(self, tmp_path):
        """detect_changes_and_update via HTTP."""
        server, url, cf = self._start_server(tmp_path)
        try:
            test_file = tmp_path / "change.py"
            test_file.write_text("a = 1\n")
            cf.index_documents([str(test_file)])

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "detect_changes",
                    "parameters": {"session_id": "test-session"},
                },
            )
            assert status == 200
            assert data["success"] is True
            assert len(data["result"]["unchanged"]) == 1
        finally:
            self._stop_server(server)

    def test_detect_modality(self, tmp_path):
        """detect_modality tool via HTTP."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "detect_modality",
                    "parameters": {"path": "test.py"},
                },
            )
            assert status == 200
            assert data["success"] is True
            assert data["result"]["modality"] == "code"
        finally:
            self._stop_server(server)

    def test_get_supported_formats(self, tmp_path):
        """get_supported_formats tool via HTTP."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "get_supported_formats",
                    "parameters": {},
                },
            )
            assert status == 200
            assert data["success"] is True
            assert "text" in data["result"]["formats"]
            assert "code" in data["result"]["formats"]
        finally:
            self._stop_server(server)

    def test_find_references_and_definition(self, tmp_path):
        """find_references and find_definition via HTTP."""
        server, url, cf = self._start_server(tmp_path)
        try:
            test_file = tmp_path / "refs.py"
            test_file.write_text("def helper():\n    pass\n\nhelper()\n")
            cf.index_documents([str(test_file)])

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "find_references",
                    "parameters": {"symbol": "helper"},
                },
            )
            assert status == 200
            assert data["success"] is True

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "find_definition",
                    "parameters": {"symbol": "helper"},
                },
            )
            assert status == 200
            assert data["success"] is True
        finally:
            self._stop_server(server)

    def test_stale_chunks(self, tmp_path):
        """stale_chunks tool via HTTP."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "stale_chunks",
                    "parameters": {"threshold": 0.1},
                },
            )
            assert status == 200
            assert data["success"] is True
        finally:
            self._stop_server(server)

    def test_rebuild_symbol_graph(self, tmp_path):
        """rebuild_symbol_graph tool via HTTP."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "rebuild_symbols",
                    "parameters": {},
                },
            )
            assert status == 200
            assert data["success"] is True
        finally:
            self._stop_server(server)

    # -- Error handling --

    def test_unknown_tool(self, tmp_path):
        """Calling an unknown tool returns an error with available tools list."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "nonexistent_tool",
                    "parameters": {},
                },
            )
            assert status == 200  # HTTP 200, error in body
            assert "error" in data
            assert "available_tools" in data
        finally:
            self._stop_server(server)

    def test_missing_tool_field(self, tmp_path):
        """POST /call without 'tool' field returns 400."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(f"{url}/call", {"parameters": {}})
            assert status == 400
            assert "error" in data
        finally:
            self._stop_server(server)

    def test_invalid_json(self, tmp_path):
        """POST /call with invalid JSON returns 400."""
        server, url, _ = self._start_server(tmp_path)
        try:
            body = b"not json"
            req = urllib.request.Request(
                f"{url}/call",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            try:
                resp = urllib.request.urlopen(req)
                status = resp.status
                data = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                status = e.code
                data = json.loads(e.read())
            assert status == 400
            assert "error" in data
        finally:
            self._stop_server(server)

    def test_bulk_store_embeddings(self, tmp_path):
        """bulk_store_embeddings tool via HTTP."""
        server, url, cf = self._start_server(tmp_path)
        try:
            test_file = tmp_path / "embed.py"
            test_file.write_text("x = 1\n")
            cf.index_documents([str(test_file)])

            chunks = cf.storage.get_document_chunks(str(test_file))
            assert len(chunks) >= 1
            cid = chunks[0]["chunk_id"]

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "bulk_store_embeddings",
                    "parameters": {"embeddings": {cid: [0.1] * 128}},
                },
            )
            assert status == 200
            assert data["success"] is True
            assert data["result"]["stored"] >= 1
        finally:
            self._stop_server(server)

    def test_document_lock(self, tmp_path):
        """document_lock unified tool via HTTP."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "document_lock",
                    "parameters": {
                        "action": "acquire",
                        "document_path": "test.py",
                        "agent_id": "test-agent",
                    },
                },
            )
            assert status == 200
            assert data["success"] is True

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "document_lock",
                    "parameters": {
                        "action": "status",
                        "document_path": "test.py",
                    },
                },
            )
            assert status == 200
            assert data["success"] is True
        finally:
            self._stop_server(server)

    def test_annotations(self, tmp_path):
        """annotations unified tool via HTTP."""
        server, url, cf = self._start_server(tmp_path)
        try:
            test_file = tmp_path / "note.py"
            test_file.write_text("x = 1\n")
            cf.index_documents([str(test_file)])

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "annotations",
                    "parameters": {
                        "action": "create",
                        "target": str(test_file),
                        "target_type": "document",
                        "content": "Test note",
                    },
                },
            )
            assert status == 200
            assert data["success"] is True

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "annotations",
                    "parameters": {
                        "action": "search",
                        "query": "Test",
                    },
                },
            )
            assert status == 200
            assert data["success"] is True
            assert len(data["result"]["annotations"]) >= 1
        finally:
            self._stop_server(server)

    def test_query(self, tmp_path):
        """query composite tool via HTTP."""
        server, url, cf = self._start_server(tmp_path)
        try:
            test_file = tmp_path / "helper.py"
            test_file.write_text("def helper():\n    pass\n")
            cf.index_documents([str(test_file)])

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "query",
                    "parameters": {"query": "helper function", "top_k": 5},
                },
            )
            assert status == 200
            assert data["success"] is True
            assert "results" in data["result"]
            assert "sources" in data["result"]
        finally:
            self._stop_server(server)

    def test_batch(self, tmp_path):
        """batch multi-operation tool via HTTP."""
        server, url, _ = self._start_server(tmp_path)
        try:
            test_file = tmp_path / "batch.py"
            test_file.write_text("y = 2\n")

            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "batch",
                    "parameters": {
                        "operations": [
                            {
                                "method": "index_documents",
                                "params": {"paths": [str(test_file)]},
                            },
                            {
                                "method": "search",
                                "params": {"query": "y = 2", "top_k": 5},
                            },
                        ]
                    },
                },
            )
            assert status == 200
            assert data["success"] is True
            assert len(data["result"]["operations"]) == 2
        finally:
            self._stop_server(server)

    def test_invalid_parameters(self, tmp_path):
        """Calling a tool with wrong parameters returns error."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(
                f"{url}/call",
                {
                    "tool": "search",
                    "parameters": {"wrong_param": "value"},
                },
            )
            assert status == 200
            assert "error" in data
        finally:
            self._stop_server(server)

    def test_404_on_unknown_path(self, tmp_path):
        """GET on unknown path returns 404."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._get(f"{url}/nonexistent")
            assert status == 404
            assert "error" in data
        finally:
            self._stop_server(server)

    def test_post_404_on_unknown_path(self, tmp_path):
        """POST on unknown path returns 404."""
        server, url, _ = self._start_server(tmp_path)
        try:
            status, data = self._post(f"{url}/nonexistent", {})
            assert status == 404
        finally:
            self._stop_server(server)

    # -- Server lifecycle --

    def test_server_start_stop(self, tmp_path):
        """Server starts and stops cleanly."""
        server, url, _ = self._start_server(tmp_path)
        # Verify it's running
        status, _ = self._get(f"{url}/health")
        assert status == 200
        # Stop it
        self._stop_server(server)
        assert server.server is None
        assert server._thread is None
