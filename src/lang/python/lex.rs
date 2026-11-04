//! Python lexer. Tokens cover every byte, including whitespace and comments.

use crate::lang::kind;
use crate::syntax::cst::Token;

pub(crate) fn lex(source: &str) -> Vec<Token> {
    let bytes = source.as_bytes();
    let mut index = 0;
    let mut tokens = Vec::new();
    let mut indents: Vec<u32> = vec![0];
    let mut brackets = 0i32;
    let mut at_line = true;
    while index < bytes.len() {
        if at_line && brackets == 0 {
            let start = index;
            let mut column = 0u32;
            while index < bytes.len() && matches!(bytes[index], b' ' | b'\t' | b'\x0c') {
                column = if bytes[index] == b'\t' {
                    (column / 8 + 1) * 8
                } else {
                    column + 1
                };
                index += 1;
            }
            let blank = index >= bytes.len() || matches!(bytes[index], b'\n' | b'\r' | b'#');
            if index > start {
                push(&mut tokens, kind::WHITESPACE, start, index);
            }
            if !blank {
                emit_indent(&mut tokens, &mut indents, column, index);
            }
            at_line = false;
            if index >= bytes.len() {
                break;
            }
        }
        let start = index;
        let byte = bytes[index];
        if byte == b'\\' && matches!(bytes.get(index + 1), Some(b'\n' | b'\r')) {
            index += 1;
            if bytes.get(index) == Some(&b'\r') {
                index += 1;
            }
            if bytes.get(index) == Some(&b'\n') {
                index += 1;
            }
            push(&mut tokens, kind::WHITESPACE, start, index);
            continue;
        }
        if byte == b'#' {
            while index < bytes.len() && !matches!(bytes[index], b'\n' | b'\r') {
                index += 1;
            }
            push(&mut tokens, kind::COMMENT, start, index);
            continue;
        }
        if matches!(byte, b'\n' | b'\r') {
            if byte == b'\r' {
                index += 1;
            }
            if bytes.get(index) == Some(&b'\n') || (byte == b'\n') {
                if byte == b'\n' || bytes.get(index) == Some(&b'\n') {
                    if byte != b'\n' {
                        // already consumed \r
                    }
                    if bytes.get(index) == Some(&b'\n') {
                        index += 1;
                    } else if byte == b'\n' {
                        index += 1;
                    }
                }
            }
            if index == start {
                index += 1;
            }
            push(&mut tokens, kind::NEWLINE, start, index);
            if brackets == 0 {
                at_line = true;
            }
            continue;
        }
        if byte.is_ascii_whitespace() {
            index += 1;
            while index < bytes.len()
                && bytes[index].is_ascii_whitespace()
                && bytes[index] != b'\n'
                && bytes[index] != b'\r'
            {
                index += 1;
            }
            push(&mut tokens, kind::WHITESPACE, start, index);
            continue;
        }
        if let Some(end) = try_string(bytes, index) {
            // try_string pushes the pieces itself when it is an f-string.
            // For a plain string it returns the end and we emit one token.
            if end > index && !is_fstring_prefix(bytes, index) {
                push(&mut tokens, kind::STRING, start, end);
                index = end;
                continue;
            }
        }
        if is_fstring_prefix(bytes, index) {
            index = lex_fstring(bytes, index, &mut tokens);
            continue;
        }
        if byte.is_ascii_digit()
            || (byte == b'.' && bytes.get(index + 1).is_some_and(|b| b.is_ascii_digit()))
        {
            index = scan_number(bytes, index);
            push(&mut tokens, kind::NUMBER, start, index);
            continue;
        }
        if byte == b'_' || byte.is_ascii_alphabetic() || byte >= 0x80 {
            index += 1;
            while index < bytes.len()
                && (bytes[index] == b'_'
                    || bytes[index].is_ascii_alphanumeric()
                    || bytes[index] >= 0x80)
            {
                index += 1;
            }
            push(&mut tokens, kind::IDENT, start, index);
            continue;
        }
        index = scan_operator(bytes, index);
        let kind = match bytes[start] {
            b'(' | b'[' | b'{' => {
                brackets += 1;
                kind::OP
            }
            b')' | b']' | b'}' => {
                brackets -= 1;
                kind::OP
            }
            _ => kind::OP,
        };
        push(&mut tokens, kind, start, index);
    }
    while indents.len() > 1 {
        indents.pop();
        push(&mut tokens, kind::DEDENT, index, index);
    }
    tokens
}

fn push(tokens: &mut Vec<Token>, kind_id: u16, start: usize, end: usize) {
    tokens.push(Token {
        kind: kind_id,
        start: start as u32,
        end: end as u32,
    });
}

fn emit_indent(tokens: &mut Vec<Token>, indents: &mut Vec<u32>, column: u32, at: usize) {
    let current = *indents.last().unwrap();
    if column > current {
        indents.push(column);
        push(tokens, kind::INDENT, at, at);
    } else {
        while indents.len() > 1 && column < *indents.last().unwrap() {
            indents.pop();
            push(tokens, kind::DEDENT, at, at);
        }
    }
}

fn is_fstring_prefix(bytes: &[u8], index: usize) -> bool {
    let Some(quote_at) = prefix_quote(bytes, index) else {
        return false;
    };
    let prefix = &bytes[index..quote_at];
    prefix.iter().any(|byte| matches!(byte, b'f' | b'F'))
}

fn try_string(bytes: &[u8], index: usize) -> Option<usize> {
    let quote_at = prefix_quote(bytes, index)?;
    if is_fstring_prefix(bytes, index) {
        return None;
    }
    Some(scan_quoted(bytes, quote_at, false).0)
}

fn prefix_quote(bytes: &[u8], index: usize) -> Option<usize> {
    let mut cursor = index;
    while cursor < bytes.len()
        && cursor - index < 3
        && matches!(
            bytes[cursor],
            b'r' | b'R' | b'b' | b'B' | b'u' | b'U' | b'f' | b'F'
        )
    {
        cursor += 1;
        if matches!(bytes.get(cursor), Some(b'\'' | b'"')) {
            return Some(cursor);
        }
    }
    if matches!(bytes.get(index), Some(b'\'' | b'"')) {
        Some(index)
    } else {
        None
    }
}

/// Returns (end, bytes consumed as one plain string). F-strings are handled
/// by `lex_fstring`.
fn scan_quoted(bytes: &[u8], quote_at: usize, _raw: bool) -> (usize, bool) {
    let quote = bytes[quote_at];
    let triple = bytes.get(quote_at..quote_at + 3) == Some(&[quote, quote, quote]);
    let mut index = quote_at + if triple { 3 } else { 1 };
    while index < bytes.len() {
        if bytes[index] == b'\\' {
            index = (index + 2).min(bytes.len());
            continue;
        }
        if triple && bytes.get(index..index + 3) == Some(&[quote, quote, quote]) {
            return (index + 3, true);
        }
        if !triple && bytes[index] == quote {
            return (index + 1, true);
        }
        if !triple && matches!(bytes[index], b'\n' | b'\r') {
            return (index, false);
        }
        index += 1;
    }
    (bytes.len(), false)
}

fn lex_fstring(bytes: &[u8], index: usize, tokens: &mut Vec<Token>) -> usize {
    let quote_at = prefix_quote(bytes, index).unwrap_or(index);
    let quote = bytes[quote_at];
    let triple = bytes.get(quote_at..quote_at + 3) == Some(&[quote, quote, quote]);
    let mut cursor = quote_at + if triple { 3 } else { 1 };
    let mut chunk = index;
    while cursor < bytes.len() {
        if bytes[cursor] == b'\\' {
            cursor = (cursor + 2).min(bytes.len());
            continue;
        }
        if bytes[cursor] == b'{' && bytes.get(cursor + 1) == Some(&b'{') {
            cursor += 2;
            continue;
        }
        if bytes[cursor] == b'}' && bytes.get(cursor + 1) == Some(&b'}') {
            cursor += 2;
            continue;
        }
        if bytes[cursor] == b'{' {
            if cursor > chunk {
                push(tokens, kind::STRING, chunk, cursor);
            }
            push(tokens, kind::OP, cursor, cursor + 1);
            cursor += 1;
            cursor = lex_fexpr(bytes, cursor, tokens);
            chunk = cursor;
            continue;
        }
        if triple && bytes.get(cursor..cursor + 3) == Some(&[quote, quote, quote]) {
            cursor += 3;
            push(tokens, kind::STRING, chunk, cursor);
            return cursor;
        }
        if !triple && bytes[cursor] == quote {
            cursor += 1;
            push(tokens, kind::STRING, chunk, cursor);
            return cursor;
        }
        if !triple && matches!(bytes[cursor], b'\n' | b'\r') {
            push(tokens, kind::STRING, chunk, cursor);
            return cursor;
        }
        cursor += 1;
    }
    if chunk < bytes.len() {
        push(tokens, kind::STRING, chunk, bytes.len());
    }
    bytes.len()
}

fn lex_fexpr(bytes: &[u8], mut index: usize, tokens: &mut Vec<Token>) -> usize {
    let mut depth = 0i32;
    while index < bytes.len() {
        if bytes[index] == b'{' {
            depth += 1;
            push(tokens, kind::OP, index, index + 1);
            index += 1;
            continue;
        }
        if bytes[index] == b'}' && depth == 0 {
            push(tokens, kind::OP, index, index + 1);
            return index + 1;
        }
        if bytes[index] == b'}' {
            depth -= 1;
            push(tokens, kind::OP, index, index + 1);
            index += 1;
            continue;
        }
        if prefix_quote(bytes, index).is_some() && !is_fstring_prefix(bytes, index) {
            let end = try_string(bytes, index).unwrap_or(index + 1);
            push(tokens, kind::STRING, index, end);
            index = end;
            continue;
        }
        if is_fstring_prefix(bytes, index) {
            index = lex_fstring(bytes, index, tokens);
            continue;
        }
        let start = index;
        let byte = bytes[index];
        if byte.is_ascii_whitespace() {
            index += 1;
            push(tokens, kind::WHITESPACE, start, index);
            continue;
        }
        if byte == b'_' || byte.is_ascii_alphabetic() || byte >= 0x80 {
            index += 1;
            while index < bytes.len()
                && (bytes[index] == b'_'
                    || bytes[index].is_ascii_alphanumeric()
                    || bytes[index] >= 0x80)
            {
                index += 1;
            }
            push(tokens, kind::IDENT, start, index);
            continue;
        }
        if byte.is_ascii_digit() {
            index = scan_number(bytes, index);
            push(tokens, kind::NUMBER, start, index);
            continue;
        }
        index = scan_operator(bytes, index);
        if matches!(bytes[start], b'(' | b'[' | b'{') {
            // depth already handled for '{'
        }
        push(tokens, kind::OP, start, index);
        if matches!(bytes[start], b'(' | b'[') {
            // nested parens are operators; braces are handled above
        }
    }
    index
}

fn scan_number(bytes: &[u8], mut index: usize) -> usize {
    if bytes
        .get(index + 1)
        .is_some_and(|byte| matches!(byte, b'x' | b'X' | b'o' | b'O' | b'b' | b'B'))
        && bytes[index] == b'0'
    {
        index += 2;
        while index < bytes.len() && (bytes[index].is_ascii_hexdigit() || bytes[index] == b'_') {
            index += 1;
        }
        return index;
    }
    while index < bytes.len() && (bytes[index].is_ascii_digit() || bytes[index] == b'_') {
        index += 1;
    }
    if bytes.get(index) == Some(&b'.') {
        index += 1;
        while index < bytes.len() && (bytes[index].is_ascii_digit() || bytes[index] == b'_') {
            index += 1;
        }
    }
    if matches!(bytes.get(index), Some(b'e' | b'E')) {
        index += 1;
        if matches!(bytes.get(index), Some(b'+' | b'-')) {
            index += 1;
        }
        while index < bytes.len() && (bytes[index].is_ascii_digit() || bytes[index] == b'_') {
            index += 1;
        }
    }
    if matches!(bytes.get(index), Some(b'j' | b'J')) {
        index += 1;
    }
    index
}

fn scan_operator(bytes: &[u8], index: usize) -> usize {
    let rest = &bytes[index..];
    for op in [
        b"**=".as_slice(),
        b"//=".as_slice(),
        b">>=".as_slice(),
        b"<<=".as_slice(),
        b"...".as_slice(),
        b"==".as_slice(),
        b"!=".as_slice(),
        b"<=".as_slice(),
        b">=".as_slice(),
        b"->".as_slice(),
        b"+=".as_slice(),
        b"-=".as_slice(),
        b"*=".as_slice(),
        b"/=".as_slice(),
        b"%=".as_slice(),
        b"&=".as_slice(),
        b"|=".as_slice(),
        b"^=".as_slice(),
        b":=".as_slice(),
        b"//".as_slice(),
        b"<<".as_slice(),
        b">>".as_slice(),
        b"**".as_slice(),
    ] {
        if rest.starts_with(op) {
            return index + op.len();
        }
    }
    index + 1
}
