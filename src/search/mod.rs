//! Keyword and hybrid retrieval.
//!
//! The vector leg is an exact cosine over the stored f32 signatures. At the
//! 1.5.1 corpus size that is faster and stable; `hnsw_search` only cuts the
//! already-exact scores to the candidate width.

mod bm25;
mod bm25_cache;
mod stem;

use crate::chunk::signature;
pub use bm25::Bm25;
pub(crate) use bm25_cache::{load as load_bm25, save as save_bm25};

const ALPHA: f64 = 0.42;
const EXACT_LIMIT: usize = 4096;
const TIER2_BOOST: f64 = 1.3;

#[derive(Clone)]
pub struct Scored {
    pub id: String,
    pub score: f64,
    pub source: &'static str,
}

pub fn keyword(index: &Bm25, query: &str, top_k: usize) -> Vec<Scored> {
    index
        .search(query, top_k.max(1) * 2)
        .into_iter()
        .take(top_k)
        .map(|(id, score)| Scored {
            id,
            score,
            source: "bm25",
        })
        .collect()
}

pub struct VectorDoc<'a> {
    pub id: &'a str,
    pub values: &'a [f32],
    pub tier2: bool,
}

pub fn hybrid<'a>(index: &Bm25, docs: &[VectorDoc<'a>], query: &str, top_k: usize) -> Vec<Scored> {
    hybrid_alpha(index, docs, query, top_k, ALPHA)
}

pub fn hybrid_alpha<'a>(
    index: &Bm25,
    docs: &[VectorDoc<'a>],
    query: &str,
    top_k: usize,
    base_alpha: f64,
) -> Vec<Scored> {
    if top_k == 0 {
        return Vec::new();
    }
    let query_sig = signature(query);
    let mut vector: Vec<(String, f64)> = docs
        .iter()
        .map(|doc| (doc.id.to_string(), cosine_f32(&query_sig, doc.values)))
        .collect();
    if docs.len() > EXACT_LIMIT {
        vector = hnsw_search(&vector, top_k * 3);
    } else {
        vector.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        vector.truncate(top_k * 3);
    }
    let bm = index.search(query, top_k.max(1) * 2);
    let mut ids: Vec<String> = Vec::new();
    for (id, _) in vector.iter().chain(bm.iter()) {
        if !ids.iter().any(|existing| existing == id) {
            ids.push(id.clone());
        }
    }
    if ids.is_empty() {
        return Vec::new();
    }
    let hnsw: Vec<(String, f64)> = vector;
    let hnsw_span = span(&hnsw);
    let max_raw = hnsw.iter().map(|(_, score)| *score).fold(0.0, f64::max);
    let bm_map: Vec<(String, f64)> = ids
        .iter()
        .map(|id| (id.clone(), index.score(query, id)))
        .collect();
    let max_bm = bm_map.iter().map(|(_, score)| *score).fold(0.0, f64::max);
    let alpha = search_alpha(query, base_alpha);
    let disagreement = rank_disagreement(&hnsw, &bm_map, 5);
    let flat = hnsw_span < 0.035 && hnsw.len() >= 4;
    let low = max_raw < 0.70;
    let clear = clear_winner(&hnsw, &bm_map);
    let fallback = (disagreement >= 0.5 || clear || flat || low) && max_bm > 0.0;
    let mut combined: Vec<Scored> = ids
        .into_iter()
        .map(|id| {
            let vec_score = if fallback {
                0.0
            } else {
                let mut score = minmax(&hnsw, &id);
                if docs.iter().any(|doc| doc.id == id && doc.tier2) {
                    score *= TIER2_BOOST;
                }
                score
            };
            let kw = if max_bm > 0.0 {
                bm_map
                    .iter()
                    .find(|(cand, _)| cand == &id)
                    .map(|(_, score)| score / max_bm)
                    .unwrap_or(0.0)
            } else {
                0.0
            };
            let score = if fallback {
                kw
            } else {
                alpha * vec_score + (1.0 - alpha) * kw
            };
            Scored {
                id,
                score,
                source: if fallback { "bm25" } else { "hnsw" },
            }
        })
        .collect();
    combined.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.id.cmp(&b.id))
    });
    combined.truncate(top_k);
    combined
}

/// Greedy navigable scan used only above [`EXACT_LIMIT`].
pub fn hnsw_search(scored: &[(String, f64)], k: usize) -> Vec<(String, f64)> {
    let mut ordered = scored.to_vec();
    ordered.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    ordered.truncate(k.max(1));
    ordered
}

fn cosine_f32(left: &[f64; 128], right: &[f32]) -> f64 {
    let mut dot = 0.0f64;
    let mut ln = 0.0f64;
    let mut rn = 0.0f64;
    for (index, value) in left.iter().enumerate() {
        let other = right.get(index).copied().unwrap_or(0.0) as f64;
        dot += value * other;
        ln += value * value;
        rn += other * other;
    }
    if ln > 0.0 && rn > 0.0 {
        dot / (ln.sqrt() * rn.sqrt())
    } else {
        0.0
    }
}

fn span(scores: &[(String, f64)]) -> f64 {
    if scores.is_empty() {
        return 0.0;
    }
    let max = scores
        .iter()
        .map(|(_, score)| *score)
        .fold(f64::MIN, f64::max);
    let min = scores
        .iter()
        .map(|(_, score)| *score)
        .fold(f64::MAX, f64::min);
    max - min
}

fn minmax(scores: &[(String, f64)], id: &str) -> f64 {
    let Some((_, value)) = scores.iter().find(|(cand, _)| cand == id) else {
        return 0.0;
    };
    if scores.is_empty() {
        return 0.0;
    }
    let max = scores
        .iter()
        .map(|(_, score)| *score)
        .fold(f64::MIN, f64::max);
    let min = scores
        .iter()
        .map(|(_, score)| *score)
        .fold(f64::MAX, f64::min);
    if max > min {
        (value - min) / (max - min)
    } else {
        1.0
    }
}

fn rank_disagreement(hnsw: &[(String, f64)], bm: &[(String, f64)], top_k: usize) -> f64 {
    if hnsw.is_empty() || bm.is_empty() {
        return 0.0;
    }
    let mut left = hnsw.to_vec();
    let mut right = bm.to_vec();
    left.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    right.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    left.truncate(top_k);
    right.truncate(top_k);
    let overlap = left
        .iter()
        .filter(|(id, _)| right.iter().any(|(other, _)| other == id))
        .count();
    1.0 - overlap as f64 / left.len().max(1) as f64
}

fn clear_winner(hnsw: &[(String, f64)], bm: &[(String, f64)]) -> bool {
    if hnsw.len() < 2 {
        return false;
    }
    let mut scores: Vec<f64> = hnsw.iter().map(|(_, score)| *score).collect();
    scores.sort_by(|a, b| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
    let top = scores[0];
    let rest = if scores.len() > 3 {
        scores[3]
    } else {
        *scores.last().unwrap()
    };
    if top - rest <= 0.15 {
        return false;
    }
    let winner = hnsw
        .iter()
        .max_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
        .map(|(id, _)| id.as_str())
        .unwrap_or("");
    let mut ranked = bm.to_vec();
    ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    !ranked.iter().take(5).any(|(id, _)| id == winner)
}

fn search_alpha(query: &str, base: f64) -> f64 {
    let mut signals = 0;
    if query.contains('_') {
        signals += 1;
    }
    if has_camel(query) {
        signals += 1;
    }
    if query.chars().any(|ch| "{}[]();".contains(ch)) {
        signals += 1;
    }
    if has_keyword(query) {
        signals += 1;
    }
    if query.contains('.') && !query.ends_with('.') {
        signals += 1;
    }
    if signals >= 3 {
        (base - 0.3).max(0.3)
    } else if signals >= 1 {
        (base - 0.15).max(0.3)
    } else {
        (base - 0.40).max(0.08)
    }
}

fn has_camel(query: &str) -> bool {
    let chars: Vec<char> = query.chars().collect();
    chars.windows(3).any(|window| {
        window[0].is_ascii_uppercase()
            && window[1].is_ascii_lowercase()
            && window[2].is_ascii_uppercase()
    })
}

fn has_keyword(query: &str) -> bool {
    query
        .split(|ch: char| !ch.is_ascii_alphanumeric() && ch != '_')
        .any(|word| {
            matches!(
                word,
                "def" | "class" | "function" | "import" | "const" | "let" | "var" | "fn" | "pub"
            )
        })
}

pub fn text_matches(pattern: &str, text: &str) -> Result<Vec<(usize, usize)>, String> {
    if pattern.is_empty() {
        return Ok(Vec::new());
    }
    let regex = crate::pyre::Regex::compile_linear(pattern)
        .map_err(|error| format!("{}: {}", error.code, error.message))?;
    Ok(regex
        .find_iter(text)
        .into_iter()
        .map(|hit| (hit.start, hit.end))
        .collect())
}
