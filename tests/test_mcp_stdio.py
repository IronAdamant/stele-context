"""Tests for ChunkForge MCP stdio server."""

import pytest

from chunkforge import __version__
from chunkforge.engine import ChunkForge
from chunkforge.mcp_stdio import HAS_MCP


class TestMCPStdioServer:
    """Tests for MCP stdio server tool registration and execution."""

    def test_has_mcp_flag(self):
        """Test HAS_MCP flag is defined."""
        assert isinstance(HAS_MCP, bool)

    def test_create_engine(self, tmp_path):
        """Test engine creation helper."""
        from chunkforge.mcp_stdio import _create_engine

        engine = _create_engine(str(tmp_path / "storage"))
        assert isinstance(engine, ChunkForge)

    @pytest.mark.skipif(not HAS_MCP, reason="MCP SDK not installed")
    def test_create_server(self, tmp_path):
        """Test server creation with tool registration."""
        from chunkforge.mcp_stdio import create_server

        server = create_server(str(tmp_path / "storage"))
        assert server is not None

    def test_main_without_mcp_exits(self, tmp_path):
        """Test that main() exits gracefully without MCP SDK."""
        from chunkforge import mcp_stdio

        if mcp_stdio.HAS_MCP:
            pytest.skip("MCP SDK is installed")

        with pytest.raises(SystemExit):
            mcp_stdio.main(str(tmp_path / "storage"))


class TestMCPStdioIntegration:
    """Integration tests that verify tool logic without the MCP transport."""

    def test_index_tool_logic(self, tmp_path):
        """Test index tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = ChunkForge(storage_dir=str(tmp_path / "storage"))
        result = engine.index_documents(
            paths=[str(test_file)],
            force_reindex=False,
        )

        assert len(result["indexed"]) == 1
        assert result["total_chunks"] >= 1

    def test_search_tool_logic(self, tmp_path):
        """Test search tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def add(a, b): return a + b")

        engine = ChunkForge(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        result = engine.search(query="add function", top_k=5)
        assert len(result) >= 1
        assert "content" in result[0]

    def test_get_context_tool_logic(self, tmp_path):
        """Test get_context tool execution logic."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world")

        engine = ChunkForge(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        result = engine.get_context([str(test_file)])
        assert len(result["unchanged"]) == 1

    def test_stats_tool_logic(self, tmp_path):
        """Test stats tool execution logic."""
        engine = ChunkForge(storage_dir=str(tmp_path / "storage"))
        result = engine.get_stats()

        assert "version" in result
        assert result["version"] == __version__
        assert "index" in result
