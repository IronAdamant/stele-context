"""MCP stdio works with the 1.x decorator API and the 2.x handler API."""

from __future__ import annotations

import asyncio
import importlib.metadata
from pathlib import Path

import pytest

from stele_context import __version__
from stele_context.mcp_stdio import (
    HAS_MCP,
    build_initialization_options,
    create_server,
    uses_constructor_handlers,
)


class _DecoratorServer:
    """Stand-in for the mcp 1.x ``Server`` decorator surface."""

    def __init__(self, name: str, version: str | None = None, **kwargs):
        if kwargs:
            raise TypeError(f"decorator server got unexpected kwargs: {sorted(kwargs)}")
        self.name = name
        self.version = version
        self.registered: dict[str, object] = {}

    def _decorator(self, key: str):
        def decorate(fn):
            self.registered[key] = fn
            return fn

        return decorate

    def list_tools(self):
        return self._decorator("list_tools")

    def call_tool(self):
        return self._decorator("call_tool")

    def list_resources(self):
        return self._decorator("list_resources")

    def list_resource_templates(self):
        return self._decorator("list_resource_templates")

    def read_resource(self):
        return self._decorator("read_resource")

    def create_initialization_options(self):
        return {"server_name": self.name, "server_version": self.version}


class _HandlerServer:
    def __init__(self, name: str, *, version: str = "", on_list_tools=None, **_kwargs):
        self.name = name
        self.version = version
        self.on_list_tools = on_list_tools


class TestMcpDependency:
    def test_extra_spans_mcp_1_and_2(self):
        text = Path("pyproject.toml").read_text(encoding="utf-8")
        compact = "".join(text.split())
        # mcp extra and all extra. 1.x decorators and 2.x handlers are tested;
        # mcp 3 is outside that range. An open lower bound would install it.
        assert compact.count('"mcp>=1.0.0,<3"') == 2
        assert "mcp<2" not in compact


class TestApiSelection:
    def test_handler_keyword_selects_mcp2(self):
        assert uses_constructor_handlers(_HandlerServer) is True

    def test_decorator_server_selects_mcp1(self):
        assert uses_constructor_handlers(_DecoratorServer) is False


@pytest.mark.skipif(not HAS_MCP, reason="MCP SDK not installed")
class TestInstalledMcpSdk:
    def test_binding_matches_installed_major(self):
        from mcp.server import Server

        major = int(importlib.metadata.version("mcp").split(".", 1)[0])
        assert uses_constructor_handlers(Server) is (major >= 2)

    def test_server_lists_calls_and_reads(self, tmp_path):
        bundle = create_server(str(tmp_path / "storage"))
        options = build_initialization_options(bundle.server)
        assert options.server_name == "stele-context"
        assert options.server_version == __version__

        if not uses_constructor_handlers(type(bundle.server)):
            pytest.skip("installed SDK uses decorators; handler calls are separate")

        from mcp.types import CallToolRequestParams, ReadResourceRequestParams

        handlers = bundle.server._request_handlers
        listed = asyncio.run(handlers["tools/list"].handler(None, None))
        names = {tool.name for tool in listed.tools}
        assert "doctor" in names
        assert "query" in names

        doctor = asyncio.run(
            handlers["tools/call"].handler(
                None, CallToolRequestParams(name="doctor", arguments={})
            )
        )
        assert doctor.is_error is False
        assert __version__ in doctor.content[0].text

        missing = asyncio.run(
            handlers["tools/call"].handler(
                None, CallToolRequestParams(name="no-such-tool", arguments=None)
            )
        )
        assert missing.is_error is True
        assert "Unknown tool" in missing.content[0].text

        raised = asyncio.run(
            handlers["tools/call"].handler(
                None, CallToolRequestParams(name="search", arguments={})
            )
        )
        assert raised.is_error is True
        assert "error" in raised.content[0].text

        documents = asyncio.run(
            handlers["resources/read"].handler(
                None,
                ReadResourceRequestParams(uri="stele-context://documents"),
            )
        )
        assert "documents" in documents.contents[0].text

        templates = asyncio.run(
            handlers["resources/templates/list"].handler(None, None)
        )
        assert templates.resource_templates

    def test_decorator_registration_still_runs(self, tmp_path, monkeypatch):
        from stele_context import mcp_stdio

        monkeypatch.setattr(mcp_stdio, "Server", _DecoratorServer)
        bundle = create_server(str(tmp_path / "storage"))
        server = bundle.server
        assert isinstance(server, _DecoratorServer)
        assert server.version == __version__
        assert set(server.registered) == {
            "list_tools",
            "call_tool",
            "list_resources",
            "list_resource_templates",
            "read_resource",
        }

        tools = asyncio.run(server.registered["list_tools"]())
        assert any(tool.name == "doctor" for tool in tools)

        blocks = asyncio.run(server.registered["call_tool"]("no-such-tool", {}))
        assert "Unknown tool" in blocks[0].text

        body = asyncio.run(
            server.registered["read_resource"]("stele-context://documents")
        )
        assert "documents" in body

        options = build_initialization_options(server)
        assert options == {
            "server_name": "stele-context",
            "server_version": __version__,
        }

    def test_initialization_fallback_without_helper(self):
        class Bare:
            pass

        options = build_initialization_options(Bare())
        assert options.server_name == "stele-context"
        assert options.server_version == __version__
        assert options.capabilities.tools is not None
        assert options.capabilities.resources is not None
