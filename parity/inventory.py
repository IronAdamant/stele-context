"""Extension inventory taken from the live 1.5.2 modules.

Every extension ``code_patterns.py`` and ``CodeChunker`` know about is listed.
Shell and data extensions are chunked but have no symbol extractor; that is
the 1.5.2 behavior and the parity target, not an omission.
"""

from __future__ import annotations

from stele_context.chunkers.code import CodeChunker
from stele_context.chunkers.code_patterns import EXT_TO_GRAMMAR, REGEX_PATTERNS
from stele_context.chunkers.text import TextChunker

# Mirrors SymbolExtractor.extract. A probe test locks this to observed behavior.
EXTRACTOR_BY_EXT: dict[str, str] = {
    "py": "python-ast",
    "pyw": "python-ast",
    "pyx": "python-ast",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "javascript",
    "tsx": "javascript",
    "html": "html",
    "htm": "html",
    "css": "css",
    "scss": "css",
    "less": "css",
    "java": "java",
    "kt": "java",
    "kts": "java",
    "scala": "java",
    "go": "go",
    "rs": "rust",
    "cs": "csharp",
    "swift": "swift",
    "c": "c",
    "cpp": "c",
    "cc": "c",
    "cxx": "c",
    "h": "c",
    "hpp": "c",
    "hxx": "c",
    "rb": "ruby",
    "php": "php",
    "sh": "none",
    "bash": "none",
    "zsh": "none",
    "json": "none",
    "yaml": "none",
    "yml": "none",
    "toml": "none",
    "ini": "none",
    "cfg": "none",
    "sql": "none",
}


def _exts(values: list[str]) -> set[str]:
    return {ext.removeprefix(".").lower() for ext in values}


def build_inventory() -> dict:
    """Snapshot the extensions the installed package actually chunks."""
    code = _exts(CodeChunker().supported_extensions())
    text = _exts(TextChunker().supported_extensions())
    missing = sorted(code - EXTRACTOR_BY_EXT.keys())
    if missing:
        raise RuntimeError(
            "CodeChunker gained extensions with no extractor mapping: "
            + ", ".join(missing)
        )
    extra = sorted(set(EXTRACTOR_BY_EXT) - code)
    if extra:
        raise RuntimeError(
            "extractor map lists extensions CodeChunker does not chunk: "
            + ", ".join(extra)
        )
    rows = []
    for ext in sorted(code):
        grammar = EXT_TO_GRAMMAR.get(ext)
        rows.append(
            {
                "ext": ext,
                "chunker": "code",
                "extractor": EXTRACTOR_BY_EXT[ext],
                "grammar_module": grammar[0] if grammar else None,
                "grammar_key": grammar[1] if grammar else None,
                "regex_pattern": ext in REGEX_PATTERNS,
                "regex_fallback": "js" if ext not in REGEX_PATTERNS else ext,
            }
        )
    for ext in sorted(text):
        rows.append(
            {
                "ext": ext,
                "chunker": "text",
                "extractor": "none",
                "grammar_module": None,
                "grammar_key": None,
                "regex_pattern": False,
                "regex_fallback": None,
            }
        )
    return {
        "reference": "tree-sitter-off",
        "note": (
            "Tree-sitter is optional in 1.5.2 and dropped in 2.0 (D3). "
            "The parity reference is the stdlib path: ast for Python, the "
            "regex extractors otherwise, and the regex or line chunker when "
            "tree-sitter is not installed. regex_fallback 'js' means "
            "get_regex_pattern returns the JavaScript pattern."
        ),
        "extensions": rows,
    }
