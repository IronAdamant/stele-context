"""Golden-style search checks — extend as RecipeLab / real-project queries land."""

from __future__ import annotations

import pytest

from stele_context.engine import Stele


@pytest.mark.search_regression
class TestSearchKeywordRegression:
    """BM25-only mode should surface chunks that mention query-specific phrases."""

    def test_keyword_mode_surfaces_rare_phrase_file(self, tmp_path):
        (tmp_path / ".git").mkdir()
        generic = tmp_path / "generic.js"
        generic.write_text(
            "function handleRequest(req, res) {\n"
            "  return res.json({ ok: true });\n"
            "}\n" * 20
        )
        feature = tmp_path / "mcp_triangulation.js"
        feature.write_text(
            "// MCP triangulation readiness gate — feature under test\n"
            "export function mountReadinessGate() { return true; }\n"
        )

        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        cf.index_documents([str(generic), str(feature)])

        q = "MCP triangulation readiness gate"
        kw = cf.search(q, top_k=5, search_mode="keyword")
        paths_kw = [r["document_path"] for r in kw]
        assert any(
            "mcp_triangulation" in p or p.endswith("mcp_triangulation.js")
            for p in paths_kw
        ), f"expected feature file in keyword results, got {paths_kw}"

    def test_hybrid_returns_hits_for_indexed_content(self, tmp_path):
        """Sanity: opt-in hybrid search still returns chunks from a tiny index."""
        (tmp_path / ".git").mkdir()
        f = tmp_path / "note.txt"
        f.write_text("authentication session cookie handling\n" * 5)
        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        cf.index_documents([str(f)])
        r = cf.search("authentication session", top_k=3, search_mode="hybrid")
        assert len(r) >= 1
        assert "authentication" in (r[0].get("content") or "").lower()

    def test_empty_index_keyword_search(self, tmp_path):
        (tmp_path / ".git").mkdir()
        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        assert cf.search("anything", search_mode="keyword") == []

    def test_keyword_ranks_multiple_matches(self, tmp_path):
        """BM25 should prefer chunks that repeat query terms."""
        (tmp_path / ".git").mkdir()
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("hello\n")
        b.write_text(
            "recipe scaffold topology triangulation recipe scaffold topology\n" * 3
        )
        cf = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        cf.index_documents([str(a), str(b)])
        r = cf.search(
            "recipe scaffold topology triangulation",
            top_k=2,
            search_mode="keyword",
        )
        assert len(r) >= 1
        top_path = r[0]["document_path"]
        assert "b.txt" in top_path or top_path.endswith("b.txt")
