//! Lossless scanner for languages other than Python and JSON.
//!
//! Every byte is a leaf, so rendering the tree reproduces the source. Strings,
//! comments, and identifiers are their own tokens. A declaration line at brace
//! depth zero becomes a FUNC or CLASS node so an interior edit can reuse the
//! surrounding declarations.

use crate::lang::kind;
use crate::syntax::cst::{Builder, Tree};

pub fn parse(source: &str) -> Tree {
    let bytes = source.as_bytes();
    let mut builder = Builder::new();
    builder.start(kind::FILE, 0);
    let mut index = 0usize;
    let mut depth = 0i32;
    while index < bytes.len() {
        if depth == 0 && looks_like_decl(bytes, index) {
            let end = declaration_end(bytes, index);
            let word = decl_word(bytes, index);
            let node = if matches!(
                word,
                "class"
                    | "struct"
                    | "enum"
                    | "interface"
                    | "trait"
                    | "impl"
                    | "module"
                    | "protocol"
                    | "actor"
                    | "extension"
            ) {
                kind::CLASS
            } else {
                kind::FUNC
            };
            builder.start(node, index as u32);
            scan_range(&mut builder, bytes, index, end, &mut depth);
            builder.finish(end as u32);
            index = end;
            continue;
        }
        let end = token_end(bytes, index);
        let kind_id = token_kind(bytes, index, end);
        if bytes[index] == b'{' {
            depth += 1;
        } else if bytes[index] == b'}' {
            depth -= 1;
        }
        builder.leaf(kind_id, index as u32, end as u32);
        index = end;
    }
    builder.finish(source.len() as u32);
    builder.tree()
}

fn scan_range(builder: &mut Builder, bytes: &[u8], mut index: usize, end: usize, depth: &mut i32) {
    while index < end {
        let next = token_end(bytes, index).min(end);
        if bytes[index] == b'{' {
            *depth += 1;
        } else if bytes[index] == b'}' {
            *depth -= 1;
        }
        builder.leaf(token_kind(bytes, index, next), index as u32, next as u32);
        index = next;
    }
}

fn looks_like_decl(bytes: &[u8], index: usize) -> bool {
    if index > 0 && bytes[index - 1] != b'\n' {
        return false;
    }
    let word = decl_word(bytes, index);
    matches!(
        word,
        "def"
            | "class"
            | "fn"
            | "func"
            | "function"
            | "struct"
            | "enum"
            | "trait"
            | "impl"
            | "interface"
            | "module"
            | "protocol"
            | "actor"
            | "extension"
            | "type"
    )
}

fn decl_word(bytes: &[u8], mut index: usize) -> &str {
    while index < bytes.len() && matches!(bytes[index], b' ' | b'\t') {
        index += 1;
    }
    // Skip a run of modifiers such as pub, export, public, static, async.
    loop {
        let start = index;
        while index < bytes.len() && is_ident(bytes[index]) {
            index += 1;
        }
        if start == index {
            return "";
        }
        let word = std::str::from_utf8(&bytes[start..index]).unwrap_or("");
        if is_modifier(word) {
            while index < bytes.len() && matches!(bytes[index], b' ' | b'\t') {
                index += 1;
            }
            continue;
        }
        return word;
    }
}

fn is_modifier(word: &str) -> bool {
    matches!(
        word,
        "pub"
            | "export"
            | "public"
            | "private"
            | "protected"
            | "internal"
            | "static"
            | "async"
            | "final"
            | "open"
            | "override"
            | "abstract"
            | "sealed"
            | "partial"
            | "unsafe"
            | "extern"
            | "crate"
            | "default"
    )
}

fn declaration_end(bytes: &[u8], index: usize) -> usize {
    let mut cursor = index;
    let mut depth = 0i32;
    let mut started = false;
    while cursor < bytes.len() {
        if bytes[cursor] == b'"' || bytes[cursor] == b'\'' {
            cursor = string_end(bytes, cursor);
            continue;
        }
        if bytes[cursor] == b'{' {
            depth += 1;
            started = true;
        } else if bytes[cursor] == b'}' {
            depth -= 1;
            if started && depth <= 0 {
                return cursor + 1;
            }
        } else if bytes[cursor] == b'\n' && depth == 0 && started {
            return cursor + 1;
        }
        cursor += 1;
        if !started && cursor > index && bytes[cursor - 1] == b'\n' {
            return cursor;
        }
    }
    bytes.len()
}

fn token_end(bytes: &[u8], index: usize) -> usize {
    let byte = bytes[index];
    if byte == b'/' && bytes.get(index + 1) == Some(&b'/') {
        return line_end(bytes, index);
    }
    if byte == b'#' {
        return line_end(bytes, index);
    }
    if byte == b'/' && bytes.get(index + 1) == Some(&b'*') {
        let mut cursor = index + 2;
        while cursor + 1 < bytes.len() && !(bytes[cursor] == b'*' && bytes[cursor + 1] == b'/') {
            cursor += 1;
        }
        return (cursor + 2).min(bytes.len());
    }
    if byte == b'"' || byte == b'\'' {
        return string_end(bytes, index);
    }
    if byte.is_ascii_whitespace() {
        let mut cursor = index + 1;
        while cursor < bytes.len() && bytes[cursor].is_ascii_whitespace() && bytes[cursor] != b'\n'
        {
            cursor += 1;
        }
        if byte == b'\n' {
            return index + 1;
        }
        return cursor;
    }
    if is_ident(byte) {
        let mut cursor = index + 1;
        while cursor < bytes.len() && is_ident(bytes[cursor]) {
            cursor += 1;
        }
        return cursor;
    }
    char_end(bytes, index)
}

fn token_kind(bytes: &[u8], start: usize, end: usize) -> u16 {
    let byte = bytes[start];
    if byte == b'#' || (byte == b'/' && end > start + 1) {
        kind::COMMENT
    } else if byte == b'"' || byte == b'\'' {
        kind::STRING
    } else if byte.is_ascii_whitespace() {
        if byte == b'\n' {
            kind::NEWLINE
        } else {
            kind::WHITESPACE
        }
    } else if is_ident(byte) {
        kind::IDENT
    } else {
        kind::OP
    }
}

fn string_end(bytes: &[u8], index: usize) -> usize {
    let quote = bytes[index];
    let mut cursor = index + 1;
    while cursor < bytes.len() {
        if bytes[cursor] == b'\\' {
            cursor = (cursor + 2).min(bytes.len());
            continue;
        }
        if bytes[cursor] == quote || bytes[cursor] == b'\n' {
            return (cursor + 1).min(bytes.len());
        }
        cursor += 1;
    }
    bytes.len()
}

fn line_end(bytes: &[u8], index: usize) -> usize {
    bytes
        .iter()
        .skip(index)
        .position(|byte| *byte == b'\n')
        .map(|pos| index + pos + 1)
        .unwrap_or(bytes.len())
}

fn char_end(bytes: &[u8], index: usize) -> usize {
    let width = match bytes[index] {
        0x00..=0x7F => 1,
        0xC0..=0xDF => 2,
        0xE0..=0xEF => 3,
        0xF0..=0xF7 => 4,
        _ => 1,
    };
    (index + width).min(bytes.len())
}

fn is_ident(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || byte == b'_'
}
