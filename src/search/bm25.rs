//! Okapi BM25, k1 = 1.5, b = 0.75, with Porter stemming.

use super::stem::{split_identifier, stem};
use std::collections::HashMap;

#[derive(Clone, Debug)]
pub struct Bm25 {
    k1: f64,
    b: f64,
    doc_freqs: HashMap<String, u32>,
    doc_lengths: HashMap<String, usize>,
    term_freqs: HashMap<String, HashMap<String, u32>>,
    avg_dl: f64,
    stem_cache: HashMap<String, String>,
}

impl Bm25 {
    pub fn new() -> Bm25 {
        Bm25 {
            k1: 1.5,
            b: 0.75,
            doc_freqs: HashMap::new(),
            doc_lengths: HashMap::new(),
            term_freqs: HashMap::new(),
            avg_dl: 0.0,
            stem_cache: HashMap::new(),
        }
    }

    pub fn add(&mut self, id: &str, text: &str) {
        if self.term_freqs.contains_key(id) {
            self.remove(id);
        }
        let terms = self.tokenize(text);
        let mut freqs = HashMap::new();
        for term in &terms {
            *freqs.entry(term.clone()).or_insert(0) += 1;
        }
        self.doc_lengths.insert(id.to_string(), terms.len());
        for term in freqs.keys() {
            *self.doc_freqs.entry(term.clone()).or_insert(0) += 1;
        }
        self.term_freqs.insert(id.to_string(), freqs);
        self.recompute_avg();
    }

    pub fn remove(&mut self, id: &str) {
        let Some(freqs) = self.term_freqs.remove(id) else {
            return;
        };
        for term in freqs.keys() {
            if let Some(count) = self.doc_freqs.get_mut(term) {
                *count = count.saturating_sub(1);
                if *count == 0 {
                    self.doc_freqs.remove(term);
                }
            }
        }
        self.doc_lengths.remove(id);
        self.recompute_avg();
    }

    pub fn search(&self, query: &str, top_k: usize) -> Vec<(String, f64)> {
        if self.doc_lengths.is_empty() || self.avg_dl == 0.0 || top_k == 0 {
            return Vec::new();
        }
        let terms = tokenize(query);
        if terms.is_empty() {
            return Vec::new();
        }
        let mut scored: Vec<(String, f64)> = self
            .term_freqs
            .keys()
            .filter(|id| {
                self.term_freqs[*id]
                    .keys()
                    .any(|term| terms.iter().any(|query| query == term))
            })
            .map(|id| (id.clone(), self.score_terms(&terms, id)))
            .collect();
        scored.sort_by(|left, right| {
            right
                .1
                .partial_cmp(&left.1)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(left.0.cmp(&right.0))
        });
        scored.truncate(top_k);
        scored
    }

    pub fn score(&self, query: &str, id: &str) -> f64 {
        // Queries are short. Stemming them does not use the document cache
        // because this method is shared. `search` tokenizes once.
        self.score_terms(&tokenize(query), id)
    }

    fn score_terms(&self, terms: &[String], id: &str) -> f64 {
        let Some(freqs) = self.term_freqs.get(id) else {
            return 0.0;
        };
        if self.avg_dl == 0.0 {
            return 0.0;
        }
        let dl = *self.doc_lengths.get(id).unwrap_or(&0) as f64;
        let n_docs = self.doc_lengths.len() as f64;
        let mut total = 0.0;
        for term in terms {
            let n = *self.doc_freqs.get(term).unwrap_or(&0) as f64;
            if n == 0.0 {
                continue;
            }
            let idf = ((n_docs - n + 0.5) / (n + 0.5) + 1.0).ln();
            let tf = *freqs.get(term).unwrap_or(&0) as f64;
            let numerator = tf * (self.k1 + 1.0);
            let denominator = tf + self.k1 * (1.0 - self.b + self.b * dl / self.avg_dl);
            total += idf * numerator / denominator;
        }
        total
    }

    fn recompute_avg(&mut self) {
        let n = self.doc_lengths.len();
        if n == 0 {
            self.avg_dl = 0.0;
        } else {
            self.avg_dl = self.doc_lengths.values().sum::<usize>() as f64 / n as f64;
        }
    }
}

impl Default for Bm25 {
    fn default() -> Self {
        Self::new()
    }
}

impl Bm25 {
    fn tokenize(&mut self, text: &str) -> Vec<String> {
        let mut tokens = Vec::new();
        for word in ascii_words(text) {
            for part in split_identifier(&word) {
                if part.chars().count() <= 1 {
                    continue;
                }
                if let Some(cached) = self.stem_cache.get(&part) {
                    tokens.push(cached.clone());
                    continue;
                }
                let stemmed = stem(&part);
                self.stem_cache.insert(part, stemmed.clone());
                tokens.push(stemmed);
            }
        }
        tokens
    }
}

pub fn tokenize(text: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    for word in ascii_words(text) {
        for part in split_identifier(&word) {
            if part.chars().count() > 1 {
                tokens.push(stem(&part));
            }
        }
    }
    tokens
}

fn ascii_words(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut raw = Vec::new();
    let mut index = 0usize;
    while index < chars.len() {
        let prev = index > 0 && is_ascii_word(chars[index - 1]);
        if !prev && is_ascii_start(chars[index]) {
            let start = index;
            index += 1;
            while index < chars.len() && is_ascii_word(chars[index]) {
                index += 1;
            }
            let next = index < chars.len() && is_ascii_word(chars[index]);
            if !next {
                raw.push(chars[start..index].iter().collect::<String>());
            }
        } else {
            index += 1;
        }
    }
    raw
}

fn is_ascii_word(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ch == '_'
}

fn is_ascii_start(ch: char) -> bool {
    ch.is_ascii_alphabetic() || ch == '_'
}
