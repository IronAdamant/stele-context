"""C# symbol extraction.

Stdlib regex only, matching the other non-Python extractors. A declaration
is a definition. A call is a reference. Lines such as ``return Compute(a)``,
``await SaveAsync(x)``, and ``throw new InvalidOperationException(...)`` are
calls, even though a naive ``Type Name(`` pattern would capture the callee
as a definition.
"""

from __future__ import annotations

import re

from stele_context.symbol_patterns import Symbol

_MODIFIERS = frozenset(
    {
        "public",
        "private",
        "protected",
        "internal",
        "static",
        "virtual",
        "override",
        "abstract",
        "sealed",
        "async",
        "partial",
        "extern",
        "new",
        "unsafe",
        "readonly",
        "required",
        "volatile",
        "file",
        "ref",
        "event",
        "delegate",
        "const",
    }
)

# First token of a statement. Checked before modifiers are stripped so
# `return Compute(a)` cannot be read as type `return`, name `Compute`.
_STATEMENT_LEADS = frozenset(
    {
        "return",
        "await",
        "throw",
        "if",
        "else",
        "for",
        "foreach",
        "while",
        "do",
        "switch",
        "case",
        "catch",
        "finally",
        "lock",
        "yield",
        "goto",
        "break",
        "continue",
        "try",
        "checked",
        "unchecked",
        "fixed",
        "var",
        "dynamic",
        "base",
        "this",
        "new",
    }
)

_CALL_SKIP = _STATEMENT_LEADS | frozenset(
    {
        "using",
        "sizeof",
        "typeof",
        "nameof",
        "get",
        "set",
        "add",
        "remove",
        "when",
        "where",
        "and",
        "or",
        "not",
        "is",
        "as",
    }
)

_DECL_RE = re.compile(
    r"^(?P<head>.+?)\s+(?P<name>[A-Za-z_]\w*)\s*(?:<[^;{()]*>)?\s*"
    r"\((?P<args>[^;]*)\)\s*(?:where\b[^;{]*)?(?:\{|=>|;|$)"
)

_TYPE_DECL_RE = re.compile(
    r"^(?:(?:public|private|protected|internal|static|abstract|sealed|"
    r"partial|readonly|ref|file|unsafe|new)\s+)*"
    r"(?:class|interface|struct|enum|record)\s+(?:class\s+|struct\s+)?"
    r"(?P<name>[A-Za-z_]\w*)"
)

_NAMESPACE_RE = re.compile(r"^namespace\s+(?P<name>[\w.]+)\s*[{;]?$")

_USING_ALIAS_RE = re.compile(r"^using\s+(?P<alias>[\w.]+)\s*=\s*(?P<target>[\w.]+)\s*;")

_USING_RE = re.compile(r"^using\s+(?:static\s+)?(?P<name>[\w.]+)\s*;")

_CALL_RE = re.compile(r"(?<![\w])([A-Za-z_]\w*)\s*\(")

_NEW_RE = re.compile(r"\bnew\s+([A-Za-z_]\w*)")


def _code_only(line: str) -> str:
    """Drop a trailing ``//`` comment that is outside a string."""
    in_string = False
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if in_string:
            if ch == "\\" and i + 1 < len(line):
                i += 2
                continue
            if ch == quote:
                in_string = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_string = True
            quote = ch
            i += 1
            continue
        if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
            return line[:i]
        i += 1
    return line


def _strip_attributes(stripped: str) -> str:
    while stripped.startswith("["):
        end = stripped.find("]")
        if end < 0:
            break
        stripped = stripped[end + 1 :].strip()
    return stripped


def _is_declaration_head(head: str) -> bool:
    """True when ``head`` is modifiers plus a return type, not a statement."""
    if "=" in head or not head.strip():
        return False
    tokens = head.split()
    if tokens[0] in _STATEMENT_LEADS or tokens[0] == "using":
        return False
    index = 0
    while index < len(tokens) and tokens[index] in _MODIFIERS:
        index += 1
    return index < len(tokens)


def _has_access_modifier(head: str) -> bool:
    return any(token in _MODIFIERS for token in head.split())


def extract_csharp(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract C# definitions and call/type references."""
    symbols: list[Symbol] = []
    type_stack: list[tuple[str, int]] = []
    pending_type: str | None = None
    brace_depth = 0

    def _emit(name: str, kind: str, role: str, line: int) -> None:
        symbols.append(Symbol(name, kind, role, chunk_id, doc_path, line))

    for line_no, raw_line in enumerate(content.splitlines(), 1):
        code = _code_only(raw_line)
        stripped = _strip_attributes(code.strip())
        opens = code.count("{")
        closes = code.count("}")

        if pending_type and "{" in code:
            type_stack.append((pending_type, brace_depth + 1))
            pending_type = None

        current_type = type_stack[-1][0] if type_stack else None
        defined_name: str | None = None

        if stripped and not stripped.startswith("#"):
            alias = _USING_ALIAS_RE.match(stripped)
            simple_using = _USING_RE.match(stripped)
            namespace = _NAMESPACE_RE.match(stripped)
            type_decl = _TYPE_DECL_RE.match(stripped)
            if alias:
                _emit(alias.group("target"), "module", "reference", line_no)
                _emit(alias.group("alias"), "import", "reference", line_no)
            elif simple_using:
                _emit(simple_using.group("name"), "module", "reference", line_no)
            elif namespace:
                _emit(namespace.group("name"), "module", "definition", line_no)
            elif type_decl:
                type_name = type_decl.group("name")
                _emit(type_name, "class", "definition", line_no)
                defined_name = type_name
                if "{" in code:
                    type_stack.append((type_name, brace_depth + 1))
                else:
                    pending_type = type_name
            else:
                declared = _DECL_RE.match(stripped)
                if declared:
                    head = declared.group("head")
                    name = declared.group("name")
                    if _is_declaration_head(head):
                        _emit(name, "function", "definition", line_no)
                        defined_name = name
                    elif (
                        current_type
                        and name == current_type
                        and _has_access_modifier(head)
                    ):
                        _emit(name, "function", "definition", line_no)
                        defined_name = name

            for found in _NEW_RE.finditer(stripped):
                _emit(found.group(1), "class", "reference", line_no)
            for found in _CALL_RE.finditer(stripped):
                callee = found.group(1)
                if callee in _CALL_SKIP or callee == defined_name:
                    continue
                start = found.start()
                prefix = stripped[:start].rstrip()
                if prefix.endswith("new"):
                    continue
                _emit(callee, "function", "reference", line_no)

        brace_depth += opens - closes
        while type_stack and brace_depth < type_stack[-1][1]:
            type_stack.pop()

    return symbols
