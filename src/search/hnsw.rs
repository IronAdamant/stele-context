//! HNSW graph with the same rules as 1.5.2 `index.py`.
//!
//! Levels come from `random() < 1/ln(M)`. Two processes therefore report
//! different `max_level` and `avg_connections` for the same files, as two
//! 1.5.2 processes do. The counters are the graph, not constants.
//!
//! Nodes live in a vector and refer to each other by index; the layer search
//! uses binary heaps ordered like 1.5.2's `heapq` tuples (distance, then id),
//! so a doctor call on a few thousand chunks builds the graph in
//! milliseconds.

use std::cmp::{Ordering, Reverse};
use std::collections::{BinaryHeap, HashMap};

const M: usize = 16;
const M0: usize = 32;
const EF_CONSTRUCTION: usize = 200;
const EF_SEARCH: usize = 50;

#[derive(Clone, Debug)]
struct Node {
    id: String,
    vector: Vec<f32>,
    level: usize,
    /// One neighbour list per level, `0..=level`.
    connections: Vec<Vec<usize>>,
    live: bool,
}

#[derive(Clone, Debug)]
pub struct Hnsw {
    nodes: Vec<Node>,
    by_id: HashMap<String, usize>,
    entry: Option<usize>,
    max_level: usize,
    ml: f64,
    rng: u64,
    /// Per-node "seen in search N" stamps; a search bumps `epoch` instead
    /// of allocating a visited set.
    seen: std::cell::RefCell<(Vec<u32>, u32)>,
}

impl Default for Hnsw {
    fn default() -> Hnsw {
        Hnsw::new()
    }
}

/// `f64` with a total order, for heap keys.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Dist(f64);

impl Eq for Dist {}

impl PartialOrd for Dist {
    fn partial_cmp(&self, other: &Dist) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Dist {
    fn cmp(&self, other: &Dist) -> Ordering {
        self.0.total_cmp(&other.0)
    }
}

impl Hnsw {
    pub fn new() -> Hnsw {
        let seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos() as u64)
            .unwrap_or(0x9E37_79B9_7F4A_7C15);
        Hnsw {
            nodes: Vec::new(),
            by_id: HashMap::new(),
            entry: None,
            max_level: 0,
            ml: 1.0 / (M as f64).ln(),
            rng: seed | 1,
            seen: std::cell::RefCell::new((Vec::new(), 0)),
        }
    }

    pub fn clear(&mut self) {
        let rng = self.rng;
        *self = Hnsw::new();
        self.rng = rng;
    }

    pub fn max_level(&self) -> i64 {
        if self.by_id.is_empty() {
            0
        } else {
            self.max_level as i64
        }
    }

    pub fn avg_connections(&self) -> f64 {
        if self.by_id.is_empty() {
            return 0.0;
        }
        let total: usize = self
            .nodes
            .iter()
            .filter(|node| node.live)
            .map(|node| node.connections.iter().map(Vec::len).sum::<usize>())
            .sum();
        total as f64 / self.by_id.len() as f64
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
        if let Some(&index) = self.by_id.get(id) {
            self.nodes[index].vector = vector;
            return;
        }
        let level = self.random_level();
        let index = self.nodes.len();
        self.nodes.push(Node {
            id: id.to_string(),
            vector,
            level,
            connections: vec![Vec::new(); level + 1],
            live: true,
        });
        self.by_id.insert(id.to_string(), index);
        let Some(entry) = self.entry else {
            self.entry = Some(index);
            self.max_level = level;
            return;
        };
        let query = self.nodes[index].vector.clone();
        let mut current = entry;
        let top = self.max_level;
        if top > level {
            for lc in (level + 1..=top).rev() {
                if let Some((_, next)) = self.search_layer(&query, current, 1, lc).first() {
                    current = *next;
                }
            }
        }
        for lc in (0..=level.min(top)).rev() {
            let found = self.search_layer(&query, current, EF_CONSTRUCTION, lc);
            let limit = if lc == 0 { M0 } else { M };
            for neighbor in self.select_neighbors(index, &found, limit) {
                link(&mut self.nodes[index].connections[lc], neighbor);
                if lc < self.nodes[neighbor].connections.len() {
                    link(&mut self.nodes[neighbor].connections[lc], index);
                }
                self.prune(neighbor, lc);
            }
            if let Some((_, next)) = found.first() {
                current = *next;
            }
        }
        if level > self.max_level {
            self.entry = Some(index);
            self.max_level = level;
        }
    }

    pub fn remove(&mut self, id: &str) {
        let Some(index) = self.by_id.remove(id) else {
            return;
        };
        let levels = self.nodes[index].connections.clone();
        for (level, neighbors) in levels.iter().enumerate() {
            let neighbors: Vec<usize> = neighbors
                .iter()
                .copied()
                .filter(|other| self.nodes[*other].live)
                .collect();
            let max_conn = if level == 0 { M0 } else { M };
            for &neighbor in &neighbors {
                if level < self.nodes[neighbor].connections.len() {
                    self.nodes[neighbor].connections[level].retain(|other| *other != index);
                }
            }
            for &neighbor in &neighbors {
                if level >= self.nodes[neighbor].connections.len()
                    || self.nodes[neighbor].connections[level].len() >= max_conn
                {
                    continue;
                }
                for &other in &neighbors {
                    if other == neighbor || self.nodes[neighbor].connections[level].contains(&other)
                    {
                        continue;
                    }
                    if self.nodes[neighbor].connections[level].len() >= max_conn {
                        break;
                    }
                    self.nodes[neighbor].connections[level].push(other);
                    if level < self.nodes[other].connections.len() {
                        link(&mut self.nodes[other].connections[level], neighbor);
                    }
                }
            }
        }
        self.nodes[index].live = false;
        self.nodes[index].connections.clear();
        if self.entry == Some(index) {
            self.entry = self
                .nodes
                .iter()
                .enumerate()
                .filter(|(_, node)| node.live)
                .max_by_key(|(_, node)| node.level)
                .map(|(at, _)| at);
            self.max_level = self.entry.map(|at| self.nodes[at].level).unwrap_or(0);
        }
    }

    /// Keep a neighbour's list at the level cap: its closest `max_conn`.
    fn prune(&mut self, index: usize, level: usize) {
        let max_conn = if level == 0 { M0 } else { M };
        if level >= self.nodes[index].connections.len()
            || self.nodes[index].connections[level].len() <= max_conn
        {
            return;
        }
        let scored: Vec<(f64, usize)> = self.nodes[index].connections[level]
            .iter()
            .map(|other| {
                (
                    distance(&self.nodes[index].vector, &self.nodes[*other].vector),
                    *other,
                )
            })
            .collect();
        let kept = self.select_neighbors(index, &scored, max_conn);
        let removed: Vec<usize> = self.nodes[index].connections[level]
            .iter()
            .copied()
            .filter(|other| !kept.contains(other))
            .collect();
        self.nodes[index].connections[level] = kept;
        for other in removed {
            if level < self.nodes[other].connections.len() {
                self.nodes[other].connections[level].retain(|back| *back != index);
            }
        }
    }

    fn search(&self, query: &[f32], k: usize) -> Vec<(String, f64)> {
        let Some(mut current) = self.entry else {
            return Vec::new();
        };
        let ef = adaptive_ef(self.by_id.len(), k);
        for lc in (1..=self.max_level).rev() {
            if let Some((_, next)) = self.search_layer(query, current, 1, lc).first() {
                current = *next;
            }
        }
        let found = self.search_layer(query, current, ef.max(k), 0);
        let query_norm = norm(query);
        let mut scores: Vec<(String, f64)> = found
            .into_iter()
            .take(k)
            .map(|(_, at)| {
                let node = &self.nodes[at];
                (node.id.clone(), cosine(query, &node.vector, query_norm))
            })
            .collect();
        scores.sort_by(|left, right| right.1.partial_cmp(&left.1).unwrap_or(Ordering::Equal));
        scores.truncate(k);
        scores
    }

    /// 1.5.2 `_search_layer`: `(distance, node)` pairs, closest first. Ties
    /// order by node id, as Python's tuples do.
    fn search_layer(
        &self,
        query: &[f32],
        entry: usize,
        ef: usize,
        level: usize,
    ) -> Vec<(f64, usize)> {
        let mut seen = self.seen.borrow_mut();
        let (stamps, epoch) = &mut *seen;
        if stamps.len() < self.nodes.len() {
            stamps.resize(self.nodes.len(), 0);
        }
        *epoch = epoch.wrapping_add(1);
        if *epoch == 0 {
            stamps.iter_mut().for_each(|stamp| *stamp = 0);
            *epoch = 1;
        }
        let epoch = *epoch;
        stamps[entry] = epoch;
        let first = distance(query, &self.nodes[entry].vector);
        let key = |at: usize| self.nodes[at].id.as_str();
        // Min-heap: closest candidate first.
        let mut candidates: BinaryHeap<Reverse<(Dist, &str, usize)>> = BinaryHeap::new();
        // Max-heap: worst result on top; among equal distances the smallest
        // id is the one dropped, like `heappop` on `(-dist, id)`.
        let mut results: BinaryHeap<(Dist, Reverse<&str>, usize)> = BinaryHeap::new();
        candidates.push(Reverse((Dist(first), key(entry), entry)));
        results.push((Dist(first), Reverse(key(entry)), entry));
        while let Some(Reverse((Dist(cand_dist), _, cand))) = candidates.pop() {
            if let Some((Dist(worst), _, _)) = results.peek() {
                if cand_dist > *worst {
                    break;
                }
            }
            let Some(neighbors) = self.nodes[cand].connections.get(level) else {
                continue;
            };
            for &neighbor in neighbors {
                if stamps[neighbor] == epoch || !self.nodes[neighbor].live {
                    continue;
                }
                stamps[neighbor] = epoch;
                let dist = distance(query, &self.nodes[neighbor].vector);
                let worst = results.peek().map(|(Dist(d), _, _)| *d).unwrap_or(f64::MAX);
                if results.len() < ef || dist < worst {
                    candidates.push(Reverse((Dist(dist), key(neighbor), neighbor)));
                    results.push((Dist(dist), Reverse(key(neighbor)), neighbor));
                    if results.len() > ef {
                        results.pop();
                    }
                }
            }
        }
        let mut out: Vec<(f64, usize)> = results
            .into_iter()
            .map(|(Dist(dist), _, at)| (dist, at))
            .collect();
        out.sort_by(|left, right| {
            left.0
                .total_cmp(&right.0)
                .then_with(|| self.nodes[left.1].id.cmp(&self.nodes[right.1].id))
        });
        out
    }

    /// 1.5.2 `_select_neighbors`: the `limit` closest, skipping the node.
    fn select_neighbors(
        &self,
        index: usize,
        candidates: &[(f64, usize)],
        limit: usize,
    ) -> Vec<usize> {
        let mut rows = candidates.to_vec();
        rows.sort_by(|left, right| left.0.total_cmp(&right.0));
        let mut selected = Vec::new();
        for (_, cand) in rows {
            if selected.len() >= limit {
                break;
            }
            if cand != index && !selected.contains(&cand) {
                selected.push(cand);
            }
        }
        selected
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

/// Python `set.add` on a neighbour list.
fn link(list: &mut Vec<usize>, other: usize) {
    if !list.contains(&other) {
        list.push(other);
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    fn vector(seed: usize) -> Vec<f32> {
        let raw: Vec<f32> = (0..128)
            .map(|lane| (((seed * 31 + lane * 17) % 97) as f32 / 97.0) - 0.5)
            .collect();
        let n = raw.iter().map(|v| v * v).sum::<f32>().sqrt();
        raw.into_iter().map(|v| v / n).collect()
    }

    #[test]
    fn exact_on_a_small_graph_and_fast_on_a_large_one() {
        let mut graph = Hnsw::new();
        for seed in 0..60 {
            graph.insert(&format!("c{seed}"), vector(seed));
        }
        // Under ef the layer search sees every node, so the probe's top ten
        // are the exact top ten.
        let found = graph.search(&[0.1f32; 128], 10);
        let mut exact: Vec<(String, f64)> = (0..60)
            .map(|seed| {
                let v = vector(seed);
                (
                    format!("c{seed}"),
                    cosine(&[0.1f32; 128], &v, norm(&[0.1f32; 128])),
                )
            })
            .collect();
        exact.sort_by(|a, b| b.1.total_cmp(&a.1));
        let ids: Vec<&String> = found.iter().map(|(id, _)| id).collect();
        let want: Vec<&String> = exact.iter().take(10).map(|(id, _)| id).collect();
        assert_eq!(ids, want);
        graph.remove("c3");
        assert!(graph
            .search(&[0.1f32; 128], 60)
            .iter()
            .all(|(id, _)| id != "c3"));
        let started = std::time::Instant::now();
        let mut large = Hnsw::new();
        for seed in 0..3000 {
            large.insert(&format!("n{seed}"), vector(seed));
        }
        assert!(large.avg_connections() > 0.0);
        assert!(started.elapsed().as_secs() < 20, "graph build is too slow");
    }
}
