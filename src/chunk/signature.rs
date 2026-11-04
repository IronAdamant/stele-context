//! 128-dim signature matching the zero-dependency Python path.
//!
//! Counts are computed in f64, then each component is rounded to f32 the way
//! `struct.pack("<f")` rounds. Tie order does not change the stored values:
//! only the descending counts are kept.

use crate::hash::{hex, sha256};

pub fn content_sha256(text: &str) -> String {
    hex(&sha256(text.as_bytes()))
}

pub fn chunk_id(path: &str, start: usize, end: usize, content_sha256_hex: &str) -> String {
    let prefix = &content_sha256_hex[..16.min(content_sha256_hex.len())];
    let id = format!("{path}:{start}:{end}:{prefix}");
    hex(&sha256(id.as_bytes()))[..32].to_string()
}

pub fn signature_f32_hex(text: &str) -> String {
    let values = signature(text);
    let mut out = String::with_capacity(128 * 8);
    for value in values {
        for byte in (value as f32).to_le_bytes() {
            out.push(HEX[(byte >> 4) as usize] as char);
            out.push(HEX[(byte & 0xf) as usize] as char);
        }
    }
    out
}

const HEX: &[u8; 16] = b"0123456789abcdef";

pub fn signature(text: &str) -> [f64; 128] {
    let mut sig = [0.0f64; 128];
    let chars: Vec<char> = text.chars().collect();
    let length = chars.len().max(1);
    let lower: String = chars.iter().flat_map(|ch| ch.to_lowercase()).collect();
    let lower_chars: Vec<char> = lower.chars().collect();

    let trigrams = top_counts(&char_ngrams(&lower_chars, 3), 64);
    for (index, count) in trigrams.into_iter().enumerate() {
        sig[index] = count as f64 / length as f64;
    }

    let words = ascii_words(&lower);
    let filtered: Vec<&str> = words
        .iter()
        .filter(|word| word.chars().count() > 2)
        .map(String::as_str)
        .collect();
    let total_words = filtered.len().max(1);
    for (index, count) in top_counts(&owned_counts(filtered.iter().copied()), 16)
        .into_iter()
        .enumerate()
    {
        sig[64 + index] = count as f64 / total_words as f64;
    }
    let bigrams = word_bigrams(&filtered);
    let total_bigrams = bigrams.iter().map(|(_, count)| *count).sum::<u32>().max(1);
    for (index, count) in top_counts(&bigrams, 16).into_iter().enumerate() {
        sig[80 + index] = count as f64 / total_bigrams as f64;
    }

    let lines = split_lines(text);
    let num_lines = lines.len().max(1) as f64;
    sig[96] = lines.len() as f64 / 100.0;
    let line_chars: usize = lines.iter().map(|line| line.chars().count()).sum();
    sig[97] = line_chars as f64 / length as f64;
    sig[98] = lines
        .iter()
        .filter(|line| line.trim().starts_with('#'))
        .count() as f64
        / num_lines;
    sig[99] = lines
        .iter()
        .filter(|line| line.trim().starts_with("def "))
        .count() as f64
        / num_lines;
    sig[100] = lines
        .iter()
        .filter(|line| line.trim().starts_with("class "))
        .count() as f64
        / num_lines;
    sig[101] = chars.iter().filter(|ch| **ch == '(').count() as f64 / length as f64;
    sig[102] = chars.iter().filter(|ch| **ch == '{').count() as f64 / length as f64;
    sig[103] = chars.iter().filter(|ch| **ch == '[').count() as f64 / length as f64;

    let first = lines
        .first()
        .map(|line| line.trim().to_lowercase())
        .unwrap_or_default();
    sig[104] = if first.contains("def ") || first.contains("function ") {
        1.0
    } else {
        0.0
    };
    sig[105] = if first.contains("class ") { 1.0 } else { 0.0 };
    sig[106] = if first.contains("import ")
        || first.contains("from ")
        || first.contains("#include")
        || first.contains("require")
    {
        1.0
    } else {
        0.0
    };

    let indents: Vec<usize> = lines
        .iter()
        .filter(|line| !line.trim().is_empty())
        .map(|line| line.chars().count() - line.trim_start().chars().count())
        .collect();
    if !indents.is_empty() {
        let avg = indents.iter().sum::<usize>() as f64 / indents.len() as f64;
        let max = *indents.iter().max().unwrap() as f64;
        sig[107] = (avg / 16.0).min(1.0);
        sig[108] = (max / 32.0).min(1.0);
        let variance = indents
            .iter()
            .map(|value| {
                let delta = *value as f64 - avg;
                delta * delta
            })
            .sum::<f64>()
            / indents.len() as f64;
        sig[109] = (variance / 64.0).min(1.0);
    }
    let non_empty = lines.iter().filter(|line| !line.trim().is_empty()).count() as f64;
    sig[110] = non_empty / num_lines;
    let comment_chars = chars.iter().filter(|ch| **ch == '#').count() as f64;
    sig[111] = (comment_chars / length as f64 * 10.0).min(1.0);
    let unicode_words = unicode_words(&lower);
    if !unicode_words.is_empty() {
        let unique = {
            let mut set = unicode_words.clone();
            set.sort();
            set.dedup();
            set.len()
        };
        sig[112] = unique as f64 / unicode_words.len() as f64;
        let mean_len = unicode_words
            .iter()
            .map(|word| word.chars().count())
            .sum::<usize>() as f64
            / unicode_words.len() as f64;
        sig[113] = (mean_len / 10.0).min(1.0);
    }
    let punct = chars
        .iter()
        .filter(|ch| matches!(ch, '.' | ',' | ';' | ':' | '!' | '?'))
        .count() as f64;
    sig[114] = (punct / length as f64 * 10.0).min(1.0);
    let digits = chars.iter().filter(|ch| ch.is_ascii_digit()).count() as f64;
    sig[115] = (digits / length as f64 * 5.0).min(1.0);

    let norm = sig.iter().map(|value| value * value).sum::<f64>().sqrt();
    if norm > 0.0 {
        for value in &mut sig {
            *value /= norm;
        }
    }
    sig
}

fn split_lines(text: &str) -> Vec<&str> {
    if text.is_empty() {
        return vec![""];
    }
    text.split('\n').collect()
}

fn char_ngrams(chars: &[char], n: usize) -> Vec<(String, u32)> {
    let mut counts: Vec<(String, u32)> = Vec::new();
    if chars.len() < n {
        return counts;
    }
    for index in 0..=chars.len() - n {
        let gram: String = chars[index..index + n].iter().collect();
        bump(&mut counts, gram);
    }
    counts
}

fn ascii_words(lower: &str) -> Vec<String> {
    let chars: Vec<char> = lower.chars().collect();
    let mut words = Vec::new();
    let mut index = 0;
    while index < chars.len() {
        let prev_word = index > 0 && is_word(chars[index - 1]);
        if !prev_word && is_ascii_start(chars[index]) {
            let start = index;
            index += 1;
            while index < chars.len() && is_ascii_cont(chars[index]) {
                index += 1;
            }
            let next_word = index < chars.len() && is_word(chars[index]);
            if !next_word {
                words.push(chars[start..index].iter().collect());
            }
        } else {
            index += 1;
        }
    }
    words
}

fn unicode_words(lower: &str) -> Vec<String> {
    let chars: Vec<char> = lower.chars().collect();
    let mut words = Vec::new();
    let mut index = 0;
    while index < chars.len() {
        let prev_word = index > 0 && is_word(chars[index - 1]);
        if !prev_word && is_word(chars[index]) {
            let start = index;
            index += 1;
            while index < chars.len() && is_word(chars[index]) {
                index += 1;
            }
            words.push(chars[start..index].iter().collect());
        } else {
            index += 1;
        }
    }
    words
}

fn word_bigrams(words: &[&str]) -> Vec<(String, u32)> {
    let mut counts = Vec::new();
    for pair in words.windows(2) {
        bump(&mut counts, format!("{}_{}", pair[0], pair[1]));
    }
    counts
}

fn owned_counts<'a>(words: impl Iterator<Item = &'a str>) -> Vec<(String, u32)> {
    let mut counts = Vec::new();
    for word in words {
        bump(&mut counts, word.to_string());
    }
    counts
}

fn bump(counts: &mut Vec<(String, u32)>, key: String) {
    if let Some((_, count)) = counts.iter_mut().find(|(name, _)| name == &key) {
        *count += 1;
    } else {
        counts.push((key, 1));
    }
}

fn top_counts(counts: &[(String, u32)], n: usize) -> Vec<u32> {
    let mut order: Vec<usize> = (0..counts.len()).collect();
    order.sort_by(|&left, &right| counts[right].1.cmp(&counts[left].1).then(left.cmp(&right)));
    order
        .into_iter()
        .take(n)
        .map(|index| counts[index].1)
        .collect()
}

fn is_word(ch: char) -> bool {
    ch.is_alphanumeric() || ch == '_'
}

fn is_ascii_start(ch: char) -> bool {
    ch.is_ascii_alphabetic() || ch == '_'
}

fn is_ascii_cont(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ch == '_'
}
