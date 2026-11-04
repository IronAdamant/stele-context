//! Chunk-boundary patterns from `chunkers/code_patterns.py`.
//! Unknown extensions use the JavaScript pattern, as `get_regex_pattern` does.

const JS: &str = concat!(
    r"(?:^|\n)(?:export\s+)?(?:async\s+)?function\s+\w+",
    r"|(?:^|\n)(?:export\s+)?class\s+\w+",
    r"|(?:^|\n)(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:function|\()",
    r"|(?:^|\n)module\.exports\s*=",
    r"|(?:^|\n)describe\s*\(",
    r"|(?:^|\n)(?:it|test)\s*\("
);

const TS: &str = concat!(
    r"(?:^|\n)(?:export\s+)?(?:async\s+)?function\s+\w+",
    r"|(?:^|\n)(?:export\s+)?(?:abstract\s+)?class\s+\w+",
    r"|(?:^|\n)(?:export\s+)?interface\s+\w+",
    r"|(?:^|\n)(?:export\s+)?type\s+\w+"
);

const SHELL: &str = r"(?:^|\n)(?:function\s+)?\w+\s*\(\s*\)\s*\{";

pub fn for_extension(extension: &str) -> &'static str {
    match extension {
        "js" | "jsx" | "mjs" | "cjs" => JS,
        "ts" | "tsx" => TS,
        "java" => concat!(
            r"(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?",
            r"(?:static\s+)?(?:abstract\s+)?(?:class|interface|enum)\s+\w+",
            r"|(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?",
            r"(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?(?:native\s+)?",
            r"(?:abstract\s+)?[\w<>\[\]]+\s+\w+\s*\("
        ),
        "cpp" => r"(?:^|\n)(?:[\w:]+\s+)?(?:[\w:]+\s+)?[\w:]+\s+\w+\s*\([^)]*\)\s*(?:const\s*)?\{",
        "c" | "h" => r"(?:^|\n)(?:[\w*]+\s+)+\w+\s*\([^)]*\)\s*\{",
        "go" => r"(?:^|\n)func\s+(?:\([^)]+\)\s+)?\w+\s*\(",
        "rs" => concat!(
            r"(?:^|\n)(?:pub\s+)?(?:async\s+)?fn\s+\w+",
            r"|(?:^|\n)(?:pub\s+)?(?:struct|enum|trait|impl)\s+\w+"
        ),
        "rb" => r"(?:^|\n)def\s+\w+|(?:^|\n)class\s+\w+|(?:^|\n)module\s+\w+",
        "php" => concat!(
            r"(?:^|\n)(?:abstract\s+)?(?:class|interface|trait)\s+\w+",
            r"|(?:^|\n)(?:public\s+)?(?:private\s+)?(?:protected\s+)?",
            r"(?:static\s+)?function\s+\w+"
        ),
        "cs" => concat!(
            r"(?:^|\n)[ \t]*(?:(?:public|private|protected|internal|static|abstract|",
            r"sealed|partial|readonly|ref|file|unsafe|new)[ \t]+)*",
            r"(?:class|interface|struct|enum|record)[ \t]+(?:class[ \t]+|struct[ \t]+)?",
            r"[A-Za-z_]\w*",
            r"|(?:^|\n)[ \t]*(?!(?:return|await|throw|if|else|for|foreach|while|",
            r"switch|catch|new|var|using|lock)[ \t])",
            r"(?:(?:public|private|protected|internal|static|abstract|sealed|",
            r"virtual|override|async|partial|extern|unsafe)[ \t]+)+",
            r"[\w.<>,\[\]?]+\s+[A-Za-z_]\w*\s*(?:<[^>\n]+>)?\s*\("
        ),
        "swift" => concat!(
            r"(?:^|\n)[ \t]*(?:(?:public|private|internal|fileprivate|open|final|",
            r"indirect|package)(?:\([^)\n]*\))?[ \t]+)*",
            r"(?:class|struct|enum|protocol|actor|extension)[ \t]+",
            r"(?!func\b|var\b|let\b|subscript\b)[A-Za-z_]\w*",
            r"|(?:^|\n)[ \t]*(?:(?:public|private|internal|fileprivate|open|final|",
            r"static|class|override|mutating|nonmutating|required|convenience|",
            r"package)(?:\([^)\n]*\))?[ \t]+)*",
            r"func[ \t]+[A-Za-z_]\w*"
        ),
        "sh" | "bash" | "zsh" => SHELL,
        _ => JS,
    }
}
