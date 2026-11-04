//! CommonJS and `export { ... }` pre-passes. These run before the line scan.

use super::util::{self, compile, find_all, group, group_opt, line_number_at, match_at};
use super::Hit;

struct Patterns {
    exports_brace: crate::pyre::Regex,
    spread: crate::pyre::Regex,
    aliased: crate::pyre::Regex,
    simple_end: crate::pyre::Regex,
    export_brace: crate::pyre::Regex,
    export_as: crate::pyre::Regex,
    exports_assign: crate::pyre::Regex,
}

impl Patterns {
    fn load() -> Self {
        Self {
            exports_brace: compile(r"module\.exports\s*=\s*\{"),
            spread: compile(r#"\.\.\.\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)"#),
            aliased: compile(r"(\w+)\s*:\s*(\w+)"),
            simple_end: compile(r"(\w+)\s*$"),
            export_brace: compile(r#"export\s*\{([^}]+)\}(?:\s*from\s*['\"]([^'\"]+)['\"])?"#),
            export_as: compile(r"(\w+)\s+as\s+(\w+)"),
            exports_assign: compile(r"exports\.(\w+)\s*=\s*(?:new\s+)?(\w+)"),
        }
    }
}

pub(super) fn leading(content: &str) -> Vec<Hit> {
    let patterns = Patterns::load();
    let mut symbols = Vec::new();
    commonjs_object(&patterns, content, &mut symbols);
    es_export(&patterns, content, &mut symbols);
    cjs_property(&patterns, content, &mut symbols);
    symbols
}

fn commonjs_object(patterns: &Patterns, content: &str, symbols: &mut Vec<Hit>) {
    for found in find_all(&patterns.exports_brace, content) {
        let inner = brace_inner(content, found.end);
        let line = line_number_at(content, found.start);
        for entry in split_commas(&inner) {
            let entry = entry.trim();
            if entry.is_empty() {
                continue;
            }
            if let Some(spread) = match_at(&patterns.spread, entry) {
                util::emit(
                    symbols,
                    group(entry, &spread, 1),
                    "module",
                    "reference",
                    line,
                    None,
                );
                continue;
            }
            if let Some(aliased) = match_at(&patterns.aliased, entry) {
                util::emit(
                    symbols,
                    group(entry, &aliased, 1),
                    "variable",
                    "definition",
                    line,
                    None,
                );
                util::emit(
                    symbols,
                    group(entry, &aliased, 2),
                    "variable",
                    "reference",
                    line,
                    None,
                );
                continue;
            }
            if let Some(simple) = match_at(&patterns.simple_end, entry) {
                util::emit(
                    symbols,
                    group(entry, &simple, 1),
                    "variable",
                    "reference",
                    line,
                    None,
                );
            }
        }
    }
}

fn es_export(patterns: &Patterns, content: &str, symbols: &mut Vec<Hit>) {
    for found in find_all(&patterns.export_brace, content) {
        let inner = group(content, &found, 1);
        let from_mod = group_opt(content, &found, 2);
        let line = line_number_at(content, found.start);
        if let Some(module) = &from_mod {
            util::emit(symbols, module.clone(), "module", "reference", line, None);
        }
        for entry in inner.split(',') {
            let entry = entry.trim();
            if entry.is_empty() {
                continue;
            }
            if let Some(alias) = match_at(&patterns.export_as, entry) {
                let orig = group(entry, &alias, 1);
                let name = group(entry, &alias, 2);
                util::emit(symbols, name, "variable", "definition", line, None);
                util::emit(symbols, orig, "variable", "reference", line, None);
                continue;
            }
            if let Some(simple) = match_at(&patterns.simple_end, entry) {
                let name = group(entry, &simple, 1);
                if from_mod.is_some() {
                    util::emit(symbols, name, "import", "reference", line, None);
                } else {
                    util::emit(symbols, name, "variable", "reference", line, None);
                }
            }
        }
    }
}

fn cjs_property(patterns: &Patterns, content: &str, symbols: &mut Vec<Hit>) {
    for found in find_all(&patterns.exports_assign, content) {
        let line = line_number_at(content, found.start);
        util::emit(
            symbols,
            group(content, &found, 1),
            "variable",
            "definition",
            line,
            None,
        );
        let rhs = group(content, &found, 2);
        if !matches!(rhs.as_str(), "function" | "class" | "async" | "require") {
            util::emit(symbols, rhs, "variable", "reference", line, None);
        }
    }
}

fn brace_inner(content: &str, start: usize) -> String {
    let chars: Vec<char> = content.chars().collect();
    let mut depth = 1i32;
    let mut index = start;
    while index < chars.len() && depth != 0 {
        if chars[index] == '{' {
            depth += 1;
        } else if chars[index] == '}' {
            depth -= 1;
        }
        index += 1;
    }
    if depth == 0 {
        chars[start..index - 1].iter().collect()
    } else {
        chars[start..].iter().collect()
    }
}

fn split_commas(inner: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut buf = String::new();
    let mut nest = 0i32;
    for ch in inner.chars() {
        if ch == '{' {
            nest += 1;
        } else if ch == '}' {
            nest -= 1;
        }
        if ch == ',' && nest == 0 {
            parts.push(std::mem::take(&mut buf));
        } else {
            buf.push(ch);
        }
    }
    if !buf.trim().is_empty() {
        parts.push(buf);
    }
    parts
}
