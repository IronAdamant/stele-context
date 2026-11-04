//! `ast.Call.lineno` is the first line of the call expression.

use super::Cursor;
use crate::lang::kind;

impl Cursor<'_> {
    pub(super) fn chain_start_line(&self) -> u32 {
        let Some(token) = self.tokens.get(self.index) else {
            return 1;
        };
        let mut index = self.index;
        let mut depth = 0i32;
        let mut origin = token.start;
        while index > 0 {
            index -= 1;
            let token = &self.tokens[index];
            if self.skippable(token.kind) {
                continue;
            }
            let text = self.slice(token);
            if token.kind == kind::OP && matches!(text, ")" | "]" | "}") {
                depth += 1;
                continue;
            }
            if token.kind == kind::OP && matches!(text, "(" | "[" | "{") {
                if depth == 0 {
                    break;
                }
                depth -= 1;
                origin = token.start;
                continue;
            }
            if depth > 0 {
                continue;
            }
            if token.kind == kind::OP && text == "." {
                continue;
            }
            if matches!(token.kind, kind::IDENT | kind::NUMBER | kind::STRING) {
                origin = token.start;
                if !self.real_before_is_dot(index) {
                    break;
                }
                continue;
            }
            break;
        }
        self.line_at(origin)
    }

    fn real_before_is_dot(&self, index: usize) -> bool {
        let mut cursor = index;
        while cursor > 0 {
            cursor -= 1;
            let token = &self.tokens[cursor];
            if self.skippable(token.kind) {
                continue;
            }
            return token.kind == kind::OP && self.slice(token) == ".";
        }
        false
    }

    fn skippable(&self, kind_id: u16) -> bool {
        matches!(
            kind_id,
            kind::WHITESPACE | kind::COMMENT | kind::NEWLINE | kind::INDENT | kind::DEDENT
        )
    }
}
