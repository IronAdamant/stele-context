"""Swift declarations are definitions; calls are references."""

from __future__ import annotations

import re

import pytest

from stele_context.chunkers.code_patterns import (
    DEFINITION_TYPES,
    EXT_TO_GRAMMAR,
    get_regex_pattern,
)
from stele_context.engine import Stele
from stele_context.symbols import SymbolExtractor

_SAMPLE = """
import UIKit
@testable import DemoKit

@MainActor
public final class ViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()
        greet(name: "ada")
    }

    public func greet(name: String) -> String {
        return name
    }

    class func make() -> ViewController {
        return ViewController()
    }
}

struct Point {
    var x: Int
}

enum Color {
    case red
}

protocol Drawable {
    func draw()
}

extension ViewController {
    func extra() {}
}

actor Bank {
    func deposit() {}
}

typealias ID = String

private func compute(a: Int) -> Int {
    return compute(a: a)
}
"""


def _defs(syms, name=None):
    found = [s for s in syms if s.role == "definition"]
    if name is None:
        return found
    return [s for s in found if s.name == name]


class TestSwiftExtractor:
    def setup_method(self):
        self.ext = SymbolExtractor()

    def test_type_and_function_declarations(self):
        syms = self.ext.extract(_SAMPLE, "ViewController.swift", "c1", "swift")

        for name in (
            "ViewController",
            "Point",
            "Color",
            "Drawable",
            "Bank",
            "ID",
        ):
            assert _defs(syms, name), name
            assert _defs(syms, name)[0].kind == "class"

        # The class and its extension are both definitions of the type.
        assert len(_defs(syms, "ViewController")) == 2

        for name in (
            "viewDidLoad",
            "greet",
            "make",
            "draw",
            "extra",
            "deposit",
            "compute",
        ):
            assert len(_defs(syms, name)) == 1, name
            assert _defs(syms, name)[0].kind == "function"

        assert _defs(syms, "func") == []
        assert _defs(syms, "UIKit") == []
        assert any(
            s.name == "UIKit" and s.role == "reference" and s.kind == "module"
            for s in syms
        )
        assert any(
            s.name == "greet" and s.role == "reference" and s.kind == "function"
            for s in syms
        )
        assert any(
            s.name == "compute" and s.role == "reference" and s.kind == "function"
            for s in syms
        )
        assert len(_defs(syms, "compute")) == 1

    def test_class_func_is_not_a_type_named_func(self):
        code = "public class Factory {\n    class func make() -> Factory { Factory() }\n}\n"
        syms = self.ext.extract(code, "Factory.swift", "c1", ".swift")
        assert [s.name for s in _defs(syms)] == ["Factory", "make"]
        assert _defs(syms, "make")[0].kind == "function"

    def test_commented_declaration_is_ignored(self):
        code = "// class Hidden {}\n/* struct AlsoHidden {} */\nstruct Shown {}\n"
        syms = self.ext.extract(code, "Shown.swift", "c1", "swift")
        names = {s.name for s in _defs(syms)}
        assert names == {"Shown"}

    def test_grammar_mapping_uses_real_swift_nodes(self):
        assert EXT_TO_GRAMMAR["swift"] == ("tree_sitter_swift", "swift")
        assert "class_declaration" in DEFINITION_TYPES["swift"]
        assert "protocol_declaration" in DEFINITION_TYPES["swift"]
        assert "function_declaration" in DEFINITION_TYPES["swift"]

    def test_regex_chunk_pattern_covers_declarations(self):
        pattern = re.compile(get_regex_pattern("swift"))
        text = (
            "public final class ViewController {}\n"
            "class func make() {}\n"
            "struct Point {}\n"
            "enum Color {}\n"
            "protocol Drawable {}\n"
            "extension ViewController {}\n"
            "actor Bank {}\n"
            "func greet() {}\n"
        )
        matched = [m.group(0).strip() for m in pattern.finditer(text)]
        assert any(
            piece.startswith("public final class ViewController") for piece in matched
        )
        assert any(piece.startswith("struct Point") for piece in matched)
        assert any(piece.startswith("enum Color") for piece in matched)
        assert any(piece.startswith("protocol Drawable") for piece in matched)
        assert any(piece.startswith("extension ViewController") for piece in matched)
        assert any(piece.startswith("actor Bank") for piece in matched)
        assert any("func greet" in piece for piece in matched)
        assert any("func make" in piece for piece in matched)
        assert not any(
            re.search(
                r"\b(?:class|struct|enum|protocol|actor|extension)\s+func\b", piece
            )
            and not re.search(r"\bfunc\s+\w+", piece)
            for piece in matched
        )


class TestSwiftFindDefinition:
    def test_find_definition_resolves_view_controller(self, tmp_path):
        source = tmp_path / "ViewController.swift"
        source.write_text(
            "import UIKit\n\n"
            "public class ViewController: UIViewController {\n"
            "    override func viewDidLoad() {\n"
            "        super.viewDidLoad()\n"
            "    }\n"
            "}\n"
        )
        engine = Stele(
            storage_dir=str(tmp_path / "store"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        try:
            engine.index_documents([str(source)])
            result = engine.find_definition("ViewController")
        finally:
            engine.storage.close()

        assert result["count"] >= 1
        definition = result["definitions"][0]
        assert definition["kind"] == "class"
        assert definition["symbol"] == "ViewController"
        assert definition["document_path"].endswith("ViewController.swift")


def test_tree_sitter_swift_chunks_declarations():
    """Chunk .swift with tree-sitter-swift when that extra is installed."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_swift")
    from stele_context.chunkers.code import CodeChunker

    chunker = CodeChunker(chunk_size=30)
    code = (
        "import UIKit\n"
        "public class ViewController: UIViewController {\n"
        "    override func viewDidLoad() {}\n"
        "}\n"
        "struct Point { var x: Int }\n"
        "actor Bank { func deposit() {} }\n"
    )
    chunks = chunker.chunk(code, "ViewController.swift")
    assert chunks
    assert all(chunk.metadata["language"] == "swift" for chunk in chunks)
    blob = "\n".join(chunk.content for chunk in chunks)
    assert "class ViewController" in blob
    assert "struct Point" in blob
    assert "actor Bank" in blob
