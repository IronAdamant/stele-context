//! Expression walking for the Python symbol cursor.

use super::{intern_kind, intern_role, is_keyword, Cursor};
use crate::lang::kind;
use crate::syntax::cst::Token;
use crate::syntax::query::SymbolHit;

impl Cursor<'_> {
    pub(in crate::lang::python_symbols) fn expr_until_seps(&mut self, seps: &[&str]) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            self.skip_space();
            if depth == 0 && seps.iter().any(|sep| self.at_word(sep)) {
                break;
            }
            if depth == 0 && (self.at(kind::NEWLINE) || self.at(kind::DEDENT)) {
                break;
            }
            self.expr_piece(&mut depth);
        }
    }

    pub(in crate::lang::python_symbols) fn expr_until_newline(&mut self) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            if self.at(kind::DEDENT) || (depth == 0 && self.at(kind::NEWLINE)) {
                if depth == 0 && self.at(kind::NEWLINE) {
                    self.bump();
                }
                break;
            }
            if self.at_word("def") || self.at_word("class") || self.at_word("@") {
                break;
            }
            let before = self.index;
            self.expr_piece(&mut depth);
            if self.index == before {
                break;
            }
        }
    }

    pub(in crate::lang::python_symbols) fn expr_until_colon(&mut self) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            self.skip_space();
            if depth == 0 && (self.at_word(":") || self.at(kind::NEWLINE) || self.at(kind::DEDENT))
            {
                if self.at_word(":") {
                    self.bump();
                }
                break;
            }
            self.expr_piece(&mut depth);
        }
    }

    pub(in crate::lang::python_symbols) fn expr_until_comma_or_paren(&mut self) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            // Skip spaces before the terminator check. Otherwise a newline
            // and the indent before `)` are walked by expr_piece, which
            // consumes that `)` and the rest of the signature.
            self.skip_space();
            if self.at(kind::NEWLINE) && depth == 0 {
                self.bump();
                continue;
            }
            if depth == 0 && (self.at_word(",") || self.at_word(")") || self.at_word("=")) {
                break;
            }
            self.expr_piece(&mut depth);
        }
    }

    pub(in crate::lang::python_symbols) fn expr_list_until(&mut self, end: &str) {
        let mut depth = 1i32;
        while self.index < self.tokens.len() && depth > 0 {
            if self.at_word("(") || self.at_word("[") || self.at_word("{") {
                depth += 1;
                self.bump();
                continue;
            }
            if (self.at_word(end) && depth == 1)
                || self.at_word(")")
                || self.at_word("]")
                || self.at_word("}")
            {
                depth -= 1;
                self.bump();
                if depth == 0 {
                    break;
                }
                continue;
            }
            self.expr_piece(&mut depth);
        }
    }

    pub(in crate::lang::python_symbols) fn expr_until(&mut self, limit: usize) {
        let mut depth = 0i32;
        while self.index < limit && self.index < self.tokens.len() {
            let before = self.index;
            self.expr_piece(&mut depth);
            if self.index == before {
                if self.at(kind::NEWLINE) {
                    self.bump();
                    continue;
                }
                break;
            }
        }
    }

    fn balanced_args(&mut self) {
        // One depth, shared with nested calls. A separate counter misses the
        // `(` that expr_piece consumes and then treats the inner `)` as ours.
        let mut depth = 1i32;
        while self.index < self.tokens.len() && depth > 0 {
            self.skip_space();
            if self.at(kind::NEWLINE) {
                self.bump();
                continue;
            }
            if self.at(kind::DEDENT)
                || self.at_word("def")
                || self.at_word("class")
                || self.at_word("@")
            {
                break;
            }
            let before = self.index;
            self.expr_piece(&mut depth);
            if self.index == before {
                break;
            }
        }
    }

    pub(in crate::lang::python_symbols) fn expr_piece(&mut self, depth: &mut i32) {
        self.skip_space();
        if self.index >= self.tokens.len() {
            return;
        }
        // A newline outside brackets ends the expression. Swallowing it here
        // would pull the next statement into this one (the usual case is a
        // trailing comment: `value = set()  # note`).
        // A dedent closes the statement even inside an open paren. Consuming
        // it here would nest the next method inside this one.
        if self.at(kind::DEDENT) || (*depth == 0 && self.at(kind::NEWLINE)) {
            return;
        }
        if self.at_word(".") {
            self.bump();
            self.skip_space();
            if self.at(kind::IDENT) {
                let attr = self.ident_text().unwrap_or_default();
                let line = self.line_here();
                let call = self.next_real_is("(");
                self.bump();
                if call {
                    self.emit("function", "reference", &attr, line);
                    self.skip_space();
                    if self.at_word("(") {
                        self.bump();
                        *depth += 1;
                    }
                }
            }
            return;
        }
        if self.at(kind::IDENT) {
            let word = self.ident_text().unwrap_or_default();
            if word == "lambda" {
                self.bump();
                self.lambda_params();
                return;
            }
            if word == "for" || (word == "async" && self.next_real_is("for")) {
                if word == "async" {
                    self.bump();
                    self.skip_space();
                }
                self.bump();
                self.consume_for_in();
                return;
            }
            if self.next_real_is(":=") {
                self.bump();
                self.skip_space();
                if self.at_word(":=") {
                    self.bump();
                }
                return;
            }
            // Keyword argument names are not loads. `print(style="x")` must
            // not record `style`, matching CPython's ast walk.
            if *depth > 0 && self.next_real_is("=") {
                self.bump();
                return;
            }
            let name = self.ident_text().unwrap_or_default();
            let line = self.line_here();
            let is_call = self.next_real_is("(") && !is_keyword(&name);
            let is_attr = self.next_real_is(".");
            if is_call {
                if let Some((attr, attr_line)) = self.attr_after_call() {
                    self.chain_name = attr.clone();
                    self.chain_line = attr_line;
                    self.chain_armed = false;
                    self.emit("function", "reference", &attr, attr_line);
                    self.chain_armed = true;
                    if !is_keyword(&name) {
                        self.emit("function", "reference", &name, line);
                    }
                    self.bump();
                    self.skip_space();
                    self.bump();
                    self.balanced_args();
                    // The chained call was already emitted. Consume `.name(args)`
                    // here so the outer expression depth stays put and the next
                    // statement is not swallowed.
                    self.skip_space();
                    if self.at_word(".") {
                        self.bump();
                    }
                    self.skip_space();
                    if self.at(kind::IDENT) {
                        self.bump();
                    }
                    self.skip_space();
                    if self.at_word("(") {
                        self.bump();
                        *depth += 1;
                    }
                    // Later calls of the same name on this line are real.
                    self.chain_armed = false;
                    return;
                }
                self.bump();
                self.skip_space();
                self.bump();
                *depth += 1;
                if !is_keyword(&name) {
                    self.emit("function", "reference", &name, line);
                }
                return;
            }
            let format_letter = self.format_spec_letter(&name);
            self.bump();
            if is_attr {
                // The name is the receiver. A following call is handled when
                // its own identifier is seen, except we must emit the call
                // name before the receiver. `emit_trailer` fixes that chain.
                self.emit_receiver_chain(&name, line, depth, true);
                return;
            }
            if !is_keyword(&name) && name != "_" && !format_letter {
                self.emit("name", "reference", &name, line);
            }
            return;
        }
        if self.at_word("(") || self.at_word("[") || self.at_word("{") {
            *depth += 1;
        } else if self.at_word(")") || self.at_word("]") || self.at_word("}") {
            *depth -= 1;
        } else if self.at_word("!") {
            // f-string conversion (`{name!r}`) is not a load of `r`.
            self.bump();
            self.skip_space();
            if self.at(kind::IDENT) {
                let name = self.ident_text().unwrap_or_default();
                if matches!(name.as_str(), "r" | "s" | "a") {
                    self.bump();
                }
            }
            return;
        }
        self.bump();
    }

    pub(in crate::lang::python_symbols) fn emit_receiver_chain(
        &mut self,
        receiver: &str,
        line: u32,
        depth: &mut i32,
        emit_receiver: bool,
    ) {
        // `self._spans` is a Name `self` plus attributes. Only the Name is a
        // load. An attribute is a function reference only when it is called.
        self.skip_space();
        if !self.at_word(".") {
            if emit_receiver && !is_keyword(receiver) && receiver != "_" {
                self.emit("name", "reference", receiver, line);
            }
            return;
        }
        self.bump();
        self.skip_space();
        if !self.at(kind::IDENT) {
            if emit_receiver && !is_keyword(receiver) && receiver != "_" {
                self.emit("name", "reference", receiver, line);
            }
            return;
        }
        let attr = self.ident_text().unwrap_or_default();
        let attr_line = self.line_here();
        self.bump();
        self.skip_space();
        if self.at_word("(") {
            self.emit("function", "reference", &attr, attr_line);
            if emit_receiver && !is_keyword(receiver) && receiver != "_" {
                self.emit("name", "reference", receiver, line);
            }
            self.bump();
            *depth += 1;
            return;
        }
        if emit_receiver && !is_keyword(receiver) && receiver != "_" {
            self.emit("name", "reference", receiver, line);
        }
        if self.at_word(".") {
            self.emit_receiver_chain(&attr, attr_line, depth, false);
        }
    }

    pub(in crate::lang::python_symbols) fn capture(
        &mut self,
        body: impl FnOnce(&mut Self),
    ) -> Vec<SymbolHit> {
        let start = self.symbols.len();
        body(self);
        self.symbols.split_off(start)
    }

    pub(in crate::lang::python_symbols) fn dotted(&mut self) -> Option<String> {
        self.skip_space();
        // `from .pkg import Name` — the dots are the import level, not the name.
        while self.at_word(".") {
            self.bump();
            self.skip_space();
        }
        let mut parts = Vec::new();
        loop {
            self.skip_space();
            let Some(part) = self.ident_text() else {
                break;
            };
            // `from . import name` — `import` is the keyword, not the module.
            if part == "import" {
                break;
            }
            parts.push(part);
            self.bump();
            self.skip_space();
            if self.at_word(".") {
                self.bump();
                continue;
            }
            break;
        }
        if parts.is_empty() {
            None
        } else {
            Some(parts.join("."))
        }
    }

    pub(in crate::lang::python_symbols) fn emit(
        &mut self,
        kind_name: &str,
        role: &str,
        name: &str,
        line: u32,
    ) {
        if self.chain_armed
            && kind_name == "function"
            && role == "reference"
            && name == self.chain_name
            && line == self.chain_line
        {
            self.chain_armed = false;
            return;
        }
        self.symbols.push(SymbolHit {
            name: name.to_string(),
            kind: intern_kind(kind_name),
            role: intern_role(role),
            line,
            container: self.container(),
        });
    }

    pub(in crate::lang::python_symbols) fn flush_pending(&mut self) {
        let container = self.container();
        for mut hit in std::mem::take(&mut self.pending) {
            hit.container = container.clone();
            self.symbols.push(hit);
        }
    }

    pub(in crate::lang::python_symbols) fn container(&self) -> Option<String> {
        if self.scope.is_empty() {
            None
        } else {
            Some(self.scope.join("."))
        }
    }

    pub(in crate::lang::python_symbols) fn line_here(&self) -> u32 {
        let start = self
            .tokens
            .get(self.index)
            .map(|token| token.start)
            .unwrap_or(0);
        self.line_at(start)
    }

    pub(in crate::lang::python_symbols) fn line_at(&self, start: u32) -> u32 {
        self.source[..start as usize]
            .bytes()
            .filter(|byte| *byte == b'\n')
            .count() as u32
            + 1
    }

    pub(in crate::lang::python_symbols) fn ident_text(&self) -> Option<String> {
        let token = self.tokens.get(self.index)?;
        if token.kind != kind::IDENT {
            return None;
        }
        Some(self.slice(token).to_string())
    }

    pub(in crate::lang::python_symbols) fn at(&self, kind_id: u16) -> bool {
        self.tokens
            .get(self.index)
            .is_some_and(|token| token.kind == kind_id)
    }

    pub(in crate::lang::python_symbols) fn at_word(&self, word: &str) -> bool {
        self.tokens.get(self.index).is_some_and(|token| {
            self.slice(token) == word && self.word_kind_matches(token.kind, word)
        })
    }

    fn word_kind_matches(&self, kind_id: u16, word: &str) -> bool {
        // An f-string literal fragment can be a single bracket. That is a
        // string, not an operator, and must not change paren depth.
        let punct = word
            .chars()
            .next()
            .is_some_and(|ch| !ch.is_ascii_alphanumeric() && ch != '_');
        !punct || kind_id == kind::OP
    }

    pub(in crate::lang::python_symbols) fn attr_after_call(&self) -> Option<(String, u32)> {
        let mut index = self.index + 1;
        while index < self.tokens.len()
            && matches!(self.tokens[index].kind, kind::WHITESPACE | kind::COMMENT)
        {
            index += 1;
        }
        if self
            .tokens
            .get(index)
            .is_none_or(|token| token.kind != kind::OP || self.slice(token) != "(")
        {
            return None;
        }
        let mut depth = 0i32;
        while index < self.tokens.len() {
            let token = &self.tokens[index];
            let text = self.slice(token);
            if token.kind == kind::OP && (text == "(" || text == "[" || text == "{") {
                depth += 1;
            } else if token.kind == kind::OP && (text == ")" || text == "]" || text == "}") {
                depth -= 1;
                if depth == 0 {
                    index += 1;
                    break;
                }
            }
            index += 1;
        }
        while index < self.tokens.len()
            && matches!(self.tokens[index].kind, kind::WHITESPACE | kind::COMMENT)
        {
            index += 1;
        }
        if self
            .tokens
            .get(index)
            .is_none_or(|token| token.kind != kind::OP || self.slice(token) != ".")
        {
            return None;
        }
        index += 1;
        while index < self.tokens.len()
            && matches!(self.tokens[index].kind, kind::WHITESPACE | kind::COMMENT)
        {
            index += 1;
        }
        let token = self.tokens.get(index)?;
        if token.kind != kind::IDENT {
            return None;
        }
        let attr = self.slice(token).to_string();
        let line = self.line_at(token.start);
        index += 1;
        while index < self.tokens.len()
            && matches!(self.tokens[index].kind, kind::WHITESPACE | kind::COMMENT)
        {
            index += 1;
        }
        if self
            .tokens
            .get(index)
            .is_some_and(|token| token.kind == kind::OP && self.slice(token) == "(")
        {
            Some((attr, line))
        } else {
            None
        }
    }

    fn format_spec_letter(&self, name: &str) -> bool {
        // `{value:.1f}` and `{value:02x}` — the type letter is not a load.
        let mut chars = name.chars();
        let Some(ch) = chars.next() else {
            return false;
        };
        if chars.next().is_some() || !"sSdDxXoObBeEfFgGn%".contains(ch) {
            return false;
        }
        if !self.next_real_is("}") {
            return false;
        }
        let mut index = self.index;
        while index > 0 {
            index -= 1;
            let kind_id = self.tokens[index].kind;
            if matches!(kind_id, kind::WHITESPACE | kind::COMMENT) {
                continue;
            }
            if kind_id == kind::NUMBER {
                return true;
            }
            if kind_id != kind::OP {
                return false;
            }
            let text = self.slice(&self.tokens[index]);
            // `{value:d}`, `{value:.1f}`, and `{value:{width}d}`.
            return matches!(text, "." | "<" | ">" | "^" | "=" | "#" | ":" | "}");
        }
        false
    }

    pub(in crate::lang::python_symbols) fn next_real_is(&self, word: &str) -> bool {
        let mut index = self.index + 1;
        while index < self.tokens.len()
            && matches!(
                self.tokens[index].kind,
                kind::WHITESPACE | kind::COMMENT | kind::NEWLINE
            )
        {
            index += 1;
        }
        self.tokens.get(index).is_some_and(|token| {
            self.slice(token) == word && self.word_kind_matches(token.kind, word)
        })
    }

    pub(in crate::lang::python_symbols) fn slice(&self, token: &Token) -> &str {
        let start = token.start as usize;
        let end = (token.end as usize).min(self.source.len());
        if start <= end {
            &self.source[start..end]
        } else {
            ""
        }
    }

    pub(in crate::lang::python_symbols) fn bump(&mut self) {
        if self.index < self.tokens.len() {
            self.index += 1;
        }
    }

    pub(in crate::lang::python_symbols) fn bump_line(&mut self) {
        self.skip_until_newline();
    }

    pub(in crate::lang::python_symbols) fn skip_until_newline(&mut self) {
        while self.index < self.tokens.len() && !self.at(kind::NEWLINE) {
            self.bump();
        }
        if self.at(kind::NEWLINE) {
            self.bump();
        }
    }

    pub(in crate::lang::python_symbols) fn skip_space(&mut self) {
        while self.index < self.tokens.len()
            && matches!(
                self.tokens[self.index].kind,
                kind::WHITESPACE | kind::COMMENT
            )
        {
            self.index += 1;
        }
    }

    pub(in crate::lang::python_symbols) fn skip_noise(&mut self) {
        while self.index < self.tokens.len()
            && matches!(
                self.tokens[self.index].kind,
                kind::WHITESPACE | kind::COMMENT | kind::NEWLINE
            )
        {
            self.index += 1;
        }
    }
}
