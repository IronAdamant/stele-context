//! C# extraction from `symbol_csharp.py`.

use super::util::{self, compile, count_char, find_all, group, match_at, slice_chars};
use super::Hit;

struct Patterns {
    decl: crate::pyre::Regex,
    type_decl: crate::pyre::Regex,
    namespace: crate::pyre::Regex,
    using_alias: crate::pyre::Regex,
    using_name: crate::pyre::Regex,
    call: crate::pyre::Regex,
    new_expr: crate::pyre::Regex,
}

impl Patterns {
    fn load() -> Self {
        Self {
            // `.+` stays outside a capture. pyre does not backtrack a quantifier
            // that sits inside a group, and Python's `(.+?)` head needs that.
            decl: compile(
                r"^.+\s+([A-Za-z_]\w*)\s*(?:<[^;{()]*>)?\s*\([^;]*\)\s*(?:where\b[^;{]*)?(?:\{|=>|;|$)",
            ),
            type_decl: compile(
                r"^(?:(?:public|private|protected|internal|static|abstract|sealed|partial|readonly|ref|file|unsafe|new)\s+)*(?:class|interface|struct|enum|record)\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*)",
            ),
            namespace: compile(r"^namespace\s+([\w.]+)\s*[{;]?$"),
            using_alias: compile(r"^using\s+([\w.]+)\s*=\s*([\w.]+)\s*;"),
            using_name: compile(r"^using\s+(?:static\s+)?([\w.]+)\s*;"),
            call: compile(r"(?<![\w])([A-Za-z_]\w*)\s*\("),
            new_expr: compile(r"\bnew\s+([A-Za-z_]\w*)"),
        }
    }
}

pub(super) fn extract(content: &str) -> Vec<Hit> {
    let patterns = Patterns::load();
    let mut symbols = Vec::new();
    let mut type_stack: Vec<(String, i32)> = Vec::new();
    let mut pending_type: Option<String> = None;
    let mut brace_depth = 0i32;

    for (index, raw_line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let code = code_only(raw_line);
        let stripped = strip_attributes(code.trim());
        let opens = count_char(&code, '{');
        let closes = count_char(&code, '}');

        if pending_type.is_some() && code.contains('{') {
            let name = pending_type.take().unwrap();
            type_stack.push((name, brace_depth + 1));
        }
        let current_type = type_stack.last().map(|(name, _)| name.clone());
        let mut defined_name: Option<String> = None;

        if !stripped.is_empty() && !stripped.starts_with('#') {
            if let Some(alias) = match_at(&patterns.using_alias, &stripped) {
                util::emit(
                    &mut symbols,
                    group(&stripped, &alias, 2),
                    "module",
                    "reference",
                    line_no,
                    None,
                );
                util::emit(
                    &mut symbols,
                    group(&stripped, &alias, 1),
                    "import",
                    "reference",
                    line_no,
                    None,
                );
            } else if let Some(using_name) = match_at(&patterns.using_name, &stripped) {
                util::emit(
                    &mut symbols,
                    group(&stripped, &using_name, 1),
                    "module",
                    "reference",
                    line_no,
                    None,
                );
            } else if let Some(namespace) = match_at(&patterns.namespace, &stripped) {
                util::emit(
                    &mut symbols,
                    group(&stripped, &namespace, 1),
                    "module",
                    "definition",
                    line_no,
                    None,
                );
            } else if let Some(type_decl) = match_at(&patterns.type_decl, &stripped) {
                let type_name = group(&stripped, &type_decl, 1);
                util::emit(
                    &mut symbols,
                    type_name.clone(),
                    "class",
                    "definition",
                    line_no,
                    None,
                );
                defined_name = Some(type_name.clone());
                if code.contains('{') {
                    type_stack.push((type_name, brace_depth + 1));
                } else {
                    pending_type = Some(type_name);
                }
            } else if let Some(declared) = match_at(&patterns.decl, &stripped) {
                let name = group(&stripped, &declared, 1);
                let name_at = declared
                    .groups
                    .get(1)
                    .copied()
                    .flatten()
                    .map(|(start, _)| start)
                    .unwrap_or(0);
                let head = slice_chars(&stripped, 0, name_at).trim().to_string();
                if is_declaration_head(&head)
                    || (current_type.as_deref() == Some(name.as_str()) && has_modifier(&head))
                {
                    util::emit(
                        &mut symbols,
                        name.clone(),
                        "function",
                        "definition",
                        line_no,
                        None,
                    );
                    defined_name = Some(name);
                }
            }

            for found in find_all(&patterns.new_expr, &stripped) {
                util::emit(
                    &mut symbols,
                    group(&stripped, &found, 1),
                    "class",
                    "reference",
                    line_no,
                    None,
                );
            }
            for found in find_all(&patterns.call, &stripped) {
                let callee = group(&stripped, &found, 1);
                if call_skip(&callee) || defined_name.as_deref() == Some(callee.as_str()) {
                    continue;
                }
                let prefix = slice_chars(&stripped, 0, found.start);
                if prefix.trim_end().ends_with("new") {
                    continue;
                }
                util::emit(&mut symbols, callee, "function", "reference", line_no, None);
            }
        }

        brace_depth += opens - closes;
        while type_stack
            .last()
            .is_some_and(|(_, depth)| brace_depth < *depth)
        {
            type_stack.pop();
        }
    }
    symbols
}

fn code_only(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let mut index = 0;
    let mut in_string = false;
    let mut quote = '\0';
    while index < chars.len() {
        let ch = chars[index];
        if in_string {
            if ch == '\\' && index + 1 < chars.len() {
                index += 2;
                continue;
            }
            if ch == quote {
                in_string = false;
            }
            index += 1;
            continue;
        }
        if ch == '\'' || ch == '"' {
            in_string = true;
            quote = ch;
            index += 1;
            continue;
        }
        if ch == '/' && index + 1 < chars.len() && chars[index + 1] == '/' {
            return chars[..index].iter().collect();
        }
        index += 1;
    }
    line.to_string()
}

fn strip_attributes(stripped: &str) -> String {
    let mut owned = stripped.to_string();
    while owned.starts_with('[') {
        let Some(end) = owned.find(']') else {
            break;
        };
        owned = owned[end + 1..].trim().to_string();
    }
    owned
}

fn is_declaration_head(head: &str) -> bool {
    if head.contains('=') || head.trim().is_empty() {
        return false;
    }
    let tokens: Vec<&str> = head.split_whitespace().collect();
    if tokens.is_empty() || statement_lead(tokens[0]) || tokens[0] == "using" {
        return false;
    }
    let mut index = 0;
    while index < tokens.len() && is_modifier(tokens[index]) {
        index += 1;
    }
    index < tokens.len()
}

fn has_modifier(head: &str) -> bool {
    head.split_whitespace().any(is_modifier)
}

fn is_modifier(token: &str) -> bool {
    matches!(
        token,
        "public"
            | "private"
            | "protected"
            | "internal"
            | "static"
            | "virtual"
            | "override"
            | "abstract"
            | "sealed"
            | "async"
            | "partial"
            | "extern"
            | "new"
            | "unsafe"
            | "readonly"
            | "required"
            | "volatile"
            | "file"
            | "ref"
            | "event"
            | "delegate"
            | "const"
    )
}

fn statement_lead(token: &str) -> bool {
    matches!(
        token,
        "return"
            | "await"
            | "throw"
            | "if"
            | "else"
            | "for"
            | "foreach"
            | "while"
            | "do"
            | "switch"
            | "case"
            | "catch"
            | "finally"
            | "lock"
            | "yield"
            | "goto"
            | "break"
            | "continue"
            | "try"
            | "checked"
            | "unchecked"
            | "fixed"
            | "var"
            | "dynamic"
            | "base"
            | "this"
            | "new"
    )
}

fn call_skip(name: &str) -> bool {
    statement_lead(name)
        || matches!(
            name,
            "using"
                | "sizeof"
                | "typeof"
                | "nameof"
                | "get"
                | "set"
                | "add"
                | "remove"
                | "when"
                | "where"
                | "and"
                | "or"
                | "not"
                | "is"
                | "as"
        )
}
