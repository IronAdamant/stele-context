//! Search, symbol graph, sessions, locks, and annotations.

use super::{now_secs, Engine};
use crate::hash::{hex, sha256};
use crate::json::Value;
use crate::search::{self, VectorDoc};
use crate::store::{Annotation, Dynamic, Session};
use std::collections::{HashSet, VecDeque};

impl Engine {
    pub fn search(&mut self, query: &str, mode: &str, top_k: usize) -> Value {
        self.ensure_bm25();
        let top_k = top_k.max(1);
        let hits = if mode == "hybrid" {
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
            search::hybrid(&self.bm25, &docs, query, top_k)
        } else {
            search::keyword(&self.bm25, query, top_k)
        };
        Value::object(vec![("results", Value::Array(self.rows(&hits)))])
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
                ("scope", Value::string(&row.4)),
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
            let cost = 8 + rows.len() as i64 * 4;
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
            (
                "files_checked",
                Value::Int(self.store.state.documents.len() as i64),
            ),
            (
                "files_with_matches",
                Value::Array(files.into_iter().map(Value::String).collect()),
            ),
        ]))
    }

    pub fn query(&mut self, text: &str) -> Result<Value, String> {
        let lexical = self.search(text, "keyword", 8);
        let symbols = self.find_definition(text);
        let grep = self
            .search_text(text)
            .unwrap_or_else(|error| Value::object(vec![("error", Value::string(error))]));
        Ok(Value::object(vec![
            ("search", lexical),
            ("definitions", symbols),
            ("grep", grep),
        ]))
    }

    pub fn find_definition(&self, name: &str) -> Value {
        let defs: Vec<_> = self
            .store
            .state
            .symbols
            .iter()
            .filter(|sym| sym.name == name && sym.role == "definition")
            .collect();
        let shadow = defs.len().saturating_sub(1);
        let mut rows: Vec<Value> = defs
            .into_iter()
            .enumerate()
            .map(|(index, sym)| {
                let mut row = symbol_json(sym);
                if let Value::Object(pairs) = &mut row {
                    pairs.push(("definition_index".to_string(), Value::Int(index as i64)));
                    pairs.push(("shadowed".to_string(), Value::Bool(index > 0)));
                    pairs.push(("shadow_count".to_string(), Value::Int(shadow as i64)));
                }
                row
            })
            .collect();
        for sym in self
            .store
            .state
            .dynamic
            .iter()
            .filter(|sym| sym.name == name)
        {
            rows.push(Value::object(vec![
                ("name", Value::string(&sym.name)),
                ("kind", Value::string(&sym.kind)),
                ("role", Value::string("definition")),
                ("path", Value::string(&sym.path)),
                ("dynamic", Value::Bool(true)),
            ]));
        }
        Value::object(vec![("definitions", Value::Array(rows))])
    }

    pub fn find_references(&self, name: &str) -> Value {
        let rows = self
            .store
            .state
            .symbols
            .iter()
            .filter(|sym| sym.name == name)
            .map(symbol_json)
            .collect();
        Value::object(vec![("references", Value::Array(rows))])
    }

    pub fn impact(&self, symbol: &str) -> Value {
        let seeds: Vec<String> = self
            .store
            .state
            .symbols
            .iter()
            .filter(|sym| sym.name == symbol && sym.role == "definition")
            .map(|sym| sym.chunk_id.clone())
            .collect();
        let hits = dependent_chunks(&self.store.state.edges, &seeds, 2);
        let mut files = Vec::new();
        for id in &hits {
            if let Some(path) = chunk_path(&self.store.state, id) {
                if !files.iter().any(|have| have == &path) {
                    files.push(path);
                }
            }
        }
        files.sort();
        Value::object(vec![
            ("symbol", Value::string(symbol)),
            ("affected_files", Value::Int(files.len() as i64)),
            ("affected_chunks", Value::Int(hits.len() as i64)),
            (
                "affected",
                Value::Array(files.into_iter().map(Value::String).collect()),
            ),
        ])
    }

    pub fn coupling(&self, path: &str) -> Value {
        let path = path.replace('\\', "/");
        let mine: HashSet<&str> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == path)
            .map(|chunk| chunk.id.as_str())
            .collect();
        let mut peers = Vec::new();
        for edge in &self.store.state.edges {
            let other = if mine.contains(edge.source.as_str()) {
                chunk_path(&self.store.state, &edge.target)
            } else if mine.contains(edge.target.as_str()) {
                chunk_path(&self.store.state, &edge.source)
            } else {
                None
            };
            if let Some(other) = other {
                if other != path && !peers.iter().any(|have| have == &other) {
                    peers.push(other);
                }
            }
        }
        peers.sort();
        Value::object(vec![
            ("path", Value::string(path)),
            (
                "coupled_files",
                Value::Array(peers.into_iter().map(Value::String).collect()),
            ),
        ])
    }

    pub fn get_context(&mut self, path: &str, session: Option<&str>) -> Result<Value, String> {
        let path = path.replace('\\', "/");
        let chunks: Vec<Value> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == path)
            .map(|chunk| {
                Value::object(vec![
                    ("chunk_id", Value::string(&chunk.id)),
                    ("start", Value::Int(chunk.start as i64)),
                    ("end", Value::Int(chunk.end as i64)),
                    ("content", Value::string(&chunk.content)),
                    (
                        "summary",
                        match &chunk.summary {
                            Some(text) => Value::string(text),
                            None => Value::Null,
                        },
                    ),
                    (
                        "notes",
                        match &chunk.notes {
                            Some(text) => Value::string(text),
                            None => Value::Null,
                        },
                    ),
                ])
            })
            .collect();
        let abs = self.cwd.join(&path);
        let aligned = match (
            fs_meta(&abs),
            self.store
                .state
                .documents
                .iter()
                .find(|doc| doc.path == path),
        ) {
            (Some(meta), Some(doc)) => doc.size == meta.len() && doc.mtime_ns == meta_mtime(&meta),
            _ => false,
        };
        if let Some(session) = session {
            self.note_read(session, &path);
            self.store.save("read", &Value::string(&path))?;
        }
        Ok(Value::object(vec![
            ("path", Value::string(path)),
            (
                "trust",
                Value::object(vec![("cache_aligned_with_disk", Value::Bool(aligned))]),
            ),
            ("staleness_hint", Value::Bool(!aligned)),
            ("chunks", Value::Array(chunks)),
        ]))
    }

    pub fn annotations(&mut self, action: &str, path: &str, body: &str) -> Result<Value, String> {
        match action {
            "create" | "bulk_create" => {
                let id = hex(&sha256(format!("{path}\n{body}").as_bytes()))[..16].to_string();
                self.store.state.annotations.push(Annotation {
                    id: id.clone(),
                    path: path.to_string(),
                    body: body.to_string(),
                    tags: Vec::new(),
                    target_type: "document".to_string(),
                    created_at: super::now_secs(),
                    updated_at: super::now_secs(),
                });
                self.store.save("annotate", &Value::string(&id))?;
                Ok(Value::object(vec![("id", Value::string(id))]))
            }
            "get" | "search" => {
                let rows = self
                    .store
                    .state
                    .annotations
                    .iter()
                    .filter(|note| path.is_empty() || note.path == path || note.body.contains(path))
                    .map(|note| {
                        Value::object(vec![
                            ("id", Value::string(&note.id)),
                            ("path", Value::string(&note.path)),
                            ("body", Value::string(&note.body)),
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
                self.store.save("annotate-delete", &Value::string(path))?;
                Ok(Value::object(vec![(
                    "removed",
                    Value::Int((before - self.store.state.annotations.len()) as i64),
                )]))
            }
            "update" => {
                if let Some(note) = self
                    .store
                    .state
                    .annotations
                    .iter_mut()
                    .find(|note| note.id == path)
                {
                    note.body = body.to_string();
                }
                self.store.save("annotate-update", &Value::string(path))?;
                Ok(Value::object(vec![("updated", Value::string(path))]))
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
        Value::object(vec![(
            "searches",
            Value::Array(rows.into_iter().map(Value::String).collect()),
        )])
    }

    pub fn read_files(&self, session: &str) -> Value {
        let rows = self
            .store
            .state
            .sessions
            .iter()
            .find(|item| item.id == session)
            .map(|item| item.reads.clone())
            .unwrap_or_default();
        Value::object(vec![(
            "files",
            Value::Array(rows.into_iter().map(Value::String).collect()),
        )])
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
        Ok(Value::object(vec![("stored", Value::Bool(true))]))
    }

    fn rows(&self, hits: &[crate::search::Scored]) -> Vec<Value> {
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
                        Value::Bool(chunk.summary.is_some()),
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
        let slot = self.session_mut(session);
        if !slot.reads.iter().any(|item| item == path) {
            slot.reads.push(path.to_string());
        }
    }

    fn session_mut(&mut self, id: &str) -> &mut Session {
        if !self.store.state.sessions.iter().any(|item| item.id == id) {
            self.store.state.sessions.push(Session {
                id: id.to_string(),
                searches: Vec::new(),
                reads: Vec::new(),
                kv: Vec::new(),
                created_at: super::now_secs(),
                updated_at: super::now_secs(),
                turn: 0,
                tokens: 0,
                agent: String::new(),
                attached: Vec::new(),
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

fn dependent_chunks(edges: &[crate::store::Edge], seeds: &[String], depth: usize) -> Vec<String> {
    let mut queue: VecDeque<(String, usize)> = seeds.iter().map(|id| (id.clone(), 0)).collect();
    let mut seen = HashSet::new();
    let mut hits = Vec::new();
    while let Some((id, hop)) = queue.pop_front() {
        if hop > depth || !seen.insert(id.clone()) {
            continue;
        }
        if hop > 0 {
            hits.push(id.clone());
        }
        if hop == depth {
            continue;
        }
        for edge in edges {
            if edge.target == id {
                queue.push_back((edge.source.clone(), hop + 1));
            }
        }
    }
    hits
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

fn symbol_json(sym: &crate::store::Symbol) -> Value {
    Value::object(vec![
        ("name", Value::string(&sym.name)),
        ("kind", Value::string(&sym.kind)),
        ("role", Value::string(&sym.role)),
        ("line", Value::Int(sym.line as i64)),
        (
            "container",
            match &sym.container {
                Some(text) => Value::string(text),
                None => Value::Null,
            },
        ),
        ("path", Value::string(&sym.path)),
        ("chunk_id", Value::string(&sym.chunk_id)),
        ("chunk_index", Value::Int(sym.chunk_index as i64)),
    ])
}

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
