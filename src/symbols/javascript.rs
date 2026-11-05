//! JavaScript and TypeScript extraction from `extract_javascript`.

use super::util::{
    self, compile, count_char, find_all, group, group_opt, match_at, slice_chars, slice_from,
};
use super::Hit;

struct Patterns {
    function: crate::pyre::Regex,
    class: crate::pyre::Regex,
    module_exports: crate::pyre::Regex,
    exports_name: crate::pyre::Regex,
    require_var: crate::pyre::Regex,
    var_decl: crate::pyre::Regex,
    method: crate::pyre::Regex,
    method_skip: crate::pyre::Regex,
    type_decl: crate::pyre::Regex,
    import_head: crate::pyre::Regex,
    import_marker: crate::pyre::Regex,
    word: crate::pyre::Regex,
    destructure_require: crate::pyre::Regex,
    require_call: crate::pyre::Regex,
    call: crate::pyre::Regex,
    new_expr: crate::pyre::Regex,
    query_selector: crate::pyre::Regex,
    css_class: crate::pyre::Regex,
    css_id: crate::pyre::Regex,
    by_id: crate::pyre::Regex,
    by_class: crate::pyre::Regex,
    class_list: crate::pyre::Regex,
    rhs_ident: crate::pyre::Regex,
}

impl Patterns {
    fn load() -> Self {
        Self {
            function: compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"),
            class: compile(r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"),
            module_exports: compile(r"module\.exports\s*=\s*(?:new\s+)?(\w+)"),
            exports_name: compile(r"exports\.(\w+)\s*=\s*(?:new\s+)?(\w+)"),
            require_var: compile(
                r#"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)"#,
            ),
            var_decl: compile(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*="),
            method: compile(r"\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{"),
            method_skip: compile(r"\s*(if|for|while|switch|catch|function|return)\b"),
            type_decl: compile(r"(?:export\s+)?(?:interface|type)\s+(\w+)"),
            import_head: compile(r"^import\s+"),
            import_marker: compile(r#"\s+from\s+['\"]([^'\"]+)['\"]"#),
            word: compile(r"\b(\w+)\b"),
            destructure_require: compile(
                r#"(?:const|let|var)\s+\{([^}]+)\}\s*=\s*require\(['\"]([^'\"]+)['\"]\)"#,
            ),
            require_call: compile(r#"require\(['\"]([^'\"]+)['\"]\)"#),
            call: compile(r"(?:\.(\w+)|(?:^|[^\.\w])(\w+))\s*\("),
            new_expr: compile(r"new\s+(\w+)"),
            query_selector: compile(r#"querySelector(?:All)?\(['\"]([^'\"]+)['\"]\)"#),
            css_class: compile(r"\.([a-zA-Z_][\w-]*)"),
            css_id: compile(r"#([a-zA-Z_][\w-]*)"),
            by_id: compile(r#"getElementById\(['\"]([^'\"]+)['\"]\)"#),
            by_class: compile(r#"getElementsByClassName\(['\"]([^'\"]+)['\"]\)"#),
            class_list: compile(
                r#"classList\.(?:add|remove|toggle|contains)\(['\"]([^'\"]+)['\"]\)"#,
            ),
            rhs_ident: compile(r"^(\w+)$"),
        }
    }
}

pub(super) fn extract(content: &str) -> Vec<Hit> {
    let patterns = Patterns::load();
    let mut symbols = super::javascript_prepass::leading(content);
    for found in find_all(&patterns.exports_name, content) {
        let line = content
            .chars()
            .take(found.start)
            .filter(|ch| *ch == '\n')
            .count() as u32
            + 1;
        let name = group(content, &found, 1);
        let rhs = group(content, &found, 2);
        let fresh = |symbols: &[Hit], kind: &str, role: &str, label: &str| {
            !symbols.iter().any(|hit| {
                hit.name == label && hit.kind == kind && hit.role == role && hit.line == line
            })
        };
        if fresh(&symbols, "variable", "definition", &name) {
            util::emit(&mut symbols, name, "variable", "definition", line, None);
        }
        if !matches!(rhs.as_str(), "function" | "class" | "async" | "require")
            && fresh(&symbols, "variable", "reference", &rhs)
        {
            util::emit(&mut symbols, rhs, "variable", "reference", line, None);
        }
    }
    scan_lines(&patterns, content, &mut symbols);
    symbols
}

fn scan_lines(patterns: &Patterns, content: &str, symbols: &mut Vec<Hit>) {
    let mut pending_name = String::new();
    let mut pending_line = 0u32;
    let mut paren_brace_depth = 0i32;
    let mut brace_depth = 0i32;
    let mut scope: Vec<(String, i32)> = Vec::new();

    for (index, line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let stripped = line.trim();

        if !pending_name.is_empty() {
            paren_brace_depth += paren_delta(line);
            if paren_brace_depth <= 0 {
                util::emit(
                    symbols,
                    pending_name.clone(),
                    "variable",
                    "definition",
                    pending_line,
                    container_of(&scope),
                );
                pending_name.clear();
                paren_brace_depth = 0;
            }
        }

        while scope.last().is_some_and(|(_, depth)| *depth >= brace_depth) {
            scope.pop();
        }

        if let Some(found) = match_at(&patterns.function, stripped) {
            let name = group(stripped, &found, 1);
            util::emit(
                symbols,
                name.clone(),
                "function",
                "definition",
                line_no,
                container_of(&scope),
            );
            scope.push((name, brace_depth));
        }

        if let Some(found) = match_at(&patterns.class, stripped) {
            let name = group(stripped, &found, 1);
            util::emit(
                symbols,
                name.clone(),
                "class",
                "definition",
                line_no,
                container_of(&scope),
            );
            scope.push((name, brace_depth));
        }

        if let Some(found) = match_at(&patterns.module_exports, stripped) {
            util::emit(
                symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                None,
            );
        }

        if let Some(found) = match_at(&patterns.require_var, stripped) {
            let req_path = group(stripped, &found, 2);
            if req_path.starts_with('.') || req_path.starts_with('/') {
                util::emit(
                    symbols,
                    group(stripped, &found, 1),
                    "import",
                    "reference",
                    line_no,
                    container_of(&scope),
                );
            }
        } else if let Some(found) = match_at(&patterns.var_decl, stripped) {
            let name = group(stripped, &found, 1);
            let rhs = match stripped.find('=') {
                Some(eq) => &stripped[eq + 1..],
                None => "",
            };
            if rhs.contains('(') || rhs.contains('{') {
                pending_name = name;
                pending_line = line_no;
                paren_brace_depth = paren_delta(line);
                if paren_brace_depth <= 0 {
                    util::emit(
                        symbols,
                        pending_name.clone(),
                        "variable",
                        "definition",
                        pending_line,
                        container_of(&scope),
                    );
                    pending_name.clear();
                    paren_brace_depth = 0;
                }
            } else {
                util::emit(
                    symbols,
                    name.clone(),
                    "variable",
                    "definition",
                    line_no,
                    container_of(&scope),
                );
                let rhs_ident = rhs.trim().trim_end_matches(';').trim();
                if let Some(ident) = match_at(&patterns.rhs_ident, rhs_ident) {
                    let referenced = group(rhs_ident, &ident, 1);
                    if referenced != name {
                        util::emit(
                            symbols,
                            referenced,
                            "variable",
                            "reference",
                            line_no,
                            container_of(&scope),
                        );
                    }
                }
            }
        }

        if pending_name.is_empty() {
            if let Some(found) = match_at(&patterns.method, line) {
                if match_at(&patterns.method_skip, line).is_none() {
                    let name = group(line, &found, 1);
                    util::emit(
                        symbols,
                        name.clone(),
                        "function",
                        "definition",
                        line_no,
                        container_of(&scope),
                    );
                    scope.push((name, brace_depth));
                }
            }
        }

        if let Some(found) = match_at(&patterns.type_decl, stripped) {
            util::emit(
                symbols,
                group(stripped, &found, 1),
                "class",
                "definition",
                line_no,
                container_of(&scope),
            );
        }

        if let Some(head) = match_at(&patterns.import_head, stripped) {
            let rest = slice_from(stripped, head.end);
            if let Some(marker) = find_all(&patterns.import_marker, &rest).into_iter().next() {
                util::emit(
                    symbols,
                    group(&rest, &marker, 1),
                    "module",
                    "reference",
                    line_no,
                    container_of(&scope),
                );
                let clause = slice_chars(&rest, 0, marker.start);
                for word in find_all(&patterns.word, &clause) {
                    let name = group(&clause, &word, 1);
                    if !matches!(name.as_str(), "import" | "as" | "default" | "type" | "from") {
                        util::emit(
                            symbols,
                            name,
                            "import",
                            "reference",
                            line_no,
                            container_of(&scope),
                        );
                    }
                }
            }
        }

        if let Some(found) = match_at(&patterns.destructure_require, stripped) {
            util::emit(
                symbols,
                group(stripped, &found, 2),
                "module",
                "reference",
                line_no,
                container_of(&scope),
            );
            let clause = group(stripped, &found, 1);
            for word in find_all(&patterns.word, &clause) {
                let name = group(&clause, &word, 1);
                if name != "as" {
                    util::emit(
                        symbols,
                        name,
                        "import",
                        "reference",
                        line_no,
                        container_of(&scope),
                    );
                }
            }
        }

        for found in find_all(&patterns.require_call, stripped) {
            util::emit(
                symbols,
                group(stripped, &found, 1),
                "module",
                "reference",
                line_no,
                container_of(&scope),
            );
        }

        for found in find_all(&patterns.call, stripped) {
            let func_name = group_opt(stripped, &found, 1)
                .filter(|name| !name.is_empty())
                .or_else(|| group_opt(stripped, &found, 2))
                .unwrap_or_default();
            if !func_name.is_empty() && !call_skip(&func_name) {
                util::emit(
                    symbols,
                    func_name,
                    "function",
                    "reference",
                    line_no,
                    container_of(&scope),
                );
            }
        }

        for found in find_all(&patterns.new_expr, stripped) {
            let class_name = group(stripped, &found, 1);
            if !call_skip(&class_name) {
                util::emit(
                    symbols,
                    class_name,
                    "class",
                    "reference",
                    line_no,
                    container_of(&scope),
                );
            }
        }

        for found in find_all(&patterns.query_selector, stripped) {
            let selector = group(stripped, &found, 1);
            for class_name in find_all(&patterns.css_class, &selector) {
                util::emit(
                    symbols,
                    format!(".{}", group(&selector, &class_name, 1)),
                    "css_class",
                    "reference",
                    line_no,
                    None,
                );
            }
            for id_name in find_all(&patterns.css_id, &selector) {
                util::emit(
                    symbols,
                    format!("#{}", group(&selector, &id_name, 1)),
                    "css_id",
                    "reference",
                    line_no,
                    None,
                );
            }
        }

        for found in find_all(&patterns.by_id, stripped) {
            util::emit(
                symbols,
                format!("#{}", group(stripped, &found, 1)),
                "css_id",
                "reference",
                line_no,
                None,
            );
        }

        for found in find_all(&patterns.by_class, stripped) {
            for class_name in group(stripped, &found, 1).split_whitespace() {
                util::emit(
                    symbols,
                    format!(".{class_name}"),
                    "css_class",
                    "reference",
                    line_no,
                    None,
                );
            }
        }

        for found in find_all(&patterns.class_list, stripped) {
            util::emit(
                symbols,
                format!(".{}", group(stripped, &found, 1)),
                "css_class",
                "reference",
                line_no,
                None,
            );
        }

        brace_depth += count_char(line, '{') - count_char(line, '}');
    }

    if !pending_name.is_empty() {
        util::emit(
            symbols,
            pending_name,
            "variable",
            "definition",
            pending_line,
            container_of(&scope),
        );
    }
}

fn container_of(scope: &[(String, i32)]) -> Option<String> {
    if scope.is_empty() {
        None
    } else {
        Some(
            scope
                .iter()
                .map(|(name, _)| name.as_str())
                .collect::<Vec<_>>()
                .join("."),
        )
    }
}

fn paren_delta(line: &str) -> i32 {
    let mut depth = 0i32;
    for ch in line.chars() {
        if ch == '(' || ch == '{' {
            depth += 1;
        } else if ch == ')' || ch == '}' {
            depth -= 1;
        }
    }
    depth
}

fn call_skip(name: &str) -> bool {
    matches!(
        name,
        "if" | "else"
            | "for"
            | "while"
            | "switch"
            | "catch"
            | "return"
            | "throw"
            | "new"
            | "delete"
            | "typeof"
            | "import"
            | "export"
            | "default"
            | "case"
            | "break"
            | "continue"
            | "try"
            | "finally"
            | "do"
            | "with"
            | "function"
            | "class"
            | "extends"
            | "implements"
            | "interface"
            | "enum"
            | "const"
            | "let"
            | "var"
    )
}
