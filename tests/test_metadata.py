"""Tests for ChunkForge metadata tools: annotate, map, history, and enhancements."""

import time

from chunkforge.engine import ChunkForge


def _make_engine(tmp_path):
    """Shared helper to create a ChunkForge engine in a temp directory."""
    return ChunkForge(storage_dir=str(tmp_path / "storage"))


class TestAnnotations:
    """Tests for annotation storage and retrieval."""

    def _index_file(self, tmp_path, engine, name="test.py", content="def hello(): pass"):
        test_file = tmp_path / name
        test_file.write_text(content)
        engine.index_documents([str(test_file)])
        return str(test_file)

    def test_store_and_retrieve_annotation(self, tmp_path):
        """Test basic annotation store and retrieve."""
        engine = _make_engine(tmp_path)
        path = self._index_file(tmp_path, engine)

        result = engine.annotate(path, "document", "This is the main module")
        assert "id" in result
        assert result["target"] == path

        annotations = engine.get_annotations(target=path)
        assert len(annotations) == 1
        assert annotations[0]["content"] == "This is the main module"

    def test_annotate_with_tags(self, tmp_path):
        """Test annotation with tags and tag filtering."""
        engine = _make_engine(tmp_path)
        path = self._index_file(tmp_path, engine)

        engine.annotate(path, "document", "Architecture note", tags=["architecture"])
        engine.annotate(path, "document", "Design note", tags=["design"])

        all_annotations = engine.get_annotations(target=path)
        assert len(all_annotations) == 2

        arch = engine.get_annotations(tags=["architecture"])
        assert len(arch) == 1
        assert arch[0]["content"] == "Architecture note"

    def test_annotate_invalid_target_type(self, tmp_path):
        """Test annotation with invalid target_type returns error."""
        engine = _make_engine(tmp_path)
        result = engine.annotate("anything", "invalid", "content")
        assert "error" in result

    def test_annotate_nonexistent_document(self, tmp_path):
        """Test annotation on nonexistent document returns error."""
        engine = _make_engine(tmp_path)
        result = engine.annotate("/no/such/file.py", "document", "note")
        assert "error" in result

    def test_annotate_chunk(self, tmp_path):
        """Test annotation on a chunk."""
        engine = _make_engine(tmp_path)
        path = self._index_file(tmp_path, engine)

        chunks = engine.storage.search_chunks(document_path=path)
        assert len(chunks) >= 1
        chunk_id = chunks[0]["chunk_id"]

        result = engine.annotate(chunk_id, "chunk", "Important chunk")
        assert "id" in result
        assert result["target_type"] == "chunk"

        annotations = engine.get_annotations(target=chunk_id, target_type="chunk")
        assert len(annotations) == 1

    def test_get_annotations_filter_by_target(self, tmp_path):
        """Test filtering annotations by target."""
        engine = _make_engine(tmp_path)
        path1 = self._index_file(tmp_path, engine, "a.py", "def a(): pass")
        path2 = self._index_file(tmp_path, engine, "b.py", "def b(): pass")

        engine.annotate(path1, "document", "Note on A")
        engine.annotate(path2, "document", "Note on B")

        a_annotations = engine.get_annotations(target=path1)
        assert len(a_annotations) == 1
        assert a_annotations[0]["content"] == "Note on A"

    def test_delete_annotation(self, tmp_path):
        """Test deleting an annotation."""
        engine = _make_engine(tmp_path)
        path = self._index_file(tmp_path, engine)

        result = engine.annotate(path, "document", "To be deleted")
        ann_id = result["id"]

        delete_result = engine.delete_annotation(ann_id)
        assert delete_result["deleted"] is True

        annotations = engine.get_annotations(target=path)
        assert len(annotations) == 0

        # Deleting again returns False
        delete_result2 = engine.delete_annotation(ann_id)
        assert delete_result2["deleted"] is False


    def test_update_annotation_content(self, tmp_path):
        """Test updating annotation content."""
        engine = _make_engine(tmp_path)
        path = self._index_file(tmp_path, engine)

        result = engine.annotate(path, "document", "Original note")
        ann_id = result["id"]

        update_result = engine.update_annotation(ann_id, content="Updated note")
        assert update_result["updated"] is True

        annotations = engine.get_annotations(target=path)
        assert annotations[0]["content"] == "Updated note"

    def test_update_annotation_tags(self, tmp_path):
        """Test updating annotation tags only."""
        engine = _make_engine(tmp_path)
        path = self._index_file(tmp_path, engine)

        result = engine.annotate(path, "document", "Note", tags=["old"])
        ann_id = result["id"]

        engine.update_annotation(ann_id, tags=["new", "updated"])

        annotations = engine.get_annotations(target=path)
        assert annotations[0]["tags"] == ["new", "updated"]
        assert annotations[0]["content"] == "Note"  # content unchanged

    def test_update_nonexistent_annotation(self, tmp_path):
        """Test updating a nonexistent annotation."""
        engine = _make_engine(tmp_path)
        result = engine.update_annotation(99999, content="nope")
        assert result["updated"] is False

    def test_search_annotations(self, tmp_path):
        """Test searching annotation content text."""
        engine = _make_engine(tmp_path)
        path = self._index_file(tmp_path, engine)

        engine.annotate(path, "document", "Handles user authentication")
        engine.annotate(path, "document", "Database connection pool")

        results = engine.search_annotations("authentication")
        assert len(results) == 1
        assert "authentication" in results[0]["content"]

        results_all = engine.search_annotations("a")
        assert len(results_all) == 2

    def test_bulk_annotate(self, tmp_path):
        """Test annotating multiple targets at once."""
        engine = _make_engine(tmp_path)
        path1 = self._index_file(tmp_path, engine, "a.py", "def a(): pass")
        path2 = self._index_file(tmp_path, engine, "b.py", "def b(): pass")

        result = engine.bulk_annotate([
            {"target": path1, "target_type": "document", "content": "Module A"},
            {"target": path2, "target_type": "document", "content": "Module B", "tags": ["core"]},
        ])
        assert len(result["created"]) == 2
        assert len(result["errors"]) == 0

    def test_bulk_annotate_with_errors(self, tmp_path):
        """Test bulk annotate with mix of valid and invalid targets."""
        engine = _make_engine(tmp_path)
        path = self._index_file(tmp_path, engine)

        result = engine.bulk_annotate([
            {"target": path, "target_type": "document", "content": "Valid"},
            {"target": "/no/such/file", "target_type": "document", "content": "Invalid"},
        ])
        assert len(result["created"]) == 1
        assert len(result["errors"]) == 1
        assert "error" in result["errors"][0]


class TestMap:
    """Tests for the map (project overview) tool."""

    def test_map_empty(self, tmp_path):
        """Test map with no indexed documents."""
        engine = _make_engine(tmp_path)
        result = engine.get_map()
        assert result["total_documents"] == 0
        assert result["total_tokens"] == 0
        assert result["documents"] == []

    def test_map_with_documents(self, tmp_path):
        """Test map with indexed documents."""
        engine = _make_engine(tmp_path)

        f1 = tmp_path / "a.py"
        f1.write_text("def foo(): pass")
        f2 = tmp_path / "b.py"
        f2.write_text("def bar(): pass")
        engine.index_documents([str(f1), str(f2)])

        result = engine.get_map()
        assert result["total_documents"] == 2
        assert result["total_tokens"] > 0
        paths = {d["path"] for d in result["documents"]}
        assert str(f1) in paths
        assert str(f2) in paths

    def test_map_includes_annotations(self, tmp_path):
        """Test that map includes document annotations."""
        engine = _make_engine(tmp_path)

        f = tmp_path / "main.py"
        f.write_text("def main(): pass")
        engine.index_documents([str(f)])

        engine.annotate(str(f), "document", "Entry point", tags=["architecture"])

        result = engine.get_map()
        doc = result["documents"][0]
        assert len(doc["annotations"]) == 1
        assert doc["annotations"][0]["content"] == "Entry point"
        assert doc["annotations"][0]["tags"] == ["architecture"]


class TestHistory:
    """Tests for the history tool."""

    def test_history_empty(self, tmp_path):
        """Test history with no changes recorded."""
        engine = _make_engine(tmp_path)
        result = engine.get_history()
        assert result == []

    def test_history_recorded_on_detect_changes(self, tmp_path):
        """Test that detect_changes_and_update records history."""
        engine = _make_engine(tmp_path)

        f = tmp_path / "test.py"
        f.write_text("def hello(): pass")
        engine.index_documents([str(f)])

        engine.detect_changes_and_update("session1", [str(f)])

        history = engine.get_history()
        assert len(history) == 1
        assert history[0]["session_id"] == "session1"
        assert "summary" in history[0]

    def test_history_with_reason(self, tmp_path):
        """Test that reason is stored in history."""
        engine = _make_engine(tmp_path)

        f = tmp_path / "test.py"
        f.write_text("def hello(): pass")
        engine.index_documents([str(f)])

        engine.detect_changes_and_update(
            "session1", [str(f)], reason="Checking after refactor"
        )

        history = engine.get_history()
        assert len(history) == 1
        assert history[0]["reason"] == "Checking after refactor"

    def test_history_filter_by_document(self, tmp_path):
        """Test filtering history by document path."""
        engine = _make_engine(tmp_path)

        f1 = tmp_path / "a.py"
        f1.write_text("def a(): pass")
        f2 = tmp_path / "b.py"
        f2.write_text("def b(): pass")
        engine.index_documents([str(f1), str(f2)])

        engine.detect_changes_and_update("s1", [str(f1)])
        engine.detect_changes_and_update("s2", [str(f2)])

        history_a = engine.get_history(document_path=str(f1))
        assert len(history_a) >= 1
        # All entries should mention the filtered document
        for entry in history_a:
            summary = entry["summary"]
            mentioned = (
                str(f1) in summary.get("unchanged", [])
                or any(
                    isinstance(m, dict) and m.get("path") == str(f1)
                    for m in summary.get("modified", []) + summary.get("new", [])
                )
            )
            assert mentioned

    def test_history_limit(self, tmp_path):
        """Test limiting history results."""
        engine = _make_engine(tmp_path)

        f = tmp_path / "test.py"
        f.write_text("def hello(): pass")
        engine.index_documents([str(f)])

        for i in range(5):
            engine.detect_changes_and_update(f"session{i}", [str(f)])

        history = engine.get_history(limit=3)
        assert len(history) == 3

    def test_prune_history_by_max_entries(self, tmp_path):
        """Test pruning history to max entries."""
        engine = _make_engine(tmp_path)

        f = tmp_path / "test.py"
        f.write_text("def hello(): pass")
        engine.index_documents([str(f)])

        for i in range(5):
            engine.detect_changes_and_update(f"session{i}", [str(f)])

        result = engine.prune_history(max_entries=2)
        assert result["pruned"] == 3

        history = engine.get_history()
        assert len(history) == 2

    def test_prune_history_by_age(self, tmp_path):
        """Test pruning history by age."""
        engine = _make_engine(tmp_path)

        # Directly insert an old entry via storage
        engine.storage._metadata_storage.record_change(
            summary={"unchanged": [], "modified": [], "new": [], "removed": []},
            session_id="old",
        )
        # Backdate the entry
        import sqlite3

        with sqlite3.connect(engine.storage.db_path) as conn:
            conn.execute(
                "UPDATE change_history SET timestamp = ? WHERE session_id = 'old'",
                (time.time() - 86400 * 30,),  # 30 days ago
            )
            conn.commit()

        # Add a fresh entry
        f = tmp_path / "test.py"
        f.write_text("def hello(): pass")
        engine.index_documents([str(f)])
        engine.detect_changes_and_update("new_session", [str(f)])

        result = engine.prune_history(max_age_seconds=86400)  # 1 day
        assert result["pruned"] == 1

        history = engine.get_history()
        assert len(history) == 1
        assert history[0]["session_id"] == "new_session"
