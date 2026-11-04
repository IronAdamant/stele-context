//! Store-context targets. CPython emits a name only when its context is Load,
//! so assignment targets, `for` targets, `with` binders, and `lambda`
//! parameters are not references.

use super::{is_keyword, Cursor};
use crate::lang::kind;

pub(in crate::lang::python_symbols) struct AssignScan {
    pub colon: Option<usize>,
    pub eqs: Vec<usize>,
    pub aug: Option<usize>,
}

impl Cursor<'_> {
    pub(in crate::lang::python_symbols) fn scan_assign(&self) -> AssignScan {
        let mut index = self.index;
        let mut depth = 0i32;
        let mut colon = None;
        let mut eqs = Vec::new();
        let mut aug = None;
        while index < self.tokens.len() {
            let token = &self.tokens[index];
            if depth == 0 && matches!(token.kind, kind::NEWLINE | kind::DEDENT) {
                break;
            }
            if token.kind == kind::OP {
                let text = self.slice(token);
                if text == "(" || text == "[" || text == "{" {
                    depth += 1;
                } else if text == ")" || text == "]" || text == "}" {
                    depth -= 1;
                } else if depth == 0 && text == ":" && colon.is_none() {
                    colon = Some(index);
                } else if depth == 0 && text == "=" {
                    eqs.push(index);
                } else if depth == 0 && aug.is_none() && is_aug(text) {
                    aug = Some(index);
                }
            }
            index += 1;
        }
        AssignScan { colon, eqs, aug }
    }

    pub(in crate::lang::python_symbols) fn statement_limit(&self) -> usize {
        let mut index = self.index;
        let mut depth = 0i32;
        while index < self.tokens.len() {
            let token = &self.tokens[index];
            if token.kind == kind::OP {
                let text = self.slice(token);
                if text == "(" || text == "[" || text == "{" {
                    depth += 1;
                } else if text == ")" || text == "]" || text == "}" {
                    depth -= 1;
                }
            } else if depth == 0 && matches!(token.kind, kind::NEWLINE | kind::DEDENT) {
                return index;
            }
            index += 1;
        }
        self.tokens.len()
    }

    pub(in crate::lang::python_symbols) fn store_targets_before(&mut self, limit: usize) {
        while self.index < limit {
            let before = self.index;
            self.skip_gap(limit, false);
            if self.index >= limit {
                break;
            }
            if self.at_word(",") {
                self.bump();
                continue;
            }
            self.store_one(limit);
            if self.index == before {
                break;
            }
        }
    }

    pub(in crate::lang::python_symbols) fn consume_for_in(&mut self) {
        if let Some(at) = self.find_word_at_depth0("in") {
            self.store_targets_before(at);
            self.index = at + 1;
        }
    }

    pub(in crate::lang::python_symbols) fn with_header(&mut self) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            self.skip_space();
            if self.index >= self.tokens.len() {
                return;
            }
            if depth == 0 && (self.at_word(":") || self.at(kind::NEWLINE) || self.at(kind::DEDENT))
            {
                if self.at_word(":") {
                    self.bump();
                }
                return;
            }
            if self.at_word("as") {
                self.bump();
                self.store_one(self.tokens.len());
                continue;
            }
            let before = self.index;
            self.expr_piece(&mut depth);
            if self.index == before {
                self.bump();
            }
        }
    }

    pub(in crate::lang::python_symbols) fn except_header(&mut self) {
        self.skip_space();
        if self.at_word("*") {
            self.bump();
        }
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            self.skip_space();
            if self.index >= self.tokens.len() {
                return;
            }
            if depth == 0
                && (self.at_word("as")
                    || self.at_word(":")
                    || self.at(kind::NEWLINE)
                    || self.at(kind::DEDENT))
            {
                if self.at_word("as") {
                    self.bump();
                    self.skip_space();
                    if self.at(kind::IDENT) {
                        self.bump();
                    }
                    self.skip_space();
                }
                if self.at_word(":") {
                    self.bump();
                }
                return;
            }
            let before = self.index;
            self.expr_piece(&mut depth);
            if self.index == before {
                self.bump();
            }
        }
    }

    pub(in crate::lang::python_symbols) fn lambda_params(&mut self) {
        let mut depth = 0i32;
        while self.index < self.tokens.len() {
            self.skip_space();
            if self.index >= self.tokens.len() {
                return;
            }
            if depth == 0 && (self.at_word(":") || self.at(kind::NEWLINE) || self.at(kind::DEDENT))
            {
                if self.at_word(":") {
                    self.bump();
                }
                return;
            }
            if depth == 0 && self.at_word("=") {
                self.bump();
                self.expr_until_seps(&[",", ":"]);
                continue;
            }
            if depth == 0
                && (self.at_word(",")
                    || self.at_word("**")
                    || self.at_word("*")
                    || self.at_word("/"))
            {
                self.bump();
                continue;
            }
            if depth == 0 && self.at(kind::IDENT) {
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
            if depth > 0 {
                let before = self.index;
                self.expr_piece(&mut depth);
                if self.index == before {
                    self.bump();
                }
                continue;
            }
            self.bump();
        }
    }

    fn store_one(&mut self, limit: usize) {
        if self.index >= limit {
            return;
        }
        self.skip_gap(limit, false);
        if self.at_word("*") || self.at_word("**") {
            self.bump();
            self.skip_gap(limit, false);
        }
        if self.index >= limit {
            return;
        }
        if self.at_word("(") || self.at_word("[") {
            let end = if self.at_word("(") { ")" } else { "]" };
            self.bump();
            while self.index < limit && self.index < self.tokens.len() {
                self.skip_gap(limit, true);
                if self.index >= limit {
                    break;
                }
                if self.at_word(end) {
                    self.bump();
                    break;
                }
                if self.at_word(",") {
                    self.bump();
                    continue;
                }
                let before = self.index;
                self.store_one(limit);
                if self.index == before {
                    self.bump();
                }
            }
            return;
        }
        if self.at(kind::IDENT) {
            let name = self.ident_text().unwrap_or_default();
            let line = self.line_here();
            self.bump();
            self.skip_gap(limit, false);
            if self.index < limit && (self.at_word(".") || self.at_word("[")) {
                if !is_keyword(&name) && name != "_" {
                    self.emit("name", "reference", &name, line);
                }
                self.store_trailers(limit);
            }
            return;
        }
        if self.index < limit {
            self.bump();
        }
    }

    fn store_trailers(&mut self, limit: usize) {
        loop {
            self.skip_gap(limit, false);
            if self.index >= limit || self.index >= self.tokens.len() {
                return;
            }
            if self.at_word(".") {
                self.bump();
                self.skip_gap(limit, false);
                if self.index < limit && self.at(kind::IDENT) {
                    self.bump();
                }
                continue;
            }
            if self.at_word("[") {
                self.bump();
                let mut depth = 1i32;
                while self.index < self.tokens.len() && depth > 0 {
                    let before = self.index;
                    self.expr_piece(&mut depth);
                    if self.index == before {
                        break;
                    }
                }
                continue;
            }
            return;
        }
    }

    fn find_word_at_depth0(&self, word: &str) -> Option<usize> {
        let mut index = self.index;
        let mut depth = 0i32;
        while index < self.tokens.len() {
            let token = &self.tokens[index];
            let text = self.slice(token);
            if token.kind == kind::OP && (text == "(" || text == "[" || text == "{") {
                depth += 1;
            } else if token.kind == kind::OP && (text == ")" || text == "]" || text == "}") {
                depth -= 1;
            } else if depth == 0 && matches!(token.kind, kind::NEWLINE | kind::DEDENT) {
                return None;
            } else if depth == 0 && text == word {
                return Some(index);
            }
            index += 1;
        }
        None
    }

    fn skip_gap(&mut self, limit: usize, newlines: bool) {
        while self.index < limit && self.index < self.tokens.len() {
            let kind_id = self.tokens[self.index].kind;
            let skip = kind_id == kind::WHITESPACE
                || kind_id == kind::COMMENT
                || (newlines && kind_id == kind::NEWLINE);
            if !skip {
                break;
            }
            self.index += 1;
        }
    }
}

fn is_aug(text: &str) -> bool {
    matches!(
        text,
        "+=" | "-="
            | "*="
            | "/="
            | "//="
            | "%="
            | "**="
            | "&="
            | "|="
            | "^="
            | "<<="
            | ">>="
            | "@="
    )
}
