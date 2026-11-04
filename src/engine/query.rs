//! Search, symbol graph, sessions, locks, and annotations.

use super::{now_secs, Engine};
use crate::hash::{hex, sha256};
use crate::json::Value;
use crate::search::{self, VectorDoc};
use crate::store::{Annotation, Dynamic, Session};
use std::collections::{HashMap, HashSet, VecDeque};

fn cosine_stored(query: &[f64], stored: &[f32]) -> f64 {
    // 1.5.2 scores the f64 query signature against the f32 vector on disk.
    let mut dot = 0.0f64;
    let mut left_norm = 0.0f64;
    let mut right_norm = 0.0f64;
    for (a, b) in query.iter().zip(stored.iter()) {
        let b = f64::from(*b);
        dot += a * b;
        left_norm += a * a;
        right_norm += b * b;
    }
    if left_norm > 0.0 && right_norm > 0.0 {
        dot / (left_norm.sqrt() * right_norm.sqrt())
    } else {
        0.0
    }
}

impl Engine {
    pub fn search(&mut self, query: &str, mode: &str, top_k: usize) -> Value {
        self.ensure_bm25();
        let top_k = top_k.max(1);
        let hits = if mode == "hybrid" {
            self.store.ensure_sigs();
            let docs: Vec<VectorDoc> = self
                .store
                .state
                .chunks
                .iter()
                .enumerate()
                .map(|(index, chunk)| VectorDoc {
                    id: &chunk.id,
                    values: self
                        .store
                        .state
                        .sigs
                        .get(index)
                        .map(|sig| sig.as_slice())
                        .unwrap_or(&[]),
                    tier2: chunk.summary.is_some(),
                })
                .collect();
            search::hybrid_alpha(&self.bm25, &docs, query, top_k, self.search_alpha)
        } else {
            search::keyword(&self.bm25, query, top_k)
        };
        let mut rows = self.rows(&hits, mode == "hybrid");
        apply_proximity(&mut rows, query);
        Value::Array(rows)
    }

    pub fn search_text(&mut self, pattern: &str) -> Result<Value, String> {
        self.text_search(pattern, false, None, 50, None, false)
    }

    pub fn text_search(
        &mut self,
        pattern: &str,
        regex: bool,
        document: Option<&str>,
        limit: usize,
        session: Option<&str>,
        working_tree: bool,
    ) -> Result<Value, String> {
        if working_tree {
            self.index_working_tree();
        }
        let mut rows = Vec::new();
        let limit = limit.max(1);
        for chunk in &self.store.state.chunks {
            if document.is_some_and(|path| chunk.path != path) {
                continue;
            }
            let hits = if regex {
                search::text_matches(pattern, &chunk.content)?
            } else {
                literal_hits(pattern, &chunk.content)
            };
            if hits.is_empty() {
                continue;
            }
            let matches: Vec<Value> = hits
                .iter()
                .take(10)
                .map(|(start, end)| {
                    Value::object(vec![
                        ("start", Value::Int(*start as i64)),
                        ("end", Value::Int(*end as i64)),
                        (
                            "text",
                            Value::string(slice_chars(&chunk.content, *start, *end)),
                        ),
                    ])
                })
                .collect();
            let preview: String = chunk.content.chars().take(200).collect();
            rows.push(Value::object(vec![
                ("chunk_id", Value::string(&chunk.id)),
                ("document_path", Value::string(&chunk.path)),
                ("match_count", Value::Int(hits.len() as i64)),
                ("matches", Value::Array(matches)),
                ("content_preview", Value::string(preview)),
                ("token_count", Value::Int(chunk.tokens as i64)),
            ]));
            if rows.len() >= limit {
                break;
            }
        }
        let files = {
            let mut names = Vec::new();
            for row in &rows {
                let path = crate::store::string_field(row, "document_path");
                if !names.iter().any(|have: &String| have == &path) {
                    names.push(path);
                }
            }
            names.sort();
            names
        };
        let match_count: i64 = rows
            .iter()
            .map(|row| crate::store::int_field(row, "match_count"))
            .sum();
        if let Some(session) = session {
            let checked = self.store.state.documents.len() as i64;
            let matched = files.len() as i64;
            self.record_search_row(session, pattern, "search_text", checked, matched);
            let files_owned = files.clone();
            if !files_owned.is_empty() {
                let _ = self.index(&files_owned, false);
            }
        }
        Ok(Value::object(vec![
            ("pattern", Value::string(pattern)),
            ("regex", Value::Bool(regex)),
            ("match_count", Value::Int(match_count)),
            ("chunk_count", Value::Int(rows.len() as i64)),
            ("results", Value::Array(rows)),
            ("files_checked", Value::Int(files.len() as i64)),
            (
                "files_with_matches",
                Value::Array(files.into_iter().map(Value::String).collect()),
            ),
        ]))
    }

    pub fn agent_grep(&mut self, pattern: &str, session: Option<&str>) -> Result<Value, String> {
        self.grep_full(
            pattern, false, None, true, true, "file", 4000, true, 0, session, false,
        )
    }

    pub fn grep_full(
        &mut self,
        pattern: &str,
        regex: bool,
        document: Option<&str>,
        classify: bool,
        include_scope: bool,
        group_by: &str,
        max_tokens: i64,
        deduplicate: bool,
        context_lines: i64,
        session: Option<&str>,
        working_tree: bool,
    ) -> Result<Value, String> {
        if working_tree {
            self.index_working_tree();
        }
        let found = self.text_search(pattern, regex, document, 10_000, None, false)?;
        let Value::Object(pairs) = &found else {
            return Ok(found);
        };
        let results = pairs
            .iter()
            .find(|(key, _)| key == "results")
            .and_then(|(_, value)| match value {
                Value::Array(rows) => Some(rows.clone()),
                _ => None,
            })
            .unwrap_or_default();
        let mut matches = Vec::new();
        for row in &results {
            let file = crate::store::string_field(row, "document_path");
            let chunk_id = crate::store::string_field(row, "chunk_id");
            let content = self
                .store
                .state
                .chunks
                .iter()
                .find(|chunk| chunk.id == chunk_id)
                .map(|chunk| chunk.content.clone())
                .unwrap_or_default();
            let hit_list = match object_field(row, "matches") {
                Some(Value::Array(items)) => items.clone(),
                _ => Vec::new(),
            };
            for hit in hit_list {
                let start = crate::store::int_field(&hit, "start") as usize;
                let line = content.chars().take(start).filter(|ch| *ch == '\n').count() + 1;
                let excerpt = line_at(&content, line.saturating_sub(1));
                let class = if classify {
                    classify_line(&excerpt)
                } else {
                    String::new()
                };
                let scope = if include_scope {
                    scope_for(self, &chunk_id, line as u32)
                } else {
                    String::new()
                };
                let _ = context_lines;
                matches.push((file.clone(), line, excerpt, class, scope));
            }
        }
        if deduplicate {
            let mut seen = std::collections::HashSet::new();
            matches.retain(|row| seen.insert(format!("{}:{}", row.0, row.2.trim())));
        }
        let total = matches.len() as i64;
        let key_of = |row: &(String, usize, String, String, String)| -> String {
            match group_by {
                "scope" => {
                    if row.4.is_empty() {
                        "(top-level)".to_string()
                    } else {
                        row.4.clone()
                    }
                }
                "classification" => {
                    if row.3.is_empty() {
                        "code".to_string()
                    } else {
                        row.3.clone()
                    }
                }
                _ => row.0.clone(),
            }
        };
        let mut groups: Vec<(String, Vec<Value>)> = Vec::new();
        for row in &matches {
            let key = key_of(row);
            let item = Value::object(vec![
                ("file", Value::string(&row.0)),
                ("line", Value::Int(row.1 as i64)),
                ("excerpt", Value::string(&row.2)),
                ("classification", Value::string(&row.3)),
                (
                    "scope",
                    if row.4.is_empty() {
                        Value::Null
                    } else {
                        Value::string(&row.4)
                    },
                ),
            ]);
            if let Some(group) = groups.iter_mut().find(|(name, _)| name == &key) {
                group.1.push(item);
            } else {
                groups.push((key, vec![item]));
            }
        }
        let budget = (max_tokens - 150).max(200);
        let mut shown = 0i64;
        let mut tokens = 0i64;
        let mut kept = Vec::new();
        for (key, rows) in groups {
            let cost = group_tokens(&key, &rows) as i64;
            if tokens + cost > budget && !kept.is_empty() {
                break;
            }
            tokens += cost;
            shown += rows.len() as i64;
            kept.push(Value::object(vec![
                ("key", Value::string(key)),
                ("count", Value::Int(rows.len() as i64)),
                ("matches", Value::Array(rows)),
            ]));
        }
        let files = {
            let mut names = Vec::new();
            for row in &matches {
                if !names.iter().any(|have: &String| have == &row.0) {
                    names.push(row.0.clone());
                }
            }
            names
        };
        if let Some(session) = session {
            self.record_search_row(
                session,
                pattern,
                "agent_grep",
                self.store.state.documents.len() as i64,
                files.len() as i64,
            );
            if !files.is_empty() {
                let _ = self.index(&files, false);
            }
            let _ = self.store.save("search", &Value::string(pattern));
        }
        let summary = format!(
            "{total} match{} in {} file{}",
            if total == 1 { "" } else { "es" },
            files.len(),
            if files.len() == 1 { "" } else { "s" }
        );
        Ok(Value::object(vec![
            ("summary", Value::string(summary)),
            ("groups", Value::Array(kept)),
            ("total_matches", Value::Int(total)),
            ("shown_matches", Value::Int(shown)),
            ("truncated", Value::Int((total - shown).max(0))),
            ("total_tokens", Value::Int(tokens)),
            ("files_checked", Value::Int(files.len() as i64)),
            (
                "files_with_matches",
                Value::Array(files.into_iter().map(Value::String).collect()),
            ),
        ]))
    }

    pub fn query(&mut self, text: &str) -> Result<Value, String> {
        let top_k = 10usize;
        let lexical = self.search(text, "keyword", top_k);
        let mut results = Vec::new();
        let mut seen = HashSet::new();
        if let Value::Array(rows) = lexical {
            for row in rows {
                let id = crate::store::string_field(&row, "chunk_id");
                if id.is_empty() || !seen.insert(id) {
                    continue;
                }
                results.push(compact_query_row(row, "semantic_search"));
            }
        }
        for ident in query_idents(text).into_iter().take(3) {
            let refs = self.find_references(&ident);
            for key in ["definitions", "references"] {
                let Some(Value::Array(rows)) = field_owned(&refs, key) else {
                    continue;
                };
                for row in rows {
                    let id = crate::store::string_field(&row, "chunk_id");
                    let path = crate::store::string_field(&row, "document_path");
                    if id.is_empty() || !seen.insert(id.clone()) {
                        continue;
                    }
                    let content = self
                        .store
                        .state
                        .chunks
                        .iter()
                        .find(|chunk| chunk.id == id)
                        .map(|chunk| chunk.content.chars().take(300).collect::<String>())
                        .unwrap_or_default();
                    results.push(Value::object(vec![
                        ("chunk_id", Value::string(&id)),
                        ("document_path", Value::string(path)),
                        ("content", Value::string(content)),
                        ("source", Value::string("symbol_graph")),
                        ("symbol", Value::string(&ident)),
                    ]));
                }
            }
        }
        if let Ok(grep) = self.agent_grep(text, None) {
            if let Some(Value::Array(groups)) = field_owned(&grep, "groups") {
                for group in groups {
                    let Some(Value::Array(matches)) = field_owned(&group, "matches") else {
                        continue;
                    };
                    for hit in matches {
                        let file = crate::store::string_field(&hit, "file");
                        if file.is_empty() {
                            continue;
                        }
                        let line = crate::store::int_field(&hit, "line") as usize;
                        let Some(chunk) = self.chunk_for_line(&file, line) else {
                            continue;
                        };
                        if !seen.insert(chunk.id.clone()) {
                            continue;
                        }
                        let excerpt = crate::store::string_field(&hit, "excerpt");
                        let content: String = excerpt.chars().take(300).collect();
                        results.push(Value::object(vec![
                            ("chunk_id", Value::string(&chunk.id)),
                            ("document_path", Value::string(&file)),
                            ("content", Value::string(content)),
                            ("source", Value::string("text_match")),
                        ]));
                    }
                }
            }
        }
        let semantic = results
            .iter()
            .filter(|row| crate::store::string_field(row, "source") == "semantic_search")
            .count() as i64;
        let symbols = results
            .iter()
            .filter(|row| crate::store::string_field(row, "source") == "symbol_graph")
            .count() as i64;
        let text_n = results
            .iter()
            .filter(|row| crate::store::string_field(row, "source") == "text_match")
            .count() as i64;
        let shown: Vec<Value> = results.into_iter().take(top_k).collect();
        let thin = shown.len() < 5;
        let mut out = vec![
            ("query".to_string(), Value::string(text)),
            ("results".to_string(), Value::Array(shown.clone())),
            (
                "sources".to_string(),
                Value::object(vec![
                    ("semantic_search", Value::Int(semantic)),
                    ("symbol_graph", Value::Int(symbols)),
                    ("text_match", Value::Int(text_n)),
                ]),
            ),
            (
                "applied_defaults".to_string(),
                Value::object(vec![
                    ("working_tree", Value::Bool(false)),
                    ("working_tree_auto", Value::Bool(false)),
                    ("working_tree_requested", Value::Bool(false)),
                    ("path_prefix", Value::Null),
                    ("path_prefix_auto", Value::Bool(false)),
                    ("path_prefix_requested", Value::Null),
                    ("search_mode", Value::string("keyword")),
                    ("compact", Value::Bool(true)),
                    ("session_id", Value::Null),
                ]),
            ),
        ];
        if shown.is_empty() {
            out.push((
                "suggested_next".to_string(),
                Value::Array(vec![
                    Value::object(vec![
                        ("action", Value::string("agent_grep")),
                        (
                            "detail",
                            Value::string(
                                "No hits — try agent_grep or search_text for exact tokens.",
                            ),
                        ),
                    ]),
                    Value::object(vec![
                        ("action", Value::string("detect_changes")),
                        (
                            "detail",
                            Value::string(
                                "If files changed recently, run detect_changes / re-index.",
                            ),
                        ),
                    ]),
                    Value::object(vec![
                        ("action", Value::string("enrich_tier2")),
                        (
                            "detail",
                            Value::string(
                                "For concept queries, check doctor.enrichment_preview and bulk_store_summaries.",
                            ),
                        ),
                    ]),
                ]),
            ));
        } else if thin {
            out.push((
                "suggested_next".to_string(),
                Value::Array(vec![
                    Value::object(vec![
                        ("action", Value::string("agent_grep")),
                        (
                            "detail",
                            Value::string(
                                "Sparse results — confirm with agent_grep on key identifiers.",
                            ),
                        ),
                    ]),
                    Value::object(vec![
                        ("action", Value::string("find_definition")),
                        (
                            "detail",
                            Value::string("For symbols, use find_definition / find_references."),
                        ),
                    ]),
                ]),
            ));
        }
        Ok(Value::Object(out))
    }

    pub fn find_definition(&self, name: &str) -> Value {
        let defs = self.definition_rows(name);
        let count = defs.len() as i64;
        Value::object(vec![
            ("symbol", Value::string(name)),
            ("definitions", Value::Array(defs)),
            ("count", Value::Int(count)),
            ("symbol_index", self.symbol_index()),
            (
                "guidance",
                if count == 0 {
                    Value::string(guidance_empty(&self.symbol_index()))
                } else {
                    Value::Null
                },
            ),
        ])
    }

    pub fn find_references(&self, name: &str) -> Value {
        let defs = self.reference_rows(name, true);
        let refs = self.reference_rows(name, false);
        let total = (defs.len() + refs.len()) as i64;
        let verdict = if defs.is_empty() && refs.is_empty() {
            "not_found"
        } else if !defs.is_empty() && refs.is_empty() {
            "unreferenced"
        } else if defs.is_empty() {
            "external"
        } else {
            "referenced"
        };
        Value::object(vec![
            ("symbol", Value::string(name)),
            ("verdict", Value::string(verdict)),
            ("definitions", Value::Array(defs)),
            ("references", Value::Array(refs)),
            ("total", Value::Int(total)),
            ("symbol_index", self.symbol_index()),
            (
                "guidance",
                if total == 0 {
                    Value::string(guidance_empty(&self.symbol_index()))
                } else {
                    Value::Null
                },
            ),
        ])
    }

    pub fn impact(&self, symbol: &str) -> Value {
        self.impact_radius(symbol, None, None, 2, false)
    }

    /// Compact blast radius. The shape matches 1.5.2 `impact_radius` with
    /// `compact=True` and `direction="dependents"`.
    pub fn impact_radius(
        &self,
        symbol: &str,
        document_path: Option<&str>,
        chunk_id: Option<&str>,
        depth: usize,
        summary_mode: bool,
    ) -> Value {
        let mut seeds: Vec<String> = Vec::new();
        let origin = if let Some(id) = chunk_id.filter(|id| !id.is_empty()) {
            seeds.push(id.to_string());
            id.to_string()
        } else if !symbol.is_empty() {
            seeds.extend(
                self.store
                    .state
                    .symbols
                    .iter()
                    .filter(|sym| sym.name == symbol && sym.role == "definition")
                    .map(|sym| sym.chunk_id.clone()),
            );
            symbol.to_string()
        } else if let Some(path) = document_path.filter(|path| !path.is_empty()) {
            let path = crate::walk::native_sep(path);
            seeds.extend(
                self.store
                    .state
                    .chunks
                    .iter()
                    .filter(|chunk| chunk.path == path)
                    .map(|chunk| chunk.id.clone()),
            );
            path
        } else {
            return Value::object(vec![(
                "error",
                Value::string("Provide chunk_id, document_path, or symbol"),
            )]);
        };
        if seeds.is_empty() {
            return Value::object(vec![
                ("origin", Value::string(origin)),
                ("max_depth", Value::Int(depth as i64)),
                ("affected_chunks", Value::Int(0)),
                ("affected_files", Value::Int(0)),
                ("chunks", Value::Array(Vec::new())),
            ]);
        }
        let seed_set: HashSet<String> = seeds.iter().cloned().collect();
        let mut queue: VecDeque<(String, usize)> =
            seeds.into_iter().map(|id| (id, 0usize)).collect();
        let mut seen = HashSet::new();
        let mut by_file: HashMap<String, (i64, usize, usize)> = HashMap::new();
        let mut depth_counts: HashMap<usize, i64> = HashMap::new();
        while let Some((id, hop)) = queue.pop_front() {
            if hop > depth || !seen.insert(id.clone()) {
                continue;
            }
            if hop < depth {
                for edge in &self.store.state.edges {
                    if edge.target == id {
                        queue.push_back((edge.source.clone(), hop + 1));
                    }
                }
            }
            if seed_set.contains(&id) && hop == 0 {
                continue;
            }
            let Some(path) = chunk_path(&self.store.state, &id) else {
                continue;
            };
            let entry = by_file.entry(path).or_insert((0, hop, hop));
            entry.0 += 1;
            entry.1 = entry.1.min(hop);
            entry.2 = entry.2.max(hop);
            *depth_counts.entry(hop).or_insert(0) += 1;
        }
        let mut files: Vec<Value> = by_file
            .into_iter()
            .map(|(path, (count, depth_min, depth_max))| {
                Value::object(vec![
                    ("path", Value::string(path)),
                    ("chunk_count", Value::Int(count)),
                    ("depth_min", Value::Int(depth_min as i64)),
                    ("depth_max", Value::Int(depth_max as i64)),
                ])
            })
            .collect();
        files.sort_by(|left, right| {
            crate::store::string_field(left, "path").cmp(&crate::store::string_field(right, "path"))
        });
        let affected_chunks: i64 = files
            .iter()
            .map(|row| crate::store::int_field(row, "chunk_count"))
            .sum();
        let mut out = vec![
            ("origin".to_string(), Value::string(origin)),
            ("max_depth".to_string(), Value::Int(depth as i64)),
            ("affected_chunks".to_string(), Value::Int(affected_chunks)),
            ("affected_files".to_string(), Value::Int(files.len() as i64)),
        ];
        if summary_mode {
            out.push(("summary_mode".to_string(), Value::Bool(true)));
            let mut depths: Vec<_> = depth_counts.into_iter().collect();
            depths.sort_by_key(|(hop, _)| *hop);
            out.push((
                "depth_distribution".to_string(),
                Value::Object(
                    depths
                        .into_iter()
                        .map(|(hop, count)| (hop.to_string(), Value::Int(count)))
                        .collect(),
                ),
            ));
            out.push(("files".to_string(), Value::Array(files.clone())));
            out.push(("files_total".to_string(), Value::Int(files.len() as i64)));
        } else {
            out.push(("files".to_string(), Value::Array(files)));
        }
        out.push(("chunks".to_string(), Value::Array(Vec::new())));
        Value::Object(out)
    }

    pub fn coupling(&self, path: &str) -> Value {
        self.coupling_mode(path, "edges", 0.0)
    }

    pub fn coupling_mode(&self, path: &str, mode: &str, significance: f64) -> Value {
        let path = crate::walk::native_sep(path);
        if mode == "co_consumers" {
            return self.coupling_co_consumers(&path);
        }
        let mine: HashSet<&str> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == path)
            .map(|chunk| chunk.id.as_str())
            .collect();
        if mine.is_empty() {
            return Value::object(vec![
                ("document_path", Value::string(&path)),
                ("coupled_files", Value::Array(Vec::new())),
                ("total_coupled", Value::Int(0)),
            ]);
        }
        struct Peer {
            symbols: HashSet<String>,
            outgoing: i64,
            incoming: i64,
        }
        let mut peers: HashMap<String, Peer> = HashMap::new();
        for edge in &self.store.state.edges {
            if significance > 0.0 && is_noise(&edge.name) {
                continue;
            }
            let (other_id, outgoing) = if mine.contains(edge.source.as_str()) {
                (edge.target.as_str(), true)
            } else if mine.contains(edge.target.as_str()) {
                (edge.source.as_str(), false)
            } else {
                continue;
            };
            let Some(other) = chunk_path(&self.store.state, other_id) else {
                continue;
            };
            if other == path {
                continue;
            }
            let peer = peers.entry(other).or_insert_with(|| Peer {
                symbols: HashSet::new(),
                outgoing: 0,
                incoming: 0,
            });
            peer.symbols.insert(edge.name.clone());
            if outgoing {
                peer.outgoing += 1;
            } else {
                peer.incoming += 1;
            }
        }
        let mut rows: Vec<Value> = peers
            .into_iter()
            .map(|(other, peer)| {
                let direction = if peer.outgoing > 0 && peer.incoming > 0 {
                    "bidirectional"
                } else if peer.outgoing > 0 {
                    "depends_on"
                } else {
                    "depended_on_by"
                };
                let mut names: Vec<_> = peer.symbols.iter().cloned().collect();
                names.sort();
                let score = semantic_score(&peer.symbols, &self.store.state.symbols);
                Value::object(vec![
                    ("path", Value::string(other)),
                    (
                        "shared_symbols",
                        Value::Array(names.into_iter().map(Value::String).collect()),
                    ),
                    ("shared_symbol_count", Value::Int(peer.symbols.len() as i64)),
                    ("direction", Value::string(direction)),
                    ("edge_count", Value::Int(peer.outgoing + peer.incoming)),
                    ("semantic_score", Value::Float(score)),
                ])
            })
            .collect();
        rows.sort_by(|left, right| {
            let ls = float_of(left, "semantic_score");
            let rs = float_of(right, "semantic_score");
            rs.total_cmp(&ls).then_with(|| {
                crate::store::int_field(right, "edge_count")
                    .cmp(&crate::store::int_field(left, "edge_count"))
            })
        });
        let total = rows.len() as i64;
        let mut out = vec![
            ("document_path".to_string(), Value::string(&path)),
            ("coupled_files".to_string(), Value::Array(rows)),
            ("total_coupled".to_string(), Value::Int(total)),
        ];
        if significance > 0.0 {
            out.push((
                "significance_threshold".to_string(),
                Value::Float(significance),
            ));
        }
        Value::Object(out)
    }

    fn coupling_co_consumers(&self, path: &str) -> Value {
        Value::object(vec![
            ("document_path", Value::string(path)),
            ("mode", Value::string("co_consumers")),
            ("coupled_files", Value::Array(Vec::new())),
            ("total_coupled", Value::Int(0)),
        ])
    }

    fn chunk_for_line(&self, path: &str, line: usize) -> Option<&crate::store::Chunk> {
        let mut rows: Vec<_> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == path)
            .collect();
        rows.sort_by_key(|chunk| chunk.start);
        let mut cursor = 1usize;
        for chunk in rows {
            let newlines = chunk.content.chars().filter(|ch| *ch == '\n').count();
            let end_line = cursor + newlines + usize::from(!chunk.content.ends_with('\n'));
            if line == 0 || (cursor <= line && line < end_line) {
                return Some(chunk);
            }
            cursor = end_line;
        }
        None
    }

    pub fn get_context(&mut self, path: &str, session: Option<&str>) -> Result<Value, String> {
        self.context_paths(std::slice::from_ref(&path.to_string()), session)
    }

    pub fn context_paths(
        &mut self,
        paths: &[String],
        session: Option<&str>,
    ) -> Result<Value, String> {
        let mut unchanged = Vec::new();
        let mut changed = Vec::new();
        let mut new_rows = Vec::new();
        for path in paths {
            let path = crate::walk::native_sep(path);
            let abs = self.cwd.join(&path);
            if !abs.is_file() {
                continue;
            }
            let stored = self
                .store
                .state
                .documents
                .iter()
                .find(|doc| doc.path == path)
                .map(|doc| (doc.size, doc.mtime_ns, doc.sha256.clone()));
            let Some((size, mtime, sha)) = stored else {
                new_rows.push(Value::object(vec![("path", Value::string(&path))]));
                continue;
            };
            let meta = fs_meta(&abs);
            let aligned = meta
                .as_ref()
                .is_some_and(|meta| size == meta.len() && mtime == meta_mtime(meta));
            if let Some(session) = session {
                self.note_read(session, &path);
            }
            if aligned {
                let chunk_rows: Vec<Value> = self
                    .store
                    .state
                    .chunks
                    .iter()
                    .filter(|chunk| chunk.path == path)
                    .map(|chunk| {
                        Value::object(vec![
                            ("chunk_id", Value::string(&chunk.id)),
                            ("content", Value::string(&chunk.content)),
                            ("start_pos", Value::Int(chunk.start as i64)),
                            ("end_pos", Value::Int(chunk.end as i64)),
                            ("token_count", Value::Int(chunk.tokens as i64)),
                            ("content_truncated", Value::Bool(false)),
                            ("staleness_score", Value::Float(chunk.staleness)),
                        ])
                    })
                    .collect();
                let total_tokens: i64 = chunk_rows
                    .iter()
                    .map(|row| crate::store::int_field(row, "token_count"))
                    .sum();
                let file_size = meta.as_ref().map(|item| item.len()).unwrap_or(size);
                let file_mtime = mtime as f64 / 1_000_000_000.0;
                let mut entry = vec![
                    ("path".to_string(), Value::string(&path)),
                    ("chunks".to_string(), Value::Array(chunk_rows)),
                    ("total_tokens".to_string(), Value::Int(total_tokens)),
                ];
                entry.push((
                    "trust".to_string(),
                    Value::object(vec![
                        ("document_indexed_at", Value::Float(file_mtime)),
                        ("stored_last_modified", Value::Float(file_mtime)),
                        ("file_mtime", Value::Float(file_mtime)),
                        ("file_size", Value::Int(file_size as i64)),
                        ("cache_aligned_with_disk", Value::Bool(true)),
                        ("max_chunk_staleness", Value::Float(0.0)),
                        ("staleness_hint", Value::Bool(false)),
                    ]),
                ));
                if session.is_some() {
                    entry.push(("recently_searched".to_string(), Value::Bool(false)));
                }
                unchanged.push(Value::Object(entry));
            } else if let Ok(disk) = std::fs::read_to_string(&abs) {
                let cached_chunks: Vec<(usize, usize, String)> = self
                    .store
                    .state
                    .chunks
                    .iter()
                    .filter(|chunk| chunk.path == path)
                    .map(|chunk| (chunk.start, chunk.end, chunk.content.clone()))
                    .collect();
                let mut entry = vec![
                    ("path".to_string(), Value::string(&path)),
                    (
                        "old_hash".to_string(),
                        Value::string(sha.chars().take(16).collect::<String>()),
                    ),
                    (
                        "new_hash".to_string(),
                        Value::string({
                            let digest = hex(&sha256(disk.as_bytes()));
                            digest.chars().take(16).collect::<String>()
                        }),
                    ),
                ];
                if let Some(diff) = cache_diff(&cached_chunks, &sha, &disk) {
                    entry.push(("diff_since_cache".to_string(), diff));
                }
                if let Some(meta) = meta {
                    entry.push((
                        "trust".to_string(),
                        Value::object(vec![
                            (
                                "file_mtime",
                                Value::Float(meta_mtime(&meta) as f64 / 1_000_000_000.0),
                            ),
                            ("file_size", Value::Int(meta.len() as i64)),
                            ("indexed_cache_outdated", Value::Bool(true)),
                        ]),
                    ));
                }
                changed.push(Value::Object(entry));
            }
        }
        if session.is_some() {
            self.store.save("read", &Value::string("context"))?;
        }
        Ok(Value::object(vec![
            ("unchanged", Value::Array(unchanged)),
            ("changed", Value::Array(changed)),
            ("new", Value::Array(new_rows)),
        ]))
    }

    pub fn annotations(&mut self, action: &str, path: &str, body: &str) -> Result<Value, String> {
        self.annotations_tagged(action, path, body, &[], "")
    }

    pub fn annotations_tagged(
        &mut self,
        action: &str,
        path: &str,
        body: &str,
        tags: &[String],
        target_type: &str,
    ) -> Result<Value, String> {
        // Document targets are stored the way Python Path renders them:
        // backslashes on Windows, slashes elsewhere. A search query is text.
        let stored;
        let path = if matches!(action, "create" | "get" | "bulk_create") {
            stored = crate::walk::native_sep(path);
            stored.as_str()
        } else {
            path
        };
        match action {
            "bulk_create" => {
                let created = self.annotations("create", path, body)?;
                Ok(Value::object(vec![
                    ("created", Value::Array(vec![created])),
                    ("errors", Value::Array(Vec::new())),
                ]))
            }
            "create" => {
                self.store.state.next_annotation += 1;
                let id_num = self.store.state.next_annotation;
                let id = id_num.to_string();
                let now = super::now_us();
                self.store.state.annotations.push(Annotation {
                    id: id.clone(),
                    path: path.to_string(),
                    body: body.to_string(),
                    tags: tags.to_vec(),
                    target_type: if target_type.is_empty() {
                        "document".to_string()
                    } else {
                        target_type.to_string()
                    },
                    created_at: now,
                    updated_at: now,
                });
                self.store.save("annotate", &Value::string(&id))?;
                Ok(Value::object(vec![
                    ("id", Value::Int(id_num)),
                    ("target", Value::string(path)),
                    ("target_type", Value::string("document")),
                ]))
            }
            "get" | "search" => {
                let rows = self
                    .store
                    .state
                    .annotations
                    .iter()
                    .filter(|note| {
                        if action == "search" {
                            return path.is_empty() || note.body.contains(path);
                        }
                        (path.is_empty() || note.path == path)
                            && (target_type.is_empty() || note.target_type == target_type)
                            && (tags.is_empty()
                                || tags
                                    .iter()
                                    .all(|tag| note.tags.iter().any(|have| have == tag)))
                    })
                    .map(|note| {
                        Value::object(vec![
                            ("id", Value::Int(note.id.parse::<i64>().unwrap_or(0))),
                            ("target", Value::string(&note.path)),
                            ("target_type", Value::string(&note.target_type)),
                            ("content", Value::string(&note.body)),
                            (
                                "tags",
                                Value::Array(
                                    note.tags.iter().cloned().map(Value::String).collect(),
                                ),
                            ),
                            (
                                "created_at",
                                Value::Float(super::session_time(note.created_at)),
                            ),
                            (
                                "updated_at",
                                Value::Float(super::session_time(note.updated_at)),
                            ),
                        ])
                    })
                    .collect();
                Ok(Value::object(vec![("annotations", Value::Array(rows))]))
            }
            "delete" => {
                let before = self.store.state.annotations.len();
                self.store
                    .state
                    .annotations
                    .retain(|note| note.id != path && note.path != path);
                let deleted = before != self.store.state.annotations.len();
                self.store.save("annotate-delete", &Value::string(path))?;
                Ok(Value::object(vec![
                    ("deleted", Value::Bool(deleted)),
                    ("id", Value::Int(path.parse::<i64>().unwrap_or(0))),
                ]))
            }
            "update" => {
                let updated = if let Some(note) = self
                    .store
                    .state
                    .annotations
                    .iter_mut()
                    .find(|note| note.id == path)
                {
                    if !body.is_empty() {
                        note.body = body.to_string();
                    }
                    if !tags.is_empty() {
                        note.tags = tags.to_vec();
                    }
                    note.updated_at = super::now_us();
                    true
                } else {
                    false
                };
                self.store.save("annotate-update", &Value::string(path))?;
                Ok(Value::object(vec![
                    ("updated", Value::Bool(updated)),
                    ("id", Value::Int(path.parse::<i64>().unwrap_or(0))),
                ]))
            }
            _ => Err(format!("unknown annotations action '{action}'")),
        }
    }

    pub fn document_lock(
        &mut self,
        action: &str,
        path: &str,
        agent: &str,
        force: bool,
    ) -> Result<Value, String> {
        let now = now_secs();
        let outcome = crate::locks::apply(
            &mut self.store.state,
            &self.cwd,
            action,
            path,
            agent,
            now,
            force,
        );
        if !matches!(action, "status" | "conflicts") {
            self.store.save("lock", &Value::string(path))?;
        }
        outcome
    }

    pub fn save_kv_state(
        &mut self,
        session: &str,
        kv: &Value,
        only: &[String],
        agent: &str,
    ) -> Result<Value, String> {
        let slot = self.session_mut(session);
        if !agent.is_empty() {
            slot.agent = agent.to_string();
        }
        let turn_number = slot.turn;
        let mut saved = 0i64;
        let mut added = 0i64;
        let pairs = match kv {
            Value::Object(pairs) => pairs.clone(),
            _ => Vec::new(),
        };
        for (chunk_id, data) in pairs {
            if !only.is_empty() && !only.iter().any(|id| id == &chunk_id) {
                continue;
            }
            let tokens = self
                .store
                .state
                .chunks
                .iter()
                .find(|chunk| chunk.id == chunk_id)
                .map(|chunk| chunk.tokens);
            let Some(tokens) = tokens else {
                continue;
            };
            added += tokens as i64;
            saved += 1;
            let encoded = crate::json::to_string(&data);
            if let Some(slot) = self
                .store
                .state
                .sessions
                .iter_mut()
                .find(|item| item.id == session)
            {
                slot.attached.push((chunk_id.clone(), turn_number, 1.0));
                slot.kv_state.push((chunk_id, turn_number, encoded));
            }
        }
        let slot = self
            .store
            .state
            .sessions
            .iter_mut()
            .find(|item| item.id == session)
            .unwrap();
        slot.tokens += added;
        let total = slot.tokens;
        slot.turn = turn_number + 1;
        slot.updated_at = super::now_us();
        self.store.save("kv", &Value::string(session))?;
        Ok(Value::object(vec![
            ("session_id", Value::string(session)),
            ("turn_number", Value::Int(turn_number)),
            ("chunks_saved", Value::Int(saved)),
            ("total_tokens", Value::Int(total)),
        ]))
    }

    pub fn relevant_kv(&self, session: &str, query: &str, top_k: usize) -> Value {
        let attached = self
            .store
            .state
            .sessions
            .iter()
            .find(|item| item.id == session)
            .map(|item| item.attached.clone())
            .unwrap_or_default();
        if attached.is_empty() {
            return Value::object(vec![
                ("query", Value::string(query)),
                ("chunks", Value::Array(Vec::new())),
                ("total_tokens", Value::Int(0)),
            ]);
        }
        let query_sig = crate::chunk::signature(query);
        let mut scored = Vec::new();
        for (chunk_id, _turn, _stored) in &attached {
            let Some(chunk) = self
                .store
                .state
                .chunks
                .iter()
                .find(|chunk| &chunk.id == chunk_id)
            else {
                continue;
            };
            let stored = crate::store::decode_hex_sig(&chunk.signature);
            let score = cosine_stored(&query_sig, &stored);
            scored.push((score, chunk));
        }
        scored.sort_by(|left, right| right.0.total_cmp(&left.0));
        scored.truncate(top_k);
        let mut total = 0i64;
        let chunks: Vec<Value> = scored
            .into_iter()
            .map(|(score, chunk)| {
                total += chunk.tokens as i64;
                Value::object(vec![
                    ("chunk_id", Value::string(&chunk.id)),
                    ("content", Value::string(&chunk.content)),
                    ("document_path", Value::string(&chunk.path)),
                    ("relevance_score", Value::Float(score)),
                    ("token_count", Value::Int(chunk.tokens as i64)),
                ])
            })
            .collect();
        Value::object(vec![
            ("query", Value::string(query)),
            ("chunks", Value::Array(chunks)),
            ("total_tokens", Value::Int(total)),
        ])
    }

    pub fn save_kv(&mut self, session: &str, key: &str, value: &str) -> Result<Value, String> {
        let slot = self.session_mut(session);
        if let Some(pair) = slot.kv.iter_mut().find(|(name, _)| name == key) {
            pair.1 = value.to_string();
        } else {
            slot.kv.push((key.to_string(), value.to_string()));
        }
        self.store.save("kv", &Value::string(key))?;
        Ok(Value::object(vec![("saved", Value::Bool(true))]))
    }

    pub fn get_kv(&self, session: &str) -> Value {
        let kv = self
            .store
            .state
            .sessions
            .iter()
            .find(|item| item.id == session)
            .map(|item| {
                item.kv
                    .iter()
                    .map(|(key, value)| {
                        Value::object(vec![("k", Value::string(key)), ("v", Value::string(value))])
                    })
                    .collect()
            })
            .unwrap_or_default();
        Value::object(vec![("kv", Value::Array(kv))])
    }

    pub fn search_history(&self, session: &str) -> Value {
        let rows = self
            .store
            .state
            .sessions
            .iter()
            .find(|item| item.id == session)
            .map(|item| item.searches.clone())
            .unwrap_or_default();
        Value::object(vec![
            ("session_id", Value::string(session)),
            (
                "searches",
                Value::Array(rows.into_iter().map(Value::String).collect()),
            ),
        ])
    }

    pub fn read_files(&self, session: &str) -> Value {
        let mut rows: Vec<_> = self
            .file_reads
            .iter()
            .filter(|row| row.0 == session)
            .collect();
        rows.reverse();
        Value::Array(
            rows.into_iter()
                .map(|row| {
                    Value::object(vec![
                        ("document_path", Value::string(&row.1)),
                        (
                            "chunk_ids",
                            Value::Array(row.2.iter().cloned().map(Value::String).collect()),
                        ),
                        ("read_at", Value::Float(row.3)),
                        ("staleness_score", Value::Null),
                    ])
                })
                .collect(),
        )
    }

    pub fn register_dynamic(
        &mut self,
        name: &str,
        kind: &str,
        path: &str,
    ) -> Result<Value, String> {
        self.store.state.dynamic.push(Dynamic {
            name: name.to_string(),
            kind: kind.to_string(),
            role: "definition".to_string(),
            path: path.to_string(),
            agent: String::new(),
            line: 0,
            chunk_id: format!("runtime:default:{name}"),
        });
        self.store.save("dynamic", &Value::string(name))?;
        Ok(Value::object(vec![("registered", Value::string(name))]))
    }

    pub fn store_summary(&mut self, chunk_id: &str, summary: &str) -> Result<Value, String> {
        let Some(chunk) = self
            .store
            .state
            .chunks
            .iter_mut()
            .find(|chunk| chunk.id == chunk_id)
        else {
            return Err(format!("unknown chunk '{chunk_id}'"));
        };
        chunk.summary = Some(summary.to_string());
        self.store.save("summary", &Value::string(chunk_id))?;
        Ok(Value::object(vec![
            ("stored", Value::Bool(true)),
            ("chunk_id", Value::string(chunk_id)),
        ]))
    }

    fn symbol_index(&self) -> Value {
        let sym_n = self.store.state.symbols.len() as i64;
        let doc_n = self.store.state.documents.len() as i64;
        Value::object(vec![
            (
                "status",
                Value::string(if sym_n == 0 { "empty" } else { "ready" }),
            ),
            ("indexed_documents", Value::Int(doc_n)),
            ("symbol_row_count", Value::Int(sym_n)),
            (
                "edge_count",
                Value::Int(self.store.state.edges.len() as i64),
            ),
        ])
    }

    fn definition_rows(&self, name: &str) -> Vec<Value> {
        let mut defs: Vec<_> = self
            .store
            .state
            .symbols
            .iter()
            .filter(|sym| sym.name == name && sym.role == "definition")
            .collect();
        defs.sort_by(|left, right| left.path.cmp(&right.path).then(left.line.cmp(&right.line)));
        let mut by_doc: Vec<(String, Vec<&crate::store::Symbol>)> = Vec::new();
        for sym in defs {
            if let Some(group) = by_doc.iter_mut().find(|(path, _)| path == &sym.path) {
                group.1.push(sym);
            } else {
                by_doc.push((sym.path.clone(), vec![sym]));
            }
        }
        let mut rows = Vec::new();
        for (_, group) in by_doc {
            let shadowed = group.len() > 1;
            for (index, sym) in group.iter().enumerate() {
                let chunk = self
                    .store
                    .state
                    .chunks
                    .iter()
                    .find(|chunk| chunk.id == sym.chunk_id);
                let dynamic = sym.chunk_id.starts_with("runtime:");
                let content = chunk.map(|chunk| chunk.content.clone()).unwrap_or_else(|| {
                    if dynamic {
                        "[dynamic/runtime symbol]".to_string()
                    } else {
                        String::new()
                    }
                });
                let mut row = vec![
                    ("symbol".to_string(), Value::string(&sym.name)),
                    ("kind".to_string(), Value::string(&sym.kind)),
                    ("chunk_id".to_string(), Value::string(&sym.chunk_id)),
                    ("document_path".to_string(), Value::string(&sym.path)),
                    ("line_number".to_string(), Value::Int(sym.line as i64)),
                    ("content".to_string(), Value::string(content)),
                    (
                        "token_count".to_string(),
                        Value::Int(chunk.map(|chunk| chunk.tokens as i64).unwrap_or(0)),
                    ),
                    ("dynamic".to_string(), Value::Bool(dynamic)),
                ];
                if shadowed {
                    row.push((
                        "definition_index".to_string(),
                        Value::Int((index + 1) as i64),
                    ));
                    row.push(("shadowed".to_string(), Value::Bool(true)));
                    row.push(("shadow_count".to_string(), Value::Int(group.len() as i64)));
                }
                rows.push(Value::Object(row));
            }
        }
        rows
    }

    fn reference_rows(&self, name: &str, definitions: bool) -> Vec<Value> {
        self.store
            .state
            .symbols
            .iter()
            .filter(|sym| {
                sym.name == name
                    && if definitions {
                        sym.role == "definition"
                    } else {
                        sym.role == "reference"
                    }
            })
            .map(|sym| {
                let chunk = self
                    .store
                    .state
                    .chunks
                    .iter()
                    .find(|chunk| chunk.id == sym.chunk_id);
                let dynamic = sym.chunk_id.starts_with("runtime:");
                let preview = chunk
                    .map(|chunk| chunk.content.chars().take(200).collect::<String>())
                    .unwrap_or_else(|| {
                        if dynamic {
                            "[dynamic/runtime symbol - no static chunk content]".to_string()
                        } else {
                            String::new()
                        }
                    });
                Value::object(vec![
                    ("symbol", Value::string(&sym.name)),
                    ("kind", Value::string(&sym.kind)),
                    ("chunk_id", Value::string(&sym.chunk_id)),
                    ("document_path", Value::string(&sym.path)),
                    ("line_number", Value::Int(sym.line as i64)),
                    ("content_preview", Value::string(preview)),
                    ("dynamic", Value::Bool(dynamic)),
                ])
            })
            .collect()
    }

    fn rows(&self, hits: &[crate::search::Scored], tier2: bool) -> Vec<Value> {
        hits.iter()
            .filter_map(|hit| {
                let chunk = self
                    .store
                    .state
                    .chunks
                    .iter()
                    .find(|chunk| chunk.id == hit.id)?;
                let mut pairs = vec![
                    ("chunk_id".to_string(), Value::string(&chunk.id)),
                    ("document_path".to_string(), Value::string(&chunk.path)),
                    ("content".to_string(), Value::string(&chunk.content)),
                    ("relevance_score".to_string(), Value::Float(hit.score)),
                    ("token_count".to_string(), Value::Int(chunk.tokens as i64)),
                    ("start_pos".to_string(), Value::Int(chunk.start as i64)),
                    ("end_pos".to_string(), Value::Int(chunk.end as i64)),
                    ("source".to_string(), Value::string(hit.source)),
                    (
                        "tier2_present".to_string(),
                        Value::Bool(tier2 && chunk.summary.is_some()),
                    ),
                ];
                if let Some(edges) = edge_block(&self.store.state.edges, &chunk.id) {
                    pairs.push(("edges".to_string(), edges));
                }
                Some(Value::Object(pairs))
            })
            .collect()
    }

    fn note_read(&mut self, session: &str, path: &str) {
        if self
            .file_reads
            .iter()
            .any(|row| row.0 == session && row.1 == path)
        {
            return;
        }
        let chunks: Vec<String> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == path)
            .map(|chunk| chunk.id.clone())
            .collect();
        self.file_reads.push((
            session.to_string(),
            path.to_string(),
            chunks,
            super::now_f64(),
        ));
    }

    fn session_mut(&mut self, id: &str) -> &mut Session {
        if !self.store.state.sessions.iter().any(|item| item.id == id) {
            self.store.state.sessions.push(Session {
                id: id.to_string(),
                searches: Vec::new(),
                reads: Vec::new(),
                kv: Vec::new(),
                created_at: super::now_us(),
                updated_at: super::now_us(),
                turn: 0,
                tokens: 0,
                agent: String::new(),
                attached: Vec::new(),
                kv_state: Vec::new(),
                search_rows: Vec::new(),
            });
        }
        self.store
            .state
            .sessions
            .iter_mut()
            .find(|item| item.id == id)
            .unwrap()
    }
}

fn compact_query_row(row: Value, source: &str) -> Value {
    let Value::Object(pairs) = row else {
        return row;
    };
    let mut content = String::new();
    let mut kept = Vec::new();
    for (key, value) in pairs {
        if key == "content" {
            if let Value::String(text) = value {
                content = text;
            }
            continue;
        }
        if key == "source" {
            continue;
        }
        kept.push((key, value));
    }
    let preview = if content.chars().count() > 400 {
        let mut text: String = content.chars().take(400).collect();
        text.push('…');
        text
    } else {
        content.clone()
    };
    kept.push(("content_preview".to_string(), Value::string(preview)));
    kept.push((
        "content_token_estimate".to_string(),
        Value::Int(crate::chunk::estimate_tokens(&content) as i64),
    ));
    kept.push(("source".to_string(), Value::string(source)));
    Value::Object(kept)
}

fn query_idents(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() || ch == '_' {
            current.push(ch);
        } else if current.len() >= 3 {
            if !out.iter().any(|have: &String| have == &current) {
                out.push(current.clone());
            }
            current.clear();
        } else {
            current.clear();
        }
    }
    if current.len() >= 3 && !out.iter().any(|have| have == &current) {
        out.push(current);
    }
    out
}

fn field_owned(value: &Value, key: &str) -> Option<Value> {
    let Value::Object(pairs) = value else {
        return None;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, item)| item.clone())
}

fn is_noise(name: &str) -> bool {
    matches!(
        name,
        "push" | "has" | "addEdge" | "add" | "get" | "set" | "len" | "new"
    )
}

fn semantic_score(names: &HashSet<String>, symbols: &[crate::store::Symbol]) -> f64 {
    let mut total = 0.0;
    for name in names {
        if name.chars().count() <= 3 {
            continue;
        }
        let mut files = HashSet::new();
        for sym in symbols {
            if sym.name == *name && sym.role == "definition" {
                files.insert(sym.path.as_str());
            }
        }
        let n = (files.len().max(1)) as f64;
        total += 1.0 / (1.0 + n.log2());
    }
    (total * 100.0).round() / 100.0
}

fn float_of(value: &Value, key: &str) -> f64 {
    match field_owned(value, key) {
        Some(Value::Float(number)) => number,
        Some(Value::Int(number)) => number as f64,
        _ => 0.0,
    }
}

fn chunk_path(state: &crate::store::State, id: &str) -> Option<String> {
    state
        .chunks
        .iter()
        .find(|chunk| chunk.id == id)
        .map(|chunk| chunk.path.clone())
}

fn edge_block(edges: &[crate::store::Edge], id: &str) -> Option<Value> {
    let depends: Vec<Value> = edges
        .iter()
        .filter(|edge| edge.source == id)
        .map(|edge| {
            Value::object(vec![
                ("chunk_id", Value::string(&edge.target)),
                ("symbol", Value::string(&edge.name)),
            ])
        })
        .collect();
    let depended: Vec<Value> = edges
        .iter()
        .filter(|edge| edge.target == id)
        .map(|edge| {
            Value::object(vec![
                ("chunk_id", Value::string(&edge.source)),
                ("symbol", Value::string(&edge.name)),
            ])
        })
        .collect();
    if depends.is_empty() && depended.is_empty() {
        return None;
    }
    Some(Value::object(vec![
        ("depends_on", Value::Array(depends)),
        ("depended_on_by", Value::Array(depended)),
    ]))
}

fn guidance_empty(index: &Value) -> String {
    let status = crate::store::string_field(index, "status");
    let docs = crate::store::int_field(index, "indexed_documents");
    if status == "ready" {
        "The symbol graph is populated but this name has no matches. The symbol may be absent from indexed files, not extracted (e.g. highly dynamic code), or spelled differently. Try search_text or agent_grep to confirm text occurrences.".to_string()
    } else if docs == 0 {
        "No documents are indexed yet, so the symbol index is empty. Run index on project paths first, then rebuild_symbols if definitions and references stay empty.".to_string()
    } else {
        "Chunks are indexed but the symbol table is empty. Run index on source files you need for symbols, then rebuild_symbols to populate the graph.".to_string()
    }
}

fn group_tokens(key: &str, rows: &[Value]) -> usize {
    let mut parts = vec![key.to_string()];
    for row in rows {
        parts.push(crate::store::string_field(row, "excerpt"));
        let scope = crate::store::string_field(row, "scope");
        if !scope.is_empty() {
            parts.push(scope);
        }
        let class = crate::store::string_field(row, "classification");
        if !class.is_empty() {
            parts.push(class);
        }
        parts.push(format!(":{}", crate::store::int_field(row, "line")));
    }
    crate::chunk::estimate_tokens(&parts.join(" "))
}

fn apply_proximity(rows: &mut [Value], query: &str) {
    if rows.is_empty() {
        return;
    }
    let max = rows.iter().map(score_of).fold(0.0_f64, f64::max);
    let terms = proximity_terms(query);
    let weight = if terms.len() >= 5 { 0.35 } else { 0.28 };
    for row in rows.iter_mut() {
        let Value::Object(pairs) = row else {
            continue;
        };
        let mut score = score_of(&Value::Object(pairs.clone()));
        if max > 0.0 {
            score /= max;
        }
        let content = pairs
            .iter()
            .find(|(key, _)| key == "content")
            .and_then(|(_, value)| match value {
                Value::String(text) => Some(text.clone()),
                _ => None,
            })
            .unwrap_or_default();
        if !terms.is_empty() {
            let prox = proximity_score(&content, &terms);
            if prox > 0.0 {
                score = round6(score * (1.0 - weight) + prox * weight);
                if let Some((_, item)) =
                    pairs.iter_mut().find(|(key, _)| key == "proximity_boosted")
                {
                    *item = Value::Bool(true);
                } else {
                    pairs.push(("proximity_boosted".to_string(), Value::Bool(true)));
                }
            }
        }
        if let Some((_, item)) = pairs.iter_mut().find(|(key, _)| key == "relevance_score") {
            *item = Value::Float(score);
        }
    }
    rows.sort_by(|left, right| {
        score_of(right)
            .partial_cmp(&score_of(left))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
}

fn score_of(value: &Value) -> f64 {
    match object_field(value, "relevance_score") {
        Some(Value::Float(number)) => *number,
        Some(Value::Int(number)) => *number as f64,
        _ => 0.0,
    }
}

fn round6(number: f64) -> f64 {
    (number * 1_000_000.0).round() / 1_000_000.0
}

fn proximity_terms(query: &str) -> Vec<String> {
    let mut terms = Vec::new();
    let mut push = |word: &str| {
        let lower = word.to_ascii_lowercase();
        if lower.len() < 3 || STOP_WORDS.contains(&lower.as_str()) {
            return;
        }
        if !terms.iter().any(|have: &String| have == &lower) {
            terms.push(lower);
        }
    };
    for word in query.split(|ch: char| !ch.is_ascii_alphanumeric() && ch != '_') {
        if word.len() >= 4 || word.len() >= 3 {
            push(word);
        }
    }
    terms
}

fn proximity_score(content: &str, terms: &[String]) -> f64 {
    if terms.is_empty() || content.is_empty() {
        return 0.0;
    }
    let lower = content.to_ascii_lowercase();
    let positions: Vec<Option<usize>> = terms.iter().map(|term| lower.find(term)).collect();
    let present: Vec<usize> = positions
        .iter()
        .enumerate()
        .filter_map(|(index, pos)| pos.map(|_| index))
        .collect();
    if present.is_empty() {
        return 0.0;
    }
    let min_pos = present
        .iter()
        .filter_map(|index| positions[*index])
        .min()
        .unwrap_or(0);
    let first_score = 1.0 - (min_pos as f64 / lower.len().max(1) as f64).min(1.0);
    let weights: Vec<f64> = terms
        .iter()
        .map(|term| (term.len() as f64 / 8.0).min(1.0))
        .collect();
    let weight_sum = weights.iter().sum::<f64>().max(1.0);
    let present_weighted = present.iter().map(|index| weights[*index]).sum::<f64>() / weight_sum;
    let found: Vec<usize> = present
        .iter()
        .filter_map(|index| positions[*index])
        .collect();
    let max_dist = if found.len() > 1 {
        found.iter().max().unwrap() - found.iter().min().unwrap()
    } else {
        0
    };
    let near = 1.0 - (max_dist as f64 / 1000.0).min(1.0);
    0.4 * present_weighted + 0.3 * first_score + 0.3 * near
}

const STOP_WORDS: &[&str] = &[
    "the", "and", "for", "with", "from", "that", "this", "have", "are", "was", "not", "all", "but",
    "how",
];

fn object_field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    let Value::Object(pairs) = value else {
        return None;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, item)| item)
}

fn line_at(content: &str, index: usize) -> String {
    content.lines().nth(index).unwrap_or("").to_string()
}

fn classify_line(line: &str) -> String {
    let stripped = line.trim_start();
    if stripped.is_empty() {
        return "blank".to_string();
    }
    const COMMENTS: &[&str] = &["#", "//", "--", ";", "%", "/*", "* ", "///", "/**"];
    const IMPORTS: &[&str] = &[
        "import ",
        "from ",
        "require(",
        "use ",
        "include ",
        "#include",
        "using ",
        "extern crate ",
    ];
    const DEFS: &[&str] = &[
        "def ",
        "async def ",
        "class ",
        "function ",
        "async function ",
        "fn ",
        "pub fn ",
        "func ",
        "type ",
        "interface ",
        "struct ",
        "enum ",
        "trait ",
        "impl ",
    ];
    if COMMENTS.iter().any(|prefix| stripped.starts_with(prefix)) {
        return "comment".to_string();
    }
    if IMPORTS.iter().any(|prefix| stripped.starts_with(prefix)) {
        return "import".to_string();
    }
    if DEFS.iter().any(|prefix| stripped.starts_with(prefix)) {
        return "definition".to_string();
    }
    "code".to_string()
}

fn scope_for(engine: &Engine, chunk_id: &str, line: u32) -> String {
    engine
        .store
        .state
        .symbols
        .iter()
        .filter(|sym| sym.chunk_id == chunk_id && sym.role == "definition" && sym.line <= line)
        .max_by_key(|sym| sym.line)
        .map(|sym| sym.name.clone())
        .unwrap_or_default()
}

fn literal_hits(pattern: &str, text: &str) -> Vec<(usize, usize)> {
    if pattern.is_empty() {
        return Vec::new();
    }
    let width = pattern.chars().count();
    let mut hits = Vec::new();
    let mut from = 0usize;
    while let Some(pos) = text[from..].find(pattern) {
        let byte = from + pos;
        let start = text[..byte].chars().count();
        hits.push((start, start + width));
        if hits.len() >= 100 {
            break;
        }
        let next = byte + pattern.len();
        if next <= from {
            break;
        }
        from = next;
    }
    hits
}

fn slice_chars(text: &str, start: usize, end: usize) -> String {
    text.chars()
        .skip(start)
        .take(end.saturating_sub(start))
        .collect()
}

fn cache_diff(chunks: &[(usize, usize, String)], stored_hash: &str, disk: &str) -> Option<Value> {
    if chunks.is_empty() {
        return None;
    }
    let mut ordered = chunks.to_vec();
    ordered.sort_by_key(|item| item.0);
    let mut padded = String::new();
    let mut cursor = 0usize;
    for (start, end, content) in &ordered {
        if *start > cursor {
            padded.push_str(&"\n".repeat(start - cursor));
            cursor = *start;
        }
        padded.push_str(content);
        cursor += content.chars().count();
        if *end > cursor {
            padded.push_str(&"\n".repeat(end - cursor));
            cursor = *end;
        }
    }
    let plain: String = ordered.iter().map(|item| item.2.as_str()).collect();
    let mut candidates = Vec::new();
    for cand in [padded.clone(), format!("{padded}\n"), plain] {
        if !candidates.iter().any(|have: &String| have == &cand) {
            candidates.push(cand);
        }
    }
    let new_lines: Vec<&str> = disk.split('\n').collect();
    let mut exact = false;
    let mut diff_lines = None;
    for cand in &candidates {
        if hex(&sha256(cand.as_bytes())) == stored_hash {
            exact = true;
            diff_lines = Some(unified(cand, &new_lines));
            break;
        }
    }
    let diff_lines = diff_lines.unwrap_or_else(|| unified(&candidates[0], &new_lines));
    let added = diff_lines
        .iter()
        .filter(|ln| ln.starts_with('+') && !ln.starts_with("+++"))
        .count();
    let removed = diff_lines
        .iter()
        .filter(|ln| ln.starts_with('-') && !ln.starts_with("---"))
        .count();
    let diff_text = diff_lines.join("\n");
    let diff_tokens = crate::chunk::estimate_tokens(&diff_text);
    let new_tokens = crate::chunk::estimate_tokens(disk).max(1);
    let recommendation = if diff_tokens < (new_tokens as f64 * 0.8) as usize {
        "read_diff"
    } else {
        "reread_file"
    };
    Some(Value::object(vec![
        ("diff", Value::string(diff_text)),
        ("diff_exact", Value::Bool(exact)),
        ("added_lines", Value::Int(added as i64)),
        ("removed_lines", Value::Int(removed as i64)),
        ("diff_tokens", Value::Int(diff_tokens as i64)),
        ("diff_truncated", Value::Bool(false)),
        ("recommendation", Value::string(recommendation)),
    ]))
}

fn unified(cached: &str, new_lines: &[&str]) -> Vec<String> {
    let old_owned: Vec<&str> = cached.split('\n').collect();
    let old_lines = trim_split(&old_owned);
    let new_lines = trim_split(new_lines);
    if old_lines == new_lines {
        return Vec::new();
    }
    let groups = grouped_opcodes(&old_lines, &new_lines, 3);
    let mut out = vec!["--- cached".to_string(), "+++ disk".to_string()];
    for group in groups {
        if group.is_empty() {
            continue;
        }
        let i1 = group[0].1;
        let i2 = group.last().map(|code| code.2).unwrap_or(i1);
        let j1 = group[0].3;
        let j2 = group.last().map(|code| code.4).unwrap_or(j1);
        out.push(format!(
            "@@ -{},{} +{},{} @@",
            i1 + 1,
            i2 - i1,
            j1 + 1,
            j2 - j1
        ));
        for (tag, a0, a1, b0, b1) in group {
            if tag == "equal" || tag == "delete" || tag == "replace" {
                let prefix = if tag == "equal" { " " } else { "-" };
                for line in &old_lines[a0..a1] {
                    out.push(format!("{prefix}{line}"));
                }
            }
            if tag == "insert" || tag == "replace" {
                for line in &new_lines[b0..b1] {
                    out.push(format!("+{line}"));
                }
            }
        }
    }
    out
}

fn grouped_opcodes<'a>(
    a: &[&'a str],
    b: &[&'a str],
    context: usize,
) -> Vec<Vec<(&'static str, usize, usize, usize, usize)>> {
    let mut codes = opcodes(a, b);
    if codes.is_empty() {
        return Vec::new();
    }
    if codes[0].0 == "equal" {
        let (tag, i1, i2, j1, j2) = codes[0];
        codes[0] = (
            tag,
            i2.saturating_sub(context).max(i1),
            i2,
            j2.saturating_sub(context).max(j1),
            j2,
        );
    }
    if codes.last().is_some_and(|code| code.0 == "equal") {
        let last = codes.len() - 1;
        let (tag, i1, i2, j1, j2) = codes[last];
        codes[last] = (tag, i1, i2.min(i1 + context), j1, j2.min(j1 + context));
    }
    let nn = context + context;
    let mut groups = Vec::new();
    let mut group = Vec::new();
    for code in codes {
        let (tag, i1, i2, j1, j2) = code;
        if tag == "equal" && i2 - i1 > nn {
            group.push((tag, i1, i1 + context, j1, j1 + context));
            groups.push(std::mem::take(&mut group));
            group.push((tag, i2 - context, i2, j2 - context, j2));
        } else {
            group.push(code);
        }
    }
    if !(group.len() == 1 && group[0].0 == "equal") && !group.is_empty() {
        groups.push(group);
    }
    groups
        .into_iter()
        .filter(|group| group.iter().any(|code| code.0 != "equal"))
        .collect()
}

fn opcodes<'a>(a: &[&'a str], b: &[&'a str]) -> Vec<(&'static str, usize, usize, usize, usize)> {
    let blocks = matching_blocks(a, b);
    let mut codes = Vec::new();
    let mut i = 0usize;
    let mut j = 0usize;
    for (ai, bj, size) in blocks {
        if i < ai && j < bj {
            codes.push(("replace", i, ai, j, bj));
        } else if i < ai {
            codes.push(("delete", i, ai, j, j));
        } else if j < bj {
            codes.push(("insert", i, i, j, bj));
        }
        if size > 0 {
            codes.push(("equal", ai, ai + size, bj, bj + size));
        }
        i = ai + size;
        j = bj + size;
    }
    codes
}

fn matching_blocks<'a>(a: &[&'a str], b: &[&'a str]) -> Vec<(usize, usize, usize)> {
    let mut blocks = Vec::new();
    let mut queue = vec![(0usize, a.len(), 0usize, b.len())];
    while let Some((alo, ahi, blo, bhi)) = queue.pop() {
        let (i, j, k) = longest_match(a, b, alo, ahi, blo, bhi);
        if k == 0 {
            continue;
        }
        blocks.push((i, j, k));
        if alo < i && blo < j {
            queue.push((alo, i, blo, j));
        }
        if i + k < ahi && j + k < bhi {
            queue.push((i + k, ahi, j + k, bhi));
        }
    }
    blocks.sort_by_key(|block| block.0);
    let mut merged: Vec<(usize, usize, usize)> = Vec::new();
    let mut ia = 0usize;
    let mut ib = 0usize;
    for (i, j, k) in blocks {
        if ia == i && ib == j {
            if let Some(last) = merged.last_mut() {
                last.2 += k;
                ia = i + k;
                ib = j + k;
                continue;
            }
        }
        merged.push((i, j, k));
        ia = i + k;
        ib = j + k;
    }
    merged.push((a.len(), b.len(), 0));
    merged
}

fn longest_match<'a>(
    a: &[&'a str],
    b: &[&'a str],
    alo: usize,
    ahi: usize,
    blo: usize,
    bhi: usize,
) -> (usize, usize, usize) {
    let mut index: std::collections::HashMap<&str, Vec<usize>> = std::collections::HashMap::new();
    for (j, line) in b.iter().enumerate().take(bhi).skip(blo) {
        index.entry(*line).or_default().push(j);
    }
    let mut best_i = alo;
    let mut best_j = blo;
    let mut best = 0usize;
    let mut j2len: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();
    for i in alo..ahi {
        let mut next: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();
        if let Some(hits) = index.get(a[i]) {
            for &j in hits {
                let k = j
                    .checked_sub(1)
                    .and_then(|prev| j2len.get(&prev))
                    .copied()
                    .unwrap_or(0)
                    + 1;
                next.insert(j, k);
                if k > best {
                    best = k;
                    best_i = i + 1 - k;
                    best_j = j + 1 - k;
                }
            }
        }
        j2len = next;
    }
    (best_i, best_j, best)
}

fn trim_split<'a>(lines: &[&'a str]) -> Vec<&'a str> {
    let mut lines = lines.to_vec();
    if lines.last().is_some_and(|line| line.is_empty()) {
        lines.pop();
    }
    lines
}

fn fs_meta(path: &std::path::Path) -> Option<std::fs::Metadata> {
    std::fs::metadata(path).ok()
}

fn meta_mtime(meta: &std::fs::Metadata) -> i64 {
    meta.modified()
        .ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos() as i64)
        .unwrap_or(0)
}
