"""Swift symbol extraction.

Stdlib regex only. A ``class`` / ``struct`` / ``enum`` / ``protocol`` /
``extension`` / ``actor`` declaration is a type definition. A ``func``
declaration is a function definition. ``class func`` is a method, not a
type named ``func``. A call is a reference.
"""

from __future__ import annotations

import re

from stele_context.symbol_patterns import Symbol

_ACCESS = r"(?:public|open|internal|fileprivate|private|package)(?:\s*\([^)\n]*\))?"
_TYPE_MOD = rf"(?:{_ACCESS}|final|indirect|nonisolated|distributed|override)"
_FUNC_MOD = (
    rf"(?:{_ACCESS}|final|static|class|override|mutating|nonmutating|"
    r"required|convenience|distributed|nonisolated|optional|dynamic|"
    r"infix|prefix|postfix)"
)

_TYPE_DECL_RE = re.compile(
    rf"^(?:{_TYPE_MOD}\s+)*"
    r"(?P<kind>class|struct|enum|protocol|actor|extension)\s+"
    r"(?!func\b|var\b|let\b|subscript\b|init\b|deinit\b)"
    r"(?P<name>[A-Za-z_]\w*)"
)
_FUNC_DECL_RE = re.compile(rf"^(?:{_FUNC_MOD}\s+)*func\s+(?P<name>[A-Za-z_]\w*)")
_INIT_DECL_RE = re.compile(
    rf"^(?:(?:{_ACCESS}|convenience|required|override|final)\s+)*"
    r"(?P<name>init|deinit)\b"
)
_TYPEALIAS_RE = re.compile(rf"^(?:{_ACCESS}\s+)*typealias\s+(?P<name>[A-Za-z_]\w*)")
_IMPORT_RE = re.compile(
    r"^import\s+(?:(?:class|struct|enum|protocol|func|var|let|typealias)\s+)?"
    r"(?P<name>[\w.]+)"
)
_CALL_RE = re.compile(r"(?<![\w])([A-Za-z_]\w*)\s*\(")

_CALL_SKIP = frozenset(
    {
        "if",
        "while",
        "for",
        "switch",
        "catch",
        "guard",
        "repeat",
        "defer",
        "return",
        "throw",
        "await",
        "try",
        "as",
        "is",
        "in",
        "func",
        "where",
        "some",
        "any",
        "do",
        "async",
        "throws",
        "rethrows",
        "subscript",
        "super",
        "self",
    }
)


def _visible_code(line: str, in_block: bool) -> tuple[str, bool]:
    """Drop ``//`` and ``/* */`` comments. Strings keep their contents."""
    out: list[str] = []
    i = 0
    in_string = False
    while i < len(line):
        if in_block:
            end = line.find("*/", i)
            if end < 0:
                return "".join(out), True
            i = end + 2
            in_block = False
            continue
        ch = line[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            break
        if ch == "/" and i + 1 < len(line) and line[i + 1] == "*":
            in_block = True
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out), in_block


def _strip_attributes(text: str) -> str:
    """Remove leading ``@Attr`` and ``@Attr(...)`` markers."""
    while text.startswith("@"):
        ident = re.match(r"@\w+", text)
        if ident is None:
            break
        rest = text[ident.end() :].lstrip()
        if rest.startswith("("):
            depth = 0
            closed = False
            for index, ch in enumerate(rest):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        text = rest[index + 1 :].lstrip()
                        closed = True
                        break
            if not closed:
                break
        else:
            text = rest
    return text.strip()


def extract_swift(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract Swift definitions and call references."""
    symbols: list[Symbol] = []
    in_block = False

    def _emit(name: str, kind: str, role: str, line: int) -> None:
        symbols.append(Symbol(name, kind, role, chunk_id, doc_path, line))

    for line_no, raw_line in enumerate(content.splitlines(), 1):
        code, in_block = _visible_code(raw_line, in_block)
        stripped = _strip_attributes(code.strip())
        if not stripped:
            continue

        defined_name: str | None = None
        imported = _IMPORT_RE.match(stripped)
        type_decl = _TYPE_DECL_RE.match(stripped)
        func_decl = _FUNC_DECL_RE.match(stripped)
        init_decl = _INIT_DECL_RE.match(stripped)
        alias = _TYPEALIAS_RE.match(stripped)
        if imported:
            _emit(imported.group("name"), "module", "reference", line_no)
        elif type_decl:
            defined_name = type_decl.group("name")
            _emit(defined_name, "class", "definition", line_no)
        elif func_decl:
            defined_name = func_decl.group("name")
            _emit(defined_name, "function", "definition", line_no)
        elif init_decl:
            defined_name = init_decl.group("name")
            _emit(defined_name, "function", "definition", line_no)
        elif alias:
            defined_name = alias.group("name")
            _emit(defined_name, "class", "definition", line_no)

        for found in _CALL_RE.finditer(stripped):
            callee = found.group(1)
            if callee in _CALL_SKIP or callee == defined_name:
                continue
            if found.start() > 0 and stripped[found.start() - 1] == "#":
                continue
            _emit(callee, "function", "reference", line_no)

    return symbols
