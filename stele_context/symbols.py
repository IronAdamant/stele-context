"""
Symbol extraction and cross-file reference graph for Stele.

Extracts definitions and references from code chunks, resolves cross-file
links, and provides graph queries (find_references, find_definition,
impact_radius). Zero external dependencies — uses ast and re only.

Per-language regex extraction lives in symbol_patterns.py; this module
re-exports the Symbol dataclass, owns the AST-based Python extractor,
the dispatcher class, and the resolution algorithm.
"""

from __future__ import annotations

import ast

from stele_context.symbol_patterns import (
    Symbol,
    extract_c,
    extract_css,
    extract_go,
    extract_html,
    extract_java,
    extract_javascript,
    extract_php,
    extract_python_regex,
    extract_ruby,
    extract_rust,
)

# Names too common/ambiguous to create useful cross-file edges.
# Applied only to references (not definitions) during resolution.
_NOISE_REFS: frozenset = frozenset(
    {
        # Python builtins (never user-defined)
        "print",
        "len",
        "range",
        "enumerate",
        "zip",
        "sorted",
        "reversed",
        "isinstance",
        "issubclass",
        "hasattr",
        "getattr",
        "setattr",
        "delattr",
        "super",
        "type",
        "id",
        "hash",
        "repr",
        "input",
        "open",
        "iter",
        "next",
        "abs",
        "min",
        "max",
        "sum",
        "round",
        "any",
        "all",
        "format",
        "callable",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "bytes",
        "object",
        "property",
        "staticmethod",
        "classmethod",
        # Python exceptions
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "RuntimeError",
        "NotImplementedError",
        "StopIteration",
        "OSError",
        "FileNotFoundError",
        "ImportError",
        # Python dunder methods (every class has them)
        "__init__",
        "__new__",
        "__del__",
        "__str__",
        "__repr__",
        "__eq__",
        "__ne__",
        "__lt__",
        "__le__",
        "__gt__",
        "__ge__",
        "__hash__",
        "__bool__",
        "__len__",
        "__iter__",
        "__next__",
        "__contains__",
        "__call__",
        "__getitem__",
        "__setitem__",
        "__delitem__",
        "__getattr__",
        "__setattr__",
        "__enter__",
        "__exit__",
        "__add__",
        "__sub__",
        "__mul__",
        # JS/TS globals
        "console",
        "log",
        "error",
        "warn",
        "info",
        "debug",
        "setTimeout",
        "setInterval",
        "parseInt",
        "parseFloat",
        "isNaN",
        "then",
        "catch",
        "resolve",
        "reject",
        "finally",
        "toString",
        "valueOf",
        "toJSON",
        "Array",
        "Object",
        "String",
        "Number",
        "Boolean",
        "Map",
        "Set",
        "Promise",
        "Error",
        "Date",
        "RegExp",
        "JSON",
        "Math",
        # Ambiguous method names (defined on too many types)
        "get",
        "set",
        "add",
        "remove",
        "pop",
        "push",
        "append",
        "extend",
        "update",
        "clear",
        "copy",
        "keys",
        "values",
        "items",
        "entries",
        "find",
        "findIndex",
        "indexOf",
        "includes",
        "contains",
        "join",
        "split",
        "replace",
        "match",
        "test",
        "search",
        "trim",
        "forEach",
        "reduce",
        "slice",
        "splice",
        "close",
        "read",
        "write",
        "flush",
        "seek",
        "apply",
        "start",
        "stop",
        "run",
        "execute",
        # Context names
        "self",
        "cls",
        "this",
        # Node.js stdlib modules (commonly imported as variable names)
        "path",
        "fs",
        "http",
        "https",
        "crypto",
        "os",
        "url",
        "util",
        "stream",
        "events",
        "buffer",
        "net",
        "tls",
        "dns",
        "zlib",
        "assert",
        "process",
        "child_process",
        # Generic method names that cause false coupling
        "constructor",
        "getStats",
        "toJSON",
        "toObject",
        "initialize",
        "configure",
        "validate",
        "handle",
        "emit",
        "on",
        "off",
        "once",
        "listen",
        "send",
        "receive",
        "create",
        "destroy",
        "reset",
        "connect",
        "disconnect",
        "dispatch",
        "notify",
        "render",
        "setup",
        "teardown",
    }
)


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
    ) -> list[Symbol]:
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
            return extract_javascript(content, document_path, chunk_id)
        if ext in ("html", "htm"):
            return extract_html(content, document_path, chunk_id)
        if ext in ("css", "scss", "less"):
            return extract_css(content, document_path, chunk_id)
        if ext in ("java", "kt", "kts", "scala"):
            return extract_java(content, document_path, chunk_id)
        if ext == "go":
            return extract_go(content, document_path, chunk_id)
        if ext == "rs":
            return extract_rust(content, document_path, chunk_id)
        if ext in ("c", "cpp", "cc", "cxx", "h", "hpp", "hxx", "cs"):
            return extract_c(content, document_path, chunk_id)
        if ext == "rb":
            return extract_ruby(content, document_path, chunk_id)
        if ext == "php":
            return extract_php(content, document_path, chunk_id)
        return []

    # -- Python (AST with regex fallback) ------------------------------------

    def _extract_python(
        self, content: str, doc_path: str, chunk_id: str
    ) -> list[Symbol]:
        """Extract symbols from Python using AST, with regex fallback."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return extract_python_regex(content, doc_path, chunk_id)

        symbols: list[Symbol] = []

        # Collect node IDs of ast.Name/ast.Attribute that are ast.Call.func
        # so the ast.Name pass can skip them (already captured with kind="function").
        _call_func_node_ids: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(
                node.func, (ast.Name, ast.Attribute)
            ):
                _call_func_node_ids.add(id(node.func))

        for node in ast.walk(tree):
            line = getattr(node, "lineno", None)

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                symbols.append(
                    Symbol(
                        node.name, "function", "definition", chunk_id, doc_path, line
                    )
                )
            elif isinstance(node, ast.ClassDef):
                symbols.append(
                    Symbol(node.name, "class", "definition", chunk_id, doc_path, line)
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    symbols.append(
                        Symbol(
                            alias.name, "module", "reference", chunk_id, doc_path, line
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    symbols.append(
                        Symbol(
                            node.module, "module", "reference", chunk_id, doc_path, line
                        )
                    )
                for alias in node.names or []:
                    if alias.name != "*":
                        symbols.append(
                            Symbol(
                                alias.name,
                                "import",
                                "reference",
                                chunk_id,
                                doc_path,
                                line,
                            )
                        )
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    symbols.append(
                        Symbol(
                            node.func.id,
                            "function",
                            "reference",
                            chunk_id,
                            doc_path,
                            line,
                        )
                    )
                elif isinstance(node.func, ast.Attribute):
                    symbols.append(
                        Symbol(
                            node.func.attr,
                            "function",
                            "reference",
                            chunk_id,
                            doc_path,
                            line,
                        )
                    )
            elif isinstance(node, ast.Name):
                # Capture name references beyond direct calls: function-as-value
                # (keyword args, assignments, returns, collection elements).
                # Skip names already captured as ast.Call.func (kind="function"),
                # Store-context names (assignment targets), and the discard name.
                if (
                    id(node) not in _call_func_node_ids
                    and isinstance(node.ctx, ast.Load)
                    and node.id != "_"
                ):
                    symbols.append(
                        Symbol(
                            node.id,
                            "name",
                            "reference",
                            chunk_id,
                            doc_path,
                            line,
                        )
                    )

        return symbols


# -- Resolution --------------------------------------------------------------


def _build_module_hints(symbols: list[Symbol]) -> dict[str, set[str]]:
    """Build chunk_id -> set of module paths referenced in that chunk.

    Used to prefer definitions from imported modules over unrelated
    files that happen to define a symbol with the same name.
    """
    hints: dict[str, set[str]] = {}
    for sym in symbols:
        if sym.role == "reference" and sym.kind == "module":
            hints.setdefault(sym.chunk_id, set()).add(sym.name)
    return hints


def _module_matches_path(module_name: str, file_path: str) -> bool:
    """Check if a module reference plausibly maps to a file path.

    Handles Python dotted imports, JS/TS relative require paths,
    and bare web paths from HTML <script src> / <link href>.

    Examples:
      'stele.engine'        matches '.../stele/engine.py'
      'os.path'             matches '.../os/path.py'
      '../models/Recipe'    matches '.../models/Recipe.js'
      './utils'             matches '.../utils.js' or '.../utils/index.js'
      'app.js'              matches '.../app.js'
      'js/main.js'          matches '.../js/main.js'
      '/static/app.js'      matches '.../static/app.js'
      'styles.css'          matches '.../styles.css'
    """
    norm = file_path.replace("\\", "/")

    # Python: dotted module → /foo/bar.py or /foo/bar/__init__.py
    parts = module_name.replace(".", "/")
    if norm.endswith(f"/{parts}.py") or norm.endswith(f"/{parts}/__init__.py"):
        return True

    # JS/TS: relative require path → strip ./ ../ prefix, match suffix
    if module_name.startswith(("./", "../")):
        clean = module_name
        while clean.startswith(("../", "./")):
            clean = clean[clean.index("/") + 1 :]
        if clean:
            for ext in ("", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
                suffix = f"{clean}{ext}"
                if norm.endswith(f"/{suffix}") or norm == suffix:
                    return True
            for idx_name in (f"{clean}/index.js", f"{clean}/index.ts"):
                if norm.endswith(f"/{idx_name}") or norm == idx_name:
                    return True
        return False

    # HTML bare web paths: "app.js", "js/main.js", "/static/app.css"
    # These come from <script src> and <link href> without ./ or ../ prefix.
    _WEB_EXTS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".css", ".scss", ".less")
    clean = module_name.lstrip("/")
    if clean and any(clean.endswith(ext) for ext in _WEB_EXTS):
        if norm.endswith(f"/{clean}") or norm == clean:
            return True

    return False


def resolve_symbols(symbols: list[Symbol]) -> list[tuple[str, str, str, str]]:
    """Resolve references to definitions, producing edges.

    Returns list of (source_chunk_id, target_chunk_id, edge_type, symbol_name).
    An edge means: source_chunk references a symbol defined in target_chunk.

    Module path precision: when a chunk imports ``from foo.bar import Baz``,
    the resolution prefers a ``Baz`` definition in a file matching ``foo/bar.py``
    over an unrelated ``Baz`` in another file.
    """
    # Build definition index: name -> [(chunk_id, document_path)]
    definitions: dict[str, list[tuple[str, str]]] = {}
    for sym in symbols:
        if sym.role == "definition":
            definitions.setdefault(sym.name, []).append(
                (sym.chunk_id, sym.document_path)
            )

    # Build module hints for path-aware resolution
    module_hints = _build_module_hints(symbols)

    # Build document path -> first chunk_id index for module-to-file fallback.
    # When HTML <script src="app.js"> references a file but no definition
    # name matches "app.js", this allows linking to any chunk in the target file.
    doc_chunks: dict[str, str] = {}
    for sym in symbols:
        if sym.role == "definition" and sym.document_path not in doc_chunks:
            doc_chunks[sym.document_path] = sym.chunk_id

    # Match references to definitions
    edges: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for sym in symbols:
        if sym.role != "reference":
            continue

        # Skip noisy references that create false edges
        if len(sym.name) < 2 or sym.name in _NOISE_REFS:
            continue

        defs = definitions.get(sym.name, [])

        # Module-to-file fallback: if a module reference (e.g. "app.js" from
        # HTML <script src>) has no matching definition by name, check if it
        # matches a document path directly. Creates edges to the target file.
        if not defs and sym.kind == "module":
            for dp, first_cid in doc_chunks.items():
                if _module_matches_path(sym.name, dp):
                    defs = [(first_cid, dp)]
                    break

        if not defs:
            continue

        # Filter: prefer definitions from modules imported in this chunk
        hints = module_hints.get(sym.chunk_id, set())
        if hints and len(defs) > 1:
            hinted = [
                (cid, dp)
                for cid, dp in defs
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
