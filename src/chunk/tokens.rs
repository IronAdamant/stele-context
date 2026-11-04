//! Token estimate used by the Python chunker. The pattern is the same
//! leftmost-alternation scan as `_TOKEN_RE` in `chunkers/base.py`.

pub fn estimate_tokens(text: &str) -> usize {
    let chars: Vec<char> = text.chars().collect();
    let mut tokens: Vec<String> = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if let Some(end) = match_token(&chars, i) {
            tokens.push(chars[i..end].iter().collect());
            i = end;
        } else {
            i += 1;
        }
    }
    let n = tokens.len();
    if n == 0 {
        return 1;
    }
    let mut merges = 0usize;
    let mut index = 0usize;
    while index + 1 < n {
        let a = &tokens[index];
        let b = &tokens[index + 1];
        if a.chars().all(|ch| ch.is_whitespace())
            && !b.is_empty()
            && !b.chars().all(|ch| ch.is_whitespace())
        {
            merges += 1;
            index += 2;
            continue;
        }
        let a_chars: Vec<char> = a.chars().collect();
        let b_chars: Vec<char> = b.chars().collect();
        if a_chars.len() == 1
            && b_chars.len() == 1
            && !python_alnum(a_chars[0])
            && !python_alnum(b_chars[0])
            && !a_chars[0].is_whitespace()
            && !b_chars[0].is_whitespace()
        {
            merges += 1;
            index += 2;
            continue;
        }
        index += 1;
    }
    (n - merges).max(1)
}

fn match_token(chars: &[char], start: usize) -> Option<usize> {
    if let Some(end) = match_word(chars, start) {
        return Some(end);
    }
    if chars[start].is_ascii_digit() {
        let mut end = start + 1;
        while end < chars.len() && chars[end].is_ascii_digit() {
            end += 1;
        }
        return Some(end);
    }
    if !is_word(chars[start]) && !chars[start].is_whitespace() {
        return Some(start + 1);
    }
    if chars[start].is_whitespace() {
        let mut end = start + 1;
        while end < chars.len() && chars[end].is_whitespace() {
            end += 1;
        }
        return Some(end);
    }
    None
}

/// `[A-Z]?[a-z]+` or `[A-Z]+(?=[A-Z][a-z]|\b)`.
fn match_word(chars: &[char], start: usize) -> Option<usize> {
    let ch = chars[start];
    if ch.is_ascii_lowercase() {
        let mut end = start + 1;
        while end < chars.len() && chars[end].is_ascii_lowercase() {
            end += 1;
        }
        return Some(end);
    }
    if ch.is_ascii_uppercase() {
        // `[A-Z]?[a-z]+` wins when the second character is lowercase.
        if chars
            .get(start + 1)
            .is_some_and(|next| next.is_ascii_lowercase())
        {
            let mut end = start + 2;
            while end < chars.len() && chars[end].is_ascii_lowercase() {
                end += 1;
            }
            return Some(end);
        }
        // `[A-Z]+(?=[A-Z][a-z]|\b)`. Leave the last capital when it starts
        // a Capitalized word, so JSONHighlighter is JSON + Highlighter.
        let mut end = start + 1;
        while end < chars.len() && chars[end].is_ascii_uppercase() {
            end += 1;
        }
        if end < chars.len() && chars[end].is_ascii_lowercase() && end - start > 1 {
            return Some(end - 1);
        }
        if end == chars.len() || !is_word(chars[end]) {
            return Some(end);
        }
        return None;
    }
    None
}

fn is_word(ch: char) -> bool {
    python_alnum(ch) || ch == '_'
}

/// Python `str.isalnum`: categories L and N. Not Rust's Alphabetic property.
fn python_alnum(ch: char) -> bool {
    super::python_alnum::is_python_word(ch) && ch != '_'
}

#[cfg(test)]
mod tests {
    use super::estimate_tokens;

    #[test]
    fn devanagari_marks_and_acronyms_match_python() {
        assert_eq!(estimate_tokens("किताब"), 1);
        assert_eq!(estimate_tokens("नमस्ते दुनिया"), 3);
        assert_eq!(estimate_tokens("JSONHighlighter"), 2);
        assert_eq!(estimate_tokens("hello world"), 2);
        assert_eq!(estimate_tokens("Aक"), 1);
        assert_eq!(estimate_tokens("नहीं"), 1);
    }
}
