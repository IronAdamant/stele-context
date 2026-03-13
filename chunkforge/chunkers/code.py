"""
Code chunker for ChunkForge.

Splits code files into semantically coherent chunks using AST parsing
for Python and regex patterns for other languages. Zero dependencies.
"""

import ast
import re
from typing import Any, Dict, List

from chunkforge.chunkers.base import BaseChunker, Chunk


class CodeChunker(BaseChunker):
    """
    Chunker for code files.

    Uses AST parsing for Python and regex patterns for other languages
    to split code at function/class boundaries. Zero external dependencies.
    """

    def __init__(
        self,
        chunk_size: int = 256,
        max_chunk_size: int = 4096,
    ):
        """
        Initialize code chunker.

        Args:
            chunk_size: Target tokens per chunk
            max_chunk_size: Maximum tokens per chunk
        """
        self.chunk_size = chunk_size
        self.max_chunk_size = max_chunk_size

    def supported_extensions(self) -> List[str]:
        """Return supported code file extensions."""
        return [
            # Python
            ".py",
            ".pyw",
            ".pyx",
            # JavaScript/TypeScript
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".mjs",
            ".cjs",
            # Java/Kotlin/Scala
            ".java",
            ".kt",
            ".kts",
            ".scala",
            # C/C++
            ".c",
            ".cpp",
            ".cc",
            ".cxx",
            ".h",
            ".hpp",
            ".hxx",
            # C#
            ".cs",
            # Go
            ".go",
            # Rust
            ".rs",
            # Ruby
            ".rb",
            # PHP
            ".php",
            # Swift
            ".swift",
            # Shell
            ".sh",
            ".bash",
            ".zsh",
            # Config
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            # SQL
            ".sql",
            # HTML/CSS
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
        """
        Split code content into chunks.

        Args:
            content: Code content to chunk
            document_path: Path to source document
            **kwargs: Additional options

        Returns:
            List of Chunk objects
        """
        if not isinstance(content, str):
            content = str(content)

        ext = document_path.lower().split(".")[-1] if "." in document_path else ""

        # Use AST for Python
        if ext in ("py", "pyw", "pyx"):
            return self._chunk_python(content, document_path)

        # Use regex patterns for other languages
        return self._chunk_regex(content, document_path, ext)

    def _chunk_python(self, content: str, document_path: str) -> List[Chunk]:
        """
        Chunk Python code using AST parsing.

        Splits at function and class boundaries for optimal semantic coherence.
        """
        chunks: List[Chunk] = []
        chunk_index = 0

        try:
            tree = ast.parse(content)
        except SyntaxError:
            # Fall back to regex if AST parsing fails
            return self._chunk_regex(content, document_path, "py")

        lines = content.splitlines(keepends=True)

        # Extract top-level definitions
        definitions: List[Dict[str, Any]] = []

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start_line = node.lineno - 1  # 0-indexed
                end_line = (
                    node.end_lineno
                    if hasattr(node, "end_lineno") and node.end_lineno
                    else start_line + 1
                )

                definitions.append(
                    {
                        "type": "function"
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        else "class",
                        "name": node.name,
                        "start_line": start_line,
                        "end_line": end_line,
                    }
                )

        # If no definitions found, fall back to regex
        if not definitions:
            return self._chunk_regex(content, document_path, "py")

        # Create chunks from definitions
        current_chunk_lines: List[str] = []
        current_start = 0
        last_end = 0

        for defn in definitions:
            # Add lines before this definition to current chunk
            if defn["start_line"] > last_end:
                current_chunk_lines.extend(lines[last_end : defn["start_line"]])

            # Check if adding this definition would exceed chunk size
            def_lines = lines[defn["start_line"] : defn["end_line"]]
            def_tokens = sum(len(line) for line in def_lines) // 4

            current_tokens = sum(len(line) for line in current_chunk_lines) // 4

            if current_tokens + def_tokens > self.chunk_size and current_chunk_lines:
                # Create chunk from accumulated lines
                chunk_content = "".join(current_chunk_lines).strip()
                if chunk_content:
                    chunk = Chunk(
                        content=chunk_content,
                        modality="code",
                        start_pos=current_start,
                        end_pos=current_start + len(chunk_content),
                        document_path=document_path,
                        chunk_index=chunk_index,
                        metadata={"language": "python"},
                    )
                    chunks.append(chunk)
                    chunk_index += 1

                # Start new chunk
                current_start = current_start + len("".join(current_chunk_lines))
                current_chunk_lines = []

            # Add definition to current chunk
            current_chunk_lines.extend(def_lines)
            last_end = defn["end_line"]

        # Add remaining lines
        if last_end < len(lines):
            current_chunk_lines.extend(lines[last_end:])

        # Create final chunk
        if current_chunk_lines:
            chunk_content = "".join(current_chunk_lines).strip()
            if chunk_content:
                chunk = Chunk(
                    content=chunk_content,
                    modality="code",
                    start_pos=current_start,
                    end_pos=current_start + len(chunk_content),
                    document_path=document_path,
                    chunk_index=chunk_index,
                    metadata={"language": "python"},
                )
                chunks.append(chunk)

        # Handle empty content
        if not chunks:
            chunks.append(
                Chunk(
                    content="",
                    modality="code",
                    start_pos=0,
                    end_pos=0,
                    document_path=document_path,
                    chunk_index=0,
                    metadata={"language": "python"},
                )
            )

        return chunks

    def _chunk_regex(
        self,
        content: str,
        document_path: str,
        language: str,
    ) -> List[Chunk]:
        """
        Chunk code using regex patterns.

        Works for JavaScript, TypeScript, Java, C++, Go, Rust, etc.
        """
        chunks: List[Chunk] = []
        chunk_index = 0

        # Language-specific patterns for function/class definitions
        _js = r"(?:^|\n)(?:export\s+)?(?:async\s+)?function\s+\w+|(?:^|\n)(?:export\s+)?class\s+\w+|(?:^|\n)(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:function|\()"
        _ts = r"(?:^|\n)(?:export\s+)?(?:async\s+)?function\s+\w+|(?:^|\n)(?:export\s+)?(?:abstract\s+)?class\s+\w+|(?:^|\n)(?:export\s+)?interface\s+\w+|(?:^|\n)(?:export\s+)?type\s+\w+"
        _shell = r"(?:^|\n)(?:function\s+)?\w+\s*\(\s*\)\s*\{"
        patterns = {
            "js": _js,
            "jsx": _js,
            "mjs": _js,
            "cjs": _js,
            "ts": _ts,
            "tsx": _ts,
            "java": r"(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:static\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+\w+|(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?(?:native\s+)?(?:abstract\s+)?[\w<>\[\]]+\s+\w+\s*\(",
            "cpp": r"(?:^|\n)(?:[\w:]+\s+)?(?:[\w:]+\s+)?[\w:]+\s+\w+\s*\([^)]*\)\s*(?:const\s*)?\{",
            "c": r"(?:^|\n)(?:[\w*]+\s+)+\w+\s*\([^)]*\)\s*\{",
            "go": r"(?:^|\n)func\s+(?:\([^)]+\)\s+)?\w+\s*\(",
            "rs": r"(?:^|\n)(?:pub\s+)?(?:async\s+)?fn\s+\w+|(?:^|\n)(?:pub\s+)?(?:struct|enum|trait|impl)\s+\w+",
            "rb": r"(?:^|\n)def\s+\w+|(?:^|\n)class\s+\w+|(?:^|\n)module\s+\w+",
            "php": r"(?:^|\n)(?:abstract\s+)?(?:class|interface|trait)\s+\w+|(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?(?:static\s+)?function\s+\w+",
            "swift": r"(?:^|\n)(?:public\s+)?(?:private\s+)?(?:internal\s+)?(?:open\s+)?(?:final\s+)?class\s+\w+|(?:^|\n)(?:public\s+)?(?:private\s+)?(?:internal\s+)?(?:static\s+)?func\s+\w+",
            "sh": _shell,
            "bash": _shell,
            "zsh": _shell,
        }

        pattern = patterns.get(language, patterns.get("js", r"(?:^|\n)\w+"))

        # Find all matches
        matches = list(re.finditer(pattern, content))

        if not matches:
            # No patterns found, split by lines
            return self._chunk_by_lines(content, document_path, language)

        # Create chunks from matches
        last_end = 0

        for i, match in enumerate(matches):
            match_start = match.start()

            # Determine end of this definition (start of next or end of file)
            if i + 1 < len(matches):
                match_end = matches[i + 1].start()
            else:
                match_end = len(content)

            # Add content before this match
            if match_start > last_end:
                pre_content = content[last_end:match_start].strip()
                if pre_content:
                    chunk = Chunk(
                        content=pre_content,
                        modality="code",
                        start_pos=last_end,
                        end_pos=match_start,
                        document_path=document_path,
                        chunk_index=chunk_index,
                        metadata={"language": language},
                    )
                    chunks.append(chunk)
                    chunk_index += 1

            # Add this definition
            def_content = content[match_start:match_end].strip()
            if def_content:
                chunk = Chunk(
                    content=def_content,
                    modality="code",
                    start_pos=match_start,
                    end_pos=match_end,
                    document_path=document_path,
                    chunk_index=chunk_index,
                    metadata={"language": language},
                )
                chunks.append(chunk)
                chunk_index += 1

            last_end = match_end

        # Add remaining content
        if last_end < len(content):
            remaining = content[last_end:].strip()
            if remaining:
                chunk = Chunk(
                    content=remaining,
                    modality="code",
                    start_pos=last_end,
                    end_pos=len(content),
                    document_path=document_path,
                    chunk_index=chunk_index,
                    metadata={"language": language},
                )
                chunks.append(chunk)

        # Handle empty content
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

        # Estimate lines per chunk (assuming ~4 tokens per line)
        lines_per_chunk = max(1, self.chunk_size // 4)

        for i in range(0, len(lines), lines_per_chunk):
            chunk_lines = lines[i : i + lines_per_chunk]
            chunk_content = "".join(chunk_lines).strip()

            if chunk_content:
                start_pos = sum(len(line) for line in lines[:i])
                end_pos = start_pos + len(chunk_content)

                chunk = Chunk(
                    content=chunk_content,
                    modality="code",
                    start_pos=start_pos,
                    end_pos=end_pos,
                    document_path=document_path,
                    chunk_index=chunk_index,
                    metadata={"language": language},
                )
                chunks.append(chunk)
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
