"""Tests for SymbolStorage: persistent symbol and edge storage."""

from stele_context.symbol_patterns import Symbol
from stele_context.symbol_storage import SymbolStorage


def _make_storage(tmp_path):
    """Create a SymbolStorage backed by a temp SQLite database."""
    db_path = tmp_path / "test.db"
    return SymbolStorage(db_path)


def _sym(name, kind="function", role="definition", chunk_id="c1", doc="a.py", line=1):
    """Shorthand for creating a Symbol."""
    return Symbol(
        name=name,
        kind=kind,
        role=role,
        chunk_id=chunk_id,
        document_path=doc,
        line_number=line,
    )


class TestStoreAndRetrieveSymbols:
    """Test storing symbols and reading them back."""

    def test_store_single_symbol(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([_sym("hello")])

        all_syms = ss.get_all_symbols()
        assert len(all_syms) == 1
        assert all_syms[0]["name"] == "hello"
        assert all_syms[0]["kind"] == "function"
        assert all_syms[0]["role"] == "definition"
        assert all_syms[0]["chunk_id"] == "c1"
        assert all_syms[0]["document_path"] == "a.py"
        assert all_syms[0]["line_number"] == 1

    def test_store_multiple_symbols(self, tmp_path):
        ss = _make_storage(tmp_path)
        syms = [
            _sym("foo", chunk_id="c1", line=1),
            _sym("bar", chunk_id="c1", line=5),
            _sym("baz", chunk_id="c2", line=1),
        ]
        ss.store_symbols(syms)

        all_syms = ss.get_all_symbols()
        assert len(all_syms) == 3
        names = {s["name"] for s in all_syms}
        assert names == {"foo", "bar", "baz"}

    def test_store_empty_list_is_noop(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([])
        assert ss.get_all_symbols() == []

    def test_store_symbol_with_none_line_number(self, tmp_path):
        ss = _make_storage(tmp_path)
        sym = Symbol(
            name="mystery",
            kind="variable",
            role="reference",
            chunk_id="c1",
            document_path="a.py",
            line_number=None,
        )
        ss.store_symbols([sym])

        all_syms = ss.get_all_symbols()
        assert len(all_syms) == 1
        assert all_syms[0]["line_number"] is None


class TestFindDefinitionsAndReferences:
    """Test querying definitions and references by name."""

    def test_find_definitions(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols(
            [
                _sym("Widget", kind="class", role="definition", chunk_id="c1"),
                _sym("Widget", kind="class", role="reference", chunk_id="c2"),
                _sym("Other", kind="function", role="definition", chunk_id="c3"),
            ]
        )

        defs = ss.find_definitions("Widget")
        assert len(defs) == 1
        assert defs[0]["chunk_id"] == "c1"
        assert defs[0]["role"] == "definition"

    def test_find_definitions_no_match(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([_sym("foo")])
        assert ss.find_definitions("nonexistent") == []

    def test_find_references_by_name(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols(
            [
                _sym("Widget", role="definition", chunk_id="c1"),
                _sym("Widget", role="reference", chunk_id="c2"),
                _sym("Widget", role="reference", chunk_id="c3"),
            ]
        )

        refs = ss.find_references_by_name("Widget")
        assert len(refs) == 2
        ref_chunks = {r["chunk_id"] for r in refs}
        assert ref_chunks == {"c2", "c3"}

    def test_find_references_no_match(self, tmp_path):
        ss = _make_storage(tmp_path)
        assert ss.find_references_by_name("ghost") == []

    def test_duplicate_symbol_names_stored_separately(self, tmp_path):
        """Same name defined in two different chunks is not deduplicated."""
        ss = _make_storage(tmp_path)
        ss.store_symbols(
            [
                _sym("init", chunk_id="c1", doc="a.py"),
                _sym("init", chunk_id="c2", doc="b.py"),
            ]
        )

        defs = ss.find_definitions("init")
        assert len(defs) == 2
        docs = {d["document_path"] for d in defs}
        assert docs == {"a.py", "b.py"}


class TestSearchSymbolNames:
    """Test token-based symbol name search."""

    def test_search_finds_matching_definition(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols(
            [
                _sym("process_data", role="definition", chunk_id="c1"),
                _sym("process_data", role="reference", chunk_id="c2"),
            ]
        )

        results = ss.search_symbol_names(["process_data"])
        assert len(results) == 1
        assert results[0]["name"] == "process_data"
        assert results[0]["chunk_id"] == "c1"

    def test_search_is_case_insensitive(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([_sym("MyClass", kind="class", role="definition")])

        results = ss.search_symbol_names(["myclass"])
        assert len(results) == 1
        assert results[0]["name"] == "MyClass"

    def test_search_multiple_tokens(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols(
            [
                _sym("alpha", role="definition", chunk_id="c1"),
                _sym("beta", role="definition", chunk_id="c2"),
                _sym("gamma", role="definition", chunk_id="c3"),
            ]
        )

        results = ss.search_symbol_names(["alpha", "gamma"])
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"alpha", "gamma"}

    def test_search_empty_tokens(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([_sym("foo", role="definition")])
        assert ss.search_symbol_names([]) == []

    def test_search_no_match(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([_sym("foo", role="definition")])
        assert ss.search_symbol_names(["zzz"]) == []

    def test_search_returns_distinct_results(self, tmp_path):
        """Same chunk defining same name twice should still be deduplicated."""
        ss = _make_storage(tmp_path)
        # Two identical symbols in the same chunk (edge case)
        ss.store_symbols(
            [
                _sym(
                    "dup", role="definition", chunk_id="c1", doc="a.py", kind="function"
                ),
                _sym(
                    "dup", role="definition", chunk_id="c1", doc="a.py", kind="function"
                ),
            ]
        )

        results = ss.search_symbol_names(["dup"])
        # DISTINCT on (chunk_id, name, kind, document_path)
        assert len(results) == 1


class TestStoreAndQueryEdges:
    """Test storing and querying symbol edges."""

    def test_store_and_get_edges_for_chunk(self, tmp_path):
        ss = _make_storage(tmp_path)
        edges = [
            ("c1", "c2", "calls", "foo"),
            ("c3", "c1", "imports", "bar"),
        ]
        ss.store_edges(edges)

        # c1 is both source and target
        result = ss.get_edges_for_chunk("c1")
        assert len(result) == 2
        types = {e["edge_type"] for e in result}
        assert types == {"calls", "imports"}

    def test_get_incoming_edges(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_edges(
            [
                ("c1", "c2", "calls", "foo"),
                ("c3", "c2", "imports", "bar"),
                ("c2", "c4", "uses", "baz"),
            ]
        )

        incoming = ss.get_incoming_edges("c2")
        assert len(incoming) == 2
        sources = {e["source_chunk_id"] for e in incoming}
        assert sources == {"c1", "c3"}

    def test_get_outgoing_edges(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_edges(
            [
                ("c1", "c2", "calls", "foo"),
                ("c1", "c3", "imports", "bar"),
                ("c4", "c1", "uses", "baz"),
            ]
        )

        outgoing = ss.get_outgoing_edges("c1")
        assert len(outgoing) == 2
        targets = {e["target_chunk_id"] for e in outgoing}
        assert targets == {"c2", "c3"}

    def test_get_edges_no_results(self, tmp_path):
        ss = _make_storage(tmp_path)
        assert ss.get_edges_for_chunk("nonexistent") == []
        assert ss.get_incoming_edges("nonexistent") == []
        assert ss.get_outgoing_edges("nonexistent") == []

    def test_store_empty_edges_is_noop(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_edges([])
        stats = ss.get_symbol_stats()
        assert stats["edge_count"] == 0


class TestClearOperations:
    """Test clearing symbols and edges."""

    def test_clear_chunk_symbols(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols(
            [
                _sym("a", chunk_id="c1"),
                _sym("b", chunk_id="c2"),
                _sym("c", chunk_id="c3"),
            ]
        )

        ss.clear_chunk_symbols(["c1", "c2"])

        remaining = ss.get_all_symbols()
        assert len(remaining) == 1
        assert remaining[0]["chunk_id"] == "c3"

    def test_clear_chunk_symbols_empty_list(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([_sym("a")])
        ss.clear_chunk_symbols([])
        assert len(ss.get_all_symbols()) == 1

    def test_clear_chunk_edges(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_edges(
            [
                ("c1", "c2", "calls", "foo"),
                ("c2", "c3", "imports", "bar"),
                ("c4", "c5", "uses", "baz"),
            ]
        )

        # Remove edges involving c2 (appears as source and target)
        ss.clear_chunk_edges(["c2"])

        stats = ss.get_symbol_stats()
        assert stats["edge_count"] == 1  # only c4->c5 remains

    def test_clear_chunk_edges_empty_list(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_edges([("c1", "c2", "calls", "foo")])
        ss.clear_chunk_edges([])
        assert ss.get_symbol_stats()["edge_count"] == 1

    def test_clear_document_symbols(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols(
            [
                _sym("x", chunk_id="c1", doc="a.py"),
                _sym("y", chunk_id="c2", doc="a.py"),
                _sym("z", chunk_id="c3", doc="b.py"),
            ]
        )

        ss.clear_document_symbols("a.py")

        remaining = ss.get_all_symbols()
        assert len(remaining) == 1
        assert remaining[0]["document_path"] == "b.py"

    def test_clear_all_symbols(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([_sym("a"), _sym("b")])
        ss.clear_all_symbols()
        assert ss.get_all_symbols() == []

    def test_clear_all_edges(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_edges([("c1", "c2", "calls", "foo")])
        ss.clear_all_edges()
        assert ss.get_symbol_stats()["edge_count"] == 0


class TestSymbolStats:
    """Test symbol and edge statistics."""

    def test_empty_stats(self, tmp_path):
        ss = _make_storage(tmp_path)
        stats = ss.get_symbol_stats()
        assert stats == {
            "symbol_count": 0,
            "definition_count": 0,
            "reference_count": 0,
            "edge_count": 0,
            "runtime_symbol_count": 0,
        }

    def test_stats_count_roles(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols(
            [
                _sym("Widget", role="definition", chunk_id="c1"),
                _sym("Widget", role="reference", chunk_id="c2"),
                _sym("Widget", role="reference", chunk_id="c3"),
                _sym("Other", role="definition", chunk_id="c4"),
            ]
        )
        ss.store_edges(
            [
                ("c2", "c1", "uses", "Widget"),
                ("c3", "c1", "uses", "Widget"),
            ]
        )

        stats = ss.get_symbol_stats()
        assert stats["symbol_count"] == 4
        assert stats["definition_count"] == 2
        assert stats["reference_count"] == 2
        assert stats["edge_count"] == 2

    def test_stats_after_clear(self, tmp_path):
        ss = _make_storage(tmp_path)
        ss.store_symbols([_sym("a"), _sym("b", role="reference")])
        ss.store_edges([("c1", "c2", "calls", "foo")])

        ss.clear_all_symbols()
        ss.clear_all_edges()

        stats = ss.get_symbol_stats()
        assert stats["symbol_count"] == 0
        assert stats["edge_count"] == 0


class TestInitialization:
    """Test SymbolStorage initialization and table creation."""

    def test_creates_tables_on_init(self, tmp_path):
        """Tables should exist immediately after construction."""
        ss = _make_storage(tmp_path)
        # Prove tables exist by running queries without error
        assert ss.get_all_symbols() == []
        assert ss.get_symbol_stats()["edge_count"] == 0

    def test_reinit_preserves_data(self, tmp_path):
        """Creating a second SymbolStorage on the same DB keeps existing data."""
        db_path = tmp_path / "test.db"
        ss1 = SymbolStorage(db_path)
        ss1.store_symbols([_sym("preserved")])

        ss2 = SymbolStorage(db_path)
        all_syms = ss2.get_all_symbols()
        assert len(all_syms) == 1
        assert all_syms[0]["name"] == "preserved"
