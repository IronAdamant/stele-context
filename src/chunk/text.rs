//! Paragraph chunker. Offsets count Unicode scalars, and `end_pos` covers the
//! rebuilt `paragraph + "\n\n"` buffer, matching `TextChunker`.

use super::tokens::estimate_tokens;

pub struct Piece {
    pub content: String,
    pub start: usize,
    pub end: usize,
}

pub fn chunk(content: &str, chunk_size: usize, max_chunk_size: usize) -> Vec<Piece> {
    let chars: Vec<char> = content.chars().collect();
    let parts = split_paragraphs(&chars);
    let mut chunks = Vec::new();
    let mut current = String::new();
    let mut current_tokens = 0usize;
    let mut current_start = 0usize;

    for (start, end) in parts {
        let para: String = chars[start..end]
            .iter()
            .collect::<String>()
            .trim()
            .to_string();
        if para.is_empty() {
            continue;
        }
        let density = content_density(&para);
        let mut target = (chunk_size as f64 * (1.0 - density * 0.5)) as usize;
        let floor = chunk_size / 2;
        if target < floor {
            target = floor;
        }
        if target > max_chunk_size {
            target = max_chunk_size;
        }
        let para_tokens = estimate_tokens(&para);
        if current_tokens + para_tokens > target && !current.is_empty() {
            push(&mut chunks, &current, current_start);
            current_start += current.chars().count();
            current = format!("{para}\n\n");
            current_tokens = para_tokens;
        } else if current.is_empty() {
            current = format!("{para}\n\n");
            current_tokens = para_tokens;
        } else {
            current.push_str(&para);
            current.push_str("\n\n");
            current_tokens += para_tokens;
        }
    }
    if current.trim().is_empty() {
        chunks.push(Piece {
            content: String::new(),
            start: 0,
            end: 0,
        });
    } else {
        push(&mut chunks, &current, current_start);
    }
    chunks
}

fn push(chunks: &mut Vec<Piece>, current: &str, start: usize) {
    let content = current.trim().to_string();
    if content.is_empty() {
        return;
    }
    chunks.push(Piece {
        content,
        start,
        end: start + current.chars().count(),
    });
}

fn split_paragraphs(chars: &[char]) -> Vec<(usize, usize)> {
    let mut parts = Vec::new();
    let mut cursor = 0usize;
    let mut index = 0usize;
    while index < chars.len() {
        if let Some(end) = paragraph_break(chars, index) {
            parts.push((cursor, index));
            cursor = end;
            index = end;
        } else {
            index += 1;
        }
    }
    parts.push((cursor, chars.len()));
    parts
}

fn paragraph_break(chars: &[char], index: usize) -> Option<usize> {
    if chars.get(index) != Some(&'\n') {
        return None;
    }
    let mut last_newline = None;
    let mut cursor = index + 1;
    while cursor < chars.len() && chars[cursor].is_whitespace() {
        if chars[cursor] == '\n' {
            last_newline = Some(cursor);
        }
        cursor += 1;
    }
    last_newline.map(|at| at + 1)
}

fn content_density(text: &str) -> f64 {
    if text.is_empty() {
        return 0.0;
    }
    let lines: Vec<&str> = text.split('\n').collect();
    let line_chars: usize = lines.iter().map(|line| line.chars().count()).sum();
    let avg = line_chars as f64 / lines.len() as f64;
    let line_score = (1.0 - avg / 80.0).max(0.0);
    let special = text
        .chars()
        .filter(|ch| "{}[]()<>:=|&%$#@!~`".contains(*ch))
        .count();
    let special_score = (special as f64 / text.chars().count().max(1) as f64 * 10.0).min(1.0);
    let indented = lines
        .iter()
        .filter(|line| line.starts_with(' ') || line.starts_with('\t'))
        .count();
    let indent_score = indented as f64 / lines.len().max(1) as f64;
    let bullets = lines.iter().filter(|line| bullet_line(line)).count();
    let numbers = lines.iter().filter(|line| number_line(line)).count();
    let list_score = (bullets + numbers) as f64 / lines.len().max(1) as f64;
    (line_score * 0.3 + special_score * 0.3 + indent_score * 0.2 + list_score * 0.2).clamp(0.0, 1.0)
}

fn bullet_line(line: &str) -> bool {
    let trimmed = line.trim_start_matches([' ', '\t']);
    let rest = trimmed.trim_start_matches(['-', '*', '•']);
    rest.len() != trimmed.len() && rest.starts_with([' ', '\t'])
}

fn number_line(line: &str) -> bool {
    let trimmed = line.trim_start_matches([' ', '\t']);
    let mut chars = trimmed.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    if !first.is_ascii_digit() {
        return false;
    }
    let rest: String = chars.collect();
    let rest = rest.trim_start_matches(|ch: char| ch.is_ascii_digit());
    rest.starts_with(['.', ')']) && rest[1..].starts_with([' ', '\t'])
}
