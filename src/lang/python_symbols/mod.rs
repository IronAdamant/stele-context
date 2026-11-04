//! Symbol table matching CPython's `ast` walk for a valid module, and the
//! 1.5.2 regex fallback when the module is not valid Python.
//!
//! Visit order follows `FunctionDef` fields: the definition, then parameters,
//! then the body, then decorators, then the return annotation.

mod expr;

use crate::lang::kind;
use crate::lang::python::lex::lex;
use crate::syntax::cst::Token;
use crate::syntax::query::SymbolHit;

pub fn extract(source: &str) -> Vec<SymbolHit> {
    let tokens = lex(source);
    if !brackets_balanced(&tokens, source) {
        return regex_fallback(source);
    }
    let mut cursor = Cursor {
        source,
        tokens: &tokens,
        index: 0,
        symbols: Vec::new(),
        scope: Vec::new(),
        pending: Vec::new(),
    };
    cursor.module();
    cursor.symbols
}

fn brackets_balanced(tokens: &[Token], source: &str) -> bool {
    let mut depth = 0i32;
    for token in tokens {
        if token.kind != kind::OP || token.end <= token.start {
            continue;
        }
        match source.as_bytes().get(token.start as usize).copied() {
            Some(b'(' | b'[' | b'{') => depth += 1,
            Some(b')' | b']' | b'}') => {
                depth -= 1;
                if depth < 0 {
                    return false;
                }
            }
            _ => {}
        }
    }
    depth == 0
}

struct Cursor<'a> {
    source: &'a str,
    tokens: &'a [Token],
    index: usize,
    symbols: Vec<SymbolHit>,
    scope: Vec<String>,
    pending: Vec<SymbolHit>,
}

impl<'a> Cursor<'a> {
    fn module(&mut self) {
        while self.index < self.tokens.len() {
            self.skip_noise();
            if self.index >= self.tokens.len() || self.at(kind::DEDENT) {
                break;
            }
            self.statement();
        }
    }

    fn statement(&mut self) {
        self.skip_noise();
        if self.at_word("@") {
            let hits = self.capture(|cursor| cursor.decorator());
            self.pending.extend(hits);
            return;
        }
        if self.at_word("async") {
            self.bump();
            self.skip_space();
        }
        if self.at_word("def") {
            self.function();
            return;
        }
        if self.at_word("class") {
            self.class_def();
            return;
        }
        if self.at_word("import") || self.at_word("from") {
            self.import_stmt();
            return;
        }
        if self.at_word("if")
            || self.at_word("elif")
            || self.at_word("while")
            || self.at_word("for")
            || self.at_word("with")
            || self.at_word("match")
        {
            self.header_and_suite();
            return;
        }
        if self.at_word("try") {
            self.bump_line();
            self.suite();
            return;
        }
        if self.at_word("else")
            || self.at_word("finally")
            || self.at_word("except")
            || self.at_word("case")
        {
            self.header_and_suite();
            return;
        }
        self.simple();
    }

    fn function(&mut self) {
        self.bump();
        self.skip_space();
        let Some(name) = self.ident_text() else {
            self.simple();
            return;
        };
        let line = self.line_here();
        self.emit("function", "definition", &name, line);
        self.scope.push(name);
        self.bump();
        self.params();
        self.skip_space();
        let returns = if self.at_word("->") {
            self.bump();
            self.capture(|cursor| cursor.expr_until_colon())
        } else {
            Vec::new()
        };
        self.skip_until_newline();
        self.suite();
        self.flush_pending();
        self.symbols.extend(returns);
        self.scope.pop();
    }

    fn class_def(&mut self) {
        self.bump();
        self.skip_space();
        let Some(name) = self.ident_text() else {
            self.simple();
            return;
        };
        let line = self.line_here();
        self.emit("class", "definition", &name, line);
        self.scope.push(name);
        self.bump();
        if self.at_word("(") {
            self.bump();
            self.expr_list_until(")");
        }
        self.skip_until_newline();
        self.suite();
        self.flush_pending();
        self.scope.pop();
    }

    fn params(&mut self) {
        if !self.at_word("(") {
            return;
        }
        self.bump();
        let mut depth = 1i32;
        while self.index < self.tokens.len() && depth > 0 {
            self.skip_space();
            if self.at_word("(") || self.at_word("[") || self.at_word("{") {
                depth += 1;
                self.bump();
                continue;
            }
            if self.at_word(")") || self.at_word("]") || self.at_word("}") {
                depth -= 1;
                self.bump();
                continue;
            }
            if depth == 1 && self.at_word(":") {
                self.bump();
                self.expr_until_comma_or_paren();
                continue;
            }
            self.bump();
        }
    }

    fn suite(&mut self) {
        self.skip_noise();
        if self.at(kind::INDENT) {
            self.bump();
            while self.index < self.tokens.len() && !self.at(kind::DEDENT) {
                self.statement();
                self.skip_noise();
            }
            if self.at(kind::DEDENT) {
                self.bump();
            }
        }
    }

    fn header_and_suite(&mut self) {
        self.bump();
        self.expr_until_colon();
        self.skip_until_newline();
        self.suite();
    }

    fn decorator(&mut self) {
        self.bump();
        self.expr_until_newline();
    }

    fn import_stmt(&mut self) {
        let from = self.at_word("from");
        self.bump();
        self.skip_space();
        if from {
            if let Some(module) = self.dotted() {
                let line = self.line_of_last();
                self.emit("module", "reference", &module, line);
            }
            self.skip_space();
            if self.at_word("import") {
                self.bump();
            }
            self.import_names();
        } else if let Some(module) = self.dotted() {
            let line = self.line_of_last();
            self.emit("module", "reference", &module, line);
        }
        self.skip_until_newline();
    }

    fn import_names(&mut self) {
        loop {
            self.skip_space();
            if self.at(kind::NEWLINE) || self.index >= self.tokens.len() {
                break;
            }
            if let Some(name) = self.ident_text() {
                if name != "as" {
                    let line = self.line_here();
                    self.emit("import", "reference", &name, line);
                }
                self.bump();
            } else {
                self.bump();
            }
            self.skip_space();
            if self.at_word("as") {
                self.bump();
                self.skip_space();
                if self.at(kind::IDENT) {
                    self.bump();
                }
            }
            self.skip_space();
            if self.at_word(",") {
                self.bump();
                continue;
            }
            break;
        }
    }

    fn simple(&mut self) {
        // Assignment: walk the target in store context, then the value.
        let saved = self.index;
        let mut depth = 0i32;
        let mut eq = None;
        let mut walrus = None;
        while self.index < self.tokens.len() && !(depth == 0 && self.at(kind::NEWLINE)) {
            if self.at_word("(") || self.at_word("[") || self.at_word("{") {
                depth += 1;
            } else if self.at_word(")") || self.at_word("]") || self.at_word("}") {
                depth -= 1;
            } else if depth == 0 && self.at_word("=") {
                eq = Some(self.index);
                break;
            } else if depth == 0 && self.at_word(":=") {
                walrus = Some(self.index);
                break;
            }
            self.bump();
        }
        if let Some(at) = eq {
            self.index = saved;
            self.store_target_until(at);
            self.index = at + 1;
            self.expr_until_newline();
            return;
        }
        if let Some(at) = walrus {
            self.index = saved;
            self.skip_until(at);
            self.index = at + 1;
            self.expr_until_newline();
            return;
        }
        self.index = saved;
        self.expr_until_newline();
    }

    fn store_target_until(&mut self, limit: usize) {
        // Emit loads inside an attribute target (`self` in `self.value`) and
        // skip a bare name, which is a store.
        if self.at(kind::IDENT) {
            let name_at = self.index;
            self.bump();
            self.skip_space();
            if self.index < limit && self.at_word(".") {
                self.index = name_at;
                self.expr_until(limit);
                return;
            }
            return;
        }
        self.expr_until(limit);
    }
}

fn is_keyword(name: &str) -> bool {
    matches!(
        name,
        "def"
            | "class"
            | "async"
            | "await"
            | "if"
            | "elif"
            | "else"
            | "for"
            | "while"
            | "try"
            | "except"
            | "finally"
            | "with"
            | "as"
            | "import"
            | "from"
            | "return"
            | "yield"
            | "raise"
            | "pass"
            | "break"
            | "continue"
            | "lambda"
            | "match"
            | "case"
            | "and"
            | "or"
            | "not"
            | "in"
            | "is"
            | "global"
            | "nonlocal"
            | "assert"
            | "del"
            | "True"
            | "False"
            | "None"
    )
}

fn intern_kind(kind_name: &str) -> &'static str {
    match kind_name {
        "function" => "function",
        "class" => "class",
        "module" => "module",
        "import" => "import",
        _ => "name",
    }
}

fn intern_role(role: &str) -> &'static str {
    if role == "definition" {
        "definition"
    } else {
        "reference"
    }
}

fn regex_fallback(source: &str) -> Vec<SymbolHit> {
    let mut symbols = Vec::new();
    for (index, line) in source.lines().enumerate() {
        let stripped = line.trim();
        let line_no = index as u32 + 1;
        if let Some(name) = keyword_name(stripped, "def").or_else(|| {
            stripped
                .strip_prefix("async")
                .map(str::trim_start)
                .and_then(|rest| keyword_name(rest, "def"))
        }) {
            symbols.push(hit(&name, "function", "definition", line_no));
            continue;
        }
        if let Some(name) = keyword_name(stripped, "class") {
            symbols.push(hit(&name, "class", "definition", line_no));
            continue;
        }
        if let Some(rest) = stripped.strip_prefix("from ") {
            if let Some((module, names)) = rest.split_once(" import ") {
                symbols.push(hit(module.trim(), "module", "reference", line_no));
                for name in names.split(|ch: char| !ch.is_ascii_alphanumeric() && ch != '_') {
                    if !name.is_empty() && name != "as" && name != "import" {
                        symbols.push(hit(name, "import", "reference", line_no));
                    }
                }
            }
            continue;
        }
        if let Some(rest) = stripped.strip_prefix("import ") {
            let module = rest.split_whitespace().next().unwrap_or(rest);
            symbols.push(hit(
                module.trim_end_matches(','),
                "module",
                "reference",
                line_no,
            ));
        }
    }
    symbols
}

fn keyword_name(stripped: &str, keyword: &str) -> Option<String> {
    let rest = stripped.strip_prefix(keyword)?.trim_start();
    let mut name = String::new();
    for ch in rest.chars() {
        if ch == '_' || ch.is_ascii_alphanumeric() || !ch.is_ascii() {
            name.push(ch);
        } else {
            break;
        }
    }
    if name.is_empty() {
        None
    } else {
        Some(name)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::json::{self, Value};

    #[test]
    fn keyword_only_params_still_walk_the_body() {
        let source = "class Spinner:\n    def __init__(self, name: str, *, style: \"T\" = None) -> None:\n        self.style = style\n";
        let hits = extract(source);
        assert!(
            hits.iter()
                .any(|hit| hit.name == "self" && hit.role == "reference"),
            "{hits:?}"
        );
    }

    #[test]
    fn garbled_and_truncated_inputs_yield_symbols() {
        let samples = ["", "\0\u{fffd}", "def (", "((((", "class\n", "def ok(\n"];
        for sample in samples {
            let _ = extract(sample);
        }
        let mut state = 7u64;
        for _ in 0..200 {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            let len = (state % 40) as usize;
            let mut bytes = Vec::with_capacity(len);
            for _ in 0..len {
                state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
                bytes.push(state as u8);
            }
            let text = String::from_utf8_lossy(&bytes);
            let _ = extract(&text);
        }
    }

    #[test]
    fn python_fixture_symbols_match_the_golden() {
        let golden = std::fs::read_to_string("parity/goldens/fixtures.json").unwrap();
        let value = json::parse(&golden).unwrap();
        let files = object_get(&value, "files").unwrap();
        let Value::Array(files) = files else {
            panic!("files");
        };
        for file in files {
            let path = string_field(file, "path");
            let ext = string_field(file, "extension");
            if ext != "py" && ext != "pyw" && ext != "pyx" {
                continue;
            }
            let source = std::fs::read_to_string(format!("parity/fixtures/{path}")).unwrap();
            let got = extract(&source);
            let expected = symbol_rows(file);
            assert_eq!(
                got.len(),
                expected.len(),
                "{path}\n got {got:?}\n exp {expected:?}"
            );
            for (left, right) in got.iter().zip(expected.iter()) {
                assert_eq!(left, right, "{path}");
            }
        }
    }

    fn symbol_rows(file: &Value) -> Vec<SymbolHit> {
        let Value::Array(rows) = object_get(file, "symbols").unwrap() else {
            panic!("symbols");
        };
        rows.iter()
            .map(|row| SymbolHit {
                name: string_field(row, "name"),
                kind: intern_kind(&string_field(row, "kind")),
                role: intern_role(&string_field(row, "role")),
                line: match object_get(row, "line") {
                    Some(Value::Int(line)) => *line as u32,
                    _ => 0,
                },
                container: match object_get(row, "container") {
                    Some(Value::String(text)) => Some(text.clone()),
                    _ => None,
                },
            })
            .collect()
    }

    fn string_field(value: &Value, key: &str) -> String {
        match object_get(value, key) {
            Some(Value::String(text)) => text.clone(),
            _ => String::new(),
        }
    }

    fn object_get<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
        let Value::Object(pairs) = value else {
            return None;
        };
        pairs
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, item)| item)
    }
}

fn hit(name: &str, kind_name: &str, role: &str, line: u32) -> SymbolHit {
    SymbolHit {
        name: name.to_string(),
        kind: intern_kind(kind_name),
        role: intern_role(role),
        line,
        container: None,
    }
}
