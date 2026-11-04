//! Symbol table matching CPython's `ast` walk for a valid module, and the
//! 1.5.2 regex fallback when the module is not valid Python.
//!
//! Visit order follows `FunctionDef` fields: the definition, then parameters,
//! then the body, then decorators, then the return annotation.

mod call_line;
mod expr;
mod store;
mod valid;

use crate::lang::kind;
use crate::lang::python::lex::lex;
use crate::syntax::cst::Token;
use crate::syntax::query::SymbolHit;

pub fn extract(source: &str) -> Vec<SymbolHit> {
    let tokens = lex(source);
    // ast.parse failures use the regex fallback, including a chunk that was
    // cut through a list, a dict, or a decorator.
    if !valid::module_is_valid(source) {
        return regex_fallback(source);
    }
    let mut cursor = Cursor {
        source,
        tokens: &tokens,
        index: 0,
        symbols: Vec::new(),
        scope: Vec::new(),
        pending: Vec::new(),
        chain_name: String::new(),
        chain_line: 0,
        chain_armed: false,
    };
    cursor.module();
    cursor.symbols
}

struct Cursor<'a> {
    source: &'a str,
    tokens: &'a [Token],
    index: usize,
    symbols: Vec<SymbolHit>,
    scope: Vec<String>,
    pending: Vec<SymbolHit>,
    /// One chained call (`obj.method().other`) is emitted before its receiver.
    /// A second emit of that same call on the same line is the walk of the
    /// chain, not another call.
    chain_name: String,
    chain_line: u32,
    chain_armed: bool,
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
        // Decorators collected before this `def` belong to it, not to a
        // nested function that would otherwise flush `pending` first.
        let decorators = std::mem::take(&mut self.pending);
        self.bump();
        self.skip_space();
        let Some(name) = self.ident_text() else {
            self.pending = decorators;
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
        self.adopt(decorators);
        self.flush_pending();
        self.symbols.extend(returns);
        self.scope.pop();
    }

    fn class_def(&mut self) {
        let decorators = std::mem::take(&mut self.pending);
        self.bump();
        self.skip_space();
        let Some(name) = self.ident_text() else {
            self.pending = decorators;
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
        self.adopt(decorators);
        self.flush_pending();
        self.scope.pop();
    }

    fn adopt(&mut self, decorators: Vec<SymbolHit>) {
        let container = self.container();
        for mut hit in decorators {
            hit.container = container.clone();
            self.symbols.push(hit);
        }
    }

    fn params(&mut self) {
        // `arguments` visits every annotation, then keyword-only defaults,
        // then positional defaults. That is not source order.
        if !self.at_word("(") {
            return;
        }
        self.bump();
        let mut depth = 1i32;
        let mut kwonly = false;
        let mut annotations = Vec::new();
        let mut kw_defaults = Vec::new();
        let mut defaults = Vec::new();
        while self.index < self.tokens.len() && depth > 0 {
            self.skip_space();
            if self.index >= self.tokens.len() {
                break;
            }
            if self.at(kind::NEWLINE) {
                self.bump();
                continue;
            }
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
            if depth != 1 {
                self.bump();
                continue;
            }
            if self.at_word("**") {
                self.bump();
                self.skip_space();
                if self.at(kind::IDENT) {
                    self.bump();
                }
                self.skip_space();
                if self.at_word(":") {
                    self.bump();
                    annotations.extend(self.capture(|cursor| cursor.expr_until_comma_or_paren()));
                }
                continue;
            }
            if self.at_word("*") {
                self.bump();
                self.skip_space();
                kwonly = true;
                if self.at(kind::IDENT) {
                    self.bump();
                    self.skip_space();
                    if self.at_word(":") {
                        self.bump();
                        annotations
                            .extend(self.capture(|cursor| cursor.expr_until_comma_or_paren()));
                    }
                }
                continue;
            }
            if self.at_word("/") || self.at_word(",") {
                self.bump();
                continue;
            }
            if self.at(kind::IDENT) {
                self.bump();
                self.skip_space();
                if self.at_word(":") {
                    self.bump();
                    annotations.extend(self.capture(|cursor| cursor.expr_until_comma_or_paren()));
                    self.skip_space();
                }
                if self.at_word("=") {
                    self.bump();
                    let value = self.capture(|cursor| cursor.expr_until_comma_or_paren());
                    if kwonly {
                        kw_defaults.extend(value);
                    } else {
                        defaults.extend(value);
                    }
                }
                continue;
            }
            self.bump();
        }
        self.symbols.extend(annotations);
        self.symbols.extend(kw_defaults);
        self.symbols.extend(defaults);
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
        let keyword = self.ident_text().unwrap_or_default();
        self.bump();
        match keyword.as_str() {
            "for" => {
                self.consume_for_in();
                self.expr_until_colon();
            }
            "with" => self.with_header(),
            "except" => self.except_header(),
            _ => self.expr_until_colon(),
        }
        self.skip_until_newline();
        self.suite();
    }

    fn decorator(&mut self) {
        self.bump();
        self.expr_until_newline();
    }

    fn import_stmt(&mut self) {
        // Every alias shares the statement line. `ast` uses ImportFrom.lineno,
        // not the line of each parenthesized name.
        let line = self.line_here();
        let from = self.at_word("from");
        self.bump();
        self.skip_space();
        if from {
            if let Some(module) = self.dotted() {
                self.emit("module", "reference", &module, line);
            }
            self.skip_space();
            if self.at_word("import") {
                self.bump();
            }
            self.import_names(line);
        } else {
            loop {
                self.skip_space();
                let Some(module) = self.dotted() else {
                    break;
                };
                self.emit("module", "reference", &module, line);
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
        self.skip_until_newline();
    }

    fn import_names(&mut self, line: u32) {
        self.skip_space();
        let paren = self.at_word("(");
        if paren {
            self.bump();
        }
        loop {
            if paren {
                self.skip_noise();
            } else {
                self.skip_space();
            }
            if self.index >= self.tokens.len() {
                break;
            }
            if paren && self.at_word(")") {
                self.bump();
                break;
            }
            if !paren && self.at(kind::NEWLINE) {
                break;
            }
            if let Some(name) = self.ident_text() {
                if name != "as" {
                    self.emit("import", "reference", &name, line);
                }
                self.bump();
            } else {
                self.bump();
            }
            if paren {
                self.skip_noise();
            } else {
                self.skip_space();
            }
            if self.at_word("as") {
                self.bump();
                if paren {
                    self.skip_noise();
                } else {
                    self.skip_space();
                }
                if self.at(kind::IDENT) {
                    self.bump();
                }
            }
            if paren {
                self.skip_noise();
            } else {
                self.skip_space();
            }
            if self.at_word(",") {
                self.bump();
                continue;
            }
            if paren && self.at_word(")") {
                self.bump();
            }
            break;
        }
    }

    fn simple(&mut self) {
        self.skip_space();
        if self.at_word("global") || self.at_word("nonlocal") {
            self.skip_until_newline();
            return;
        }
        if self.at_word("del") {
            self.bump();
            let end = self.statement_limit();
            self.store_targets_before(end);
            self.index = end;
            if self.at(kind::NEWLINE) {
                self.bump();
            }
            return;
        }
        if self.at_word("lambda") {
            self.expr_until_newline();
            return;
        }
        let saved = self.index;
        let scan = self.scan_assign();
        if let Some(aug_at) = scan.aug {
            if scan.eqs.first().is_none_or(|eq| aug_at < *eq) {
                self.store_targets_before(aug_at);
                self.index = aug_at + 1;
                self.expr_until_newline();
                return;
            }
        }
        if let Some(&first) = scan.eqs.first() {
            if let Some(colon_at) = scan.colon {
                if colon_at < first {
                    self.store_targets_before(colon_at);
                    self.index = colon_at + 1;
                    self.expr_until(first);
                    self.index = first + 1;
                    self.expr_until_newline();
                    return;
                }
            }
            let mut at = saved;
            for &eq in &scan.eqs {
                self.index = at;
                self.store_targets_before(eq);
                at = eq + 1;
            }
            self.index = at;
            self.expr_until_newline();
            return;
        }
        if let Some(colon_at) = scan.colon {
            self.store_targets_before(colon_at);
            self.index = colon_at + 1;
            self.expr_until_newline();
            return;
        }
        self.index = saved;
        self.expr_until_newline();
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
    let rest = stripped.strip_prefix(keyword)?;
    // `default_showtraceback` starts with `def` but is not a definition.
    if !rest.starts_with(|ch: char| ch.is_whitespace()) {
        return None;
    }
    let rest = rest.trim_start();
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
mod tests;

fn hit(name: &str, kind_name: &str, role: &str, line: u32) -> SymbolHit {
    SymbolHit {
        name: name.to_string(),
        kind: intern_kind(kind_name),
        role: intern_role(role),
        line,
        container: None,
    }
}
