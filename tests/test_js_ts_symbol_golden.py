"""JS/TS tier-A symbol golden cases: alias, re-export, destructured export."""

from __future__ import annotations

from pathlib import Path

import pytest

from stele_context.engine import Stele

FIXTURES = Path(__file__).parent / "fixtures" / "js_symbols"


@pytest.fixture
def js_engine(tmp_path):
    engine = Stele(storage_dir=str(tmp_path / "storage"), enable_coordination=False)
    paths = sorted(str(p) for p in FIXTURES.glob("*") if p.suffix in {".js", ".ts"})
    assert paths, "golden fixtures missing"
    # Copy into tmp so paths are worktree-local and writable isolation
    local = tmp_path / "src"
    local.mkdir()
    indexed = []
    for p in paths:
        dest = local / Path(p).name
        dest.write_text(Path(p).read_text())
        indexed.append(str(dest))
    engine.index_documents(indexed)
    return engine


def _def_names(result: dict) -> set[str]:
    return {d.get("name") or d.get("symbol") for d in result.get("definitions", [])}


def _all_names(result: dict) -> set[str]:
    names = set()
    for key in ("definitions", "references"):
        for row in result.get(key) or []:
            n = row.get("name") or row.get("symbol")
            if n:
                names.add(n)
    return names


class TestJsTsSymbolGolden:
    def test_class_definition_semantic_navigator(self, js_engine):
        defs = js_engine.find_definition("SemanticCodeNavigatorService")
        # find_definition may return list or dict depending on version
        if isinstance(defs, dict):
            rows = defs.get("definitions") or defs.get("results") or []
            if not rows and defs.get("document_path"):
                rows = [defs]
        else:
            rows = defs or []
        assert rows, (
            f"expected definition for SemanticCodeNavigatorService, got {defs!r}"
        )

    def test_const_alias_code_navigator(self, js_engine):
        refs = js_engine.find_references("CodeNavigator")
        assert (
            refs.get("verdict")
            in {
                "referenced",
                "unreferenced",
                "external",
            }
            or refs.get("definitions")
            or refs.get("references")
        )
        # Definitions or references should mention the alias file
        all_paths = [
            r.get("document_path", "")
            for bucket in ("definitions", "references")
            for r in (refs.get(bucket) or [])
        ]
        assert any("aliases" in p for p in all_paths), (
            f"CodeNavigator not resolved to aliases.js: {refs}"
        )

    def test_destructured_export_dynamic_dispatcher(self, js_engine):
        defs = js_engine.find_definition("DynamicDispatcher")
        if isinstance(defs, dict):
            rows = defs.get("definitions") or defs.get("results") or []
            if not rows and defs.get("document_path"):
                rows = [defs]
        else:
            rows = defs or []
        assert rows, f"DynamicDispatcher definition missing: {defs!r}"
        paths = [r.get("document_path", "") for r in rows]
        assert any("dispatcher" in p for p in paths)

    def test_export_as_alias_typescript(self, js_engine):
        refs = js_engine.find_references("LineageSnapshot")
        paths = [
            r.get("document_path", "")
            for bucket in ("definitions", "references")
            for r in (refs.get(bucket) or [])
        ]
        assert any("reexport" in p for p in paths), (
            f"LineageSnapshot (export as) not found: {refs}"
        )

    def test_exports_dot_alias(self, js_engine):
        refs = js_engine.find_references("RecipeSnapshot")
        paths = [
            r.get("document_path", "")
            for bucket in ("definitions", "references")
            for r in (refs.get(bucket) or [])
        ]
        # Present in cjs_exports.js and/or reexport.ts
        assert paths, f"RecipeSnapshot not found at all: {refs}"
        assert any("cjs_exports" in p or "reexport" in p for p in paths)

    def test_find_references_original_class_still_works(self, js_engine):
        refs = js_engine.find_references("SemanticCodeNavigatorService")
        paths = [
            r.get("document_path", "")
            for bucket in ("definitions", "references")
            for r in (refs.get(bucket) or [])
        ]
        assert any("aliases" in p for p in paths)
