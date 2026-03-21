"""
Code chunker for Stele.

Splits code files into semantically coherent chunks using:
  - Python stdlib ast for Python files
  - tree-sitter for JS/TS, Java, C/C++, Go, Rust, Ruby, PHP (when installed)
  - regex patterns as fallback for all languages

Tree-sitter is optional: pip install stele[tree-sitter]
"""

import ast
import re
from typing import Any, Dict, List, Optional

from stele.chunkers.base import BaseChunker, Chunk, estimate_tokens
from stele.chunkers.code_patterns import (
    DEFINITION_TYPES,
    EXT_TO_GRAMMAR,
    get_regex_pattern,
)

# ---------------------------------------------------------------------------
# Tree-sitter optional import + lazy grammar loading
# ---------------------------------------------------------------------------

try:
    import tree_sitter as _ts

    HAS_TREE_SITTER = True
except ImportError:
    _ts = None  # type: ignore[assignment]
    HAS_TREE_SITTER = False

# Grammar loaders -- each returns a tree_sitter.Language or None.
# We import individual grammar packages lazily so missing ones don't crash.

_GRAMMAR_CACHE: Dict[str, Any] = {}


def _get_ts_parser(ext: str) -> Optional[Any]:
    """Get a tree-sitter parser for a file extension, or None."""
    if not HAS_TREE_SITTER:
        return None

    info = EXT_TO_GRAMMAR.get(ext)
    if info is None:
        return None

    module_name, lang_key = info

    # Use ext-based cache key so .ts and .tsx get distinct parsers
    cache_key = f"{lang_key}_{ext}" if lang_key == "typescript" else lang_key

    if cache_key in _GRAMMAR_CACHE:
        return _GRAMMAR_CACHE[cache_key]

    try:
        import importlib

        mod = importlib.import_module(module_name)
        # TypeScript has separate .language_typescript() / .language_tsx()
        if lang_key == "typescript" and ext == "tsx":
            lang_fn = getattr(mod, "language_tsx", None) or mod.language
        elif lang_key == "typescript":
            lang_fn = getattr(mod, "language_typescript", None) or mod.language
        else:
            lang_fn = mod.language

        language = _ts.Language(lang_fn())
        parser = _ts.Parser(language)
        _GRAMMAR_CACHE[cache_key] = parser
        return parser
    except Exception:
        _GRAMMAR_CACHE[cache_key] = None
        return None


class CodeChunker(BaseChunker):
    """
    Chunker for code files.

    Uses AST parsing for Python, tree-sitter for other languages
    when available, and regex patterns as fallback.
    """

    def __init__(
        self,
        chunk_size: int = 256,
        max_chunk_size: int = 4096,
    ):
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size

    def supported_extensions(self) -> List[str]:
        """Return supported code file extensions."""
        return [
            ".py",
            ".pyw",
            ".pyx",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".mjs",
            ".cjs",
            ".java",
            ".kt",
            ".kts",
            ".scala",
            ".c",
            ".cpp",
            ".cc",
            ".cxx",
            ".h",
            ".hpp",
            ".hxx",
            ".cs",
            ".go",
            ".rs",
            ".rb",
            ".php",
            ".swift",
            ".sh",
            ".bash",
            ".zsh",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".sql",
            ".html",
            ".htm",
            ".css",
            ".scss",
            ".less",
        ]

    def chunk(
        self,
        content: Any,
        document_path: str,
        **kwargs: Any,
    ) -> List[Chunk]:
        """Split code content into chunks."""
        if not isinstance(content, str):
            content = str(content)

        ext = document_path.lower().split(".")[-1] if "." in document_path else ""

        # Python: stdlib ast (always available, most accurate)
        if ext in ("py", "pyw", "pyx"):
            return self._chunk_python(content, document_path)

        # Try tree-sitter for supported languages
        parser = _get_ts_parser(ext)
        if parser is not None:
            lang_key = EXT_TO_GRAMMAR[ext][1]
            result = self._chunk_tree_sitter(content, document_path, parser, lang_key)
            if result:
                return result

        # Regex fallback
        return self._chunk_regex(content, document_path, ext)

    # -- Tree-sitter chunking -------------------------------------------------

    def _chunk_tree_sitter(
        self,
        content: str,
        document_path: str,
        parser: Any,
        lang_key: str,
    ) -> List[Chunk]:
        """Chunk code using tree-sitter AST."""
        try:
            tree = parser.parse(content.encode("utf-8"))
        except Exception:
            return []

        root = tree.root_node
        def_types = DEFINITION_TYPES.get(lang_key, frozenset())

        # Collect top-level definition boundaries
        definitions: List[Dict[str, Any]] = []
        for child in root.children:
            if child.type in def_types:
                definitions.append(
                    {
                        "type": child.type,
                        "start_byte": child.start_byte,
                        "end_byte": child.end_byte,
                    }
                )

        if not definitions:
            return []  # signal caller to fall back to regex

        return self._boundaries_to_chunks(
            content,
            document_path,
            definitions,
            lang_key,
        )

    def _boundaries_to_chunks(
        self,
        content: str,
        document_path: str,
        definitions: List[Dict[str, Any]],
        language: str,
    ) -> List[Chunk]:
        """Convert a list of definition boundaries into Chunk objects.

        Shared by tree-sitter and Python AST paths.  Accumulates
        definitions into chunks respecting chunk_size limits.
        """
        chunks: List[Chunk] = []
        chunk_index = 0
        current_parts: List[str] = []
        current_start = 0
        current_tokens = 0
        last_end = 0

        for defn in definitions:
            start = defn["start_byte"]
            end = defn["end_byte"]

            # Pre-definition gap
            if start > last_end:
                gap = content[last_end:start]
                current_parts.append(gap)
                current_tokens += estimate_tokens(gap)

            # Definition content
            def_text = content[start:end]
            def_tokens = estimate_tokens(def_text)

            if current_tokens + def_tokens > self.chunk_size and current_parts:
                chunk_content = "".join(current_parts).strip()
                if chunk_content:
                    chunks.append(
                        Chunk(
                            content=chunk_content,
                            modality="code",
                            start_pos=current_start,
                            end_pos=current_start + len(chunk_content),
                            document_path=document_path,
                            chunk_index=chunk_index,
                            metadata={"language": language},
                        )
                    )
                    chunk_index += 1
                current_start += len("".join(current_parts))
                current_parts = []
                current_tokens = 0

            current_parts.append(def_text)
            current_tokens += def_tokens
            last_end = end

        # Trailing content
        if last_end < len(content):
            current_parts.append(content[last_end:])

        if current_parts:
            chunk_content = "".join(current_parts).strip()
            if chunk_content:
                chunks.append(
                    Chunk(
                        content=chunk_content,
                        modality="code",
                        start_pos=current_start,
                        end_pos=current_start + len(chunk_content),
                        document_path=document_path,
                        chunk_index=chunk_index,
                        metadata={"language": language},
                    )
                )

        if not chunks:
            chunks.append(
                Chunk(
                    content="",
                    modality="code",
                    start_pos=0,
                    end_pos=0,
                    document_path=document_path,
                    chunk_index=0,
                    metadata={"language": language},
                )
            )

        return chunks

    # -- Python AST chunking --------------------------------------------------

    def _chunk_python(self, content: str, document_path: str) -> List[Chunk]:
        """Chunk Python code using stdlib ast."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return self._chunk_regex(content, document_path, "py")

        lines = content.splitlines(keepends=True)

        definitions: List[Dict[str, Any]] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start_line = node.lineno - 1
                end_line = (
                    node.end_lineno
                    if hasattr(node, "end_lineno") and node.end_lineno
                    else start_line + 1
                )
                # Convert line ranges to byte offsets for shared boundary logic
                start_byte = sum(len(ln) for ln in lines[:start_line])
                end_byte = sum(len(ln) for ln in lines[:end_line])
                definitions.append(
                    {
                        "type": type(node).__name__,
                        "start_byte": start_byte,
                        "end_byte": end_byte,
                    }
                )

        if not definitions:
            return self._chunk_regex(content, document_path, "py")

        return self._boundaries_to_chunks(
            content,
            document_path,
            definitions,
            "python",
        )

    # -- Regex fallback -------------------------------------------------------

    def _chunk_regex(
        self,
        content: str,
        document_path: str,
        language: str,
    ) -> List[Chunk]:
        """Chunk code using regex patterns (fallback)."""
        chunks: List[Chunk] = []
        chunk_index = 0

        pattern = get_regex_pattern(language)
        matches = list(re.finditer(pattern, content))

        if not matches:
            return self._chunk_by_lines(content, document_path, language)

        last_end = 0
        for i, match in enumerate(matches):
            match_start = match.start()
            match_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)

            if match_start > last_end:
                pre_content = content[last_end:match_start].strip()
                if pre_content:
                    chunks.append(
                        Chunk(
                            content=pre_content,
                            modality="code",
                            start_pos=last_end,
                            end_pos=match_start,
                            document_path=document_path,
                            chunk_index=chunk_index,
                            metadata={"language": language},
                        )
                    )
                    chunk_index += 1

            def_content = content[match_start:match_end].strip()
            if def_content:
                chunks.append(
                    Chunk(
                        content=def_content,
                        modality="code",
                        start_pos=match_start,
                        end_pos=match_end,
                        document_path=document_path,
                        chunk_index=chunk_index,
                        metadata={"language": language},
                    )
                )
                chunk_index += 1

            last_end = match_end

        if last_end < len(content):
            remaining = content[last_end:].strip()
            if remaining:
                chunks.append(
                    Chunk(
                        content=remaining,
                        modality="code",
                        start_pos=last_end,
                        end_pos=len(content),
                        document_path=document_path,
                        chunk_index=chunk_index,
                        metadata={"language": language},
                    )
                )

        if not chunks:
            chunks.append(
                Chunk(
                    content="",
                    modality="code",
                    start_pos=0,
                    end_pos=0,
                    document_path=document_path,
                    chunk_index=0,
                    metadata={"language": language},
                )
            )

        return chunks

    def _chunk_by_lines(
        self,
        content: str,
        document_path: str,
        language: str,
    ) -> List[Chunk]:
        """Fallback: chunk by line count."""
        lines = content.splitlines(keepends=True)
        chunks: List[Chunk] = []
        chunk_index = 0

        total_tokens = estimate_tokens(content) if content else 1
        avg_tokens_per_line = max(1, total_tokens // max(len(lines), 1))
        lines_per_chunk = max(1, self.chunk_size // avg_tokens_per_line)

        for i in range(0, len(lines), lines_per_chunk):
            chunk_lines = lines[i : i + lines_per_chunk]
            chunk_content = "".join(chunk_lines).strip()

            if chunk_content:
                start_pos = sum(len(line) for line in lines[:i])
                chunks.append(
                    Chunk(
                        content=chunk_content,
                        modality="code",
                        start_pos=start_pos,
                        end_pos=start_pos + len(chunk_content),
                        document_path=document_path,
                        chunk_index=chunk_index,
                        metadata={"language": language},
                    )
                )
                chunk_index += 1

        return (
            chunks
            if chunks
            else [
                Chunk(
                    content="",
                    modality="code",
                    start_pos=0,
                    end_pos=0,
                    document_path=document_path,
                    chunk_index=0,
                    metadata={"language": language},
                )
            ]
        )
