//! Python statement parser. The lexer lives in `lex`. Every token is attached,
//! so rendering the tree reproduces the source.

pub(crate) mod lex;

use crate::lang::kind;
use crate::syntax::cst::{Builder, Token, Tree};
use crate::syntax::query::SymbolHit;

pub fn parse(source: &str) -> (Tree, Vec<SymbolHit>) {
    let tokens = lex::lex(source);
    let mut parser = Parser {
        source,
        tokens: &tokens,
        index: 0,
        builder: Builder::new(),
    };
    parser.builder.start(kind::FILE, 0);
    parser.statements(0);
    parser.drain();
    parser.builder.finish(source.len() as u32);
    let symbols = super::python_symbols::extract(source);
    (parser.builder.tree(), symbols)
}

struct Parser<'a> {
    source: &'a str,
    tokens: &'a [Token],
    index: usize,
    builder: Builder,
}

impl<'a> Parser<'a> {
    fn statements(&mut self, _indent: u32) {
        while self.index < self.tokens.len() {
            if self.tokens[self.index].kind == kind::DEDENT {
                self.bump();
                break;
            }
            self.statement();
        }
    }

    fn statement(&mut self) {
        while self.index < self.tokens.len()
            && matches!(
                self.tokens[self.index].kind,
                kind::NEWLINE | kind::WHITESPACE | kind::COMMENT | kind::INDENT
            )
        {
            self.bump();
        }
        if self.index >= self.tokens.len() || self.tokens[self.index].kind == kind::DEDENT {
            return;
        }
        if self.looks_like_def() {
            self.compound();
            return;
        }
        let start = self.tokens[self.index].start;
        self.builder.start(kind::SUITE, start);
        while self.index < self.tokens.len()
            && self.tokens[self.index].kind != kind::NEWLINE
            && self.tokens[self.index].kind != kind::DEDENT
        {
            self.bump();
        }
        if self.index < self.tokens.len() && self.tokens[self.index].kind == kind::NEWLINE {
            self.bump();
        }
        let end = self.prev_end(start);
        self.builder.finish(end);
    }

    fn looks_like_def(&self) -> bool {
        let mut index = self.index;
        while index < self.tokens.len()
            && matches!(
                self.tokens[index].kind,
                kind::WHITESPACE | kind::COMMENT | kind::NEWLINE | kind::INDENT
            )
        {
            index += 1;
        }
        self.is_ident(index, "def")
            || self.is_ident(index, "class")
            || self.is_ident(index, "async")
            || self.is_op(index, "@")
    }

    fn compound(&mut self) {
        let start = self.tokens[self.index].start;
        let name_slot = self.upcoming_name();
        let is_class = name_slot.as_deref() == Some("class") || self.header_is_class();
        let kind_id = if is_class { kind::CLASS } else { kind::FUNC };
        self.builder.start(kind_id, start);
        // Consume through the header newline, then a suite if an INDENT follows.
        while self.index < self.tokens.len() && self.tokens[self.index].kind != kind::NEWLINE {
            self.bump();
        }
        if self.index < self.tokens.len() && self.tokens[self.index].kind == kind::NEWLINE {
            self.bump();
        }
        while self.index < self.tokens.len()
            && matches!(
                self.tokens[self.index].kind,
                kind::WHITESPACE | kind::COMMENT
            )
        {
            self.bump();
        }
        if self.index < self.tokens.len() && self.tokens[self.index].kind == kind::INDENT {
            self.bump();
            self.statements(0);
        }
        let end = self.prev_end(start);
        self.builder.finish(end);
        let _ = name_slot;
    }

    fn header_is_class(&self) -> bool {
        let mut index = self.index;
        while index < self.tokens.len() && self.tokens[index].kind != kind::NEWLINE {
            if self.is_ident(index, "class") {
                return true;
            }
            if self.is_ident(index, "def") {
                return false;
            }
            index += 1;
        }
        false
    }

    fn upcoming_name(&self) -> Option<String> {
        None
    }

    fn drain(&mut self) {
        while self.index < self.tokens.len() {
            self.bump();
        }
    }

    fn bump(&mut self) {
        if self.index >= self.tokens.len() {
            return;
        }
        let token = &self.tokens[self.index];
        self.builder.leaf(token.kind, token.start, token.end);
        self.index += 1;
    }

    fn prev_end(&self, fallback: u32) -> u32 {
        self.tokens
            .get(self.index.saturating_sub(1))
            .map(|token| token.end)
            .unwrap_or(fallback)
    }

    fn is_ident(&self, index: usize, text: &str) -> bool {
        let Some(token) = self.tokens.get(index) else {
            return false;
        };
        token.kind == kind::IDENT && self.slice(token) == text
    }

    fn is_op(&self, index: usize, text: &str) -> bool {
        let Some(token) = self.tokens.get(index) else {
            return false;
        };
        token.kind == kind::OP && self.slice(token) == text
    }

    fn slice(&self, token: &Token) -> &str {
        let start = token.start as usize;
        let end = token.end as usize;
        if end <= self.source.len() && start <= end {
            &self.source[start..end]
        } else {
            ""
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn covers(source: &str) -> bool {
        let tokens = lex::lex(source);
        let mut cursor = 0u32;
        for token in &tokens {
            if token.start != cursor {
                return false;
            }
            cursor = token.end;
        }
        cursor == source.len() as u32
    }

    #[test]
    fn corpus_python_round_trips() {
        let root = std::path::Path::new("parity/.cache");
        if !root.exists() {
            return;
        }
        let mut files = 0usize;
        let mut stack = vec![root.to_path_buf()];
        while let Some(dir) = stack.pop() {
            for entry in std::fs::read_dir(&dir).unwrap() {
                let entry = entry.unwrap();
                let path = entry.path();
                if path.is_dir() {
                    stack.push(path);
                    continue;
                }
                if path.extension().and_then(|ext| ext.to_str()) != Some("py") {
                    continue;
                }
                let bytes = std::fs::read(&path).unwrap();
                let source = String::from_utf8_lossy(&bytes).into_owned();
                assert!(covers(&source), "lex gap in {}", path.display());
                let (tree, _) = parse(&source);
                assert_eq!(tree.render(&source), source, "render {}", path.display());
                files += 1;
            }
        }
        assert!(
            files > 200,
            "expected the rich and flask trees, saw {files}"
        );
    }

    #[test]
    fn fixtures_and_edges_cover_every_byte() {
        let samples = [
            "def cafe():\n    return f\"héllo {1}\"\n",
            "x = (\n  1)\n",
            "r\"\\\"\"\n",
            "def broken(\n    return ok(\n",
            "",
            "# only\n",
            "async def fetch(url: str) -> str:\n    return f\"hello {url}\"\n",
        ];
        for sample in samples {
            assert!(covers(sample), "{sample:?}");
            let (tree, _) = parse(sample);
            assert_eq!(tree.render(sample), sample);
        }
    }
}
