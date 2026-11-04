//! Swift extraction from `symbol_swift.py`.

use super::util::{self, compile, find_all, group, match_at, slice_chars, slice_from};
use super::Hit;
use crate::pyre::Regex;

struct Patterns {
    type_decl: Regex,
    func_decl: Regex,
    init_decl: Regex,
    typealias_decl: Regex,
    import_decl: Regex,
    call: Regex,
    attribute: Regex,
}

impl Patterns {
    fn load() -> Self {
        let access = r"(?:public|open|internal|fileprivate|private|package)(?:\s*\([^)\n]*\))?";
        Self {
            type_decl: compile(&format!(
                r"^(?:(?:{access}|final|indirect|nonisolated|distributed|override)\s+)*(?:class|struct|enum|protocol|actor|extension)\s+(?!func\b|var\b|let\b|subscript\b|init\b|deinit\b)([A-Za-z_]\w*)"
            )),
            func_decl: compile(&format!(
                r"^(?:(?:{access}|final|static|class|override|mutating|nonmutating|required|convenience|distributed|nonisolated|optional|dynamic|infix|prefix|postfix)\s+)*func\s+([A-Za-z_]\w*)"
            )),
            init_decl: compile(&format!(
                r"^(?:(?:{access}|convenience|required|override|final)\s+)*(init|deinit)\b"
            )),
            typealias_decl: compile(&format!(r"^(?:{access}\s+)*typealias\s+([A-Za-z_]\w*)")),
            import_decl: compile(
                r"^import\s+(?:(?:class|struct|enum|protocol|func|var|let|typealias)\s+)?([\w.]+)",
            ),
            call: compile(r"(?<![\w])([A-Za-z_]\w*)\s*\("),
            attribute: compile(r"@\w+"),
        }
    }
}

pub(super) fn extract(content: &str) -> Vec<Hit> {
    let patterns = Patterns::load();
    let mut symbols = Vec::new();
    let mut in_block = false;
    for (index, raw_line) in util::python_lines(content).into_iter().enumerate() {
        let line_no = index as u32 + 1;
        let code = {
            let (visible, next_block) = visible_code(raw_line, in_block);
            in_block = next_block;
            visible
        };
        let stripped = strip_attributes(code.trim(), &patterns.attribute);
        if stripped.is_empty() {
            continue;
        }
        let mut defined_name: Option<String> = None;
        if let Some(imported) = match_at(&patterns.import_decl, &stripped) {
            util::emit(
                &mut symbols,
                group(&stripped, &imported, 1),
                "module",
                "reference",
                line_no,
                None,
            );
        } else if let Some(type_decl) = match_at(&patterns.type_decl, &stripped) {
            let name = group(&stripped, &type_decl, 1);
            util::emit(
                &mut symbols,
                name.clone(),
                "class",
                "definition",
                line_no,
                None,
            );
            defined_name = Some(name);
        } else if let Some(func_decl) = match_at(&patterns.func_decl, &stripped) {
            let name = group(&stripped, &func_decl, 1);
            util::emit(
                &mut symbols,
                name.clone(),
                "function",
                "definition",
                line_no,
                None,
            );
            defined_name = Some(name);
        } else if let Some(init_decl) = match_at(&patterns.init_decl, &stripped) {
            let name = group(&stripped, &init_decl, 1);
            util::emit(
                &mut symbols,
                name.clone(),
                "function",
                "definition",
                line_no,
                None,
            );
            defined_name = Some(name);
        } else if let Some(alias) = match_at(&patterns.typealias_decl, &stripped) {
            let name = group(&stripped, &alias, 1);
            util::emit(
                &mut symbols,
                name.clone(),
                "class",
                "definition",
                line_no,
                None,
            );
            defined_name = Some(name);
        }

        for found in find_all(&patterns.call, &stripped) {
            let callee = group(&stripped, &found, 1);
            if call_skip(&callee) || defined_name.as_deref() == Some(callee.as_str()) {
                continue;
            }
            if found.start > 0 && slice_chars(&stripped, found.start - 1, found.start) == "#" {
                continue;
            }
            util::emit(&mut symbols, callee, "function", "reference", line_no, None);
        }
    }
    symbols
}

fn visible_code(line: &str, mut in_block: bool) -> (String, bool) {
    let chars: Vec<char> = line.chars().collect();
    let mut out = String::new();
    let mut index = 0;
    let mut in_string = false;
    while index < chars.len() {
        if in_block {
            match find_pair(&chars, index) {
                Some(end) => {
                    index = end + 2;
                    in_block = false;
                }
                None => return (out, true),
            }
            continue;
        }
        let ch = chars[index];
        if in_string {
            out.push(ch);
            if ch == '\\' && index + 1 < chars.len() {
                out.push(chars[index + 1]);
                index += 2;
                continue;
            }
            if ch == '"' {
                in_string = false;
            }
            index += 1;
            continue;
        }
        if ch == '"' {
            in_string = true;
            out.push(ch);
            index += 1;
            continue;
        }
        if ch == '/' && index + 1 < chars.len() && chars[index + 1] == '/' {
            break;
        }
        if ch == '/' && index + 1 < chars.len() && chars[index + 1] == '*' {
            in_block = true;
            index += 2;
            continue;
        }
        out.push(ch);
        index += 1;
    }
    (out, in_block)
}

fn find_pair(chars: &[char], start: usize) -> Option<usize> {
    chars[start..]
        .windows(2)
        .position(|window| window == ['*', '/'])
        .map(|offset| start + offset)
}

fn strip_attributes(text: &str, attribute: &Regex) -> String {
    let mut text = text.trim().to_string();
    while text.starts_with('@') {
        let Some(ident) = match_at(attribute, &text) else {
            break;
        };
        let rest = slice_from(&text, ident.end).trim_start().to_string();
        if let Some(rest_body) = rest.strip_prefix('(') {
            let mut depth = 1i32;
            let mut closed_at = None;
            for (index, ch) in rest_body.chars().enumerate() {
                if ch == '(' {
                    depth += 1;
                } else if ch == ')' {
                    depth -= 1;
                    if depth == 0 {
                        closed_at = Some(index);
                        break;
                    }
                }
            }
            let Some(index) = closed_at else {
                break;
            };
            text = slice_from(rest_body, index + 1).trim_start().to_string();
        } else {
            text = rest;
        }
    }
    text.trim().to_string()
}

fn call_skip(name: &str) -> bool {
    matches!(
        name,
        "if" | "while"
            | "for"
            | "switch"
            | "catch"
            | "guard"
            | "repeat"
            | "defer"
            | "return"
            | "throw"
            | "await"
            | "try"
            | "as"
            | "is"
            | "in"
            | "func"
            | "where"
            | "some"
            | "any"
            | "do"
            | "async"
            | "throws"
            | "rethrows"
            | "subscript"
            | "super"
            | "self"
    )
}
