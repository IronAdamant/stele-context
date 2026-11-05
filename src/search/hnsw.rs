//! HNSW graph with the same rules as 1.5.2 `index.py`.
//!
//! Levels come from `random() < 1/ln(M)`. Two processes therefore report
//! different `max_level` and `avg_connections` for the same files. The
//! counters are the graph, not constants.

use std::collections::{HashMap, HashSet};

const M: usize = 16;
const M0: usize = 32;
const EF_CONSTRUCTION: usize = 200;
const EF_SEARCH: usize = 50;

#[derive(Clone, Debug)]
struct Node {
    vector: Vec<f32>,
    level: usize,
    connections: Vec<HashSet<String>>,
}

#[derive(Clone, Debug)]
pub struct Hnsw {
    nodes: HashMap<String, Node>,
    entry: Option<String>,
    max_level: usize,
    ml: f64,
    rng: u64,
}

impl Default for Hnsw {
    fn default() -> Hnsw {
        Hnsw::new()
    }
}

impl Hnsw {
    pub fn new() -> Hnsw {
        let seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos() as u64)
            .unwrap_or(0x9E37_79B9_7F4A_7C15);
        Hnsw {
            nodes: HashMap::new(),
            entry: None,
            max_level: 0,
            ml: 1.0 / (M as f64).ln(),
            rng: seed | 1,
        }
    }

    pub fn clear(&mut self) {
        let rng = self.rng;
        *self = Hnsw::new();
        self.rng = rng;
    }

    pub fn max_level(&self) -> i64 {
        if self.nodes.is_empty() {
            0
        } else {
            self.max_level as i64
        }
    }

    pub fn avg_connections(&self) -> f64 {
        if self.nodes.is_empty() {
            return 0.0;
        }
        let total: usize = self
            .nodes
            .values()
            .map(|node| node.connections.iter().map(HashSet::len).sum::<usize>())
            .sum();
        total as f64 / self.nodes.len() as f64
    }

    /// `search([0.1] * 128, k=10)` then max(score) - min(score), rounded to 4 dp.
    pub fn probe_span(&self) -> f64 {
        let probe = vec![0.1f32; 128];
        let scores = self.search(&probe, 10);
        if scores.len() < 2 {
            return 0.0;
        }
        let mut lo = scores[0].1;
        let mut hi = scores[0].1;
        for (_, score) in &scores {
            lo = lo.min(*score);
            hi = hi.max(*score);
        }
        ((hi - lo) * 10_000.0).round() / 10_000.0
    }

    pub fn insert(&mut self, id: &str, vector: Vec<f32>) {
        if let Some(node) = self.nodes.get_mut(id) {
            node.vector = vector;
            return;
        }
        let level = self.random_level();
        let mut connections = Vec::new();
        connections.resize_with(level + 1, HashSet::new);
        self.nodes.insert(
            id.to_string(),
            Node {
                vector,
                level,
                connections,
            },
        );
        if self.entry.is_none() {
            self.entry = Some(id.to_string());
            self.max_level = level;
            return;
        }
        let query = self
            .nodes
            .get(id)
            .map(|node| node.vector.clone())
            .unwrap_or_default();
        let mut current = self.entry.clone().unwrap_or_default();
        let top = self.max_level;
        if top > level {
            for lc in (level + 1..=top).rev() {
                let found = self.search_layer(&query, &[current.clone()], 1, lc);
                if let Some((_, next)) = found.first() {
                    current = next.clone();
                }
            }
        }
        let bottom = level.min(top);
        for lc in (0..=bottom).rev() {
            let found = self.search_layer(&query, &[current.clone()], EF_CONSTRUCTION, lc);
            let limit = if lc == 0 { M0 } else { M };
            let neighbors = select_neighbors(id, &found, limit);
            for neighbor_id in &neighbors {
                if let Some(node) = self.nodes.get_mut(id) {
                    if lc < node.connections.len() {
                        node.connections[lc].insert(neighbor_id.clone());
                    }
                }
                if let Some(node) = self.nodes.get_mut(neighbor_id) {
                    if lc < node.connections.len() {
                        node.connections[lc].insert(id.to_string());
                    }
                }
                self.prune(neighbor_id, lc);
            }
            if let Some((_, next)) = found.first() {
                current = next.clone();
            }
        }
        if level > self.max_level {
            self.entry = Some(id.to_string());
            self.max_level = level;
        }
    }

    pub fn remove(&mut self, id: &str) {
        let Some(node) = self.nodes.remove(id) else {
            return;
        };
        for (level, neighbors) in node.connections.iter().enumerate() {
            let neighbor_list: Vec<String> = neighbors
                .iter()
                .filter(|name| self.nodes.contains_key(name.as_str()))
                .cloned()
                .collect();
            let max_conn = if level == 0 { M0 } else { M };
            for neighbor_id in &neighbor_list {
                if let Some(neighbor) = self.nodes.get_mut(neighbor_id) {
                    if level < neighbor.connections.len() {
                        neighbor.connections[level].remove(id);
                    }
                }
            }
            for neighbor_id in &neighbor_list {
                let Some(neighbor) = self.nodes.get(neighbor_id) else {
                    continue;
                };
                if level >= neighbor.connections.len() {
                    continue;
                }
                if neighbor.connections[level].len() >= max_conn {
                    continue;
                }
                let extras: Vec<String> = neighbor_list
                    .iter()
                    .filter(|other| *other != neighbor_id)
                    .cloned()
                    .collect();
                let mut linked = Vec::new();
                if let Some(neighbor) = self.nodes.get_mut(neighbor_id) {
                    for other in extras {
                        if neighbor.connections[level].len() >= max_conn {
                            break;
                        }
                        if neighbor.connections[level].insert(other.clone()) {
                            linked.push(other);
                        }
                    }
                }
                for other in linked {
                    if let Some(slot) = self.nodes.get_mut(&other) {
                        if level < slot.connections.len() {
                            slot.connections[level].insert(neighbor_id.clone());
                        }
                    }
                }
            }
        }
        if self.entry.as_deref() == Some(id) {
            self.entry = self
                .nodes
                .iter()
                .max_by_key(|(_, node)| node.level)
                .map(|(name, _)| name.clone());
            self.max_level = self
                .entry
                .as_ref()
                .and_then(|name| self.nodes.get(name))
                .map(|node| node.level)
                .unwrap_or(0);
        }
    }

    fn prune(&mut self, id: &str, level: usize) {
        let max_conn = if level == 0 { M0 } else { M };
        let Some(node) = self.nodes.get(id) else {
            return;
        };
        if level >= node.connections.len() || node.connections[level].len() <= max_conn {
            return;
        }
        let vector = node.vector.clone();
        let mut scored = Vec::new();
        for neighbor_id in &node.connections[level] {
            if let Some(neighbor) = self.nodes.get(neighbor_id) {
                scored.push((distance(&vector, &neighbor.vector), neighbor_id.clone()));
            }
        }
        let kept = select_neighbors(id, &scored, max_conn);
        let removed: Vec<String> = self
            .nodes
            .get(id)
            .map(|node| {
                node.connections[level]
                    .iter()
                    .filter(|name| !kept.iter().any(|keep| keep == *name))
                    .cloned()
                    .collect()
            })
            .unwrap_or_default();
        if let Some(node) = self.nodes.get_mut(id) {
            node.connections[level] = kept.iter().cloned().collect();
        }
        for other in removed {
            if let Some(node) = self.nodes.get_mut(&other) {
                if level < node.connections.len() {
                    node.connections[level].remove(id);
                }
            }
        }
    }

    fn search(&self, query: &[f32], k: usize) -> Vec<(String, f64)> {
        if self.nodes.is_empty() || self.entry.is_none() {
            return Vec::new();
        }
        let ef = adaptive_ef(self.nodes.len(), k);
        let mut current = self.entry.clone().unwrap_or_default();
        if self.max_level > 0 {
            for lc in (1..=self.max_level).rev() {
                let found = self.search_layer(query, &[current.clone()], 1, lc);
                if let Some((_, next)) = found.first() {
                    current = next.clone();
                }
            }
        }
        let found = self.search_layer(query, &[current], ef.max(k), 0);
        let query_norm = norm(query);
        let mut scores = Vec::new();
        for (_, id) in found.into_iter().take(k) {
            if let Some(node) = self.nodes.get(&id) {
                scores.push((id, cosine(query, &node.vector, query_norm)));
            }
        }
        scores.sort_by(|left, right| {
            right
                .1
                .partial_cmp(&left.1)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        scores.truncate(k);
        scores
    }

    fn search_layer(
        &self,
        query: &[f32],
        entry_points: &[String],
        ef: usize,
        level: usize,
    ) -> Vec<(f64, String)> {
        let mut visited = HashSet::new();
        let mut candidates: Vec<(f64, String)> = Vec::new();
        for ep in entry_points {
            if visited.insert(ep.clone()) {
                if let Some(node) = self.nodes.get(ep) {
                    candidates.push((distance(query, &node.vector), ep.clone()));
                }
            }
        }
        candidates.sort_by(|left, right| {
            left.0
                .partial_cmp(&right.0)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let mut results = candidates.clone();
        while let Some((cand_dist, cand_id)) = candidates.first().cloned() {
            candidates.remove(0);
            if let Some((worst, _)) = results.iter().max_by(|left, right| {
                left.0
                    .partial_cmp(&right.0)
                    .unwrap_or(std::cmp::Ordering::Equal)
            }) {
                if cand_dist > *worst && results.len() >= ef {
                    break;
                }
            }
            let neighbors: Vec<String> = self
                .nodes
                .get(&cand_id)
                .and_then(|node| node.connections.get(level))
                .map(|set| set.iter().cloned().collect())
                .unwrap_or_default();
            for neighbor_id in neighbors {
                if !visited.insert(neighbor_id.clone()) {
                    continue;
                }
                let Some(neighbor) = self.nodes.get(&neighbor_id) else {
                    continue;
                };
                let dist = distance(query, &neighbor.vector);
                let worst = results.iter().map(|(value, _)| *value).fold(0.0, f64::max);
                if results.len() < ef || dist < worst {
                    candidates.push((dist, neighbor_id.clone()));
                    results.push((dist, neighbor_id));
                    if results.len() > ef {
                        if let Some(index) = results.iter().enumerate().max_by(|left, right| {
                            left.1
                                 .0
                                .partial_cmp(&right.1 .0)
                                .unwrap_or(std::cmp::Ordering::Equal)
                        }) {
                            results.remove(index.0);
                        }
                    }
                    candidates.sort_by(|left, right| {
                        left.0
                            .partial_cmp(&right.0)
                            .unwrap_or(std::cmp::Ordering::Equal)
                    });
                }
            }
        }
        results.sort_by(|left, right| {
            left.0
                .partial_cmp(&right.0)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        results
    }

    fn random_level(&mut self) -> usize {
        let mut level = 0usize;
        while self.next_unit() < self.ml && level < 32 {
            level += 1;
        }
        level
    }

    fn next_unit(&mut self) -> f64 {
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 7;
        self.rng ^= self.rng << 17;
        let bits = self.rng >> 11;
        (bits as f64) / ((1u64 << 53) as f64)
    }
}

fn select_neighbors(id: &str, candidates: &[(f64, String)], limit: usize) -> Vec<String> {
    let mut rows = candidates.to_vec();
    rows.sort_by(|left, right| {
        left.0
            .partial_cmp(&right.0)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut selected = Vec::new();
    for (_, cand) in rows {
        if selected.len() >= limit {
            break;
        }
        if cand != id && !selected.iter().any(|have| have == &cand) {
            selected.push(cand);
        }
    }
    selected
}

fn distance(left: &[f32], right: &[f32]) -> f64 {
    let mut dot = 0.0f64;
    for (a, b) in left.iter().zip(right) {
        dot += f64::from(*a) * f64::from(*b);
    }
    1.0 - dot
}

fn norm(values: &[f32]) -> f64 {
    values
        .iter()
        .map(|value| f64::from(*value) * f64::from(*value))
        .sum::<f64>()
        .sqrt()
}

fn cosine(query: &[f32], vector: &[f32], query_norm: f64) -> f64 {
    let other = norm(vector);
    if query_norm == 0.0 || other == 0.0 {
        return 0.0;
    }
    let mut dot = 0.0f64;
    for (a, b) in query.iter().zip(vector) {
        dot += f64::from(*a) * f64::from(*b);
    }
    dot / (query_norm * other)
}

fn adaptive_ef(nodes: usize, k: usize) -> usize {
    if nodes < 100 {
        k.max(10)
    } else if nodes < 1000 {
        k.max(EF_SEARCH)
    } else if nodes < 10_000 {
        k.max(EF_SEARCH * 2)
    } else {
        k.max(EF_SEARCH * 4)
    }
}
