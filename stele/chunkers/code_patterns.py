"""
Pattern definitions and grammar mappings for the code chunker.

Contains:
  - Tree-sitter definition node types per language
  - File extension to grammar module mapping
  - Regex patterns for fallback chunking

Only imports from stdlib; no internal Stele dependencies.
"""

from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Tree-sitter definition boundary types per language
# ---------------------------------------------------------------------------

DEFINITION_TYPES: Dict[str, frozenset] = {
    "javascript": frozenset(
        {
            "function_declaration",
            "class_declaration",
            "lexical_declaration",
            "variable_declaration",
            "export_statement",
        }
    ),
    "typescript": frozenset(
        {
            "function_declaration",
            "class_declaration",
            "lexical_declaration",
            "variable_declaration",
            "export_statement",
            "interface_declaration",
            "type_alias_declaration",
            "enum_declaration",
        }
    ),
    "java": frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "annotation_type_declaration",
            "import_declaration",
            "package_declaration",
        }
    ),
    "c": frozenset(
        {
            "function_definition",
            "declaration",
            "preproc_include",
            "preproc_define",
            "struct_specifier",
            "enum_specifier",
            "type_definition",
        }
    ),
    "cpp": frozenset(
        {
            "function_definition",
            "declaration",
            "class_specifier",
            "struct_specifier",
            "namespace_definition",
            "template_declaration",
            "preproc_include",
            "preproc_define",
            "enum_specifier",
            "type_definition",
        }
    ),
    "go": frozenset(
        {
            "function_declaration",
            "method_declaration",
            "type_declaration",
            "var_declaration",
            "const_declaration",
            "import_declaration",
        }
    ),
    "rust": frozenset(
        {
            "function_item",
            "struct_item",
            "enum_item",
            "impl_item",
            "trait_item",
            "mod_item",
            "const_item",
            "static_item",
            "type_item",
            "use_declaration",
        }
    ),
    "ruby": frozenset(
        {
            "method",
            "class",
            "module",
            "singleton_method",
        }
    ),
    "php": frozenset(
        {
            "function_definition",
            "class_declaration",
            "interface_declaration",
            "trait_declaration",
            "namespace_definition",
        }
    ),
}

# ---------------------------------------------------------------------------
# Extension -> (grammar_module_name, language_key)
# ---------------------------------------------------------------------------

EXT_TO_GRAMMAR: Dict[str, Tuple[str, str]] = {
    "js": ("tree_sitter_javascript", "javascript"),
    "jsx": ("tree_sitter_javascript", "javascript"),
    "mjs": ("tree_sitter_javascript", "javascript"),
    "cjs": ("tree_sitter_javascript", "javascript"),
    "ts": ("tree_sitter_typescript", "typescript"),
    "tsx": ("tree_sitter_typescript", "typescript"),
    "java": ("tree_sitter_java", "java"),
    "c": ("tree_sitter_c", "c"),
    "h": ("tree_sitter_c", "c"),
    "cpp": ("tree_sitter_cpp", "cpp"),
    "cc": ("tree_sitter_cpp", "cpp"),
    "cxx": ("tree_sitter_cpp", "cpp"),
    "hpp": ("tree_sitter_cpp", "cpp"),
    "hxx": ("tree_sitter_cpp", "cpp"),
    "go": ("tree_sitter_go", "go"),
    "rs": ("tree_sitter_rust", "rust"),
    "rb": ("tree_sitter_ruby", "ruby"),
    "php": ("tree_sitter_php", "php"),
}

# ---------------------------------------------------------------------------
# Regex patterns for fallback chunking (keyed by file extension)
# ---------------------------------------------------------------------------

_JS_PATTERN = (
    r"(?:^|\n)(?:export\s+)?(?:async\s+)?function\s+\w+"
    r"|(?:^|\n)(?:export\s+)?class\s+\w+"
    r"|(?:^|\n)(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:function|\()"
)

_TS_PATTERN = (
    r"(?:^|\n)(?:export\s+)?(?:async\s+)?function\s+\w+"
    r"|(?:^|\n)(?:export\s+)?(?:abstract\s+)?class\s+\w+"
    r"|(?:^|\n)(?:export\s+)?interface\s+\w+"
    r"|(?:^|\n)(?:export\s+)?type\s+\w+"
)

_SHELL_PATTERN = r"(?:^|\n)(?:function\s+)?\w+\s*\(\s*\)\s*\{"

REGEX_PATTERNS: Dict[str, str] = {
    "js": _JS_PATTERN,
    "jsx": _JS_PATTERN,
    "mjs": _JS_PATTERN,
    "cjs": _JS_PATTERN,
    "ts": _TS_PATTERN,
    "tsx": _TS_PATTERN,
    "java": (
        r"(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?"
        r"(?:static\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+\w+"
        r"|(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?"
        r"(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?(?:native\s+)?"
        r"(?:abstract\s+)?[\w<>\[\]]+\s+\w+\s*\("
    ),
    "cpp": (
        r"(?:^|\n)(?:[\w:]+\s+)?(?:[\w:]+\s+)?"
        r"[\w:]+\s+\w+\s*\([^)]*\)\s*(?:const\s*)?\{"
    ),
    "c": r"(?:^|\n)(?:[\w*]+\s+)+\w+\s*\([^)]*\)\s*\{",
    "go": r"(?:^|\n)func\s+(?:\([^)]+\)\s+)?\w+\s*\(",
    "rs": (
        r"(?:^|\n)(?:pub\s+)?(?:async\s+)?fn\s+\w+"
        r"|(?:^|\n)(?:pub\s+)?(?:struct|enum|trait|impl)\s+\w+"
    ),
    "rb": r"(?:^|\n)def\s+\w+|(?:^|\n)class\s+\w+|(?:^|\n)module\s+\w+",
    "php": (
        r"(?:^|\n)(?:abstract\s+)?(?:class|interface|trait)\s+\w+"
        r"|(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?"
        r"(?:static\s+)?function\s+\w+"
    ),
    "swift": (
        r"(?:^|\n)(?:public\s+)?(?:private\s+)?(?:internal\s+)?"
        r"(?:open\s+)?(?:final\s+)?class\s+\w+"
        r"|(?:^|\n)(?:public\s+)?(?:private\s+)?(?:internal\s+)?"
        r"(?:static\s+)?func\s+\w+"
    ),
    "sh": _SHELL_PATTERN,
    "bash": _SHELL_PATTERN,
    "zsh": _SHELL_PATTERN,
}


def get_regex_pattern(language: str) -> str:
    """Return the regex pattern for a language extension.

    Falls back to the JS pattern when the language is unknown.
    """
    return REGEX_PATTERNS.get(language, REGEX_PATTERNS["js"])
