//! Whether a chunk is a valid Python module.
//!
//! 1.5.2 calls `ast.parse` on each chunk and, on `SyntaxError`, keeps only the
//! regex fallback (definitions and imports). A chunk cut in the middle of a
//! list, dict, or decorator is not a module, so the full walk must not run.

pub fn module_is_valid(text: &str) -> bool {
    scan_error(text).is_none()
}

fn scan_error(text: &str) -> Option<&'static str> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut i = 0usize;
    let mut brackets: Vec<char> = Vec::new();
    let mut indents = vec![0u32];
    let mut expect_suite = false;
    let mut line_start = true;
    let mut pending_at = false;
    let mut stmt_string = false;
    let mut saw_token = false;
    let mut colon_pending = false;
    while i < n {
        if line_start {
            let mut j = i;
            let mut col = 0u32;
            while j < n && matches!(chars[j], ' ' | '\t') {
                col = if chars[j] == '\t' {
                    (col / 8 + 1) * 8
                } else {
                    col + 1
                };
                j += 1;
            }
            if j < n && !matches!(chars[j], '\n' | '\r' | '#') {
                if brackets.is_empty() {
                    let current = *indents.last().unwrap();
                    if col > current {
                        if !expect_suite {
                            return Some("unexpected indent");
                        }
                        indents.push(col);
                        expect_suite = false;
                        colon_pending = false;
                    } else if col < current {
                        while indents.last().is_some_and(|level| col < *level) {
                            indents.pop();
                        }
                        if indents.last() != Some(&col) {
                            return Some("unindent");
                        }
                        expect_suite = false;
                        colon_pending = false;
                    } else if expect_suite {
                        return Some("expected indent");
                    } else {
                        expect_suite = false;
                    }
                    saw_token = false;
                    stmt_string = false;
                }
                line_start = false;
                i = j;
                continue;
            }
            line_start = false;
            i = j;
            continue;
        }
        let ch = chars[i];
        if ch == '#' {
            while i < n && !matches!(chars[i], '\n' | '\r') {
                i += 1;
            }
            continue;
        }
        if matches!(ch, '\n' | '\r') {
            if ch == '\r' && chars.get(i + 1) == Some(&'\n') {
                i += 1;
            }
            i += 1;
            line_start = true;
            saw_token = false;
            stmt_string = false;
            continue;
        }
        if matches!(ch, ' ' | '\t') {
            i += 1;
            continue;
        }
        if colon_pending && brackets.is_empty() {
            colon_pending = false;
            expect_suite = false;
        }
        if matches!(ch, '"' | '\'') {
            if !saw_token && brackets.is_empty() {
                stmt_string = true;
            }
            saw_token = true;
            let quote = ch;
            let triple = chars.get(i..i + 3) == Some(&[quote, quote, quote]);
            i += if triple { 3 } else { 1 };
            if triple {
                let mut end = None;
                let mut k = i;
                while k + 2 < n {
                    if chars[k] == quote && chars[k + 1] == quote && chars[k + 2] == quote {
                        end = Some(k);
                        break;
                    }
                    k += 1;
                }
                let Some(end) = end else {
                    return Some("unterminated string");
                };
                i = end + 3;
            } else {
                while i < n && chars[i] != quote {
                    if chars[i] == '\\' {
                        i += 2;
                        continue;
                    }
                    if matches!(chars[i], '\n' | '\r') {
                        return Some("unterminated string");
                    }
                    i += 1;
                }
                if i >= n {
                    return Some("unterminated string");
                }
                i += 1;
            }
            continue;
        }
        if matches!(ch, '(' | '[' | '{') {
            brackets.push(ch);
            saw_token = true;
            stmt_string = false;
            i += 1;
            continue;
        }
        if matches!(ch, ')' | ']' | '}') {
            if brackets.is_empty() {
                return Some("unmatched close");
            }
            brackets.pop();
            saw_token = true;
            i += 1;
            continue;
        }
        if ch == ':' && brackets.is_empty() {
            if stmt_string {
                return Some("illegal annotation");
            }
            expect_suite = true;
            colon_pending = true;
            pending_at = false;
            saw_token = true;
            i += 1;
            continue;
        }
        if ch == '@' && brackets.is_empty() {
            pending_at = true;
            saw_token = true;
            i += 1;
            continue;
        }
        if ch.is_alphabetic() || ch == '_' || !ch.is_ascii() {
            let start = i;
            i += 1;
            while i < n && (chars[i].is_alphanumeric() || chars[i] == '_' || !chars[i].is_ascii()) {
                i += 1;
            }
            let word: String = chars[start..i].iter().collect();
            if brackets.is_empty() && matches!(word.as_str(), "def" | "class" | "async") {
                pending_at = false;
            }
            saw_token = true;
            stmt_string = false;
            continue;
        }
        saw_token = true;
        i += 1;
    }
    if !brackets.is_empty() {
        return Some("unclosed");
    }
    if pending_at {
        return Some("trailing decorator");
    }
    if expect_suite {
        return Some("expected indent");
    }
    None
}
