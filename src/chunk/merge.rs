//! Adjacent-chunk merge. Code refuses to cross a definition lead. The
//! separator width comes from the gap between offsets and the stripped text.

use super::signature::signature;
use super::Produced;

const DEF_STARTS: &[&str] = &[
    "def ",
    "class ",
    "function ",
    "func ",
    "fn ",
    "pub fn ",
    "async def ",
    "async function ",
    "export function ",
    "export class ",
    "export default ",
    "const ",
    "let ",
    "var ",
    "module.exports",
    "describe(",
    "it(",
    "test(",
];

pub fn merge_similar(
    chunks: Vec<Produced>,
    max_chunk_size: usize,
    merge_fallback: f64,
) -> Vec<Produced> {
    if chunks.len() <= 1 {
        return chunks;
    }
    // 1.5.2 modality table wins. `merge_fallback` is the config value used
    // when the modality is not in that table.
    let threshold = match chunks[0].modality.as_str() {
        "code" => 0.85,
        "text" => 0.70,
        "pdf" => 0.75,
        _ => merge_fallback,
    };
    let mut merged = vec![chunks[0].clone()];
    for chunk in chunks.into_iter().skip(1) {
        let current = merged.last().unwrap().clone();
        if current.modality == "code" {
            let leading = chunk.content.trim_start();
            if DEF_STARTS.iter().any(|lead| leading.starts_with(lead)) {
                merged.push(chunk);
                continue;
            }
        }
        let similarity = cosine(&signature(&current.content), &signature(&chunk.content));
        let combined = estimate(&current.content) + estimate(&chunk.content);
        if similarity >= threshold && combined <= max_chunk_size {
            let pad = chunk.start as isize
                - current.start as isize
                - current.content.chars().count() as isize;
            let separator = if pad >= 1 {
                "\n".repeat(pad as usize)
            } else {
                "\n\n".to_string()
            };
            let mut joined = current.clone();
            joined.content = format!("{}{separator}{}", current.content, chunk.content);
            joined.end = chunk.end;
            if joined.language.is_none() {
                joined.language = chunk.language;
            }
            *merged.last_mut().unwrap() = joined;
        } else {
            merged.push(chunk);
        }
    }
    merged
}

fn estimate(text: &str) -> usize {
    super::tokens::estimate_tokens(text)
}

fn cosine(left: &[f64; 128], right: &[f64; 128]) -> f64 {
    let dot: f64 = left.iter().zip(right).map(|(a, b)| a * b).sum();
    let left_norm = left.iter().map(|value| value * value).sum::<f64>().sqrt();
    let right_norm = right.iter().map(|value| value * value).sum::<f64>().sqrt();
    if left_norm > 0.0 && right_norm > 0.0 {
        dot / (left_norm * right_norm)
    } else {
        0.0
    }
}
