//! Where a chunk's lines sit in its file.
//!
//! A chunk is trimmed text, and merged chunks are joined with a run of
//! newlines as long as the gap they skipped (1.5.2's rule), so a line
//! number counted inside a chunk drifts from the file's. The map records,
//! for each run of chunk lines that are consecutive file lines, the first
//! chunk line and the file line it is on (both 0-based). It is found by
//! matching each non-blank chunk line against the file, in order.

/// Search window past the previous match. Chunks are consecutive slices of
/// the file; only blank lines differ, so the next line is always close.
const WINDOW: usize = 512;

/// Map `content` onto `file_lines`, starting at `*cursor`. Leaves `*cursor`
/// after the last matched line, ready for the next chunk of the same file.
pub fn line_map(file_lines: &[&str], content: &str, cursor: &mut usize) -> Vec<(u32, u32)> {
    let mut map: Vec<(u32, u32)> = Vec::new();
    let mut offset: Option<i64> = None;
    for (index, line) in content_lines(content).iter().enumerate() {
        let text = line.trim();
        if text.is_empty() {
            continue;
        }
        let end = (*cursor + WINDOW).min(file_lines.len());
        let Some(found) = (*cursor..end).find(|at| file_lines[*at].trim() == text) else {
            continue;
        };
        let shift = found as i64 - index as i64;
        if offset != Some(shift) {
            map.push((index as u32, found as u32));
            offset = Some(shift);
        }
        *cursor = found + 1;
    }
    map
}

/// The 1-based file line of 1-based chunk line `line`; `line` itself when
/// the chunk has no map (no file text, or a store written before 2.3).
pub fn absolute(map: &[(u32, u32)], line: u32) -> u32 {
    if line == 0 || map.is_empty() {
        return line;
    }
    let index = line - 1;
    let (chunk_at, file_at) = map
        .iter()
        .rev()
        .find(|(chunk_at, _)| *chunk_at <= index)
        .copied()
        .unwrap_or(map[0]);
    (file_at as i64 + index as i64 - chunk_at as i64).max(0) as u32 + 1
}

/// The 0-based chunk line that holds 1-based file line `line`.
pub fn relative(map: &[(u32, u32)], line: u32) -> Option<usize> {
    if line == 0 || map.is_empty() {
        return None;
    }
    let target = line - 1;
    let (chunk_at, file_at) = map
        .iter()
        .rev()
        .find(|(_, file_at)| *file_at <= target)
        .copied()?;
    Some((chunk_at + (target - file_at)) as usize)
}

/// The chunk's lines as the symbol extractors count them.
pub fn content_lines(content: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0usize;
    for (at, ch) in content.char_indices() {
        if ch == '\n' || ch == '\r' || is_break(ch) {
            if ch == '\n' && at > 0 && content.as_bytes()[at - 1] == b'\r' {
                start = at + 1;
                continue;
            }
            lines.push(&content[start..at]);
            start = at + ch.len_utf8();
        }
    }
    if start < content.len() {
        lines.push(&content[start..]);
    }
    lines
}

/// Python `str.splitlines` breaks also on these.
fn is_break(ch: char) -> bool {
    matches!(
        ch,
        '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn merged_chunks_map_back_to_file_lines() {
        let file = "use a;\n\nfn one() {\n}\n\n    fn two() {\n        1\n    }\n";
        let lines: Vec<&str> = file.split('\n').collect();
        // 1.5.2 joins the two pieces with one newline per gap character.
        let content = "fn one() {\n}\n\n\n\n\n\n\nfn two() {\n        1\n    }";
        let mut cursor = 1;
        let map = line_map(&lines, content, &mut cursor);
        assert_eq!(map, vec![(0, 2), (8, 5)]);
        assert_eq!(absolute(&map, 1), 3);
        assert_eq!(absolute(&map, 9), 6);
        assert_eq!(absolute(&map, 10), 7);
        assert_eq!(relative(&map, 6), Some(8));
        assert_eq!(relative(&map, 3), Some(0));
        assert_eq!(cursor, 8);
        assert_eq!(absolute(&[], 4), 4);
    }

    #[test]
    fn lines_split_like_python() {
        assert_eq!(content_lines("a\r\nb\rc\n"), vec!["a", "b", "c"]);
        assert_eq!(content_lines("a\u{0c}b"), vec!["a", "b"]);
        assert_eq!(content_lines(""), Vec::<&str>::new());
    }
}
