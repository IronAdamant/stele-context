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
    let mut lower_chars = Vec::with_capacity(chars.len());
    for ch in &chars {
        if ch.is_ascii() {
            lower_chars.push(ch.to_ascii_lowercase());
        } else {
            lower_chars.extend(ch.to_lowercase());
        }
    }

    for (index, count) in top_trigrams(&lower_chars, 64).into_iter().enumerate() {
        sig[index] = count as f64 / length as f64;
    }

    // Words of three or more ASCII characters, as ranges of `lower_chars`.
    let filtered: Vec<(usize, usize)> = ascii_words(&lower_chars)
        .into_iter()
        .filter(|(start, end)| end - start > 2)
        .collect();
    let total_words = filtered.len().max(1);
    for (index, count) in word_counts(&lower_chars, &filtered, 16)
        .into_iter()
        .enumerate()
    {
        sig[64 + index] = count as f64 / total_words as f64;
    }
    let total_bigrams = filtered.len().saturating_sub(1).max(1);
    for (index, count) in bigram_counts(&lower_chars, &filtered, 16)
        .into_iter()
        .enumerate()
    {
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
    let mut unicode_words = unicode_words(&lower_chars);
    if !unicode_words.is_empty() {
        let mean_len = unicode_words
            .iter()
            .map(|(start, end)| end - start)
            .sum::<usize>() as f64
            / unicode_words.len() as f64;
        let total = unicode_words.len();
        unicode_words.sort_unstable_by(|left, right| {
            lower_chars[left.0..left.1].cmp(&lower_chars[right.0..right.1])
        });
        unicode_words
            .dedup_by(|left, right| lower_chars[left.0..left.1] == lower_chars[right.0..right.1]);
        sig[112] = unicode_words.len() as f64 / total as f64;
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

/// The `n` largest trigram counts, largest first. Only the counts are
/// kept, so equal counts need no tie order: sort the packed trigrams and
/// count runs.
fn top_trigrams(chars: &[char], n: usize) -> Vec<u32> {
    if chars.len() < 3 {
        return Vec::new();
    }
    // A char is at most 21 bits, so three fit in one u64 exactly.
    let mut keys: Vec<u64> = chars
        .windows(3)
        .map(|w| ((w[0] as u64) << 42) | ((w[1] as u64) << 21) | w[2] as u64)
        .collect();
    keys.sort_unstable();
    let mut counts = runs(&keys, |left, right| left == right);
    largest(&mut counts, n)
}

/// `[a-z_][a-z0-9_]*` over the lowered text, as char ranges.
fn ascii_words(chars: &[char]) -> Vec<(usize, usize)> {
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
                words.push((start, index));
            }
        } else {
            index += 1;
        }
    }
    words
}

/// `\w+` runs, as char ranges.
fn unicode_words(chars: &[char]) -> Vec<(usize, usize)> {
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
            words.push((start, index));
        } else {
            index += 1;
        }
    }
    words
}

/// The `n` largest word counts.
fn word_counts(chars: &[char], words: &[(usize, usize)], n: usize) -> Vec<u32> {
    let mut sorted: Vec<&[char]> = words
        .iter()
        .map(|(start, end)| &chars[*start..*end])
        .collect();
    sorted.sort_unstable();
    let mut counts = runs(&sorted, |left, right| left == right);
    largest(&mut counts, n)
}

/// The `n` largest counts of `f"{a}_{b}"` over adjacent words. The key is
/// the joined text, so `ab_c`+`d` and `ab`+`c_d` count as one bigram.
fn bigram_counts(chars: &[char], words: &[(usize, usize)], n: usize) -> Vec<u32> {
    let joined = |pair: &[(usize, usize)]| {
        chars[pair[0].0..pair[0].1]
            .iter()
            .chain(std::iter::once(&'_'))
            .chain(chars[pair[1].0..pair[1].1].iter())
    };
    let mut pairs: Vec<&[(usize, usize)]> = words.windows(2).collect();
    pairs.sort_unstable_by(|left, right| joined(left).cmp(joined(right)));
    let mut counts = runs(&pairs, |left, right| joined(left).eq(joined(right)));
    largest(&mut counts, n)
}

/// Lengths of the runs of equal neighbours in a sorted slice.
fn runs<T>(sorted: &[T], same: impl Fn(&T, &T) -> bool) -> Vec<u32> {
    let mut counts = Vec::new();
    let mut index = 0;
    while index < sorted.len() {
        let mut end = index + 1;
        while end < sorted.len() && same(&sorted[index], &sorted[end]) {
            end += 1;
        }
        counts.push((end - index) as u32);
        index = end;
    }
    counts
}

fn largest(counts: &mut Vec<u32>, n: usize) -> Vec<u32> {
    counts.sort_unstable_by(|left, right| right.cmp(left));
    counts.truncate(n);
    std::mem::take(counts)
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
