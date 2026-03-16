"""Tests for symbol extraction, resolution, and graph queries."""

import tempfile
from pathlib import Path

import pytest

from chunkforge.engine import ChunkForge
from chunkforge.symbols import SymbolExtractor, Symbol, resolve_symbols
from chunkforge.symbol_storage import SymbolStorage


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
    """Test symbol graph integration in ChunkForge engine."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cf = ChunkForge(storage_dir=self.tmpdir)

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

    def test_impact_radius(self):
        self._write_and_index("base.py", "class Base:\n    pass\n")
        self._write_and_index(
            "child.py", "from base import Base\nclass Child(Base):\n    pass\n"
        )
        defn = self.cf.find_definition("Base")
        assert defn["count"] >= 1
        chunk_id = defn["definitions"][0]["chunk_id"]
        impact = self.cf.impact_radius(chunk_id, depth=1)
        assert impact["affected_chunks"] >= 1

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
