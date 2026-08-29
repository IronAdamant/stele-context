"""
Regex-based symbol extraction patterns for Stele.

Per-language extraction functions that produce Symbol instances from code
content using regular expressions. Each function handles one language family.

This module is standalone — zero internal Stele dependencies. The Symbol
dataclass is defined here and re-exported by symbols.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class Symbol:
    """A symbol extracted from a code chunk."""

    name: str
    kind: str  # function, class, variable, module, css_class, css_id
    role: str  # definition, reference
    chunk_id: str
    document_path: str
    line_number: int | None = None
    container: str | None = None  # e.g. "ClassName" or "ClassName.methodName"


# -- Python regex fallback ---------------------------------------------------


def extract_python_regex(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Regex fallback for Python when AST parsing fails."""
    symbols: list[Symbol] = []

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(r"(?:async\s+)?def\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )
            continue
        m = re.match(r"class\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )
            continue

        m = re.match(r"from\s+([\w.]+)\s+import\s+(.+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )
            for name in re.findall(r"\b(\w+)\b", m.group(2)):
                if name not in ("as", "import"):
                    symbols.append(
                        Symbol(name, "import", "reference", chunk_id, doc_path, i)
                    )
            continue
        m = re.match(r"import\s+([\w.]+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

    return symbols


# -- JavaScript / TypeScript -------------------------------------------------


def extract_javascript(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from JavaScript/TypeScript.

    Handles multi-line ``const``/``let``/``var`` declarations and tracks
    scope containers (class / function names) so that symbols carry
    ``container`` information for more precise coupling and impact analysis.
    """
    symbols: list[Symbol] = []

    def _emit(
        name: str,
        kind: str,
        role: str,
        line: int,
        container: str | None = None,
    ) -> None:
        symbols.append(Symbol(name, kind, role, chunk_id, doc_path, line, container))

    def _container() -> str | None:
        return ".".join(s[0] for s in scope_stack) if scope_stack else None

    # Pre-pass: destructured module.exports = { X, Y, Alias: Original, ...require('./x') }
    # Use a brace-depth scan so multi-line export objects are handled.
    for m_dexp in re.finditer(r"module\.exports\s*=\s*\{", content):
        start = m_dexp.end()
        depth = 1
        i = start
        while i < len(content) and depth:
            ch = content[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            i += 1
        inner = content[start : i - 1] if depth == 0 else content[start:]
        exp_line = content[: m_dexp.start()].count("\n") + 1
        # Split on commas not inside nested braces (shallow entries only).
        parts: list[str] = []
        buf = ""
        nest = 0
        for ch in inner:
            if ch == "{":
                nest += 1
            elif ch == "}":
                nest -= 1
            if ch == "," and nest == 0:
                parts.append(buf)
                buf = ""
            else:
                buf += ch
        if buf.strip():
            parts.append(buf)
        for entry_str in parts:
            entry_str = entry_str.strip()
            if not entry_str:
                continue
            m_spread = re.match(
                r"\.\.\.\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", entry_str
            )
            if m_spread:
                _emit(m_spread.group(1), "module", "reference", exp_line)
                continue
            m_aliased = re.match(r"(\w+)\s*:\s*(\w+)", entry_str)
            if m_aliased:
                _emit(m_aliased.group(1), "variable", "definition", exp_line)
                _emit(m_aliased.group(2), "variable", "reference", exp_line)
                continue
            m_simple = re.match(r"(\w+)\s*$", entry_str)
            if m_simple:
                # Shorthand property = re-export of an in-scope definition name
                _emit(m_simple.group(1), "variable", "reference", exp_line)

    # ES export { name as alias, name2 } and export { name as alias } from 'mod'
    for m_expb in re.finditer(
        r"export\s*\{([^}]+)\}(?:\s*from\s*['\"]([^'\"]+)['\"])?", content
    ):
        inner = m_expb.group(1)
        from_mod = m_expb.group(2)
        exp_line = content[: m_expb.start()].count("\n") + 1
        if from_mod:
            _emit(from_mod, "module", "reference", exp_line)
        for entry_str in inner.split(","):
            entry_str = entry_str.strip()
            if not entry_str:
                continue
            m_as = re.match(r"(\w+)\s+as\s+(\w+)", entry_str)
            if m_as:
                orig, alias = m_as.group(1), m_as.group(2)
                _emit(alias, "variable", "definition", exp_line)
                _emit(orig, "variable", "reference", exp_line)
                continue
            m_simple = re.match(r"(\w+)\s*$", entry_str)
            if m_simple:
                name = m_simple.group(1)
                if from_mod:
                    _emit(name, "import", "reference", exp_line)
                else:
                    _emit(name, "variable", "reference", exp_line)

    # exports.Name = Original  /  exports.Name = class/function ...
    for m_ex in re.finditer(r"exports\.(\w+)\s*=\s*(?:new\s+)?(\w+)", content):
        exp_line = content[: m_ex.start()].count("\n") + 1
        _emit(m_ex.group(1), "variable", "definition", exp_line)
        if m_ex.group(2) not in ("function", "class", "async", "require"):
            _emit(m_ex.group(2), "variable", "reference", exp_line)

    # State
    pending_name: str = ""
    pending_line: int = 0
    paren_brace_depth: int = 0
    brace_depth: int = 0
    scope_stack: list[tuple[str, int]] = []  # (name, brace_depth_at_entry)

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        # -- Multi-line declaration accumulation ------------------------------
        if pending_name:
            for ch in line:
                if ch == "(" or ch == "{":
                    paren_brace_depth += 1
                elif ch == ")" or ch == "}":
                    paren_brace_depth -= 1
            if paren_brace_depth <= 0:
                _emit(
                    pending_name, "variable", "definition", pending_line, _container()
                )
                pending_name = ""
                paren_brace_depth = 0

        # -- Scope exit: pop scopes whose braces closed on previous lines -----
        while scope_stack and scope_stack[-1][1] >= brace_depth:
            scope_stack.pop()

        # -- Definitions ------------------------------------------------------
        m = re.match(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", stripped)
        if m:
            _emit(m.group(1), "function", "definition", i, _container())
            scope_stack.append((m.group(1), brace_depth))

        m = re.match(r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", stripped)
        if m:
            _emit(m.group(1), "class", "definition", i, _container())
            scope_stack.append((m.group(1), brace_depth))

        m_exp = re.match(r"module\.exports\s*=\s*(?:new\s+)?(\w+)", stripped)
        if m_exp:
            _emit(m_exp.group(1), "class", "definition", i)

        m_req = re.match(
            r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)",
            stripped,
        )
        if m_req:
            req_path = m_req.group(2)
            if req_path.startswith((".", "/")):
                _emit(m_req.group(1), "import", "reference", i, _container())
        else:
            m_var = re.match(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=", stripped)
            if m_var:
                rhs = stripped[stripped.index("=") + 1 :]
                if "(" in rhs or "{" in rhs:
                    pending_name = m_var.group(1)
                    pending_line = i
                    paren_brace_depth = 0
                    for ch in line:
                        if ch == "(" or ch == "{":
                            paren_brace_depth += 1
                        elif ch == ")" or ch == "}":
                            paren_brace_depth -= 1
                    if paren_brace_depth <= 0:
                        _emit(
                            pending_name,
                            "variable",
                            "definition",
                            pending_line,
                            _container(),
                        )
                        pending_name = ""
                        paren_brace_depth = 0
                else:
                    _emit(m_var.group(1), "variable", "definition", i, _container())
                    rhs_ident = rhs.strip().rstrip(";").strip()
                    m_rhs = re.match(r"^(\w+)$", rhs_ident)
                    if m_rhs and m_rhs.group(1) != m_var.group(1):
                        _emit(m_rhs.group(1), "variable", "reference", i, _container())

        if not pending_name:
            m = re.match(r"\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{", line)
            if m and not re.match(
                r"\s*(if|for|while|switch|catch|function|return)\b", line
            ):
                _emit(m.group(1), "function", "definition", i, _container())
                scope_stack.append((m.group(1), brace_depth))

        m = re.match(r"(?:export\s+)?(?:interface|type)\s+(\w+)", stripped)
        if m:
            _emit(m.group(1), "class", "definition", i, _container())

        # -- References -------------------------------------------------------
        m_imp = re.match(r"import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]", stripped)
        if m_imp:
            _emit(m_imp.group(2), "module", "reference", i, _container())
            for name in re.findall(r"\b(\w+)\b", m_imp.group(1)):
                if name not in ("import", "as", "default", "type", "from"):
                    _emit(name, "import", "reference", i, _container())

        m_dreq = re.match(
            r"(?:const|let|var)\s+\{([^}]+)\}\s*=\s*require\(['\"]([^'\"]+)['\"]\)",
            stripped,
        )
        if m_dreq:
            _emit(m_dreq.group(2), "module", "reference", i, _container())
            for name in re.findall(r"\b(\w+)\b", m_dreq.group(1)):
                if name != "as":
                    _emit(name, "import", "reference", i, _container())

        for m in re.finditer(r"require\(['\"]([^'\"]+)['\"]\)", stripped):
            _emit(m.group(1), "module", "reference", i, _container())

        _CALL_SKIP_WORDS = {
            "if",
            "else",
            "for",
            "while",
            "switch",
            "catch",
            "return",
            "throw",
            "new",
            "delete",
            "typeof",
            "import",
            "export",
            "default",
            "case",
            "break",
            "continue",
            "try",
            "finally",
            "do",
            "with",
            "function",
            "class",
            "extends",
            "implements",
            "interface",
            "enum",
            "const",
            "let",
            "var",
        }
        for m in re.finditer(r"(?:\.(\w+)|(?:^|[^\.\w])(\w+))\s*\(", stripped):
            func_name = m.group(1) or m.group(2)
            if func_name and func_name not in _CALL_SKIP_WORDS:
                _emit(func_name, "function", "reference", i, _container())

        for m in re.finditer(r"new\s+(\w+)", stripped):
            class_name = m.group(1)
            if class_name not in _CALL_SKIP_WORDS:
                _emit(class_name, "class", "reference", i, _container())

        # DOM API
        for m in re.finditer(r"querySelector(?:All)?\(['\"]([^'\"]+)['\"]\)", stripped):
            selector = m.group(1)
            for cls in re.findall(r"\.([a-zA-Z_][\w-]*)", selector):
                _emit(f".{cls}", "css_class", "reference", i)
            for id_ in re.findall(r"#([a-zA-Z_][\w-]*)", selector):
                _emit(f"#{id_}", "css_id", "reference", i)

        for m in re.finditer(r"getElementById\(['\"]([^'\"]+)['\"]\)", stripped):
            _emit(f"#{m.group(1)}", "css_id", "reference", i)

        for m in re.finditer(
            r"getElementsByClassName\(['\"]([^'\"]+)['\"]\)", stripped
        ):
            for cls in m.group(1).split():
                _emit(f".{cls}", "css_class", "reference", i)

        for m in re.finditer(
            r"classList\.(?:add|remove|toggle|contains)\(['\"]([^'\"]+)['\"]\)",
            stripped,
        ):
            _emit(f".{m.group(1)}", "css_class", "reference", i)

        # -- Update brace depth for next iteration ----------------------------
        brace_delta = line.count("{") - line.count("}")
        brace_depth += brace_delta

    if pending_name:
        _emit(pending_name, "variable", "definition", pending_line, _container())

    return symbols


# -- HTML --------------------------------------------------------------------


def extract_html(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from HTML."""
    symbols: list[Symbol] = []

    # CSS class references
    for m in re.finditer(r'class\s*=\s*["\']([^"\']+)["\']', content):
        for cls in m.group(1).split():
            symbols.append(
                Symbol(f".{cls}", "css_class", "reference", chunk_id, doc_path)
            )

    # ID references
    for m in re.finditer(r'id\s*=\s*["\']([^"\']+)["\']', content):
        symbols.append(
            Symbol(f"#{m.group(1)}", "css_id", "reference", chunk_id, doc_path)
        )

    # Script src references
    for m in re.finditer(r'<script[^>]+src\s*=\s*["\']([^"\']+)["\']', content):
        symbols.append(Symbol(m.group(1), "module", "reference", chunk_id, doc_path))

    # Link href references (stylesheets)
    for m in re.finditer(r'<link[^>]+href\s*=\s*["\']([^"\']+)["\']', content):
        href = m.group(1)
        if href.endswith((".css", ".scss", ".less")):
            symbols.append(Symbol(href, "module", "reference", chunk_id, doc_path))

    # Inline event handlers
    for m in re.finditer(r'on\w+\s*=\s*["\'](\w+)\s*\(', content):
        symbols.append(Symbol(m.group(1), "function", "reference", chunk_id, doc_path))

    return symbols


# -- CSS / SCSS / LESS -------------------------------------------------------


def extract_css(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from CSS/SCSS/LESS."""
    symbols: list[Symbol] = []

    # Strip comments
    clean = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)

    # Class definitions
    for m in re.finditer(r"\.([a-zA-Z_][\w-]*)\s*[{,:\s]", clean):
        symbols.append(
            Symbol(f".{m.group(1)}", "css_class", "definition", chunk_id, doc_path)
        )

    # ID definitions
    for m in re.finditer(r"#([a-zA-Z_][\w-]*)\s*[{,:\s]", clean):
        symbols.append(
            Symbol(f"#{m.group(1)}", "css_id", "definition", chunk_id, doc_path)
        )

    # @import references
    for m in re.finditer(r'@import\s+["\']([^"\']+)["\']', clean):
        symbols.append(Symbol(m.group(1), "module", "reference", chunk_id, doc_path))

    # url() references
    for m in re.finditer(r'url\(["\']?([^)"\'\s]+)["\']?\)', clean):
        symbols.append(Symbol(m.group(1), "module", "reference", chunk_id, doc_path))

    return symbols


# -- Java / Kotlin / Scala ---------------------------------------------------


def extract_java(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from Java/Kotlin/Scala."""
    symbols: list[Symbol] = []
    _skip = {"if", "while", "for", "switch", "catch", "return", "new", "throw"}

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(
            r"(?:public\s+|private\s+|protected\s+)?"
            r"(?:abstract\s+)?(?:static\s+)?(?:final\s+)?"
            r"(?:class|interface|enum)\s+(\w+)",
            stripped,
        )
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        m = re.match(
            r"(?:public\s+|private\s+|protected\s+)?"
            r"(?:abstract\s+)?(?:static\s+)?(?:final\s+)?"
            r"(?:synchronized\s+)?[\w<>\[\]]+\s+(\w+)\s*\(",
            stripped,
        )
        if m and m.group(1) not in _skip:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r"import\s+(?:static\s+)?([\w.]+)(?:\.\*)?;", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )
            parts = m.group(1).rsplit(".", 1)
            if len(parts) > 1:
                symbols.append(
                    Symbol(parts[-1], "import", "reference", chunk_id, doc_path, i)
                )

    return symbols


# -- Go ----------------------------------------------------------------------


def extract_go(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from Go."""
    symbols: list[Symbol] = []

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(r"func\s+(?:\([^)]+\)\s+)?(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r"type\s+(\w+)\s+(?:struct|interface)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r'"([\w./]+)"', stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

    return symbols


# -- Rust --------------------------------------------------------------------


def extract_rust(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from Rust."""
    symbols: list[Symbol] = []

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r"(?:pub\s+)?(?:struct|enum|trait)\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r"(?:pub\s+)?impl(?:<[^>]+>)?\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "reference", chunk_id, doc_path, i)
            )

        m = re.match(r"use\s+([\w:]+)", stripped)
        if m:
            path = m.group(1)
            symbols.append(Symbol(path, "module", "reference", chunk_id, doc_path, i))
            parts = path.split("::")
            if len(parts) > 1:
                symbols.append(
                    Symbol(parts[-1], "import", "reference", chunk_id, doc_path, i)
                )

    return symbols


# -- C / C++ / C# -----------------------------------------------------------


def extract_c(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from C/C++/C#."""
    symbols: list[Symbol] = []
    _skip = {"if", "while", "for", "switch", "return", "sizeof", "typeof"}

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(r'#include\s+[<"]([^>"]+)[>"]', stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

        m = re.match(r"(?:class|struct|enum)\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r"(?:[\w:*&]+\s+)+(\w+)\s*\([^)]*\)\s*\{", stripped)
        if m and m.group(1) not in _skip:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )

        # using/namespace (C#/C++)
        m = re.match(r"using\s+([\w.]+)\s*;", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

    return symbols


# -- Ruby --------------------------------------------------------------------


def extract_ruby(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from Ruby."""
    symbols: list[Symbol] = []

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(r"def\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )
        m = re.match(r"class\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )
        m = re.match(r"module\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )
        m = re.match(r"require\s+['\"]([^'\"]+)['\"]", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

    return symbols


# -- PHP ---------------------------------------------------------------------


def extract_php(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from PHP."""
    symbols: list[Symbol] = []

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(r"(?:abstract\s+)?(?:class|interface|trait)\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )
        m = re.match(
            r"(?:public\s+|private\s+|protected\s+)?"
            r"(?:static\s+)?function\s+(\w+)",
            stripped,
        )
        if m:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )
        m = re.match(r"use\s+([\w\\]+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

    return symbols


# -- C# ----------------------------------------------------------------------


def extract_csharp(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from C#."""
    symbols: list[Symbol] = []
    _skip = {"if", "while", "for", "foreach", "switch", "catch", "return", "new", "throw", "using", "lock"}

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(
            r"(?:public\s+|private\s+|protected\s+|internal\s+)?"
            r"(?:abstract\s+|sealed\s+|static\s+|partial\s+)*"
            r"(?:class|interface|struct|enum|record)\s+(\w+)",
            stripped,
        )
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        m = re.match(
            r"(?:public\s+|private\s+|protected\s+|internal\s+)?"
            r"(?:virtual\s+|override\s+|abstract\s+|sealed\s+|static\s+|async\s+|extern\s+|partial\s+)*"
            r"[\w<>\[\]?,\s]+?\s+(\w+)\s*\(",
            stripped,
        )
        if m and m.group(1) not in _skip:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r"(?:public\s+|private\s+)?using\s+(?:static\s+)?([\w.]+)(?:\.\*)?;", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

    return symbols


# -- Swift -------------------------------------------------------------------


def extract_swift(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from Swift."""
    symbols: list[Symbol] = []
    _skip = {"if", "while", "for", "switch", "catch", "return", "guard", "defer"}

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(
            r"(?:public\s+|private\s+|internal\s+|fileprivate\s+|open\s+)?"
            r"(?:final\s+|static\s+)?"
            r"(?:class|struct|enum|protocol|actor|extension)\s+(\w+)",
            stripped,
        )
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        m = re.match(
            r"(?:public\s+|private\s+|internal\s+|fileprivate\s+|open\s+)?"
            r"(?:static\s+|class\s+|override\s+|final\s+)?"
            r"(?:mutating\s+)?func\s+(\w+)",
            stripped,
        )
        if m:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )

        m = re.match(
            r"(?:public\s+|private\s+|internal\s+)?(?:typealias\s+(\w+))", stripped
        )
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r"(?:import|@import)\s+([\w.]+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

    return symbols


# -- Dart --------------------------------------------------------------------


def extract_dart(content: str, doc_path: str, chunk_id: str) -> list[Symbol]:
    """Extract symbols from Dart."""
    symbols: list[Symbol] = []
    _skip = {"if", "while", "for", "switch", "catch", "return", "new", "throw"}

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        m = re.match(
            r"(?:abstract\s+|sealed\s+|final\s+|base\s+|mixin\s+)*"
            r"(?:class|mixin|enum|extension)\s+(\w+)",
            stripped,
        )
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        m = re.match(
            r"(?:\w+(?:<[\w<>\[\], ?]*>)?\s+)?(\w+)\s*\([^;]*\)\s*(?:async\s*)?\{",
            stripped,
        )
        if m and m.group(1) not in _skip:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )

        m = re.match(r"(?:import|export|part)\s+['\"]([^'\"]+)['\"]", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

    return symbols
