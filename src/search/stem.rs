//! Porter stemmer and identifier split, matching `stemmer.py`.

pub fn stem(word: &str) -> String {
    let mut word = word.trim().to_lowercase();
    if word.chars().count() <= 2 {
        return word;
    }
    word = step1a(&word);
    word = step1b(&word);
    word = step1c(&word);
    word = step_suffix(&word, STEP2, true);
    word = step_suffix(&word, STEP3, true);
    word = step4(&word);
    word = step5a(&word);
    step5b(&word)
}

pub fn split_identifier(name: &str) -> Vec<String> {
    let name = name.trim();
    if name.is_empty() {
        return Vec::new();
    }
    let mut words = Vec::new();
    for part in name.split(|ch| ch == '_' || ch == '-') {
        if part.is_empty() {
            continue;
        }
        words.extend(camel_parts(part));
    }
    words
}

fn camel_parts(part: &str) -> Vec<String> {
    let chars: Vec<char> = part.chars().collect();
    let mut words = Vec::new();
    let mut index = 0usize;
    while index < chars.len() {
        if let Some(end) = camel_token(&chars, index) {
            let word: String = chars[index..end].iter().collect::<String>().to_lowercase();
            if !word.is_empty() {
                words.push(word);
            }
            index = end;
        } else {
            index += 1;
        }
    }
    words
}

/// `[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)|[A-Z]+|[a-z]+|\d+`
fn camel_token(chars: &[char], start: usize) -> Option<usize> {
    let ch = chars[start];
    if ch.is_ascii_uppercase() {
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
        let mut end = start + 1;
        while end < chars.len() && chars[end].is_ascii_uppercase() {
            end += 1;
        }
        if end < chars.len() && chars[end].is_ascii_lowercase() && end - start > 1 {
            end -= 1;
        }
        return Some(end);
    }
    if ch.is_ascii_lowercase() {
        let mut end = start + 1;
        while end < chars.len() && chars[end].is_ascii_lowercase() {
            end += 1;
        }
        return Some(end);
    }
    if ch.is_ascii_digit() {
        let mut end = start + 1;
        while end < chars.len() && chars[end].is_ascii_digit() {
            end += 1;
        }
        return Some(end);
    }
    None
}

fn is_vowel(word: &str, index: usize) -> bool {
    let bytes = word.as_bytes();
    let ch = bytes[index];
    if matches!(ch, b'a' | b'e' | b'i' | b'o' | b'u') {
        return true;
    }
    ch == b'y' && (index == 0 || !is_vowel(word, index - 1))
}

fn contains_vowel(word: &str) -> bool {
    (0..word.len()).any(|index| is_vowel(word, index))
}

fn measure(word: &str) -> usize {
    if word.is_empty() {
        return 0;
    }
    let mut pattern = String::new();
    for index in 0..word.len() {
        let mark = if is_vowel(word, index) { 'V' } else { 'C' };
        if pattern.chars().last() != Some(mark) {
            pattern.push(mark);
        }
    }
    if pattern.starts_with('C') {
        pattern.remove(0);
    }
    if pattern.ends_with('V') {
        pattern.pop();
    }
    pattern.matches("VC").count()
}

fn ends_double_consonant(word: &str) -> bool {
    let bytes = word.as_bytes();
    bytes.len() >= 2
        && bytes[bytes.len() - 1] == bytes[bytes.len() - 2]
        && !is_vowel(word, bytes.len() - 1)
}

fn ends_cvc(word: &str) -> bool {
    if word.len() < 3 {
        return false;
    }
    let last = word.as_bytes()[word.len() - 1];
    !is_vowel(word, word.len() - 1)
        && is_vowel(word, word.len() - 2)
        && !is_vowel(word, word.len() - 3)
        && !matches!(last, b'w' | b'x' | b'y')
}

fn step1a(word: &str) -> String {
    if let Some(stem) = word.strip_suffix("sses") {
        return format!("{stem}ss");
    }
    if let Some(stem) = word.strip_suffix("ies") {
        return format!("{stem}i");
    }
    if word.ends_with("ss") {
        return word.to_string();
    }
    if let Some(stem) = word.strip_suffix('s') {
        return stem.to_string();
    }
    word.to_string()
}

fn step1b(word: &str) -> String {
    if let Some(stem) = word.strip_suffix("eed") {
        if measure(stem) > 0 {
            return format!("{stem}ee");
        }
        return word.to_string();
    }
    let (mut word, changed) = if let Some(stem) = word.strip_suffix("ed") {
        if contains_vowel(stem) {
            (stem.to_string(), true)
        } else {
            (word.to_string(), false)
        }
    } else if let Some(stem) = word.strip_suffix("ing") {
        if contains_vowel(stem) {
            (stem.to_string(), true)
        } else {
            (word.to_string(), false)
        }
    } else {
        (word.to_string(), false)
    };
    if changed {
        if word.ends_with("at") || word.ends_with("bl") || word.ends_with("iz") {
            word.push('e');
        } else if ends_double_consonant(&word)
            && !matches!(word.as_bytes().last(), Some(b'l' | b's' | b'z'))
        {
            word.pop();
        } else if measure(&word) == 1 && ends_cvc(&word) {
            word.push('e');
        }
    }
    word
}

fn step1c(word: &str) -> String {
    if let Some(stem) = word.strip_suffix('y') {
        if contains_vowel(stem) {
            return format!("{stem}i");
        }
    }
    word.to_string()
}

const STEP2: &[(&str, &str)] = &[
    ("ational", "ate"),
    ("tional", "tion"),
    ("enci", "ence"),
    ("anci", "ance"),
    ("izer", "ize"),
    ("abli", "able"),
    ("alli", "al"),
    ("entli", "ent"),
    ("eli", "e"),
    ("ousli", "ous"),
    ("ization", "ize"),
    ("ation", "ate"),
    ("ator", "ate"),
    ("alism", "al"),
    ("iveness", "ive"),
    ("fulness", "ful"),
    ("ousness", "ous"),
    ("aliti", "al"),
    ("iviti", "ive"),
    ("biliti", "ble"),
];

const STEP3: &[(&str, &str)] = &[
    ("icate", "ic"),
    ("ative", ""),
    ("alize", "al"),
    ("iciti", "ic"),
    ("ical", "ic"),
    ("ful", ""),
    ("ness", ""),
];

const STEP4: &[&str] = &[
    "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ion", "ou",
    "ism", "ate", "iti", "ous", "ive", "ize",
];

fn step_suffix(word: &str, table: &[(&str, &str)], _measure_gt0: bool) -> String {
    for (suffix, replacement) in table {
        if let Some(stem) = word.strip_suffix(suffix) {
            if measure(stem) > 0 {
                return format!("{stem}{replacement}");
            }
            return word.to_string();
        }
    }
    word.to_string()
}

fn step4(word: &str) -> String {
    for suffix in STEP4 {
        if let Some(stem) = word.strip_suffix(suffix) {
            if *suffix == "ion" {
                if measure(stem) > 1 && stem.ends_with(['s', 't']) {
                    return stem.to_string();
                }
            } else if measure(stem) > 1 {
                return stem.to_string();
            }
            return word.to_string();
        }
    }
    word.to_string()
}

fn step5a(word: &str) -> String {
    if let Some(stem) = word.strip_suffix('e') {
        let m = measure(stem);
        if m > 1 || (m == 1 && !ends_cvc(stem)) {
            return stem.to_string();
        }
    }
    word.to_string()
}

fn step5b(word: &str) -> String {
    if ends_double_consonant(word) && measure(word) > 1 {
        return word[..word.len() - 1].to_string();
    }
    word.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn porter_examples() {
        assert_eq!(stem("running"), "run");
        assert_eq!(stem("connections"), "connect");
        assert_eq!(split_identifier("loginHandler"), vec!["login", "handler"]);
        assert_eq!(
            split_identifier("get_user_name"),
            vec!["get", "user", "name"]
        );
        assert_eq!(split_identifier("HTMLParser"), vec!["html", "parser"]);
    }
}
