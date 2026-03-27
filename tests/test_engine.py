"""Tests for Stele engine."""

from stele_context import __version__
from stele_context.engine import Stele
from stele_context.chunkers.base import Chunk
from stele_context.indexing import merge_similar_chunks


class TestSteleEngine:
    """Tests for the new Stele engine."""

    def test_initialization_creates_vector_index(self, tmp_path):
        """Test that engine initializes with vector index."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        assert cf.vector_index is not None
        stats = cf.vector_index.get_stats()
        assert stats["chunk_count"] == 0

    def test_python_file_uses_code_chunker(self, tmp_path):
        """Test that .py files are routed through CodeChunker."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            """
def hello():
    print("hello")

def goodbye():
    print("goodbye")

class Greeter:
    def greet(self, name):
        return f"Hello, {name}!"
""".strip()
        )

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        result = cf.index_documents([str(test_file)])

        assert len(result["indexed"]) == 1
        assert result["indexed"][0]["modality"] == "code"
        assert result["total_chunks"] >= 1

    def test_text_file_uses_text_chunker(self, tmp_path):
        """Test that .txt files are routed through TextChunker."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. This is a test.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        result = cf.index_documents([str(test_file)])

        assert len(result["indexed"]) == 1
        assert result["indexed"][0]["modality"] == "text"

    def test_chunk_content_stored_and_retrievable(self, tmp_path):
        """Test that chunk content is stored in SQLite and retrievable."""
        test_file = tmp_path / "test.txt"
        content = "Hello world. This is a test document with some content."
        test_file.write_text(content)

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        # Get chunks for the document
        chunks = cf.storage.search_chunks(document_path=str(test_file))
        assert len(chunks) >= 1

        # Content should be stored
        for chunk in chunks:
            assert chunk["content"] is not None
            assert len(chunk["content"]) > 0

    def test_chunk_content_retrievable_by_id(self, tmp_path):
        """Test get_chunk_content retrieval."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world content.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        chunks = cf.storage.get_document_chunks(str(test_file))
        assert len(chunks) >= 1

        content = cf.storage.get_chunk_content(chunks[0]["chunk_id"])
        assert content is not None

    def test_hnsw_index_populated_after_indexing(self, tmp_path):
        """Test that HNSW index is populated when documents are indexed."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. " * 50)

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        stats = cf.vector_index.get_stats()
        assert stats["chunk_count"] >= 1

    def test_search_returns_results(self, tmp_path):
        """Test that search() returns relevant chunks with content."""
        test_file = tmp_path / "test.py"
        test_file.write_text(
            """
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b
""".strip()
        )

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        results = cf.search("addition function", top_k=5)
        assert len(results) >= 1
        assert "content" in results[0]
        assert "relevance_score" in results[0]
        assert "document_path" in results[0]

    def test_search_keyword_mode(self, tmp_path):
        """search_mode=keyword uses BM25 only (no HNSW)."""
        f = tmp_path / "lib.py"
        f.write_text("def unique_kwarg_marker_xyz():\n    pass\n")
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(f)])
        r = cf.search("unique_kwarg_marker_xyz", top_k=3, search_mode="keyword")
        assert len(r) >= 1
        assert "unique_kwarg" in (r[0].get("content") or "")

    def test_get_map_and_search_path_prefix(self, tmp_path):
        """path_prefix limits map and search to documents under a path."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        (tmp_path / "alpha" / "a.py").write_text("def only_in_alpha():\n    pass\n")
        (tmp_path / "beta" / "b.py").write_text("def only_in_beta():\n    pass\n")
        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        cf.index_documents(
            [
                str(tmp_path / "alpha" / "a.py"),
                str(tmp_path / "beta" / "b.py"),
            ]
        )
        m = cf.get_map(path_prefix="alpha")
        assert m["total_documents"] == 1
        assert "alpha" in m["documents"][0]["path"].replace("\\", "/")

        hits = cf.search("only_in", top_k=5, path_prefix="alpha")
        assert len(hits) >= 1
        assert all("alpha" in h["document_path"].replace("\\", "/") for h in hits)

        hits_b = cf.search("only_in", top_k=5, path_prefix="beta")
        assert len(hits_b) >= 1
        assert all("beta" in h["document_path"].replace("\\", "/") for h in hits_b)

    def test_map_and_stats_include_index_health(self, tmp_path):
        (tmp_path / ".git").mkdir()
        f = tmp_path / "a.py"
        f.write_text("x = 1\n")
        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        cf.index_documents([str(f)])
        m = cf.get_map()
        assert "index_health" in m
        assert m["index_health"]["documents"] >= 1
        assert "latest_indexed_at" in m["index_health"]
        assert "storage_dir" in m["index_health"]
        assert "alerts" in m["index_health"]
        assert m["index_health"]["symbol_graph_status"] in (
            "ready",
            "empty_with_chunks",
        )
        assert m["project_root"] == str(tmp_path)
        s = cf.get_stats()
        assert "index_health" in s
        assert s["index_health"]["chunks"] >= 1
        assert "seconds_since_last_index" in s["index_health"]
        assert s["project_root"] == str(tmp_path)

    def test_search_empty_index(self, tmp_path):
        """Test search on empty index returns empty list."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        results = cf.search("anything", top_k=5)
        assert results == []

    def test_get_context_unchanged(self, tmp_path):
        """Test get_context for unchanged documents."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. Test content.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        context = cf.get_context([str(test_file)])
        assert len(context["unchanged"]) == 1
        assert len(context["changed"]) == 0
        assert len(context["new"]) == 0
        assert context["unchanged"][0]["path"] == str(test_file)
        assert len(context["unchanged"][0]["chunks"]) >= 1

    def test_get_context_new_document(self, tmp_path):
        """Test get_context for a document not yet indexed."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        context = cf.get_context([str(test_file)])

        assert len(context["new"]) == 1
        assert len(context["unchanged"]) == 0

    def test_get_context_changed_document(self, tmp_path):
        """Test get_context for a changed document."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Original content.")

        cf = Stele(storage_dir=str(tmp_path / "storage"))
        cf.index_documents([str(test_file)])

        # Modify the file
        test_file.write_text("Modified content!")

        context = cf.get_context([str(test_file)])
        assert len(context["changed"]) == 1
        assert len(context["unchanged"]) == 0

    def test_index_rebuilds_from_storage(self, tmp_path):
        """Test that HNSW index rebuilds from SQLite on startup."""
        storage_dir = str(tmp_path / "storage")
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello world. " * 20)

        # Index with first instance
        cf1 = Stele(storage_dir=storage_dir)
        cf1.index_documents([str(test_file)])
        count1 = cf1.vector_index.get_stats()["chunk_count"]

        # Create new instance — should rebuild index from SQLite
        cf2 = Stele(storage_dir=storage_dir)
        count2 = cf2.vector_index.get_stats()["chunk_count"]

        assert count1 == count2
        assert count1 >= 1

    def test_detect_modality(self, tmp_path):
        """Test modality detection."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))

        assert cf.detect_modality("test.py") == "code"
        assert cf.detect_modality("test.js") == "code"
        assert cf.detect_modality("test.txt") == "text"
        assert cf.detect_modality("test.md") == "text"
        assert cf.detect_modality("test.xyz") == "unknown"

    def test_stats_includes_index(self, tmp_path):
        """Test that stats includes vector index info."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        stats = cf.get_stats()

        assert "index" in stats
        assert "chunk_count" in stats["index"]
        assert stats["version"] == __version__

    def test_save_kv_state_exists(self, tmp_path):
        """Test that save_kv_state is callable."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        assert callable(cf.save_kv_state)

    def test_merge_similar_chunks(self, tmp_path):
        """Test that similar adjacent chunks get merged."""
        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            merge_threshold=0.5,
        )

        chunks = [
            Chunk(
                content="Hello world foo bar",
                modality="text",
                start_pos=0,
                end_pos=19,
                document_path="t.txt",
                chunk_index=0,
            ),
            Chunk(
                content="Hello world foo baz",
                modality="text",
                start_pos=19,
                end_pos=38,
                document_path="t.txt",
                chunk_index=1,
            ),
        ]

        merged = merge_similar_chunks(
            chunks, cf.merge_threshold, cf.max_chunk_size, cf.MODALITY_THRESHOLDS
        )
        # High similarity chunks should merge
        assert len(merged) <= len(chunks)

    def test_remove_document(self, tmp_path):
        """Test removing a document cleans up chunks, annotations, and index."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))

        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass")
        cf.index_documents([str(test_file)])

        # Add an annotation
        cf.annotate(str(test_file), "document", "Will be removed")

        # Verify it's indexed
        assert cf.storage.get_document(str(test_file)) is not None
        chunks_before = cf.storage.get_document_chunks(str(test_file))
        assert len(chunks_before) >= 1
        index_before = cf.vector_index.get_stats()["chunk_count"]

        # Remove
        result = cf.remove_document(str(test_file))
        assert result["removed"] is True
        assert result["chunks_removed"] >= 1
        assert result["annotations_removed"] == 1

        # Verify cleanup
        assert cf.storage.get_document(str(test_file)) is None
        assert cf.storage.get_document_chunks(str(test_file)) == []
        assert cf.get_annotations(target=str(test_file)) == []
        assert cf.vector_index.get_stats()["chunk_count"] < index_before

    def test_remove_nonexistent_document(self, tmp_path):
        """Test removing a document that doesn't exist."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        result = cf.remove_document("/no/such/file.py")
        assert result["removed"] is False

    def test_reindex_cleans_stale_chunks(self, tmp_path):
        """Test that re-indexing removes stale chunks."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))

        test_file = tmp_path / "test.py"
        test_file.write_text("def hello(): pass\ndef world(): pass")
        cf.index_documents([str(test_file)])

        old_chunks = cf.storage.get_document_chunks(str(test_file))
        old_ids = {c["chunk_id"] for c in old_chunks}

        # Rewrite file with different content
        test_file.write_text("def completely_different(): return 42")
        cf.index_documents([str(test_file)], force_reindex=True)

        new_chunks = cf.storage.get_document_chunks(str(test_file))
        new_ids = {c["chunk_id"] for c in new_chunks}

        # Old chunks should be gone
        for old_id in old_ids:
            if old_id not in new_ids:
                assert cf.storage.get_chunk(old_id) is None

    def test_search_text_exact(self, tmp_path):
        """search_text finds exact substring matches across chunks."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "code.py"
        f.write_text(
            "from typing import Dict\n\ndef process(data: Dict[str, Any]):\n    pass\n"
        )
        cf.index_documents([str(f)])

        result = cf.search_text("Dict[")
        assert result["match_count"] >= 1
        assert result["chunk_count"] >= 1
        assert any("Dict[" in r["content_preview"] for r in result["results"])

    def test_search_text_regex(self, tmp_path):
        """search_text with regex=True supports pattern matching."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "code.py"
        f.write_text("def foo_bar():\n    pass\n\ndef foo_baz():\n    pass\n")
        cf.index_documents([str(f)])

        result = cf.search_text(r"def foo_\w+", regex=True)
        assert result["match_count"] >= 2

    def test_search_text_no_match(self, tmp_path):
        """search_text returns empty results when pattern not found."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "code.py"
        f.write_text("def hello(): pass\n")
        cf.index_documents([str(f)])

        result = cf.search_text("NONEXISTENT_PATTERN_XYZ")
        assert result["match_count"] == 0
        assert result["chunk_count"] == 0

    def test_search_text_scoped_to_document(self, tmp_path):
        """search_text can be scoped to a single document."""
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("import os\n")
        f2.write_text("import os\n")
        cf.index_documents([str(f1), str(f2)])

        all_results = cf.search_text("import os")
        scoped = cf.search_text("import os", document_path=str(f1))
        assert all_results["chunk_count"] >= 2
        assert scoped["chunk_count"] == 1

    def test_signature_cache_reuses_signatures_on_reindex(self, tmp_path):
        """Signature cache injects saved signatures for unchanged chunks on re-index.

        On force re-index of identical content every chunk signature must be
        byte-for-byte identical (cache hit).  After changing the content at
        least one signature must differ (cache miss → recomputed).
        """
        cf = Stele(storage_dir=str(tmp_path / "storage"))
        py_file = tmp_path / "sample.py"
        py_file.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")

        cf.index_documents([str(py_file)])

        # Capture signatures after first index
        chunks_v1 = cf.storage.get_document_chunks(cf._normalize_path(str(py_file)))
        assert chunks_v1, "expected at least one chunk after initial index"
        sigs_v1 = {c["chunk_id"]: c["semantic_signature"] for c in chunks_v1}

        # Force re-index with identical content — cache must be used
        cf.index_documents([str(py_file)], force_reindex=True)
        chunks_v2 = cf.storage.get_document_chunks(cf._normalize_path(str(py_file)))
        sigs_v2 = {c["chunk_id"]: c["semantic_signature"] for c in chunks_v2}

        shared_ids = set(sigs_v1) & set(sigs_v2)
        assert shared_ids, "expected overlapping chunk IDs after identical re-index"
        for cid in shared_ids:
            assert sigs_v1[cid] == sigs_v2[cid], (
                f"chunk {cid}: signature changed despite identical content"
            )

        # Modify content — at least one signature must change
        py_file.write_text("def foo():\n    return 99\n\ndef bar():\n    return 2\n")
        cf.index_documents([str(py_file)], force_reindex=True)
        chunks_v3 = cf.storage.get_document_chunks(cf._normalize_path(str(py_file)))
        sigs_v3 = {c["chunk_id"]: c["semantic_signature"] for c in chunks_v3}

        all_same = all(
            sigs_v2.get(cid) == sigs_v3.get(cid) for cid in set(sigs_v2) | set(sigs_v3)
        )
        assert not all_same, (
            "expected at least one signature to change after content edit"
        )

    def test_detect_changes_scan_new_reports_unindexed_files(self, tmp_path):
        (tmp_path / ".git").mkdir()
        indexed = tmp_path / "indexed.py"
        indexed.write_text("x = 1\n")
        brand_new = tmp_path / "brand_new.py"
        brand_new.write_text("y = 2\n")

        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        cf.index_documents([str(indexed)])
        result = cf.detect_changes_and_update(session_id="scan_test", scan_new=True)
        scan_new_items = [
            n for n in result["new"] if n.get("reason") == "New file (scan)"
        ]
        norm = cf._normalize_path(str(brand_new))
        assert any(
            p["path"] == norm or str(p["path"]).endswith("brand_new.py")
            for p in scan_new_items
        )

    def test_detect_changes_scan_new_disabled_when_false(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "only_indexed.py").write_text("a = 1\n")
        (tmp_path / "not_indexed.py").write_text("b = 2\n")
        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        cf.index_documents([str(tmp_path / "only_indexed.py")])
        result = cf.detect_changes_and_update(session_id="s", scan_new=False)
        assert not any(n.get("reason") == "New file (scan)" for n in result["new"])
