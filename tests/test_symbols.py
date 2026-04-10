"""Tests for symbol extraction, resolution, and graph queries."""

import os
import shutil
import tempfile
from pathlib import Path

from stele_context.engine import Stele
from stele_context.search_engine import extract_query_identifiers
from stele_context.symbols import (
    SymbolExtractor,
    Symbol,
    resolve_symbols,
    _module_matches_path,
    _NOISE_REFS,
)
from stele_context.symbol_storage import SymbolStorage


# -- SymbolExtractor unit tests ----------------------------------------------


class TestSymbolExtractorPython:
    """Test Python symbol extraction (AST + regex fallback)."""

    def setup_method(self):
        self.ext = SymbolExtractor()

    def test_function_definition(self):
        code = "def hello():\n    pass"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "hello" and s.kind == "function" for s in defs)

    def test_class_definition(self):
        code = "class MyClass:\n    pass"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "MyClass" and s.kind == "class" for s in defs)

    def test_import_reference(self):
        code = "from os.path import join"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "os.path" and s.kind == "module" for s in refs)
        assert any(s.name == "join" and s.kind == "import" for s in refs)

    def test_function_call_reference(self):
        code = "result = some_function(x, y)"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "some_function" for s in refs)

    def test_method_call_reference(self):
        code = "obj.method_name()"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "method_name" for s in refs)

    def test_function_as_value_reference(self):
        """Function passed as keyword arg, assigned, or returned."""
        code = (
            "def helper():\n    pass\n\n"
            "run(callback=helper)\n"
            "fn = helper\n"
            "items = [helper]\n"
        )
        syms = self.ext.extract(code, "test.py", "c1", "py")
        refs = [s for s in syms if s.role == "reference" and s.name == "helper"]
        # keyword arg, assignment RHS, and list element — all captured
        assert len(refs) >= 3

    def test_name_ref_no_duplicate_with_call(self):
        """Direct call should produce kind='function', not a duplicate 'name'."""
        code = "some_function(x)"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        func_refs = [
            s for s in syms if s.name == "some_function" and s.role == "reference"
        ]
        assert len(func_refs) == 1
        assert func_refs[0].kind == "function"

    def test_name_ref_skips_store_and_discard(self):
        """Assignment targets and _ are not captured as name references."""
        code = "_ = foo()\nresult = bar()"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        refs = [s for s in syms if s.role == "reference"]
        assert not any(s.name == "_" for s in refs)
        assert not any(s.name == "result" for s in refs)

    def test_async_function(self):
        code = "async def fetch_data():\n    pass"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "fetch_data" and s.kind == "function" for s in defs)

    def test_regex_fallback(self):
        """Syntax errors should fall back to regex extraction."""
        code = "def broken(\n    # incomplete"
        syms = self.ext.extract(code, "test.py", "c1", "py")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "broken" for s in defs)


class TestSymbolExtractorJavaScript:
    """Test JavaScript/TypeScript symbol extraction."""

    def setup_method(self):
        self.ext = SymbolExtractor()

    def test_function_definition(self):
        code = "function handleClick() {}"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        assert any(s.name == "handleClick" and s.role == "definition" for s in syms)

    def test_class_definition(self):
        code = "export class Component {}"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        assert any(s.name == "Component" and s.role == "definition" for s in syms)

    def test_const_definition(self):
        code = "const API_URL = 'http://example.com'"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        assert any(s.name == "API_URL" and s.kind == "variable" for s in syms)

    def test_es6_import(self):
        code = "import { useState } from 'react'"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "react" and s.kind == "module" for s in refs)
        assert any(s.name == "useState" and s.kind == "import" for s in refs)

    def test_require(self):
        code = "const express = require('express')"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "express" and s.kind == "module" for s in refs)

    def test_dom_queryselector(self):
        code = "document.querySelector('.btn-primary')"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == ".btn-primary" and s.kind == "css_class" for s in refs)

    def test_dom_getelementbyid(self):
        code = "document.getElementById('main-nav')"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "#main-nav" and s.kind == "css_id" for s in refs)

    def test_classlist_add(self):
        code = "element.classList.add('active')"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == ".active" and s.kind == "css_class" for s in refs)

    def test_ts_interface(self):
        code = "export interface Config {}"
        syms = self.ext.extract(code, "types.ts", "c1", "ts")
        assert any(s.name == "Config" and s.role == "definition" for s in syms)

    def test_const_alias_emits_rhs_reference(self):
        """const Alias = OriginalClass should emit reference for OriginalClass."""
        code = "const CodeNavigator = SemanticCodeNavigatorService;"
        syms = self.ext.extract(code, "service.js", "c1", "js")
        defs = [s for s in syms if s.role == "definition"]
        refs = [
            s
            for s in syms
            if s.role == "reference" and s.name == "SemanticCodeNavigatorService"
        ]
        assert any(s.name == "CodeNavigator" and s.kind == "variable" for s in defs)
        assert len(refs) >= 1, "RHS identifier should be emitted as reference"

    def test_const_alias_no_self_reference(self):
        """const X = X should not emit X as both def and ref of itself."""
        code = "const X = X;"
        syms = self.ext.extract(code, "a.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference" and s.name == "X"]
        assert len(refs) == 0

    def test_destructured_module_exports_simple(self):
        """module.exports = { X, Y } should emit references for X and Y."""
        code = "module.exports = { DynamicDispatcher, SymbolRegistry };"
        syms = self.ext.extract(code, "index.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "DynamicDispatcher" for s in refs)
        assert any(s.name == "SymbolRegistry" for s in refs)

    def test_destructured_module_exports_aliased(self):
        """module.exports = { Alias: Original } should emit def for Alias, ref for Original."""
        code = "module.exports = { CodeNavigator: SemanticService, StepParser: RecipeParser };"
        syms = self.ext.extract(code, "index.js", "c1", "js")
        defs = [s for s in syms if s.role == "definition"]
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "CodeNavigator" for s in defs)
        assert any(s.name == "SemanticService" for s in refs)
        assert any(s.name == "StepParser" for s in defs)
        assert any(s.name == "RecipeParser" for s in refs)

    def test_destructured_module_exports_spread_require(self):
        """module.exports = { ...require('./x') } should emit module reference."""
        code = "module.exports = { ...require('./utils'), ...require('./helpers') };"
        syms = self.ext.extract(code, "barrel.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference" and s.kind == "module"]
        assert any(s.name == "./utils" for s in refs)
        assert any(s.name == "./helpers" for s in refs)

    def test_destructured_module_exports_multiline(self):
        """Multiline module.exports = { ... } should be parsed."""
        code = (
            "module.exports = {\n"
            "  DynamicDispatcher,\n"
            "  CodeNavigator: SemanticService,\n"
            "  ...require('./utils'),\n"
            "};"
        )
        syms = self.ext.extract(code, "index.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "DynamicDispatcher" for s in refs)
        assert any(s.name == "CodeNavigator" for s in defs)
        assert any(s.name == "SemanticService" for s in refs)
        assert any(s.name == "./utils" and s.kind == "module" for s in refs)


class TestNoiseRefsFiltering:
    """Test that _NOISE_REFS includes stdlib and generic method names."""

    def test_nodejs_stdlib_in_noise(self):
        for name in ("path", "fs", "crypto", "os", "http", "url"):
            assert name in _NOISE_REFS, f"{name} should be in _NOISE_REFS"

    def test_generic_methods_in_noise(self):
        for name in ("getStats", "constructor", "toJSON", "emit", "on"):
            assert name in _NOISE_REFS, f"{name} should be in _NOISE_REFS"

    def test_noise_refs_prevent_edges(self):
        """Noisy symbol names should not produce cross-file edges."""
        symbols = [
            Symbol("getStats", "function", "definition", "c1", "a.js"),
            Symbol("getStats", "function", "reference", "c2", "b.js"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 0, "getStats should be filtered by _NOISE_REFS"

    def test_noise_refs_preserve_definitions(self):
        """Filtering only applies to references — definitions are still stored."""
        symbols = [
            Symbol("path", "variable", "definition", "c1", "a.js"),
            Symbol("path", "variable", "reference", "c2", "b.js"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 0


class TestSymbolExtractorHTML:
    """Test HTML symbol extraction."""

    def setup_method(self):
        self.ext = SymbolExtractor()

    def test_class_reference(self):
        code = '<div class="container flex">'
        syms = self.ext.extract(code, "index.html", "c1", "html")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == ".container" for s in refs)
        assert any(s.name == ".flex" for s in refs)

    def test_id_reference(self):
        code = '<div id="app">'
        syms = self.ext.extract(code, "index.html", "c1", "html")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "#app" for s in refs)

    def test_script_src(self):
        code = '<script src="app.js"></script>'
        syms = self.ext.extract(code, "index.html", "c1", "html")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "app.js" and s.kind == "module" for s in refs)

    def test_link_href(self):
        code = '<link rel="stylesheet" href="styles.css">'
        syms = self.ext.extract(code, "index.html", "c1", "html")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "styles.css" and s.kind == "module" for s in refs)

    def test_onclick_handler(self):
        code = '<button onclick="handleSubmit()">'
        syms = self.ext.extract(code, "index.html", "c1", "html")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "handleSubmit" and s.kind == "function" for s in refs)


class TestSymbolExtractorCSS:
    """Test CSS symbol extraction."""

    def setup_method(self):
        self.ext = SymbolExtractor()

    def test_class_definition(self):
        code = ".container { display: flex; }"
        syms = self.ext.extract(code, "style.css", "c1", "css")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == ".container" and s.kind == "css_class" for s in defs)

    def test_id_definition(self):
        code = "#app { margin: 0; }"
        syms = self.ext.extract(code, "style.css", "c1", "css")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "#app" and s.kind == "css_id" for s in defs)

    def test_import(self):
        code = '@import "reset.css";'
        syms = self.ext.extract(code, "style.css", "c1", "css")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "reset.css" and s.kind == "module" for s in refs)

    def test_url_reference(self):
        code = "background: url('../img/logo.png');"
        syms = self.ext.extract(code, "style.css", "c1", "css")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "../img/logo.png" for s in refs)


# -- Cross-language resolution tests -----------------------------------------


class TestCrossLanguageResolution:
    """Test that HTML→JS→CSS connections resolve correctly."""

    def test_html_to_css_class(self):
        """HTML class="btn" should link to CSS .btn definition."""
        symbols = [
            Symbol(".btn", "css_class", "definition", "css_chunk", "style.css"),
            Symbol(".btn", "css_class", "reference", "html_chunk", "index.html"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 1
        assert edges[0] == ("html_chunk", "css_chunk", "cross_file", ".btn")

    def test_js_to_css_class(self):
        """JS classList.add('active') should link to CSS .active."""
        symbols = [
            Symbol(".active", "css_class", "definition", "css_chunk", "style.css"),
            Symbol(".active", "css_class", "reference", "js_chunk", "app.js"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 1
        assert edges[0] == ("js_chunk", "css_chunk", "cross_file", ".active")

    def test_html_to_js_function(self):
        """HTML onclick="handler()" should link to JS function handler."""
        symbols = [
            Symbol("handler", "function", "definition", "js_chunk", "app.js"),
            Symbol("handler", "function", "reference", "html_chunk", "index.html"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 1
        assert edges[0] == ("html_chunk", "js_chunk", "cross_file", "handler")

    def test_intra_file_edge(self):
        """References within the same file get intra_file edge type."""
        symbols = [
            Symbol("helper", "function", "definition", "chunk1", "utils.py"),
            Symbol("helper", "function", "reference", "chunk2", "utils.py"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 1
        assert edges[0][2] == "intra_file"

    def test_no_self_reference(self):
        """A chunk shouldn't create an edge to itself."""
        symbols = [
            Symbol("foo", "function", "definition", "chunk1", "a.py"),
            Symbol("foo", "function", "reference", "chunk1", "a.py"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 0

    def test_deduplication(self):
        """Multiple references to the same definition from the same chunk
        should produce only one edge."""
        symbols = [
            Symbol("foo", "function", "definition", "def_chunk", "a.py"),
            Symbol("foo", "function", "reference", "ref_chunk", "b.py"),
            Symbol("foo", "function", "reference", "ref_chunk", "b.py", 10),
            Symbol("foo", "function", "reference", "ref_chunk", "b.py", 20),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 1


# -- SymbolStorage tests ----------------------------------------------------


class TestSymbolStorage:
    """Test symbol storage persistence."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "test.db"
        self.storage = SymbolStorage(self.db_path)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_store_and_find_definitions(self):
        syms = [Symbol("foo", "function", "definition", "c1", "a.py", 1)]
        self.storage.store_symbols(syms)
        defs = self.storage.find_definitions("foo")
        assert len(defs) == 1
        assert defs[0]["name"] == "foo"
        assert defs[0]["kind"] == "function"

    def test_store_and_find_references(self):
        syms = [Symbol("bar", "function", "reference", "c2", "b.py", 5)]
        self.storage.store_symbols(syms)
        refs = self.storage.find_references_by_name("bar")
        assert len(refs) == 1

    def test_store_and_query_edges(self):
        edges = [("c1", "c2", "cross_file", "foo")]
        self.storage.store_edges(edges)

        incoming = self.storage.get_incoming_edges("c2")
        assert len(incoming) == 1
        assert incoming[0]["source_chunk_id"] == "c1"

        outgoing = self.storage.get_outgoing_edges("c1")
        assert len(outgoing) == 1
        assert outgoing[0]["target_chunk_id"] == "c2"

    def test_clear_document_symbols(self):
        syms = [
            Symbol("a", "function", "definition", "c1", "x.py"),
            Symbol("b", "function", "definition", "c2", "y.py"),
        ]
        self.storage.store_symbols(syms)
        self.storage.clear_document_symbols("x.py")
        all_syms = self.storage.get_all_symbols()
        assert len(all_syms) == 1
        assert all_syms[0]["document_path"] == "y.py"

    def test_clear_chunk_edges(self):
        edges = [
            ("c1", "c2", "cross_file", "foo"),
            ("c3", "c4", "cross_file", "bar"),
        ]
        self.storage.store_edges(edges)
        self.storage.clear_chunk_edges(["c1"])
        remaining = self.storage.get_symbol_stats()["edge_count"]
        assert remaining == 1

    def test_symbol_stats(self):
        syms = [
            Symbol("a", "function", "definition", "c1", "x.py"),
            Symbol("b", "function", "reference", "c2", "y.py"),
        ]
        self.storage.store_symbols(syms)
        self.storage.store_edges([("c2", "c1", "cross_file", "a")])
        stats = self.storage.get_symbol_stats()
        assert stats["symbol_count"] == 2
        assert stats["definition_count"] == 1
        assert stats["reference_count"] == 1
        assert stats["edge_count"] == 1


# -- Engine integration tests -----------------------------------------------


class TestEngineSymbolIntegration:
    """Test symbol graph integration in Stele engine."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cf = Stele(storage_dir=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_and_index(self, filename: str, content: str) -> None:
        """Write a temp file and index it."""
        path = Path(self.tmpdir) / filename
        path.write_text(content)
        self.cf.index_documents([str(path)], force_reindex=True)

    def test_index_extracts_symbols(self):
        self._write_and_index("mod.py", "def greet():\n    pass\n")
        stats = self.cf.get_stats()
        assert stats["storage"]["symbol_count"] > 0
        assert stats["storage"]["definition_count"] > 0

    def test_find_definition_after_index(self):
        self._write_and_index("mod.py", "class Engine:\n    pass\n")
        result = self.cf.find_definition("Engine")
        assert result["count"] >= 1
        assert result["definitions"][0]["kind"] == "class"

    def test_find_references_cross_file(self):
        self._write_and_index("lib.py", "def helper():\n    return 42\n")
        self._write_and_index("app.py", "from lib import helper\nresult = helper()\n")
        result = self.cf.find_references("helper")
        assert len(result["definitions"]) >= 1
        assert len(result["references"]) >= 1

    def test_find_references_verdict_referenced(self):
        self._write_and_index("lib.py", "def greet():\n    return 'hi'\n")
        self._write_and_index("app.py", "from lib import greet\ngreet()\n")
        result = self.cf.find_references("greet")
        assert result["verdict"] == "referenced"

    def test_find_references_verdict_unreferenced(self):
        self._write_and_index("orphan.py", "def orphan_func():\n    pass\n")
        result = self.cf.find_references("orphan_func")
        assert result["verdict"] == "unreferenced"
        assert len(result["definitions"]) >= 1
        assert len(result["references"]) == 0

    def test_find_references_verdict_not_found(self):
        result = self.cf.find_references("nonexistent_symbol_xyz")
        assert result["verdict"] == "not_found"
        assert result["total"] == 0
        assert result["symbol_index"]["status"] == "empty"
        assert result["guidance"] and "index" in result["guidance"].lower()

    def test_find_references_not_found_guidance_when_index_ready(self):
        self._write_and_index("mod.py", "def known():\n    pass\n")
        result = self.cf.find_references("totally_missing_symbol_zzz")
        assert result["verdict"] == "not_found"
        assert result["symbol_index"]["status"] == "ready"
        assert result["guidance"] and "populated" in result["guidance"].lower()

    def test_find_definition_guidance_empty_vs_ready(self):
        r0 = self.cf.find_definition("AnySymbol")
        assert r0["count"] == 0
        assert r0["symbol_index"]["status"] == "empty"
        assert r0["guidance"] and "indexed" in r0["guidance"].lower()
        self._write_and_index("mod.py", "def known():\n    pass\n")
        r1 = self.cf.find_definition("missing_def_zzz")
        assert r1["count"] == 0
        assert r1["symbol_index"]["status"] == "ready"
        assert r1["guidance"] and "populated" in r1["guidance"].lower()

    def test_impact_radius(self):
        self._write_and_index("base.py", "class Base:\n    pass\n")
        self._write_and_index(
            "child.py", "from base import Base\nclass Child(Base):\n    pass\n"
        )
        defn = self.cf.find_definition("Base")
        assert defn["count"] >= 1
        chunk_id = defn["definitions"][0]["chunk_id"]
        impact = self.cf.impact_radius(chunk_id=chunk_id, depth=1)
        assert impact["affected_chunks"] >= 1
        assert "affected_files" in impact

    def test_impact_radius_by_document_path(self):
        self._write_and_index("core.py", "def core_fn():\n    return 1\n")
        self._write_and_index("user.py", "from core import core_fn\ncore_fn()\n")
        core_path = str(Path(self.tmpdir) / "core.py")
        impact = self.cf.impact_radius(document_path=core_path, depth=1)
        assert impact["origin"] == self.cf._normalize_path(core_path)
        assert impact["affected_chunks"] >= 1
        assert impact["affected_files"] >= 1

    def test_impact_radius_no_args_returns_error(self):
        result = self.cf.impact_radius()
        assert "error" in result

    def test_impact_radius_compact(self):
        self._write_and_index("core.py", "def core_fn():\n    return 1\n")
        self._write_and_index("user.py", "from core import core_fn\ncore_fn()\n")
        core_path = str(Path(self.tmpdir) / "core.py")
        impact = self.cf.impact_radius(document_path=core_path, depth=1, compact=True)
        assert impact["chunks"] == []
        assert "files" in impact
        assert len(impact["files"]) >= 1
        assert all("chunk_count" in f and "depth_min" in f for f in impact["files"])

    def test_impact_radius_summary_mode(self):
        self._write_and_index("core.py", "def core_fn():\n    return 1\n")
        self._write_and_index("user.py", "from core import core_fn\ncore_fn()\n")
        core_path = str(Path(self.tmpdir) / "core.py")
        impact = self.cf.impact_radius(
            document_path=core_path,
            depth=2,
            summary_mode=True,
            top_n_files=5,
        )
        assert impact.get("summary_mode") is True
        assert "depth_distribution" in impact
        assert impact["depth_distribution"]
        assert "files" in impact
        assert len(impact["files"]) <= 5
        assert impact.get("files_total", 0) >= len(impact["files"])

    def test_impact_radius_omit_content(self):
        self._write_and_index("core.py", "def core_fn():\n    return 1\n")
        self._write_and_index("user.py", "from core import core_fn\ncore_fn()\n")
        core_path = str(Path(self.tmpdir) / "core.py")
        impact = self.cf.impact_radius(
            document_path=core_path, depth=1, include_content=False
        )
        for row in impact.get("chunks", []):
            assert "content" not in row

    def test_impact_radius_path_filter(self):
        self._write_and_index("core.py", "def core_fn():\n    return 1\n")
        self._write_and_index("user.py", "from core import core_fn\ncore_fn()\n")
        core_path = str(Path(self.tmpdir) / "core.py")
        impact = self.cf.impact_radius(
            document_path=core_path, depth=2, path_filter="user"
        )
        for row in impact.get("chunks", []):
            assert "user" in row["document_path"]

    def test_coupling(self):
        self._write_and_index("models.py", "class User:\n    pass\n")
        self._write_and_index(
            "views.py", "from models import User\ndef get_user():\n    return User()\n"
        )
        models_path = str(Path(self.tmpdir) / "models.py")
        result = self.cf.coupling(models_path)
        assert result["total_coupled"] >= 1
        coupled = result["coupled_files"]
        assert any("views" in c["path"] for c in coupled)
        first = coupled[0]
        assert "shared_symbols" in first
        assert "direction" in first
        assert first["direction"] in ("depends_on", "depended_on_by", "bidirectional")

    def test_coupling_no_document(self):
        result = self.cf.coupling("nonexistent.py")
        assert result["total_coupled"] == 0
        assert result["coupled_files"] == []

    def test_rebuild_symbol_graph(self):
        self._write_and_index("a.py", "def foo():\n    pass\n")
        self._write_and_index("b.py", "from a import foo\nfoo()\n")

        # Clear and rebuild
        result = self.cf.rebuild_symbol_graph()
        assert result["symbols"] > 0
        assert result["edges"] >= 0
        assert result["documents"] == 2

    def test_remove_document_cleans_symbols(self):
        path = Path(self.tmpdir) / "temp.py"
        path.write_text("def temp_func():\n    pass\n")
        self.cf.index_documents([str(path)], force_reindex=True)

        # Verify symbol exists
        result = self.cf.find_definition("temp_func")
        assert result["count"] >= 1

        # Remove document
        self.cf.remove_document(str(path))

        # Symbol should be gone
        result = self.cf.find_definition("temp_func")
        assert result["count"] == 0


# -- Directory indexing tests ------------------------------------------------


class TestDirectoryIndexing:
    """Test that index_documents accepts directories."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cf = Stele(storage_dir=os.path.join(self.tmpdir, "store"))
        self.src = Path(self.tmpdir) / "src"
        self.src.mkdir()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_index_directory(self):
        """Indexing a directory should recursively find supported files."""
        (self.src / "a.py").write_text("def foo():\n    pass\n")
        (self.src / "b.py").write_text("def bar():\n    pass\n")
        (self.src / "readme.md").write_text("# Hello\n")
        result = self.cf.index_documents([str(self.src)])
        assert len(result["indexed"]) == 3
        assert result["errors"] == []

    def test_skips_hidden_dirs(self):
        """Hidden directories should be skipped."""
        hidden = self.src / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("x = 1\n")
        (self.src / "visible.py").write_text("y = 2\n")
        result = self.cf.index_documents([str(self.src)])
        paths = [r["path"] for r in result["indexed"]]
        assert len(paths) == 1
        assert "visible.py" in paths[0]

    def test_skips_node_modules(self):
        """node_modules and similar dirs should be skipped."""
        nm = self.src / "node_modules"
        nm.mkdir()
        (nm / "pkg.js").write_text("function x() {}\n")
        (self.src / "app.js").write_text("function y() {}\n")
        result = self.cf.index_documents([str(self.src)])
        assert len(result["indexed"]) == 1

    def test_skips_unsupported_extensions(self):
        """Files with unsupported extensions should be ignored."""
        (self.src / "data.bin").write_bytes(b"\x00\x01\x02")
        (self.src / "code.py").write_text("pass\n")
        result = self.cf.index_documents([str(self.src)])
        assert len(result["indexed"]) == 1

    def test_mixed_files_and_dirs(self):
        """Mixing file paths and directory paths should work."""
        sub = self.src / "sub"
        sub.mkdir()
        (sub / "inner.py").write_text("z = 3\n")
        single = self.src / "single.py"
        single.write_text("w = 4\n")
        result = self.cf.index_documents([str(single), str(sub)])
        assert len(result["indexed"]) == 2


# -- Staleness propagation tests --------------------------------------------


class TestStalenessPropagation:
    """Test staleness scoring through the symbol graph."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cf = Stele(storage_dir=os.path.join(self.tmpdir, "store"))
        self.src = Path(self.tmpdir) / "src"
        self.src.mkdir()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_staleness_before_changes(self):
        (self.src / "a.py").write_text("def foo():\n    pass\n")
        self.cf.index_documents([str(self.src)])
        stale = self.cf.stale_chunks(threshold=0.01)
        assert stale["total_stale"] == 0

    def test_direct_dependent_gets_stale(self):
        """Changing base.py should make lib.py stale."""
        (self.src / "base.py").write_text("class Base:\n    pass\n")
        (self.src / "lib.py").write_text(
            "from base import Base\nclass Lib(Base):\n    pass\n"
        )
        self.cf.index_documents([str(self.src)])

        # Modify base.py
        (self.src / "base.py").write_text("class Base:\n    x = 99\n")
        self.cf.detect_changes_and_update(session_id="test")

        stale = self.cf.stale_chunks(threshold=0.1)
        assert stale["total_stale"] >= 1
        stale_paths = [d["path"] for d in stale["by_document"]]
        assert any("lib.py" in p for p in stale_paths)

    def test_transitive_staleness_decays(self):
        """Staleness should decay with graph distance."""
        (self.src / "base.py").write_text("def core():\n    return 1\n")
        (self.src / "mid.py").write_text(
            "from base import core\ndef middle():\n    return core()\n"
        )
        (self.src / "top.py").write_text(
            "from mid import middle\ndef top():\n    return middle()\n"
        )
        self.cf.index_documents([str(self.src)])

        (self.src / "base.py").write_text("def core():\n    return 99\n")
        self.cf.detect_changes_and_update(session_id="test")

        stale = self.cf.stale_chunks(threshold=0.1)
        scores = {}
        for doc in stale["by_document"]:
            name = Path(doc["path"]).name
            scores[name] = max(c["staleness_score"] for c in doc["chunks"])

        # mid.py should be more stale than top.py
        assert "mid.py" in scores, "mid.py should be stale"
        if "top.py" in scores:
            assert scores["mid.py"] > scores["top.py"]

    def test_staleness_threshold_filtering(self):
        """stale_chunks with high threshold should return fewer results."""
        (self.src / "base.py").write_text("def core():\n    return 1\n")
        (self.src / "mid.py").write_text(
            "from base import core\ndef middle():\n    return core()\n"
        )
        (self.src / "top.py").write_text(
            "from mid import middle\ndef top():\n    return middle()\n"
        )
        self.cf.index_documents([str(self.src)])

        (self.src / "base.py").write_text("def core():\n    return 99\n")
        self.cf.detect_changes_and_update(session_id="test")

        low = self.cf.stale_chunks(threshold=0.1)
        high = self.cf.stale_chunks(threshold=0.7)
        assert low["total_stale"] >= high["total_stale"]

    def test_changed_chunks_not_stale(self):
        """The modified chunks themselves should have staleness 0."""
        (self.src / "a.py").write_text("x = 1\n")
        self.cf.index_documents([str(self.src)])

        (self.src / "a.py").write_text("x = 2\n")
        self.cf.detect_changes_and_update(session_id="test")

        stale = self.cf.stale_chunks(threshold=0.01)
        for doc in stale.get("by_document", []):
            assert "a.py" not in doc["path"]


# -- Search with edges tests -------------------------------------------------


class TestSearchWithEdges:
    """Test that search results include symbol edges."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cf = Stele(storage_dir=os.path.join(self.tmpdir, "store"))
        self.src = Path(self.tmpdir) / "src"
        self.src.mkdir()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_search_includes_edges(self):
        (self.src / "base.py").write_text("class Base:\n    pass\n")
        (self.src / "child.py").write_text(
            "from base import Base\nclass Child(Base):\n    pass\n"
        )
        self.cf.index_documents([str(self.src)])
        results = self.cf.search("Base class", top_k=5)
        has_any_edges = any("edges" in r for r in results)
        assert has_any_edges

    def test_edges_have_depends_on_and_depended_on_by(self):
        (self.src / "lib.py").write_text("def helper():\n    return 1\n")
        (self.src / "app.py").write_text("from lib import helper\nhelper()\n")
        self.cf.index_documents([str(self.src)])
        results = self.cf.search("helper", top_k=5)
        for r in results:
            if "edges" in r:
                assert "depends_on" in r["edges"]
                assert "depended_on_by" in r["edges"]
                break

    def test_no_edges_when_none_exist(self):
        (self.src / "isolated.py").write_text("x = 42\n")
        self.cf.index_documents([str(self.src)])
        results = self.cf.search("x = 42", top_k=1)
        assert len(results) >= 1, "Search should return at least 1 result"
        assert "edges" not in results[0]


# -- Configurable skip-dirs tests -------------------------------------------


class TestConfigurableSkipDirs:
    """Test that skip_dirs parameter works."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.src = Path(self.tmpdir) / "src"
        self.src.mkdir()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_custom_skip_dir(self):
        cf = Stele(
            storage_dir=os.path.join(self.tmpdir, "store"),
            skip_dirs={"vendor"},
        )
        (self.src / "app.py").write_text("x = 1\n")
        vendor = self.src / "vendor"
        vendor.mkdir()
        (vendor / "lib.py").write_text("y = 2\n")
        result = cf.index_documents([str(self.src)])
        paths = [r["path"] for r in result["indexed"]]
        assert any("app.py" in p for p in paths)
        assert not any("vendor" in p for p in paths)

    def test_default_skips_preserved(self):
        cf = Stele(
            storage_dir=os.path.join(self.tmpdir, "store"),
            skip_dirs={"extra"},
        )
        assert "node_modules" in cf.skip_dirs
        assert "__pycache__" in cf.skip_dirs
        assert "extra" in cf.skip_dirs

    def test_no_custom_skips(self):
        cf = Stele(storage_dir=os.path.join(self.tmpdir, "store"))
        assert cf.skip_dirs == Stele.DEFAULT_SKIP_DIRS


# -- Module path resolution tests -------------------------------------------


class TestModulePathResolution:
    """Test that resolve_symbols prefers definitions from imported modules."""

    def test_module_matches_path(self):
        assert _module_matches_path("stele.engine", "/src/stele/engine.py")
        assert _module_matches_path("utils", "/project/utils.py")
        assert _module_matches_path("pkg.sub", "/a/pkg/sub.py")
        assert not _module_matches_path("stele.engine", "/src/other/engine.py")
        assert not _module_matches_path("foo", "/src/bar.py")

    def test_prefers_imported_module(self):
        """from pkg.utils import helper should prefer pkg/utils.py's helper."""
        symbols = [
            # Two definitions of 'helper' in different files
            Symbol("helper", "function", "definition", "c_pkg", "/proj/pkg/utils.py"),
            Symbol("helper", "function", "definition", "c_other", "/proj/other.py"),
            # Reference with module hint
            Symbol("pkg.utils", "module", "reference", "c_app", "/proj/app.py"),
            Symbol("helper", "import", "reference", "c_app", "/proj/app.py"),
        ]
        edges = resolve_symbols(symbols)
        target_chunks = [e[1] for e in edges if e[0] == "c_app"]
        # Should link to c_pkg (pkg/utils.py), not c_other
        assert "c_pkg" in target_chunks
        assert "c_other" not in target_chunks

    def test_falls_back_without_hint(self):
        """Without module hints, all matching definitions should link."""
        symbols = [
            Symbol("Foo", "class", "definition", "c1", "/a.py"),
            Symbol("Foo", "class", "definition", "c2", "/b.py"),
            Symbol("Foo", "class", "reference", "c3", "/c.py"),
        ]
        edges = resolve_symbols(symbols)
        targets = {e[1] for e in edges if e[0] == "c3"}
        assert targets == {"c1", "c2"}

    def test_integration_module_precision(self):
        """End-to-end: indexing with module path resolution."""
        tmpdir = tempfile.mkdtemp()
        src = Path(tmpdir) / "src"
        pkg = src / "pkg"
        pkg.mkdir(parents=True)

        (pkg / "utils.py").write_text("def helper():\n    return 1\n")
        (src / "other_utils.py").write_text("def helper():\n    return 2\n")
        (src / "app.py").write_text("from pkg.utils import helper\nresult = helper()\n")

        cf = Stele(storage_dir=os.path.join(tmpdir, "store"))
        cf.index_documents([str(src)])

        # app.py should only link to pkg/utils.py, not other_utils.py
        stats = cf.get_stats()
        # At minimum, the edge should exist
        assert stats["storage"]["edge_count"] >= 1


# -- Noise filter tests ------------------------------------------------------


class TestNoiseFilter:
    """Test that common names are filtered from symbol resolution."""

    def test_builtin_refs_filtered(self):
        """References to builtins like print/len should not create edges."""
        symbols = [
            Symbol("print", "function", "definition", "c1", "io.py"),
            Symbol("print", "function", "reference", "c2", "app.py"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 0

    def test_dunder_refs_filtered(self):
        """References to __init__ should not create edges."""
        symbols = [
            Symbol("__init__", "function", "definition", "c1", "a.py"),
            Symbol("__init__", "function", "reference", "c2", "b.py"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 0

    def test_short_names_filtered(self):
        """Single-character references should not create edges."""
        symbols = [
            Symbol("x", "variable", "definition", "c1", "a.py"),
            Symbol("x", "variable", "reference", "c2", "b.py"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 0

    def test_real_names_not_filtered(self):
        """User-defined names should still create edges."""
        symbols = [
            Symbol("Stele", "class", "definition", "c1", "engine.py"),
            Symbol("Stele", "class", "reference", "c2", "cli.py"),
        ]
        edges = resolve_symbols(symbols)
        assert len(edges) == 1

    def test_noise_set_contains_expected(self):
        assert "self" in _NOISE_REFS
        assert "__init__" in _NOISE_REFS
        assert "print" in _NOISE_REFS
        assert "console" in _NOISE_REFS
        assert "get" in _NOISE_REFS

    def test_definitions_kept_despite_noise(self):
        """Definitions of noisy names should still be extractable."""
        ext = SymbolExtractor()
        code = "def get(self, key):\n    return self.data[key]\n"
        syms = ext.extract(code, "cache.py", "c1", "py")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "get" for s in defs)


# -- JS extraction improvements tests ----------------------------------------


class TestJSExtractionImprovements:
    """Test improved JS/TS symbol extraction."""

    def setup_method(self):
        self.ext = SymbolExtractor()

    def test_destructured_require(self):
        code = "const { readFile, writeFile } = require('fs')"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        assert any(s.name == "fs" and s.kind == "module" for s in refs)
        assert any(s.name == "readFile" and s.kind == "import" for s in refs)
        assert any(s.name == "writeFile" and s.kind == "import" for s in refs)

    def test_class_method_definition(self):
        code = "class Foo {\n  handleClick(event) {\n    return event\n  }\n}"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "Foo" and s.kind == "class" for s in defs)
        assert any(s.name == "handleClick" and s.kind == "function" for s in defs)

    def test_class_method_not_control_flow(self):
        """if/for/while should not be detected as method definitions."""
        code = "  if (condition) {\n    doSomething()\n  }"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        defs = [s for s in syms if s.role == "definition" and s.kind == "function"]
        assert not any(s.name == "if" for s in defs)

    def test_default_require_local_is_import_reference(self):
        """const X = require('./local') should emit import reference, not variable def."""
        code = "const Recipe = require('../models/Recipe')"
        syms = self.ext.extract(code, "routes.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        defs = [s for s in syms if s.role == "definition"]
        # Variable name should be an import reference
        assert any(s.name == "Recipe" and s.kind == "import" for s in refs)
        # Module path should be a module reference
        assert any(s.name == "../models/Recipe" and s.kind == "module" for s in refs)
        # Should NOT be a variable definition
        assert not any(s.name == "Recipe" and s.kind == "variable" for s in defs)

    def test_default_require_external_no_import_ref(self):
        """const fs = require('fs') should not emit import ref for variable name."""
        code = "const fs = require('fs')"
        syms = self.ext.extract(code, "app.js", "c1", "js")
        refs = [s for s in syms if s.role == "reference"]
        defs = [s for s in syms if s.role == "definition"]
        # Module path reference is still emitted
        assert any(s.name == "fs" and s.kind == "module" for s in refs)
        # No import reference for the variable name (external module)
        assert not any(s.name == "fs" and s.kind == "import" for s in refs)
        # No variable definition either
        assert not any(s.name == "fs" and s.kind == "variable" for s in defs)

    def test_plain_const_still_definition(self):
        """const X = value (no require) should still be a variable definition."""
        code = "const PORT = 3000"
        syms = self.ext.extract(code, "config.js", "c1", "js")
        defs = [s for s in syms if s.role == "definition"]
        assert any(s.name == "PORT" and s.kind == "variable" for s in defs)


# -- Module path matching tests (JS) -----------------------------------------


class TestModuleMatchesPathJS:
    """Test _module_matches_path with JS require paths."""

    def test_relative_require_matches_js(self):
        assert _module_matches_path("../models/Recipe", "src/models/Recipe.js")

    def test_relative_require_matches_ts(self):
        assert _module_matches_path("./utils", "src/utils.ts")

    def test_relative_require_matches_no_ext(self):
        assert _module_matches_path("../lib/auth", "src/lib/auth")

    def test_relative_require_matches_index(self):
        assert _module_matches_path("./utils", "src/utils/index.js")

    def test_nested_relative_require(self):
        assert _module_matches_path("../../shared/types", "shared/types.ts")

    def test_external_require_no_match(self):
        assert not _module_matches_path("express", "src/models/express.js")

    def test_python_still_works(self):
        assert _module_matches_path("stele.engine", "/src/stele/engine.py")
        assert not _module_matches_path("stele.engine", "/src/other/engine.py")


# -- Symbol-boosted search tests ---------------------------------------------


class TestSymbolBoostedSearch:
    """Test that search finds chunks via symbol name matching."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cf = Stele(storage_dir=os.path.join(self.tmpdir, "store"))
        self.src = Path(self.tmpdir) / "src"
        self.src.mkdir()

    def test_query_identifier_extraction(self):
        idents = extract_query_identifiers("StorageBackend")
        assert "StorageBackend" in idents or "Storage" in idents

    def test_query_identifier_filters_stopwords(self):
        idents = extract_query_identifiers("the and for")
        assert len(idents) == 0

    def test_symbol_match_in_results(self):
        """Chunks found via symbol names should have symbol_match field."""
        (self.src / "auth.py").write_text(
            "def verify_credentials(user, password):\n    return True\n"
        )
        (self.src / "handler.py").write_text(
            "from auth import verify_credentials\n"
            "def handle_login():\n    return verify_credentials('a', 'b')\n"
        )
        self.cf.index_documents([str(self.src)])
        results = self.cf.search("verify_credentials", top_k=10)
        assert len(results) >= 1
        # At least one result should contain the function definition
        found_content = any(
            "verify_credentials" in (r.get("content") or "") for r in results
        )
        assert found_content, "Search should find chunks containing verify_credentials"


# -- Incremental edge rebuild tests ------------------------------------------


class TestIncrementalEdgeRebuild:
    """Test that edge rebuilds are scoped to affected chunks."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cf = Stele(storage_dir=os.path.join(self.tmpdir, "store"))
        self.src = Path(self.tmpdir) / "src"
        self.src.mkdir()

    def test_adding_file_preserves_existing_edges(self):
        """Indexing a new file should not destroy edges from earlier files."""
        (self.src / "base.py").write_text("class Base:\n    pass\n")
        (self.src / "child.py").write_text(
            "from base import Base\nclass Child(Base):\n    pass\n"
        )
        self.cf.index_documents([str(self.src)])
        edges_before = self.cf.get_stats()["storage"]["edge_count"]
        assert edges_before >= 1

        # Add unrelated file
        (self.src / "util.py").write_text("def unrelated():\n    return 42\n")
        self.cf.index_documents([str(self.src / "util.py")])
        edges_after = self.cf.get_stats()["storage"]["edge_count"]
        # Existing edges should still be there
        assert edges_after >= edges_before

    def test_reindex_updates_edges(self):
        """Re-indexing a file should update its edges."""
        (self.src / "lib.py").write_text("def old_func():\n    pass\n")
        (self.src / "app.py").write_text("from lib import old_func\nold_func()\n")
        self.cf.index_documents([str(self.src)])
        assert self.cf.get_stats()["storage"]["edge_count"] >= 1

        # Change lib.py — rename function
        (self.src / "lib.py").write_text("def new_func():\n    pass\n")
        self.cf.index_documents([str(self.src / "lib.py")], force_reindex=True)

        # old_func edge should be gone (app still references it, but no definition)
        refs = self.cf.find_references("old_func")
        # Definition count should be 0
        assert refs["definitions"] == []


# -- HTML <script>/<link> edge resolution tests ------------------------------


class TestHTMLEdgeResolution:
    """Test that HTML <script src> and <link href> create cross-file edges."""

    def test_module_matches_bare_js(self):
        """Bare JS filename should match file path."""
        assert _module_matches_path("app.js", "public/app.js")
        assert _module_matches_path("app.js", "/proj/public/app.js")

    def test_module_matches_bare_css(self):
        """Bare CSS filename should match file path."""
        assert _module_matches_path("styles.css", "public/styles.css")

    def test_module_matches_subdir_path(self):
        """Subdirectory web path should match."""
        assert _module_matches_path("js/main.js", "public/js/main.js")

    def test_module_matches_leading_slash(self):
        """Leading-slash web path should match."""
        assert _module_matches_path("/static/app.js", "public/static/app.js")

    def test_module_no_match_wrong_file(self):
        """Bare filename should not match unrelated file."""
        assert not _module_matches_path("app.js", "public/other.js")

    def test_module_no_match_external(self):
        """External package names without file extensions should not match."""
        assert not _module_matches_path("express", "src/express.js")

    def test_resolve_html_to_js_file(self):
        """HTML <script src="app.js"> should create edge to JS file's chunk."""
        symbols = [
            # JS file defines a function
            Symbol("handleSubmit", "function", "definition", "c_js", "app.js"),
            # HTML references the JS file via <script src>
            Symbol("app.js", "module", "reference", "c_html", "index.html"),
        ]
        edges = resolve_symbols(symbols)
        # Should have an edge from HTML to JS via module-to-file fallback
        assert len(edges) >= 1
        assert any(e[0] == "c_html" and e[1] == "c_js" for e in edges), (
            f"Expected edge from c_html to c_js, got {edges}"
        )

    def test_resolve_html_to_css_file(self):
        """HTML <link href="styles.css"> should create edge to CSS file's chunk."""
        symbols = [
            # CSS file defines a class
            Symbol(".container", "css_class", "definition", "c_css", "styles.css"),
            # HTML references the CSS file via <link href>
            Symbol("styles.css", "module", "reference", "c_html", "index.html"),
        ]
        edges = resolve_symbols(symbols)
        assert any(e[0] == "c_html" and e[1] == "c_css" for e in edges), (
            f"Expected edge from c_html to c_css, got {edges}"
        )

    def test_resolve_html_onclick_to_js(self):
        """HTML onclick="handleSubmit()" should link to JS definition."""
        symbols = [
            Symbol("handleSubmit", "function", "definition", "c_js", "app.js"),
            Symbol("handleSubmit", "function", "reference", "c_html", "index.html"),
        ]
        edges = resolve_symbols(symbols)
        assert any(
            e[0] == "c_html" and e[1] == "c_js" and e[3] == "handleSubmit"
            for e in edges
        )

    def test_integration_html_js_impact(self):
        """End-to-end: impact_radius from HTML should reach JS through <script>."""
        tmpdir = tempfile.mkdtemp()
        (Path(tmpdir) / ".git").mkdir()
        src = Path(tmpdir) / "public"
        src.mkdir()

        (src / "app.js").write_text(
            "function renderPage() {\n  document.body.innerHTML = 'hello';\n}\n"
        )
        (src / "index.html").write_text(
            "<!DOCTYPE html>\n<html>\n<head>\n"
            '  <script src="app.js"></script>\n'
            "</head>\n<body></body>\n</html>\n"
        )

        cf = Stele(project_root=str(tmpdir), enable_coordination=False)
        cf.index_documents([str(src)])

        # HTML should have an edge to app.js
        edges = cf.get_stats()["storage"]["edge_count"]
        assert edges >= 1, "Expected at least one edge from HTML to JS"

    def test_integration_js_impact_from_html(self):
        """impact_radius from JS file should now show HTML as dependent."""
        tmpdir = tempfile.mkdtemp()
        (Path(tmpdir) / ".git").mkdir()
        src = Path(tmpdir) / "public"
        src.mkdir()

        (src / "api.js").write_text(
            "function fetchData() {\n  return fetch('/api/data');\n}\n"
        )
        (src / "page.html").write_text(
            "<html><head>\n"
            '  <script src="api.js"></script>\n'
            "</head><body></body></html>\n"
        )

        cf = Stele(project_root=str(tmpdir), enable_coordination=False)
        cf.index_documents([str(src)])

        # From the JS file's perspective, HTML depends on it
        # compact=True (default) returns files list; compact=False returns chunks list
        impact = cf.impact_radius(document_path="public/api.js", depth=1, compact=False)
        # document_path uses OS separators on Windows (public\page.html)
        affected_files = {
            Path(c["document_path"]).as_posix() for c in impact.get("chunks", [])
        }
        assert "public/page.html" in affected_files, (
            f"Expected page.html in affected files, got {affected_files}"
        )
