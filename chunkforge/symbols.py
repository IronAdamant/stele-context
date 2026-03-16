"""
Symbol extraction and cross-file reference graph for ChunkForge.

Extracts definitions and references from code chunks, resolves cross-file
links, and provides graph queries (find_references, find_definition,
impact_radius). Zero external dependencies — uses ast and re only.
"""

import ast
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


# Names too common/ambiguous to create useful cross-file edges.
# Applied only to references (not definitions) during resolution.
_NOISE_REFS: frozenset = frozenset({
    # Python builtins (never user-defined)
    "print", "len", "range", "enumerate", "zip", "sorted", "reversed",
    "isinstance", "issubclass", "hasattr", "getattr", "setattr", "delattr",
    "super", "type", "id", "hash", "repr", "input", "open", "iter", "next",
    "abs", "min", "max", "sum", "round", "any", "all", "format", "callable",
    "str", "int", "float", "bool", "list", "dict", "set", "tuple", "bytes",
    "object", "property", "staticmethod", "classmethod",
    # Python exceptions
    "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "NotImplementedError", "StopIteration",
    "OSError", "FileNotFoundError", "ImportError",
    # Python dunder methods (every class has them)
    "__init__", "__new__", "__del__", "__str__", "__repr__", "__eq__",
    "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__hash__", "__bool__",
    "__len__", "__iter__", "__next__", "__contains__", "__call__",
    "__getitem__", "__setitem__", "__delitem__", "__getattr__", "__setattr__",
    "__enter__", "__exit__", "__add__", "__sub__", "__mul__",
    # JS/TS globals
    "console", "log", "error", "warn", "info", "debug",
    "setTimeout", "setInterval", "parseInt", "parseFloat", "isNaN",
    "then", "catch", "resolve", "reject", "finally",
    "toString", "valueOf", "toJSON",
    "Array", "Object", "String", "Number", "Boolean", "Map", "Set",
    "Promise", "Error", "Date", "RegExp", "JSON", "Math",
    # Ambiguous method names (defined on too many types)
    "get", "set", "add", "remove", "pop", "push", "append", "extend",
    "update", "clear", "copy", "keys", "values", "items", "entries",
    "find", "findIndex", "indexOf", "includes", "contains",
    "join", "split", "replace", "match", "test", "search", "trim",
    "forEach", "reduce", "slice", "splice",
    "close", "read", "write", "flush", "seek",
    "apply", "call", "bind",
    "start", "stop", "run", "execute",
    # Context names
    "self", "cls", "this",
})


@dataclass
class Symbol:
    """A symbol extracted from a code chunk."""

    name: str
    kind: str  # function, class, variable, module, css_class, css_id
    role: str  # definition, reference
    chunk_id: str
    document_path: str
    line_number: Optional[int] = None


class SymbolExtractor:
    """Extract symbols from code content by language.

    Supports Python (AST), JS/TS, HTML, CSS, Java, Go, Rust, C/C++.
    All extraction uses stdlib only (ast + re).
    """

    def extract(
        self,
        content: str,
        document_path: str,
        chunk_id: str,
        language: str,
    ) -> List[Symbol]:
        """Extract symbols from a chunk's content.

        Args:
            content: The text content of the chunk
            document_path: Source file path
            chunk_id: ID of the chunk containing this content
            language: File extension (without dot) or language name
        """
        ext = language.lstrip(".").lower()

        if ext in ("py", "pyw", "pyx"):
            return self._extract_python(content, document_path, chunk_id)
        if ext in ("js", "jsx", "mjs", "cjs", "ts", "tsx"):
            return self._extract_javascript(content, document_path, chunk_id)
        if ext in ("html", "htm"):
            return self._extract_html(content, document_path, chunk_id)
        if ext in ("css", "scss", "less"):
            return self._extract_css(content, document_path, chunk_id)
        if ext in ("java", "kt", "kts", "scala"):
            return self._extract_java(content, document_path, chunk_id)
        if ext == "go":
            return self._extract_go(content, document_path, chunk_id)
        if ext == "rs":
            return self._extract_rust(content, document_path, chunk_id)
        if ext in ("c", "cpp", "cc", "cxx", "h", "hpp", "hxx", "cs"):
            return self._extract_c(content, document_path, chunk_id)
        if ext in ("rb",):
            return self._extract_ruby(content, document_path, chunk_id)
        if ext in ("php",):
            return self._extract_php(content, document_path, chunk_id)
        return []

    # -- Python (AST with regex fallback) ------------------------------------

    def _extract_python(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from Python using AST, with regex fallback."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._extract_python_regex(content, doc_path, chunk_id)

        symbols: List[Symbol] = []

        for node in ast.walk(tree):
            line = getattr(node, "lineno", None)

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    Symbol(node.name, "function", "definition", chunk_id, doc_path, line)
                )
            elif isinstance(node, ast.ClassDef):
                symbols.append(
                    Symbol(node.name, "class", "definition", chunk_id, doc_path, line)
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    symbols.append(
                        Symbol(alias.name, "module", "reference", chunk_id, doc_path, line)
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    symbols.append(
                        Symbol(node.module, "module", "reference", chunk_id, doc_path, line)
                    )
                for alias in node.names or []:
                    if alias.name != "*":
                        symbols.append(
                            Symbol(
                                alias.name, "import", "reference", chunk_id, doc_path, line
                            )
                        )
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    symbols.append(
                        Symbol(
                            node.func.id, "function", "reference", chunk_id, doc_path, line
                        )
                    )
                elif isinstance(node.func, ast.Attribute):
                    symbols.append(
                        Symbol(
                            node.func.attr, "function", "reference", chunk_id, doc_path, line
                        )
                    )

        return symbols

    def _extract_python_regex(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Regex fallback for Python when AST parsing fails."""
        symbols: List[Symbol] = []

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

    # -- JavaScript / TypeScript ---------------------------------------------

    def _extract_javascript(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from JavaScript/TypeScript."""
        symbols: List[Symbol] = []

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
            if m and not re.match(r"\s*(if|for|while|switch|catch)\b", line):
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

            # DOM API — cross-language HTML/CSS references
            for m in re.finditer(
                r"querySelector(?:All)?\(['\"]([^'\"]+)['\"]\)", stripped
            ):
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
                        Symbol(
                            f".{cls}", "css_class", "reference", chunk_id, doc_path, i
                        )
                    )

            for m in re.finditer(
                r"classList\.(?:add|remove|toggle|contains)\(['\"]([^'\"]+)['\"]\)",
                stripped,
            ):
                symbols.append(
                    Symbol(
                        f".{m.group(1)}", "css_class", "reference", chunk_id, doc_path, i
                    )
                )

        return symbols

    # -- HTML ----------------------------------------------------------------

    def _extract_html(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from HTML."""
        symbols: List[Symbol] = []

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
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path)
            )

        # Link href references (stylesheets)
        for m in re.finditer(r'<link[^>]+href\s*=\s*["\']([^"\']+)["\']', content):
            href = m.group(1)
            if href.endswith((".css", ".scss", ".less")):
                symbols.append(
                    Symbol(href, "module", "reference", chunk_id, doc_path)
                )

        # Inline event handlers
        for m in re.finditer(r'on\w+\s*=\s*["\'](\w+)\s*\(', content):
            symbols.append(
                Symbol(m.group(1), "function", "reference", chunk_id, doc_path)
            )

        return symbols

    # -- CSS / SCSS / LESS ---------------------------------------------------

    def _extract_css(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from CSS/SCSS/LESS."""
        symbols: List[Symbol] = []

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
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path)
            )

        # url() references
        for m in re.finditer(r'url\(["\']?([^)"\'\s]+)["\']?\)', clean):
            symbols.append(
                Symbol(m.group(1), "module", "reference", chunk_id, doc_path)
            )

        return symbols

    # -- Java / Kotlin / Scala -----------------------------------------------

    def _extract_java(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from Java/Kotlin/Scala."""
        symbols: List[Symbol] = []
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

    # -- Go ------------------------------------------------------------------

    def _extract_go(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from Go."""
        symbols: List[Symbol] = []

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

            m = re.match(r'\s*"([\w./]+)"', stripped)
            if m:
                symbols.append(
                    Symbol(m.group(1), "module", "reference", chunk_id, doc_path, i)
                )

        return symbols

    # -- Rust ----------------------------------------------------------------

    def _extract_rust(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from Rust."""
        symbols: List[Symbol] = []

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
                symbols.append(
                    Symbol(path, "module", "reference", chunk_id, doc_path, i)
                )
                parts = path.split("::")
                if len(parts) > 1:
                    symbols.append(
                        Symbol(parts[-1], "import", "reference", chunk_id, doc_path, i)
                    )

        return symbols

    # -- C / C++ / C# -------------------------------------------------------

    def _extract_c(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from C/C++/C#."""
        symbols: List[Symbol] = []
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

            m = re.match(r"(?:[\w:*&]+\s+)+(\w+)\s*\([^)]*\)\s*\{?", stripped)
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

    # -- Ruby ----------------------------------------------------------------

    def _extract_ruby(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from Ruby."""
        symbols: List[Symbol] = []

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

    # -- PHP -----------------------------------------------------------------

    def _extract_php(
        self, content: str, doc_path: str, chunk_id: str
    ) -> List[Symbol]:
        """Extract symbols from PHP."""
        symbols: List[Symbol] = []

        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()

            m = re.match(
                r"(?:abstract\s+)?(?:class|interface|trait)\s+(\w+)", stripped
            )
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


# -- Resolution --------------------------------------------------------------


def _build_module_hints(symbols: List[Symbol]) -> Dict[str, Set[str]]:
    """Build chunk_id -> set of module paths referenced in that chunk.

    Used to prefer definitions from imported modules over unrelated
    files that happen to define a symbol with the same name.
    """
    hints: Dict[str, Set[str]] = {}
    for sym in symbols:
        if sym.role == "reference" and sym.kind == "module":
            hints.setdefault(sym.chunk_id, set()).add(sym.name)
    return hints


def _module_matches_path(module_name: str, file_path: str) -> bool:
    """Check if a dotted module name plausibly maps to a file path.

    Examples:
      'chunkforge.engine' matches '.../chunkforge/engine.py'
      'os.path'           matches '.../os/path.py' or '.../os/path/__init__.py'
      'utils'             matches '.../utils.py' or '.../utils/__init__.py'
    """
    # Convert dotted module to path segments
    parts = module_name.replace(".", "/")
    norm = file_path.replace("\\", "/")
    # Match as directory component + filename (with or without extension)
    return norm.endswith(f"/{parts}.py") or norm.endswith(f"/{parts}/__init__.py")


def resolve_symbols(symbols: List[Symbol]) -> List[Tuple[str, str, str, str]]:
    """Resolve references to definitions, producing edges.

    Returns list of (source_chunk_id, target_chunk_id, edge_type, symbol_name).
    An edge means: source_chunk references a symbol defined in target_chunk.

    Module path precision: when a chunk imports ``from foo.bar import Baz``,
    the resolution prefers a ``Baz`` definition in a file matching ``foo/bar.py``
    over an unrelated ``Baz`` in another file.
    """
    # Build definition index: name -> [(chunk_id, document_path)]
    definitions: Dict[str, List[Tuple[str, str]]] = {}
    for sym in symbols:
        if sym.role == "definition":
            definitions.setdefault(sym.name, []).append(
                (sym.chunk_id, sym.document_path)
            )

    # Build module hints for path-aware resolution
    module_hints = _build_module_hints(symbols)

    # Match references to definitions
    edges: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()

    for sym in symbols:
        if sym.role != "reference":
            continue

        # Skip noisy references that create false edges
        if len(sym.name) < 2 or sym.name in _NOISE_REFS:
            continue

        defs = definitions.get(sym.name, [])
        if not defs:
            continue

        # Filter: prefer definitions from modules imported in this chunk
        hints = module_hints.get(sym.chunk_id, set())
        if hints and len(defs) > 1:
            hinted = [
                (cid, dp) for cid, dp in defs
                if any(_module_matches_path(mod, dp) for mod in hints)
            ]
            if hinted:
                defs = hinted

        for def_chunk_id, def_doc_path in defs:
            # Skip self-references within the same chunk
            if def_chunk_id == sym.chunk_id:
                continue

            edge_key = (sym.chunk_id, def_chunk_id, sym.name)
            if edge_key in seen:
                continue
            seen.add(edge_key)

            if sym.document_path == def_doc_path:
                edge_type = "intra_file"
            else:
                edge_type = "cross_file"

            edges.append((sym.chunk_id, def_chunk_id, edge_type, sym.name))

    return edges
