//! Search, symbol graph, sessions, locks, and annotations.

use super::{now_secs, Engine};
use crate::hash::{hex, sha256};
use crate::json::Value;
use crate::search::{self, VectorDoc};
use crate::store::{Annotation, Dynamic, Lock, Session};

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
        let mut rows = Vec::new();
        let literal = is_literal(pattern);
        for chunk in &self.store.state.chunks {
            let hits = if literal {
                literal_hits(pattern, &chunk.content)
            } else {
                search::text_matches(pattern, &chunk.content)?
            };
            for (start, end) in hits {
                rows.push(Value::object(vec![
                    ("path", Value::string(&chunk.path)),
                    ("chunk_id", Value::string(&chunk.id)),
                    ("start", Value::Int(start as i64)),
                    ("end", Value::Int(end as i64)),
                    (
                        "text",
                        Value::string(slice_chars(&chunk.content, start, end)),
                    ),
                ]));
                if rows.len() >= 100 {
                    return Ok(Value::object(vec![("matches", Value::Array(rows))]));
                }
            }
        }
        Ok(Value::object(vec![("matches", Value::Array(rows))]))
    }

    pub fn agent_grep(&mut self, pattern: &str, session: Option<&str>) -> Result<Value, String> {
        let found = self.search_text(pattern)?;
        if let Some(session) = session {
            self.note_search(session, pattern);
            self.store.save("search", &Value::string(pattern))?;
        }
        Ok(found)
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
        let mut files = Vec::new();
        for sym in &self.store.state.symbols {
            if sym.name == symbol && !files.iter().any(|path| path == &sym.path) {
                files.push(sym.path.clone());
            }
        }
        Value::object(vec![
            ("symbol", Value::string(symbol)),
            ("files", Value::Int(files.len() as i64)),
            (
                "affected",
                Value::Array(files.into_iter().map(Value::String).collect()),
            ),
        ])
    }

    pub fn coupling(&self, path: &str) -> Value {
        let path = path.replace('\\', "/");
        let imports: Vec<String> = self
            .store
            .state
            .symbols
            .iter()
            .filter(|sym| sym.path == path && sym.role == "reference" && sym.kind == "module")
            .map(|sym| sym.name.clone())
            .collect();
        let mut peers = Vec::new();
        for doc in &self.store.state.documents {
            if doc.path == path {
                continue;
            }
            let shares = self.store.state.symbols.iter().any(|sym| {
                sym.path == doc.path
                    && sym.role == "reference"
                    && sym.kind == "module"
                    && imports.iter().any(|name| name == &sym.name)
            });
            if shares {
                peers.push(Value::string(&doc.path));
            }
        }
        Value::object(vec![
            ("path", Value::string(path)),
            ("co_importers", Value::Array(peers)),
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
    ) -> Result<Value, String> {
        let now = now_secs();
        self.store.state.locks.retain(|lock| lock.until > now);
        match action {
            "acquire" | "refresh" => {
                if let Some(existing) = self.store.state.locks.iter().find(|lock| lock.path == path)
                {
                    if existing.agent != agent {
                        return Err(format!("locked by {}", existing.agent));
                    }
                }
                self.store.state.locks.retain(|lock| lock.path != path);
                self.store.state.locks.push(Lock {
                    path: path.to_string(),
                    agent: agent.to_string(),
                    until: now + 300,
                });
                self.store.save("lock", &Value::string(path))?;
                Ok(Value::object(vec![
                    ("locked", Value::Bool(true)),
                    ("locked_by", Value::string(agent)),
                ]))
            }
            "release" | "release_all" => {
                self.store.state.locks.retain(|lock| {
                    if action == "release_all" {
                        lock.agent != agent
                    } else {
                        !(lock.path == path && lock.agent == agent)
                    }
                });
                self.store.save("unlock", &Value::string(path))?;
                Ok(Value::object(vec![("released", Value::Bool(true))]))
            }
            "status" => {
                let lock = self.store.state.locks.iter().find(|lock| lock.path == path);
                Ok(Value::object(vec![
                    ("locked", Value::Bool(lock.is_some())),
                    (
                        "locked_by",
                        lock.map(|item| Value::string(&item.agent))
                            .unwrap_or(Value::Null),
                    ),
                ]))
            }
            "conflicts" => Ok(Value::object(vec![("conflicts", Value::Array(Vec::new()))])),
            "reap" => {
                let before = self.store.state.locks.len();
                self.store.state.locks.retain(|lock| lock.until > now);
                Ok(Value::object(vec![(
                    "reaped",
                    Value::Int((before - self.store.state.locks.len()) as i64),
                )]))
            }
            _ => Err(format!("unknown document_lock action '{action}'")),
        }
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
            path: path.to_string(),
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
                Some(Value::object(vec![
                    ("chunk_id", Value::string(&chunk.id)),
                    ("document_path", Value::string(&chunk.path)),
                    ("content", Value::string(&chunk.content)),
                    ("relevance_score", Value::Float(hit.score)),
                    ("token_count", Value::Int(chunk.tokens as i64)),
                    ("start_pos", Value::Int(chunk.start as i64)),
                    ("end_pos", Value::Int(chunk.end as i64)),
                    ("source", Value::string(hit.source)),
                    ("tier2_present", Value::Bool(chunk.summary.is_some())),
                ]))
            })
            .collect()
    }

    fn note_search(&mut self, session: &str, pattern: &str) {
        let slot = self.session_mut(session);
        slot.searches.push(pattern.to_string());
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

fn is_literal(pattern: &str) -> bool {
    !pattern.chars().any(|ch| {
        matches!(
            ch,
            '\\' | '.' | '[' | ']' | '(' | ')' | '{' | '}' | '*' | '+' | '?' | '^' | '$' | '|'
        )
    })
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
