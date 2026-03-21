"""
Regex-based symbol extraction patterns for Stele.

Per-language extraction functions that produce Symbol instances from code
content using regular expressions. Each function handles one language family.

This module is standalone — zero internal Stele dependencies. The Symbol
dataclass is defined here and re-exported by symbols.py.
"""

import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Symbol:
    """A symbol extracted from a code chunk."""

    name: str
    kind: str  # function, class, variable, module, css_class, css_id
    role: str  # definition, reference
    chunk_id: str
    document_path: str
    line_number: Optional[int] = None


# -- Python regex fallback ---------------------------------------------------


def extract_python_regex(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Regex fallback for Python when AST parsing fails."""
    symbols: List["Symbol"] = []

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


def extract_javascript(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from JavaScript/TypeScript."""
    symbols: List["Symbol"] = []

    for i, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()

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

        # Variable/const definitions (including arrow functions)
        m = re.match(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=", stripped)
        if m:
            symbols.append(
                Symbol(m.group(1), "variable", "definition", chunk_id, doc_path, i)
            )

        # Class method definitions (indented, no function keyword)
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

    return symbols


# -- HTML --------------------------------------------------------------------


def extract_html(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from HTML."""
    symbols: List["Symbol"] = []

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


def extract_css(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from CSS/SCSS/LESS."""
    symbols: List["Symbol"] = []

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


def extract_java(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from Java/Kotlin/Scala."""
    symbols: List["Symbol"] = []
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


def extract_go(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from Go."""
    symbols: List["Symbol"] = []

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


def extract_rust(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from Rust."""
    symbols: List["Symbol"] = []

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


def extract_c(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from C/C++/C#."""
    symbols: List["Symbol"] = []
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


def extract_ruby(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from Ruby."""
    symbols: List["Symbol"] = []

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


def extract_php(content: str, doc_path: str, chunk_id: str) -> List["Symbol"]:
    """Extract symbols from PHP."""
    symbols: List["Symbol"] = []

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
