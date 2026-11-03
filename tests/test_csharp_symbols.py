"""C# declarations are definitions; calls are references."""

from __future__ import annotations

import re

from stele_context.chunkers.code_patterns import get_regex_pattern
from stele_context.symbols import SymbolExtractor

_SAMPLE = """
using System;
using Alias = System.Text;

namespace Demo
{
    public class Worker
    {
        public Worker(int seed) {}

        public async Task<int> Compute(int a)
        {
            return Compute(a);
        }

        public Task SaveAsync(int x)
        {
            await SaveAsync(x);
            throw new InvalidOperationException("nope");
        }

        public int Double(int a) => Compute(a);
    }

    public interface IStore
    {
        Task<int> Load(int id);
    }

    public record class Person(string Name);
}
"""


def _defs(syms, name=None):
    found = [s for s in syms if s.role == "definition"]
    if name is None:
        return found
    return [s for s in found if s.name == name]


class TestCSharpExtractor:
    def setup_method(self):
        self.ext = SymbolExtractor()

    def test_calls_are_not_definitions(self):
        syms = self.ext.extract(_SAMPLE, "Worker.cs", "c1", "cs")

        assert len(_defs(syms, "Compute")) == 1
        assert _defs(syms, "Compute")[0].kind == "function"
        assert any(
            s.name == "Compute" and s.role == "reference" and s.kind == "function"
            for s in syms
        )

        assert len(_defs(syms, "SaveAsync")) == 1
        assert any(
            s.name == "SaveAsync" and s.role == "reference" and s.kind == "function"
            for s in syms
        )

        assert _defs(syms, "InvalidOperationException") == []
        assert any(
            s.name == "InvalidOperationException"
            and s.role == "reference"
            and s.kind == "class"
            for s in syms
        )

    def test_bare_call_lines_define_nothing(self):
        code = "\n".join(
            [
                "return Compute(a);",
                "await SaveAsync(x);",
                'throw new InvalidOperationException("nope");',
            ]
        )
        syms = self.ext.extract(code, "Calls.cs", "c1", ".cs")
        assert [s for s in syms if s.role == "definition"] == []
        assert any(s.name == "Compute" and s.role == "reference" for s in syms)
        assert any(s.name == "SaveAsync" and s.role == "reference" for s in syms)

    def test_types_usings_and_interface_methods(self):
        syms = self.ext.extract(_SAMPLE, "Worker.cs", "c1", "csharp")
        classes = {s.name for s in _defs(syms) if s.kind == "class"}
        assert {"Worker", "IStore", "Person"} <= classes
        assert any(s.name == "Load" and s.kind == "function" for s in _defs(syms))
        assert any(s.name == "Worker" and s.kind == "function" for s in _defs(syms))
        assert any(
            s.name == "System" and s.kind == "module" and s.role == "reference"
            for s in syms
        )
        assert any(
            s.name == "System.Text" and s.kind == "module" and s.role == "reference"
            for s in syms
        )
        assert any(s.name == "Alias" and s.kind == "import" for s in syms)
        assert any(
            s.name == "Demo" and s.kind == "module" and s.role == "definition"
            for s in syms
        )
        assert _defs(syms, "Name") == []
        assert _defs(syms, "Person")[0].kind == "class"

    def test_comments_attributes_and_assignments_are_not_declarations(self):
        code = "\n".join(
            [
                "// public int Hidden(int a)",
                'string url = "http://example.com"; // public int AlsoHidden(int a)',
                "int x = Compute(a);",
                "[Obsolete] public int Marked(int a) { }",
                "public struct Box {",
                "    public int Size() { return 1; }",
                "}",
            ]
        )
        syms = self.ext.extract(code, "Edges.cs", "c1", "cs")
        defs = {(s.name, s.kind) for s in syms if s.role == "definition"}
        assert ("Hidden", "function") not in defs
        assert ("AlsoHidden", "function") not in defs
        assert ("Compute", "function") not in defs
        assert ("x", "function") not in defs
        assert ("Marked", "function") in defs
        assert ("Box", "class") in defs
        assert ("Size", "function") in defs
        assert any(s.name == "Compute" and s.role == "reference" for s in syms)

    def test_c_files_stay_on_the_c_extractor(self):
        code = "return Compute(a);\nint Compute(int a) {\n    return a;\n}\n"
        cs = self.ext.extract(code, "a.cs", "c1", "cs")
        c = self.ext.extract(code, "a.c", "c1", "c")

        def defined_on(symbols, line: int) -> set[str]:
            return {
                s.name
                for s in symbols
                if s.role == "definition" and s.line_number == line
            }

        assert defined_on(cs, 1) == set()
        assert defined_on(c, 1) == set()
        assert "Compute" in defined_on(cs, 2)
        assert "Compute" in defined_on(c, 2)


class TestCSharpChunkBoundaries:
    def test_call_lines_are_not_chunk_boundaries(self):
        pattern = get_regex_pattern("cs")
        matches = [m.group(0).strip() for m in re.finditer(pattern, _SAMPLE)]
        joined = "\n".join(matches)
        assert "class Worker" in joined
        assert "Compute(" in joined
        assert "SaveAsync(" in joined
        assert "return Compute" not in joined
        assert "SaveAsync(x)" not in joined
        assert "InvalidOperationException" not in joined
        assert "record class Person" in joined
