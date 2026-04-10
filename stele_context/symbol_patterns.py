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


@dataclass
class Symbol:
    """A symbol extracted from a code chunk."""

    name: str
    kind: str  # function, class, variable, module, css_class, css_id
    role: str  # definition, reference
    chunk_id: str
    document_path: str
    line_number: int | None = None


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

    Handles multi-line ``const``/``let``/``var`` declarations (e.g.
    ``const x = new Foo(...)\\n  arg)\\n;``) by deferring emission when the
    RHS opens a paren or brace, then emitting once the expression is balanced.
    This prevents continuation lines like ``  new Something(...)`` from being
    falsely matched as class methods.
    """
    symbols: list[Symbol] = []

    # Pre-pass: destructured module.exports = { X, Y, Alias: Original, ...require('./x') }
    # [^}] matches across newlines, so this handles multiline blocks.
    for m_dexp in re.finditer(r"module\.exports\s*=\s*\{([^}]+)\}", content):
        inner = m_dexp.group(1)
        exp_line = content[: m_dexp.start()].count("\n") + 1
        for entry_str in inner.split(","):
            entry_str = entry_str.strip()
            if not entry_str:
                continue
            # Spread require: ...require('./path')
            m_spread = re.match(
                r"\.\.\.\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)", entry_str
            )
            if m_spread:
                symbols.append(
                    Symbol(
                        m_spread.group(1),
                        "module",
                        "reference",
                        chunk_id,
                        doc_path,
                        exp_line,
                    )
                )
                continue
            # Alias: Original (renamed re-export)
            m_aliased = re.match(r"(\w+)\s*:\s*(\w+)", entry_str)
            if m_aliased:
                symbols.append(
                    Symbol(
                        m_aliased.group(1),
                        "variable",
                        "definition",
                        chunk_id,
                        doc_path,
                        exp_line,
                    )
                )
                symbols.append(
                    Symbol(
                        m_aliased.group(2),
                        "variable",
                        "reference",
                        chunk_id,
                        doc_path,
                        exp_line,
                    )
                )
                continue
            # Simple name re-export: X  (definition already captured by class/function pattern)
            m_simple = re.match(r"(\w+)\s*$", entry_str)
            if m_simple:
                symbols.append(
                    Symbol(
                        m_simple.group(1),
                        "variable",
                        "reference",
                        chunk_id,
                        doc_path,
                        exp_line,
                    )
                )

    # State for multi-line declaration accumulation.
    # When a const/let/var starts but the RHS opens a paren/brace (e.g.
    # ``const x = new Foo(`` or ``const x = {``), we defer the symbol emission
    # until paren+brace depth returns to 0.
    pending_name: str = ""
    pending_line: int = 0
    depth: int = 0

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

        # -- Accumulation: inside a multi-line declaration --------------------
        if pending_name:
            # Count paren/brace changes in the full (unstripped) line.
            for ch in line:
                if ch == "(" or ch == "{":
                    depth += 1
                elif ch == ")" or ch == "}":
                    depth -= 1

            if depth <= 0:
                # Balanced or closed — emit the pending variable symbol.
                symbols.append(
                    Symbol(
                        pending_name,
                        "variable",
                        "definition",
                        chunk_id,
                        doc_path,
                        pending_line,
                    )
                )
                pending_name = ""
                depth = 0
                # Fall through so this line is also processed normally
                # (e.g. the `}` of an object literal).

        # -- Per-line pattern matching ----------------------------------------
        # Function definitions
        m = re.match(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
            )

        # Class definitions
        m = re.match(r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        # module.exports = ClassName / module.exports = new ClassName() / module.exports = { ... }
        # Emits the RHS class/object name as a definition so find_references
        # can resolve imports that reference the exported singleton.
        m_exp = re.match(r"module\.exports\s*=\s*(?:new\s+)?(\w+)", stripped)
        if m_exp:
            rhs_name = m_exp.group(1)
            symbols.append(
                Symbol(rhs_name, "class", "definition", chunk_id, doc_path, i)
            )

        # Non-destructured require: const X = require('path')
        # Must be checked before general variable pattern to avoid
        # classifying the variable name as a definition instead of reference.
        m_req = re.match(
            r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)",
            stripped,
        )
        if m_req:
            req_path = m_req.group(2)
            # Local requires: emit import reference for variable name so
            # find_references/impact_radius can trace the dependency.
            # External requires (no ./ or ../ prefix): skip import ref to
            # avoid spurious edges between files importing the same package.
            if req_path.startswith((".", "/")):
                symbols.append(
                    Symbol(m_req.group(1), "import", "reference", chunk_id, doc_path, i)
                )
        else:
            # Variable/const definitions (including arrow functions).
            # If the RHS opens a paren/brace, defer emission until balanced.
            m_var = re.match(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=", stripped)
            if m_var:
                rhs = stripped[stripped.index("=") + 1 :]
                if "(" in rhs or "{" in rhs:
                    pending_name = m_var.group(1)
                    pending_line = i
                    # Count parens/braces in the current line.
                    depth = 0
                    for ch in line:
                        if ch == "(" or ch == "{":
                            depth += 1
                        elif ch == ")" or ch == "}":
                            depth -= 1
                    # If depth already ≤ 0 (immediate closing), emit now.
                    if depth <= 0:
                        symbols.append(
                            Symbol(
                                pending_name,
                                "variable",
                                "definition",
                                chunk_id,
                                doc_path,
                                pending_line,
                            )
                        )
                        pending_name = ""
                        depth = 0
                else:
                    symbols.append(
                        Symbol(
                            m_var.group(1),
                            "variable",
                            "definition",
                            chunk_id,
                            doc_path,
                            i,
                        )
                    )
                    # Alias tracking: const Alias = OriginalClass
                    # Emit the RHS bare identifier as a reference so edges
                    # connect the alias to the original definition.
                    rhs_ident = rhs.strip().rstrip(";").strip()
                    m_rhs = re.match(r"^(\w+)$", rhs_ident)
                    if m_rhs and m_rhs.group(1) != m_var.group(1):
                        symbols.append(
                            Symbol(
                                m_rhs.group(1),
                                "variable",
                                "reference",
                                chunk_id,
                                doc_path,
                                i,
                            )
                        )

        # Class method definitions (indented, no function keyword).
        # Skip when inside a multi-line declaration — continuation lines
        # like ``  new Foo(...)`` or ``  await something()`` would otherwise
        # be falsely matched as class methods.
        if not pending_name:
            m = re.match(r"\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{", line)
            if m and not re.match(
                r"\s*(if|for|while|switch|catch|function|return)\b", line
            ):
                symbols.append(
                    Symbol(m.group(1), "function", "definition", chunk_id, doc_path, i)
                )

        # Interface/type definitions (TS)
        m = re.match(r"(?:export\s+)?(?:interface|type)\s+(\w+)", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "class", "definition", chunk_id, doc_path, i)
            )

        # ES6 imports
        m = re.match(r"import\s+(.+?)\s+from\s+['\"]([^'\"]+)['\"]", stripped)
        if m:
            symbols.append(
                Symbol(m.group(2), "module", "reference", chunk_id, doc_path, i)
            )
            for name in re.findall(r"\b(\w+)\b", m.group(1)):
                if name not in ("import", "as", "default", "type", "from"):
                    symbols.append(
                        Symbol(name, "import", "reference", chunk_id, doc_path, i)
                    )

        # Destructured require: const { a, b } = require('pkg')
        m = re.match(
            r"(?:const|let|var)\s+\{([^}]+)\}\s*=\s*require\(['\"]([^'\"]+)['\"]\)",
            stripped,
        )
        if m:
            symbols.append(
                Symbol(m.group(2), "module", "reference", chunk_id, doc_path, i)
            )
            for name in re.findall(r"\b(\w+)\b", m.group(1)):
                if name != "as":
                    symbols.append(
                        Symbol(name, "import", "reference", chunk_id, doc_path, i)
                    )

        # require()
        for m in re.finditer(r"require\(['\"]([^'\"]+)['\"]\)", stripped):
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
            )

        # Method / attribute call targets: obj.method()
        # Extracts 'method' as a function reference so call-chains like
        # algorithms.calculateJaccard() create edges to the method definition.
        # Skips class method definitions (lines containing '{') and keywords.
        for m in re.finditer(r"\.(\w+)\s*\(", stripped):
            method_name = m.group(1)
            # Skip if this line is a class method definition (has '{' on same line
            # after the method name, i.e. "  methodName() {").
            # Also skip noise keywords that commonly appear before '.'.
            skip_words = {
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
            }
            # Check the word before the '.' to skip constructs like "new Foo.bar()"
            # or "return obj.method()". We want bare obj.method() call sites.
            # A simple heuristic: the character immediately before the '.' should
            # be a word character (part of an identifier), not a keyword.
            start = m.start()
            if start > 0 and stripped[start - 1].isalnum():
                # e.g. "algorithms.calculateJaccard(" — valid call site
                if method_name not in skip_words:
                    symbols.append(
                        Symbol(
                            method_name,
                            "function",
                            "reference",
                            chunk_id,
                            doc_path,
                            i,
                        )
                    )

        # DOM API -- cross-language HTML/CSS references
        for m in re.finditer(r"querySelector(?:All)?\(['\"]([^'\"]+)['\"]\)", stripped):
            selector = m.group(1)
            for cls in re.findall(r"\.([a-zA-Z_][\w-]*)", selector):
                symbols.append(
                    Symbol(f".{cls}", "css_class", "reference", chunk_id, doc_path, i)
                )
            for id_ in re.findall(r"#([a-zA-Z_][\w-]*)", selector):
                symbols.append(
                    Symbol(f"#{id_}", "css_id", "reference", chunk_id, doc_path, i)
                )

        for m in re.finditer(r"getElementById\(['\"]([^'\"]+)['\"]\)", stripped):
            symbols.append(
                Symbol(f"#{m.group(1)}", "css_id", "reference", chunk_id, doc_path, i)
            )

        for m in re.finditer(
            r"getElementsByClassName\(['\"]([^'\"]+)['\"]\)", stripped
        ):
            for cls in m.group(1).split():
                symbols.append(
                    Symbol(f".{cls}", "css_class", "reference", chunk_id, doc_path, i)
                )

        for m in re.finditer(
            r"classList\.(?:add|remove|toggle|contains)\(['\"]([^'\"]+)['\"]\)",
            stripped,
        ):
            symbols.append(
                Symbol(
                    f".{m.group(1)}",
                    "css_class",
                    "reference",
                    chunk_id,
                    doc_path,
                    i,
                )
            )

    # Emit any pending declaration at EOF (e.g. no closing paren/brace reached).
    if pending_name:
        symbols.append(
            Symbol(
                pending_name,
                "variable",
                "definition",
                chunk_id,
                doc_path,
                pending_line,
            )
        )

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
