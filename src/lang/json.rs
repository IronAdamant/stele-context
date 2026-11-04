//! JSON grammar. Whitespace is kept. A broken value becomes an ERROR node
//! and parsing continues at the next structural character.

use crate::lang::kind;
use crate::syntax::cst::{Builder, Token, Tree};

pub fn parse(source: &str) -> Tree {
    let tokens = lex(source);
    let mut parser = Parser {
        source,
        tokens: &tokens,
        index: 0,
        builder: Builder::new(),
    };
    parser.builder.start(kind::FILE, 0);
    parser.value();
    parser.drain();
    let end = source.len() as u32;
    parser.builder.finish(end);
    parser.builder.tree()
}

fn lex(source: &str) -> Vec<Token> {
    let bytes = source.as_bytes();
    let mut index = 0;
    let mut tokens = Vec::new();
    while index < bytes.len() {
        let start = index;
        match bytes[index] {
            b' ' | b'\t' | b'\n' | b'\r' => {
                index += 1;
                while index < bytes.len() && matches!(bytes[index], b' ' | b'\t' | b'\n' | b'\r') {
                    index += 1;
                }
                tokens.push(Token {
                    kind: kind::WHITESPACE,
                    start: start as u32,
                    end: index as u32,
                });
            }
            b'"' => {
                index = scan_string(bytes, index);
                tokens.push(Token {
                    kind: kind::STRING,
                    start: start as u32,
                    end: index as u32,
                });
            }
            b'-' | b'0'..=b'9' => {
                index = scan_number(bytes, index);
                tokens.push(Token {
                    kind: kind::NUMBER,
                    start: start as u32,
                    end: index as u32,
                });
            }
            b'{' | b'}' | b'[' | b']' | b':' | b',' => {
                index += 1;
                tokens.push(Token {
                    kind: kind::OP,
                    start: start as u32,
                    end: index as u32,
                });
            }
            b't' | b'f' | b'n' => {
                let word = if bytes[index..].starts_with(b"true") {
                    4
                } else if bytes[index..].starts_with(b"false") {
                    5
                } else if bytes[index..].starts_with(b"null") {
                    4
                } else {
                    1
                };
                index += word;
                tokens.push(Token {
                    kind: kind::IDENT,
                    start: start as u32,
                    end: index as u32,
                });
            }
            _ => {
                index += 1;
                tokens.push(Token {
                    kind: kind::ERROR,
                    start: start as u32,
                    end: index as u32,
                });
            }
        }
    }
    tokens
}

fn scan_string(bytes: &[u8], mut index: usize) -> usize {
    index += 1;
    while index < bytes.len() {
        match bytes[index] {
            b'\\' => index = (index + 2).min(bytes.len()),
            b'"' => return index + 1,
            _ => index += 1,
        }
    }
    bytes.len()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixtures_and_corpus_round_trip() {
        for sample in ["{}\n", "[\n  1,\n  true\n]\n", "{ \"a\"", ""] {
            assert_eq!(parse(sample).render(sample), sample, "{sample:?}");
        }
        let root = std::path::Path::new("parity/.cache");
        if !root.exists() {
            return;
        }
        let mut stack = vec![root.to_path_buf()];
        let mut files = 0usize;
        while let Some(dir) = stack.pop() {
            for entry in std::fs::read_dir(&dir).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    stack.push(path);
                    continue;
                }
                if path.extension().and_then(|ext| ext.to_str()) != Some("json") {
                    continue;
                }
                let bytes = std::fs::read(&path).unwrap();
                let source = String::from_utf8_lossy(&bytes).into_owned();
                assert_eq!(parse(&source).render(&source), source, "{}", path.display());
                files += 1;
            }
        }
        assert!(files > 20, "saw {files} json files");
    }
}

fn scan_number(bytes: &[u8], mut index: usize) -> usize {
    if bytes[index] == b'-' {
        index += 1;
    }
    while index < bytes.len() && bytes[index].is_ascii_digit() {
        index += 1;
    }
    if index < bytes.len() && bytes[index] == b'.' {
        index += 1;
        while index < bytes.len() && bytes[index].is_ascii_digit() {
            index += 1;
        }
    }
    if index < bytes.len() && matches!(bytes[index], b'e' | b'E') {
        index += 1;
        if index < bytes.len() && matches!(bytes[index], b'+' | b'-') {
            index += 1;
        }
        while index < bytes.len() && bytes[index].is_ascii_digit() {
            index += 1;
        }
    }
    index
}

struct Parser<'a> {
    source: &'a str,
    tokens: &'a [Token],
    index: usize,
    builder: Builder,
}

impl<'a> Parser<'a> {
    fn value(&mut self) {
        self.skip_ws();
        let Some(token) = self.tokens.get(self.index) else {
            return;
        };
        let text_kind = token.kind;
        if text_kind == kind::OP && self.byte_at(token) == Some(b'{') {
            self.object();
        } else if text_kind == kind::OP && self.byte_at(token) == Some(b'[') {
            self.array();
        } else if matches!(text_kind, kind::STRING | kind::NUMBER | kind::IDENT) {
            self.bump();
        } else {
            self.error_one();
        }
    }

    fn object(&mut self) {
        let start = self.tokens[self.index].start;
        self.builder.start(kind::JSON_OBJECT, start);
        self.bump();
        loop {
            self.skip_ws();
            if self.at_byte(b'}') || self.index >= self.tokens.len() {
                break;
            }
            self.bump();
            self.skip_ws();
            if self.at_byte(b':') {
                self.bump();
            }
            self.value();
            self.skip_ws();
            if self.at_byte(b',') {
                self.bump();
                continue;
            }
            break;
        }
        self.skip_ws();
        if self.at_byte(b'}') {
            self.bump();
        }
        let end = self.prev_end(start);
        self.builder.finish(end);
    }

    fn array(&mut self) {
        let start = self.tokens[self.index].start;
        self.builder.start(kind::JSON_ARRAY, start);
        self.bump();
        loop {
            self.skip_ws();
            if self.at_byte(b']') || self.index >= self.tokens.len() {
                break;
            }
            self.value();
            self.skip_ws();
            if self.at_byte(b',') {
                self.bump();
                continue;
            }
            break;
        }
        if self.at_byte(b']') {
            self.bump();
        }
        let end = self.prev_end(start);
        self.builder.finish(end);
    }

    fn error_one(&mut self) {
        if self.index >= self.tokens.len() {
            return;
        }
        let start = self.tokens[self.index].start;
        self.builder.start(kind::ERROR, start);
        self.bump();
        let end = self.prev_end(start);
        self.builder.finish(end);
    }

    fn drain(&mut self) {
        while self.index < self.tokens.len() {
            self.bump();
        }
    }

    fn skip_ws(&mut self) {
        while self.index < self.tokens.len() && self.tokens[self.index].kind == kind::WHITESPACE {
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

    fn at_byte(&self, byte: u8) -> bool {
        self.tokens
            .get(self.index)
            .and_then(|token| self.byte_at(token))
            == Some(byte)
    }

    fn byte_at(&self, token: &Token) -> Option<u8> {
        self.source.as_bytes().get(token.start as usize).copied()
    }

    fn prev_end(&self, fallback: u32) -> u32 {
        self.tokens
            .get(self.index.saturating_sub(1))
            .map(|token| token.end)
            .unwrap_or(fallback)
    }
}
