"""Tests for Stele MCP stdio server."""

import pytest

from stele import __version__
from stele.engine import Stele
from stele.mcp_stdio import HAS_MCP


class TestMCPStdioServer:
    """Tests for MCP stdio server tool registration and execution."""

    def test_has_mcp_flag(self):
        """Test HAS_MCP flag is defined."""
        assert isinstance(HAS_MCP, bool)

    def test_create_engine(self, tmp_path):
        """Test engine creation helper."""
        from stele.mcp_stdio import _create_engine

        engine = _create_engine(str(tmp_path / "storage"))
        assert isinstance(engine, Stele)

    @pytest.mark.skipif(not HAS_MCP, reason="MCP SDK not installed")
    def test_create_server(self, tmp_path):
        """Test server creation with tool registration."""
        from stele.mcp_stdio import create_server

        server = create_server(str(tmp_path / "storage"))
        assert server is not None

    def test_main_without_mcp_exits(self, tmp_path):
        """Test that main() exits gracefully without MCP SDK."""
        from stele import mcp_stdio

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

        engine = Stele(storage_dir=str(tmp_path / "storage"))
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

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        result = engine.search(query="add function", top_k=5)
        assert len(result) >= 1
        assert "content" in result[0]

    def test_get_context_tool_logic(self, tmp_path):
        """Test get_context tool execution logic."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        result = engine.get_context([str(test_file)])
        assert len(result["unchanged"]) == 1

    def test_stats_tool_logic(self, tmp_path):
        """Test stats tool execution logic."""
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        result = engine.get_stats()

        assert "version" in result
        assert result["version"] == __version__
        assert "index" in result

    def test_annotate_tool_logic(self, tmp_path):
        """Test annotate tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        result = engine.annotate(str(test_file), "document", "Main module")
        assert "id" in result

    def test_get_annotations_tool_logic(self, tmp_path):
        """Test get_annotations tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])
        engine.annotate(str(test_file), "document", "Note", tags=["arch"])

        result = engine.get_annotations(target=str(test_file))
        assert len(result) == 1

    def test_delete_annotation_tool_logic(self, tmp_path):
        """Test delete_annotation tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])
        ann = engine.annotate(str(test_file), "document", "Delete me")

        result = engine.delete_annotation(ann["id"])
        assert result["deleted"] is True

    def test_map_tool_logic(self, tmp_path):
        """Test map tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        result = engine.get_map()
        assert result["total_documents"] == 1
        assert result["total_tokens"] > 0

    def test_history_tool_logic(self, tmp_path):
        """Test history tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])
        engine.detect_changes_and_update("s1", [str(test_file)], reason="test")

        result = engine.get_history()
        assert len(result) == 1
        assert result[0]["reason"] == "test"

    def test_detect_changes_with_reason(self, tmp_path):
        """Test detect_changes tool with reason parameter."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        result = engine.detect_changes_and_update(
            "s1", [str(test_file)], reason="Post-refactor check"
        )
        assert "unchanged" in result

        history = engine.get_history()
        assert history[0]["reason"] == "Post-refactor check"

    def test_update_annotation_tool_logic(self, tmp_path):
        """Test update_annotation tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])
        ann = engine.annotate(str(test_file), "document", "Old text")

        result = engine.update_annotation(ann["id"], content="New text")
        assert result["updated"] is True

    def test_search_annotations_tool_logic(self, tmp_path):
        """Test search_annotations tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])
        engine.annotate(str(test_file), "document", "Auth handler")

        result = engine.search_annotations("Auth")
        assert len(result) == 1

    def test_bulk_annotate_tool_logic(self, tmp_path):
        """Test bulk_annotate tool execution logic."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass")
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(f1), str(f2)])

        result = engine.bulk_annotate(
            [
                {"target": str(f1), "target_type": "document", "content": "A"},
                {"target": str(f2), "target_type": "document", "content": "B"},
            ]
        )
        assert len(result["created"]) == 2

    def test_prune_history_tool_logic(self, tmp_path):
        """Test prune_history tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])
        for i in range(3):
            engine.detect_changes_and_update(f"s{i}", [str(test_file)])

        result = engine.prune_history(max_entries=1)
        assert result["pruned"] == 2

    def test_remove_tool_logic(self, tmp_path):
        """Test remove tool execution logic."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        result = engine.remove_document(str(test_file))
        assert result["removed"] is True
        assert result["chunks_removed"] >= 1

        # Verify it's gone
        assert engine.storage.get_document(str(test_file)) is None
