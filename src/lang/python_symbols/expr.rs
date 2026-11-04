//! Expression walking for the Python symbol cursor.

use super::{intern_kind, intern_role, is_keyword, Cursor};
use crate::lang::kind;
use crate::syntax::cst::Token;
use crate::syntax::query::SymbolHit;

impl Cursor<'_> {
    pub(super) fn expr_until_newline(&mut self) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            if depth == 0 && (self.at(kind::NEWLINE) || self.at(kind::DEDENT)) {
                if self.at(kind::NEWLINE) {
                    self.bump();
                }
                break;
            }
            self.expr_piece(&mut depth);
        }
    }

    pub(super) fn expr_until_colon(&mut self) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            if depth == 0 && self.at_word(":") {
                self.bump();
                break;
            }
            if self.at(kind::NEWLINE) && depth == 0 {
                break;
            }
            self.expr_piece(&mut depth);
        }
    }

    pub(super) fn expr_until_comma_or_paren(&mut self) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            if depth == 0 && (self.at_word(",") || self.at_word(")") || self.at_word("=")) {
                break;
            }
            self.expr_piece(&mut depth);
        }
    }

    pub(super) fn expr_list_until(&mut self, end: &str) {
        let mut depth = 1i32;
        while self.index < self.tokens.len() && depth > 0 {
            if self.at_word("(") || self.at_word("[") || self.at_word("{") {
                depth += 1;
                self.bump();
                continue;
            }
            if self.at_word(end) && depth == 1
                || self.at_word(")")
                || self.at_word("]")
                || self.at_word("}")
            {
                depth -= 1;
                if depth == 0 {
                    self.bump();
                    break;
                }
            }
            self.expr_piece(&mut depth);
        }
    }

    pub(super) fn expr_until(&mut self, limit: usize) {
        let mut depth = 0i32;
        while self.index < limit && self.index < self.tokens.len() {
            self.expr_piece(&mut depth);
        }
    }

    pub(super) fn expr_piece(&mut self, depth: &mut i32) {
        self.skip_space();
        if self.index >= self.tokens.len() {
            return;
        }
        if self.at(kind::IDENT) {
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
            let is_call = self.next_real_is("(");
            let is_attr = self.next_real_is(".");
            if is_call {
                if let Some((attr, attr_line)) = self.attr_after_call() {
                    self.emit("function", "reference", &attr, attr_line);
                    if !is_keyword(&name) {
                        self.emit("function", "reference", &name, line);
                    }
                    self.bump();
                    self.skip_space();
                    self.bump();
                    let mut inner = 1i32;
                    while self.index < self.tokens.len() && inner > 0 {
                        self.skip_space();
                        if self.at_word("(") || self.at_word("[") || self.at_word("{") {
                            inner += 1;
                            self.bump();
                            continue;
                        }
                        if self.at_word(")") || self.at_word("]") || self.at_word("}") {
                            inner -= 1;
                            self.bump();
                            continue;
                        }
                        self.expr_piece(depth);
                    }
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
            self.bump();
            if is_attr {
                // The name is the receiver. A following call is handled when
                // its own identifier is seen, except we must emit the call
                // name before the receiver. `emit_trailer` fixes that chain.
                self.emit_receiver_chain(&name, line, depth);
                return;
            }
            if !is_keyword(&name) && name != "_" {
                self.emit("name", "reference", &name, line);
            }
            return;
        }
        if self.at_word("(") || self.at_word("[") || self.at_word("{") {
            *depth += 1;
        } else if self.at_word(")") || self.at_word("]") || self.at_word("}") {
            *depth -= 1;
        }
        self.bump();
    }

    pub(super) fn emit_receiver_chain(&mut self, receiver: &str, line: u32, depth: &mut i32) {
        // NAME '.' NAME '('  => call on the attribute, receiver emitted after.
        self.skip_space();
        if !self.at_word(".") {
            if !is_keyword(receiver) && receiver != "_" {
                self.emit("name", "reference", receiver, line);
            }
            return;
        }
        self.bump();
        self.skip_space();
        if !self.at(kind::IDENT) {
            if !is_keyword(receiver) {
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
            if !is_keyword(receiver) && receiver != "_" {
                self.emit("name", "reference", receiver, line);
            }
            self.bump();
            *depth += 1;
            return;
        }
        if !is_keyword(receiver) && receiver != "_" {
            self.emit("name", "reference", receiver, line);
        }
        // Keep walking if this attribute is itself a receiver (`a.b.c`).
        if self.at_word(".") {
            self.emit_receiver_chain(&attr, attr_line, depth);
        }
    }

    pub(super) fn capture(&mut self, body: impl FnOnce(&mut Self)) -> Vec<SymbolHit> {
        let start = self.symbols.len();
        body(self);
        self.symbols.split_off(start)
    }

    pub(super) fn dotted(&mut self) -> Option<String> {
        let mut parts = Vec::new();
        loop {
            self.skip_space();
            let Some(part) = self.ident_text() else {
                break;
            };
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

    pub(super) fn emit(&mut self, kind_name: &str, role: &str, name: &str, line: u32) {
        self.symbols.push(SymbolHit {
            name: name.to_string(),
            kind: intern_kind(kind_name),
            role: intern_role(role),
            line,
            container: self.container(),
        });
    }

    pub(super) fn flush_pending(&mut self) {
        let container = self.container();
        for mut hit in std::mem::take(&mut self.pending) {
            hit.container = container.clone();
            self.symbols.push(hit);
        }
    }

    pub(super) fn container(&self) -> Option<String> {
        if self.scope.is_empty() {
            None
        } else {
            Some(self.scope.join("."))
        }
    }

    pub(super) fn line_here(&self) -> u32 {
        let start = self
            .tokens
            .get(self.index)
            .map(|token| token.start)
            .unwrap_or(0);
        self.line_at(start)
    }

    pub(super) fn line_of_last(&self) -> u32 {
        let start = self
            .tokens
            .get(self.index.saturating_sub(1))
            .map(|token| token.start)
            .unwrap_or(0);
        self.line_at(start)
    }

    pub(super) fn line_at(&self, start: u32) -> u32 {
        self.source[..start as usize]
            .bytes()
            .filter(|byte| *byte == b'\n')
            .count() as u32
            + 1
    }

    pub(super) fn ident_text(&self) -> Option<String> {
        let token = self.tokens.get(self.index)?;
        if token.kind != kind::IDENT {
            return None;
        }
        Some(self.slice(token).to_string())
    }

    pub(super) fn at(&self, kind_id: u16) -> bool {
        self.tokens
            .get(self.index)
            .is_some_and(|token| token.kind == kind_id)
    }

    pub(super) fn at_word(&self, word: &str) -> bool {
        self.tokens
            .get(self.index)
            .is_some_and(|token| self.slice(token) == word)
    }

    pub(super) fn attr_after_call(&self) -> Option<(String, u32)> {
        let mut index = self.index + 1;
        while index < self.tokens.len()
            && matches!(self.tokens[index].kind, kind::WHITESPACE | kind::COMMENT)
        {
            index += 1;
        }
        if self
            .tokens
            .get(index)
            .is_none_or(|token| self.slice(token) != "(")
        {
            return None;
        }
        let mut depth = 0i32;
        while index < self.tokens.len() {
            let text = self.slice(&self.tokens[index]);
            if text == "(" || text == "[" || text == "{" {
                depth += 1;
            } else if text == ")" || text == "]" || text == "}" {
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
            .is_none_or(|token| self.slice(token) != ".")
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
            .is_some_and(|token| self.slice(token) == "(")
        {
            Some((attr, line))
        } else {
            None
        }
    }

    pub(super) fn next_real_is(&self, word: &str) -> bool {
        let mut index = self.index + 1;
        while index < self.tokens.len()
            && matches!(
                self.tokens[index].kind,
                kind::WHITESPACE | kind::COMMENT | kind::NEWLINE
            )
        {
            index += 1;
        }
        self.tokens
            .get(index)
            .is_some_and(|token| self.slice(token) == word)
    }

    pub(super) fn slice(&self, token: &Token) -> &str {
        let start = token.start as usize;
        let end = (token.end as usize).min(self.source.len());
        if start <= end {
            &self.source[start..end]
        } else {
            ""
        }
    }

    pub(super) fn bump(&mut self) {
        if self.index < self.tokens.len() {
            self.index += 1;
        }
    }

    pub(super) fn bump_line(&mut self) {
        self.skip_until_newline();
    }

    pub(super) fn skip_until_newline(&mut self) {
        while self.index < self.tokens.len() && !self.at(kind::NEWLINE) {
            self.bump();
        }
        if self.at(kind::NEWLINE) {
            self.bump();
        }
    }

    pub(super) fn skip_until(&mut self, limit: usize) {
        while self.index < limit {
            self.bump();
        }
    }

    pub(super) fn skip_space(&mut self) {
        while self.index < self.tokens.len()
            && matches!(
                self.tokens[self.index].kind,
                kind::WHITESPACE | kind::COMMENT
            )
        {
            self.index += 1;
        }
    }

    pub(super) fn skip_noise(&mut self) {
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
