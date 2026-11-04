//! Code chunker. Python uses definition spans from the lexer (tree-sitter is
//! not the reference). Every other language uses the same boundary regex as
//! 1.5.2 with tree-sitter absent, then the line fallback.

use super::patterns;
use super::text::Piece;
use super::tokens::estimate_tokens;
use crate::lang::kind;
use crate::lang::python::lex::lex;
use crate::pyre::Regex;
use crate::syntax::cst::Token;

pub fn chunk(
    content: &str,
    path: &str,
    chunk_size: usize,
    _max_chunk_size: usize,
) -> (Vec<Piece>, Option<String>) {
    let extension = extension_of(path);
    if matches!(extension, "py" | "pyw" | "pyx") {
        if let Some(defs) = python_definitions(content) {
            if !defs.is_empty() {
                return (
                    boundaries(content, &defs, chunk_size),
                    Some("python".to_string()),
                );
            }
        }
        return (
            regex_or_lines(content, extension, chunk_size),
            Some(extension.to_string()),
        );
    }
    (
        regex_or_lines(content, extension, chunk_size),
        Some(extension.to_string()),
    )
}

fn regex_or_lines(content: &str, extension: &str, chunk_size: usize) -> Vec<Piece> {
    let pattern = patterns::for_extension(extension);
    let regex = Regex::compile(pattern)
        .unwrap_or_else(|error| panic!("chunk pattern for {extension} failed: {}", error.message));
    let matches = regex.find_iter(content);
    if matches.is_empty() {
        return by_lines(content, chunk_size);
    }
    let len = content.chars().count();
    let mut defs = Vec::new();
    for (index, hit) in matches.iter().enumerate() {
        let end = matches.get(index + 1).map(|next| next.start).unwrap_or(len);
        defs.push((hit.start, end));
    }
    boundaries(content, &defs, chunk_size)
}

fn boundaries(content: &str, defs: &[(usize, usize)], chunk_size: usize) -> Vec<Piece> {
    let chars: Vec<char> = content.chars().collect();
    let mut chunks = Vec::new();
    let mut parts: Vec<String> = Vec::new();
    let mut current_start = 0usize;
    let mut current_tokens = 0usize;
    let mut last_end = 0usize;
    for &(start, end) in defs {
        if start > last_end {
            let gap: String = chars[last_end..start].iter().collect();
            current_tokens += estimate_tokens(&gap);
            parts.push(gap);
        }
        let def_text: String = chars[start..end].iter().collect();
        let def_tokens = estimate_tokens(&def_text);
        if current_tokens + def_tokens > chunk_size && !parts.is_empty() {
            flush(&mut chunks, &parts, &mut current_start);
            parts.clear();
            current_tokens = 0;
        }
        parts.push(def_text);
        current_tokens += def_tokens;
        last_end = end;
    }
    if last_end < chars.len() {
        parts.push(chars[last_end..].iter().collect());
    }
    if !parts.is_empty() {
        flush(&mut chunks, &parts, &mut current_start);
    }
    if chunks.is_empty() {
        chunks.push(Piece {
            content: String::new(),
            start: 0,
            end: 0,
        });
    }
    chunks
}

fn flush(chunks: &mut Vec<Piece>, parts: &[String], current_start: &mut usize) {
    let raw: String = parts.concat();
    let content = raw.trim().to_string();
    let raw_len = raw.chars().count();
    if !content.is_empty() {
        chunks.push(Piece {
            content,
            start: *current_start,
            end: *current_start + raw_len,
        });
    }
    *current_start += raw_len;
}

fn by_lines(content: &str, chunk_size: usize) -> Vec<Piece> {
    if content.is_empty() {
        return vec![Piece {
            content: String::new(),
            start: 0,
            end: 0,
        }];
    }
    let lines = split_keep(&content.chars().collect::<Vec<_>>());
    let total = estimate_tokens(content).max(1);
    let avg = (total / lines.len().max(1)).max(1);
    let lines_per = (chunk_size / avg).max(1);
    let mut chunks = Vec::new();
    let mut offset = 0usize;
    let mut index = 0usize;
    while index < lines.len() {
        let end = (index + lines_per).min(lines.len());
        let raw: String = lines[index..end].concat();
        let raw_len = raw.chars().count();
        let body = raw.trim().to_string();
        if !body.is_empty() {
            chunks.push(Piece {
                content: body,
                start: offset,
                end: offset + raw_len,
            });
        }
        offset += raw_len;
        index = end;
    }
    if chunks.is_empty() {
        chunks.push(Piece {
            content: String::new(),
            start: 0,
            end: 0,
        });
    }
    chunks
}

fn split_keep(chars: &[char]) -> Vec<String> {
    let mut lines = Vec::new();
    let mut start = 0usize;
    for (index, ch) in chars.iter().enumerate() {
        if *ch == '\n' {
            lines.push(chars[start..=index].iter().collect());
            start = index + 1;
        }
    }
    if start < chars.len() {
        lines.push(chars[start..].iter().collect());
    }
    if lines.is_empty() {
        lines.push(String::new());
    }
    lines
}

fn python_definitions(source: &str) -> Option<Vec<(usize, usize)>> {
    let tokens = lex(source);
    if !brackets_balanced(&tokens, source) {
        return None;
    }
    let mut spans = Vec::new();
    let mut index = 0usize;
    let mut depth = 0i32;
    while index < tokens.len() {
        match tokens[index].kind {
            kind::INDENT => {
                depth += 1;
                index += 1;
                continue;
            }
            kind::DEDENT => {
                depth -= 1;
                index += 1;
                continue;
            }
            _ => {}
        }
        if depth == 0 && header_at(source, &tokens, index) {
            let (end_byte, next) = consume_header(source, &tokens, index);
            let start_byte = tokens[index].start as usize;
            spans.push((char_at(source, start_byte), char_at(source, end_byte)));
            index = next;
            continue;
        }
        index += 1;
    }
    Some(spans)
}

fn header_at(source: &str, tokens: &[Token], index: usize) -> bool {
    if !statement_start(tokens, index) {
        return false;
    }
    let word = token_str(source, &tokens[index]);
    if word == "def" || word == "class" {
        return true;
    }
    if word != "async" {
        return false;
    }
    let mut cursor = index + 1;
    while cursor < tokens.len()
        && matches!(
            tokens[cursor].kind,
            kind::WHITESPACE | kind::COMMENT | kind::NEWLINE
        )
    {
        if tokens[cursor].kind == kind::NEWLINE {
            return false;
        }
        cursor += 1;
    }
    cursor < tokens.len() && token_str(source, &tokens[cursor]) == "def"
}

fn statement_start(tokens: &[Token], index: usize) -> bool {
    let mut cursor = index;
    while cursor > 0 {
        cursor -= 1;
        match tokens[cursor].kind {
            kind::WHITESPACE | kind::COMMENT | kind::INDENT | kind::DEDENT => continue,
            kind::NEWLINE => return true,
            _ => return false,
        }
    }
    true
}

fn consume_header(source: &str, tokens: &[Token], start: usize) -> (usize, usize) {
    let mut index = start;
    let mut paren = 0i32;
    let mut past_header = false;
    let mut depth = 0i32;
    let mut end_byte = tokens[start].end as usize;
    while index < tokens.len() {
        let token = &tokens[index];
        if token.kind == kind::OP {
            match source.as_bytes().get(token.start as usize).copied() {
                Some(b'(' | b'[' | b'{') => paren += 1,
                Some(b')' | b']' | b'}') => paren -= 1,
                _ => {}
            }
        }
        if token.kind == kind::INDENT {
            depth += 1;
        }
        if token.kind == kind::DEDENT {
            depth -= 1;
            if past_header && depth <= 0 {
                // Swallow the dedent. The outer scan's depth does not include
                // this suite, so leaving the token behind would drop depth
                // below zero and hide every later top-level definition.
                return (end_byte, index + 1);
            }
        }
        if matches!(token.kind, kind::INDENT | kind::DEDENT) {
            index += 1;
            continue;
        }
        if token.end as usize > end_byte && !matches!(token.kind, kind::WHITESPACE | kind::COMMENT)
        {
            end_byte = token.end as usize;
        }
        if token.kind == kind::NEWLINE && paren == 0 {
            end_byte = token.end as usize;
            if !past_header {
                past_header = true;
                if !follows_indent(tokens, index + 1) {
                    return (end_byte, index + 1);
                }
            } else if depth == 0 {
                return (end_byte, index + 1);
            }
        }
        index += 1;
    }
    (end_byte, index)
}

fn follows_indent(tokens: &[Token], mut index: usize) -> bool {
    while index < tokens.len() && matches!(tokens[index].kind, kind::WHITESPACE | kind::COMMENT) {
        index += 1;
    }
    index < tokens.len() && tokens[index].kind == kind::INDENT
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

fn token_str<'a>(source: &'a str, token: &Token) -> &'a str {
    let start = token.start as usize;
    let end = token.end as usize;
    if start <= end
        && end <= source.len()
        && source.is_char_boundary(start)
        && source.is_char_boundary(end)
    {
        &source[start..end]
    } else {
        ""
    }
}

fn char_at(source: &str, byte: usize) -> usize {
    let byte = byte.min(source.len());
    let byte = (0..=byte)
        .rev()
        .find(|at| source.is_char_boundary(*at))
        .unwrap_or(0);
    source[..byte].chars().count()
}

pub fn extension_of(path: &str) -> &str {
    path.rsplit(['/', '\\'])
        .next()
        .and_then(|name| name.rsplit_once('.'))
        .map(|(_, ext)| ext)
        .unwrap_or("")
}
