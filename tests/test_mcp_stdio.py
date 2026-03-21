"""Tests for Stele MCP stdio server."""

import json
import threading

import pytest

from stele_context import __version__
from stele_context.engine import Stele
from stele_context.mcp_stdio import HAS_MCP


class TestMCPStdioServer:
    """Tests for MCP stdio server tool registration and execution."""

    def test_has_mcp_flag(self):
        """Test HAS_MCP flag is defined."""
        assert isinstance(HAS_MCP, bool)

    def test_create_engine(self, tmp_path):
        """Test engine creation helper."""
        from stele_context.mcp_stdio import _create_engine

        engine = _create_engine(str(tmp_path / "storage"))
        assert isinstance(engine, Stele)

    @pytest.mark.skipif(not HAS_MCP, reason="MCP SDK not installed")
    def test_create_server(self, tmp_path):
        """Test server creation with tool registration."""
        from stele_context.mcp_stdio import create_server

        server = create_server(str(tmp_path / "storage"))
        assert server is not None

    def test_main_without_mcp_exits(self, tmp_path):
        """Test that main() exits gracefully without MCP SDK."""
        from stele_context import mcp_stdio

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


class TestMCPResourceLogic:
    """Tests for MCP resource handler backing logic."""

    def test_resource_documents_list(self, tmp_path):
        """Test stele-context://documents resource returns indexed document data."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass")
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(f1), str(f2)])

        result = engine.get_map()
        assert result["total_documents"] == 2
        assert len(result["documents"]) == 2
        # Verify JSON-serializable (resource handler does json.dumps)
        json.dumps(result, default=str)

    def test_resource_document_chunks(self, tmp_path):
        """Test stele-context://document/{path} resource returns enriched chunks."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass\ndef world(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        # Same logic as read_resource handler
        chunks_meta = engine.storage.get_document_chunks(str(test_file))
        assert len(chunks_meta) >= 1

        enriched = []
        for meta in chunks_meta:
            chunk = engine.storage.get_chunk(meta["chunk_id"])
            if chunk:
                enriched.append(chunk)

        assert len(enriched) >= 1
        assert "content" in enriched[0]
        assert "chunk_id" in enriched[0]
        # Verify JSON-serializable
        json.dumps(enriched, default=str)

    def test_resource_unknown_document(self, tmp_path):
        """Test reading chunks for nonexistent document returns empty."""
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        chunks = engine.storage.get_document_chunks("nonexistent.py")
        assert chunks == []

    @pytest.mark.skipif(not HAS_MCP, reason="MCP SDK not installed")
    def test_server_registers_resources(self, tmp_path):
        """Test create_server registers resource and template handlers."""
        from stele_context.mcp_stdio import create_server

        bundle = create_server(str(tmp_path / "storage"))
        assert bundle is not None
        # Server should have resource handlers registered
        assert bundle.server is not None


class TestMCPConcurrency:
    """Tests for concurrent access through the engine layer."""

    def test_concurrent_reads(self, tmp_path):
        """Test multiple threads can read simultaneously without errors."""
        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass\n" * 10)

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(test_file)])

        errors = []
        results = []

        def do_search(query):
            try:
                r = engine.search(query, top_k=3)
                results.append(r)
            except Exception as e:
                errors.append(e)

        def do_stats():
            try:
                r = engine.get_stats()
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = []
        for q in ["hello", "function", "pass", "def"]:
            threads.append(threading.Thread(target=do_search, args=(q,)))
        for _ in range(4):
            threads.append(threading.Thread(target=do_stats))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Concurrent reads produced errors: {errors}"
        assert len(results) == 8

    def test_concurrent_read_write(self, tmp_path):
        """Test readers don't block while writer holds lock."""
        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass")
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass")

        engine = Stele(storage_dir=str(tmp_path / "storage"))
        engine.index_documents([str(f1)])

        errors = []

        def do_index():
            try:
                engine.index_documents([str(f2)])
            except Exception as e:
                errors.append(e)

        def do_search():
            try:
                engine.search("function", top_k=3)
            except Exception as e:
                errors.append(e)

        writer = threading.Thread(target=do_index)
        readers = [threading.Thread(target=do_search) for _ in range(4)]

        writer.start()
        for r in readers:
            r.start()

        writer.join(timeout=10)
        for r in readers:
            r.join(timeout=10)

        assert not errors, f"Concurrent read/write produced errors: {errors}"
