//! Shared regex helpers. Offsets from `crate::pyre` are Unicode scalar indexes.

use super::Hit;
use crate::pyre::{self, Match, Regex};

pub(super) fn compile(pattern: &str) -> Regex {
    Regex::compile(pattern)
        .unwrap_or_else(|error| panic!("symbol pattern `{pattern}` failed: {}", error.message))
}

pub(super) fn match_at(regex: &Regex, text: &str) -> Option<Match> {
    pyre::find_at(regex, text, 0)
}

pub(super) fn find_all(regex: &Regex, text: &str) -> Vec<Match> {
    regex.find_iter(text)
}

pub(super) fn group(text: &str, matched: &Match, index: usize) -> String {
    group_opt(text, matched, index).unwrap_or_default()
}

pub(super) fn group_opt(text: &str, matched: &Match, index: usize) -> Option<String> {
    let (start, end) = matched.groups.get(index).copied().flatten()?;
    Some(slice_chars(text, start, end))
}

pub(super) fn slice_chars(text: &str, start: usize, end: usize) -> String {
    if start >= end {
        return String::new();
    }
    text.chars().skip(start).take(end - start).collect()
}

pub(super) fn slice_from(text: &str, start: usize) -> String {
    text.chars().skip(start).collect()
}

pub(super) fn line_number_at(content: &str, char_index: usize) -> u32 {
    content
        .chars()
        .take(char_index)
        .filter(|ch| *ch == '\n')
        .count() as u32
        + 1
}

pub(super) fn count_char(text: &str, needle: char) -> i32 {
    text.chars().filter(|ch| *ch == needle).count() as i32
}

pub(super) fn emit(
    out: &mut Vec<Hit>,
    name: impl Into<String>,
    kind: &str,
    role: &str,
    line: u32,
    container: Option<String>,
) {
    out.push(Hit {
        name: name.into(),
        kind: kind.to_string(),
        role: role.to_string(),
        line,
        container,
    });
}

/// Python `str.splitlines`: break on `\n`, `\r`, `\r\n`, and the Unicode line separators.
pub(super) fn python_lines(content: &str) -> Vec<&str> {
    if content.is_empty() {
        return Vec::new();
    }
    let mut lines = Vec::new();
    let bytes = content.as_bytes();
    let mut start = 0usize;
    let mut index = 0usize;
    while index < bytes.len() {
        let ch = content[index..].chars().next().unwrap();
        let len = ch.len_utf8();
        if ch == '\r' {
            lines.push(&content[start..index]);
            index += if bytes.get(index + 1) == Some(&b'\n') {
                2
            } else {
                1
            };
            start = index;
            continue;
        }
        if is_line_break(ch) {
            lines.push(&content[start..index]);
            index += len;
            start = index;
            continue;
        }
        index += len;
    }
    if start < content.len() {
        lines.push(&content[start..]);
    }
    lines
}

fn is_line_break(ch: char) -> bool {
    matches!(
        ch,
        '\n' | '\u{000b}'
            | '\u{000c}'
            | '\u{001c}'
            | '\u{001d}'
            | '\u{001e}'
            | '\u{0085}'
            | '\u{2028}'
            | '\u{2029}'
    )
}
