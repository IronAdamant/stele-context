//! Standard-mode tools and the 1.5 singleton names.
//! Each function does the work. None of them return a placeholder.

use super::{now_secs, Engine};
use crate::json::Value;
use crate::store::{ChangeRow, ChunkHist, SearchRow};
use std::fs;
use std::path::Path;

impl Engine {
    pub fn prune_history(
        &mut self,
        max_age: Option<i64>,
        max_entries: Option<i64>,
    ) -> Result<Value, String> {
        let now = now_secs();
        let before = self.store.state.change_log.len();
        if let Some(age) = max_age {
            let cutoff = now - age;
            self.store.state.change_log.retain(|row| row.at >= cutoff);
        }
        if let Some(max) = max_entries {
            let max = max.max(0) as usize;
            if self.store.state.change_log.len() > max {
                let drop_n = self.store.state.change_log.len() - max;
                self.store.state.change_log.drain(0..drop_n);
            }
        }
        let pruned = (before - self.store.state.change_log.len()) as i64;
        // A no-op prune must not rewrite the snapshot. Index calls this after
        // its own save, and a second fsync showed up as a cold-index regression.
        if pruned > 0 {
            self.store.save("prune_history", &Value::Int(pruned))?;
        }
        Ok(Value::object(vec![
            ("pruned", Value::Int(pruned)),
            ("operation_log_pruned", Value::Int(0)),
        ]))
    }

    pub fn rollback(&mut self, session_id: &str, target_turn: i64) -> Result<Value, String> {
        let Some(session) = self
            .store
            .state
            .sessions
            .iter_mut()
            .find(|item| item.id == session_id)
        else {
            return Ok(Value::object(vec![(
                "error",
                Value::string("Session not found"),
            )]));
        };
        if target_turn < 0 {
            return Ok(Value::object(vec![(
                "error",
                Value::string("Target turn must be non-negative"),
            )]));
        }
        if target_turn >= session.turn {
            return Ok(Value::object(vec![(
                "error",
                Value::string(format!(
                    "Target turn {target_turn} >= current turn {}",
                    session.turn
                )),
            )]));
        }
        let previous = session.turn;
        let removed = session
            .attached
            .iter()
            .filter(|(_, turn, _)| *turn > target_turn)
            .count();
        session.attached.retain(|(_, turn, _)| *turn <= target_turn);
        session.turn = target_turn;
        session.updated_at = now_secs();
        self.store.save("rollback", &Value::string(session_id))?;
        Ok(Value::object(vec![
            ("session_id", Value::string(session_id)),
            ("previous_turn", Value::Int(previous)),
            ("current_turn", Value::Int(target_turn)),
            ("chunks_removed", Value::Int(removed as i64)),
        ]))
    }

    pub fn prune_session_chunks(
        &mut self,
        session_id: &str,
        max_tokens: i64,
    ) -> Result<Value, String> {
        let Some(session) = self
            .store
            .state
            .sessions
            .iter_mut()
            .find(|item| item.id == session_id)
        else {
            return Ok(Value::object(vec![(
                "error",
                Value::string("Session not found"),
            )]));
        };
        let chunks = &self.store.state.chunks;
        let mut rows: Vec<(String, i64, f64, i64)> = session
            .attached
            .iter()
            .map(|(id, turn, score)| {
                let tokens = chunks
                    .iter()
                    .find(|chunk| chunk.id == *id)
                    .map(|chunk| chunk.tokens as i64)
                    .unwrap_or(0);
                (id.clone(), *turn, *score, tokens)
            })
            .collect();
        let current: i64 = rows.iter().map(|row| row.3).sum();
        if current <= max_tokens {
            return Ok(Value::object(vec![
                ("session_id", Value::string(session_id)),
                ("current_tokens", Value::Int(current)),
                ("max_tokens", Value::Int(max_tokens)),
                ("chunks_pruned", Value::Int(0)),
                ("message", Value::string("Already under limit")),
            ]));
        }
        rows.sort_by(|a, b| a.2.partial_cmp(&b.2).unwrap_or(std::cmp::Ordering::Equal));
        let mut kept = Vec::new();
        let mut used = 0i64;
        let mut pruned = 0i64;
        for row in rows.into_iter().rev() {
            if used + row.3 <= max_tokens
                || kept.is_empty() && used == 0 && row.3 > max_tokens && kept.is_empty()
            {
                // keep highest relevance first until the budget is spent
            }
            if used + row.3 <= max_tokens {
                used += row.3;
                kept.push((row.0, row.1, row.2));
            } else {
                pruned += 1;
            }
        }
        session.attached = kept;
        session.tokens = used;
        let previous = current;
        self.store
            .save("prune_chunks", &Value::string(session_id))?;
        Ok(Value::object(vec![
            ("session_id", Value::string(session_id)),
            ("previous_tokens", Value::Int(previous)),
            ("current_tokens", Value::Int(used)),
            ("max_tokens", Value::Int(max_tokens)),
            ("chunks_pruned", Value::Int(pruned)),
            ("tokens_saved", Value::Int(previous - used)),
        ]))
    }

    pub fn list_sessions(&self, agent: Option<&str>) -> Value {
        let mut rows = Vec::new();
        for session in &self.store.state.sessions {
            if let Some(agent) = agent {
                if session.agent != agent {
                    continue;
                }
            }
            rows.push(Value::object(vec![
                ("session_id", Value::string(&session.id)),
                ("created_at", Value::Int(session.created_at)),
                ("last_updated", Value::Int(session.updated_at)),
                ("turn_count", Value::Int(session.turn)),
                ("total_tokens", Value::Int(session.tokens)),
                (
                    "agent_id",
                    if session.agent.is_empty() {
                        Value::Null
                    } else {
                        Value::string(&session.agent)
                    },
                ),
            ]));
        }
        Value::Array(rows)
    }

    pub fn rebuild_symbols(&mut self) -> Result<Value, String> {
        self.store.state.symbols.clear();
        let chunks: Vec<_> = self.store.state.chunks.clone();
        for chunk in &chunks {
            let ext = chunk
                .language
                .clone()
                .unwrap_or_else(|| super::extension_of(&chunk.path).to_string());
            for hit in crate::symbols::extract(&ext, &chunk.content) {
                self.store.state.symbols.push(crate::store::Symbol {
                    name: hit.name,
                    kind: hit.kind,
                    role: hit.role,
                    line: hit.line,
                    container: hit.container,
                    chunk_id: chunk.id.clone(),
                    path: chunk.path.clone(),
                    chunk_index: 0,
                });
            }
        }
        self.store.state.edges = crate::symbols::build_edges(&self.store.state.symbols);
        let documents = self.store.state.documents.len() as i64;
        let symbols = self.store.state.symbols.len() as i64;
        let edges = self.store.state.edges.len() as i64;
        self.store.save("rebuild_symbols", &Value::Int(symbols))?;
        Ok(Value::object(vec![
            ("documents", Value::Int(documents)),
            ("symbols", Value::Int(symbols)),
            ("edges", Value::Int(edges)),
        ]))
    }

    pub fn stale_chunks(&self, threshold: f64, max_age: Option<i64>) -> Value {
        let now = now_secs();
        let mut by_path: Vec<(String, Vec<Value>)> = Vec::new();
        for chunk in &self.store.state.chunks {
            if chunk.staleness < threshold {
                continue;
            }
            if let Some(age) = max_age {
                if let Some(since) = chunk.stale_since {
                    if since < now - age {
                        continue;
                    }
                }
            }
            let preview: String = chunk.content.chars().take(200).collect();
            let row = Value::object(vec![
                ("chunk_id", Value::string(&chunk.id)),
                ("staleness_score", Value::Float(chunk.staleness)),
                ("token_count", Value::Int(chunk.tokens as i64)),
                ("content_preview", Value::string(preview)),
            ]);
            if let Some(group) = by_path.iter_mut().find(|(path, _)| path == &chunk.path) {
                group.1.push(row);
            } else {
                by_path.push((chunk.path.clone(), vec![row]));
            }
        }
        let total: usize = by_path.iter().map(|(_, rows)| rows.len()).sum();
        let mut pairs = vec![
            ("threshold", Value::Float(threshold)),
            ("total_stale", Value::Int(total as i64)),
            ("files_affected", Value::Int(by_path.len() as i64)),
            (
                "by_document",
                Value::Array(
                    by_path
                        .into_iter()
                        .map(|(path, rows)| {
                            Value::object(vec![
                                ("path", Value::string(path)),
                                ("chunks", Value::Array(rows)),
                            ])
                        })
                        .collect(),
                ),
            ),
        ];
        if threshold <= 0.3 && total > 50 {
            pairs.push((
                "guidance",
                Value::string("Many chunks are stale at the default threshold. Pass threshold 0.5 or 0.64, or max_age_seconds."),
            ));
        }
        Value::Object(pairs.into_iter().map(|(k, v)| (k.to_string(), v)).collect())
    }

    pub fn dynamic_symbols(&self, agent: Option<&str>) -> Value {
        let rows = self
            .store
            .state
            .dynamic
            .iter()
            .filter(|sym| agent.is_none_or(|id| sym.agent == id))
            .map(|sym| {
                Value::object(vec![
                    ("name", Value::string(&sym.name)),
                    ("kind", Value::string(&sym.kind)),
                    ("role", Value::string(&sym.role)),
                    ("chunk_id", Value::string(&sym.chunk_id)),
                    ("document_path", Value::string(&sym.path)),
                    ("line_number", Value::Int(sym.line as i64)),
                ])
            })
            .collect();
        Value::Array(rows)
    }

    pub fn list_agents(&self, active_only: bool) -> Value {
        let now = now_secs();
        let rows = read_coord_array(&self.cwd, "agents");
        let filtered: Vec<Value> = rows
            .into_iter()
            .filter(|row| {
                if !active_only {
                    return true;
                }
                text_of(row, "status") != "stopped"
            })
            .map(|row| {
                let beat = int_of(&row, "last_heartbeat");
                let mut pairs = match row {
                    Value::Object(pairs) => pairs,
                    other => return other,
                };
                pairs.push(("stale".to_string(), Value::Bool(now - beat > 600)));
                Value::Object(pairs)
            })
            .collect();
        Value::Array(filtered)
    }

    pub fn notifications(
        &mut self,
        since: Option<i64>,
        exclude: Option<&str>,
        limit: i64,
    ) -> Value {
        let now = now_secs();
        let mut rows = read_coord_array(&self.cwd, "notifications");
        rows.retain(|row| int_of(row, "created_at") >= now - 86400);
        if let Some(since) = since {
            rows.retain(|row| int_of(row, "created_at") > since);
        }
        if let Some(exclude) = exclude {
            rows.retain(|row| text_of(row, "agent_id") != exclude);
        }
        rows.sort_by(|a, b| int_of(b, "created_at").cmp(&int_of(a, "created_at")));
        let limit = limit.max(0) as usize;
        if rows.len() > limit {
            rows.truncate(limit);
        }
        let latest = rows
            .iter()
            .map(|row| int_of(row, "created_at"))
            .max()
            .unwrap_or(0);
        let count = rows.len() as i64;
        Value::object(vec![
            ("notifications", Value::Array(rows)),
            ("count", Value::Int(count)),
            ("latest_timestamp", Value::Int(latest)),
        ])
    }

    pub fn environment_check(&self) -> Value {
        let root = crate::root::detect_project_root(&self.cwd);
        let Some(root) = root else {
            return Value::object(vec![
                ("issues", Value::Array(Vec::new())),
                ("total_issues", Value::Int(0)),
            ]);
        };
        let mut issues = Vec::new();
        let stale = stale_pycache(&root);
        if stale.0 > 0 {
            issues.push(Value::object(vec![
                ("type", Value::string("stale_bytecache")),
                ("total_stale_files", Value::Int(stale.0)),
                (
                    "stale_dirs",
                    Value::Array(stale.1.into_iter().map(Value::string).collect()),
                ),
            ]));
        }
        if let Some(egg) = egg_info_issue(&root) {
            issues.push(egg);
        }
        Value::object(vec![
            ("issues", Value::Array(issues.clone())),
            ("total_issues", Value::Int(issues.len() as i64)),
        ])
    }

    pub fn clean_bytecache(&mut self) -> Value {
        let root = crate::root::detect_project_root(&self.cwd);
        let Some(root) = root else {
            return Value::object(vec![("cleaned", Value::Int(0))]);
        };
        let (count, dirs) = stale_pycache(&root);
        for dir in dirs {
            let _ = fs::remove_dir_all(dir);
        }
        Value::object(vec![
            ("stale_dirs", Value::Array(Vec::new())),
            ("total_stale_files", Value::Int(count)),
            ("cleaned", Value::Int(count)),
        ])
    }

    pub fn bulk_embeddings(&mut self, pairs: &[(String, Vec<f64>)]) -> Result<Value, String> {
        let mut stored = 0i64;
        let mut errors = Vec::new();
        for (id, values) in pairs {
            if values.len() != 128 {
                errors.push(Value::string(format!("{id}: expected 128 floats")));
                continue;
            }
            let Some(index) = self
                .store
                .state
                .chunks
                .iter()
                .position(|chunk| chunk.id == *id)
            else {
                errors.push(Value::string(id.clone()));
                continue;
            };
            self.store.ensure_sigs();
            let mut sig = [0f32; 128];
            for (lane, value) in values.iter().enumerate() {
                sig[lane] = *value as f32;
            }
            self.store.state.sigs[index] = sig;
            stored += 1;
        }
        self.store.save("embeddings", &Value::Int(stored))?;
        Ok(Value::object(vec![
            ("stored", Value::Int(stored)),
            ("errors", Value::Array(errors)),
            ("total", Value::Int(pairs.len() as i64)),
        ]))
    }

    pub fn bulk_notes(&mut self, pairs: &[(String, String)]) -> Result<Value, String> {
        let mut stored = 0i64;
        let mut errors = Vec::new();
        for (id, notes) in pairs {
            let Some(chunk) = self
                .store
                .state
                .chunks
                .iter_mut()
                .find(|chunk| chunk.id == *id)
            else {
                errors.push(Value::string(id.clone()));
                continue;
            };
            chunk.notes = Some(notes.clone());
            stored += 1;
        }
        self.store.save("notes", &Value::Int(stored))?;
        Ok(Value::object(vec![
            ("stored", Value::Int(stored)),
            ("errors", Value::Array(errors)),
            ("total", Value::Int(pairs.len() as i64)),
        ]))
    }

    pub fn bulk_summaries(&mut self, pairs: &[(String, String)]) -> Result<Value, String> {
        let mut stored = 0i64;
        let mut errors = Vec::new();
        for (id, summary) in pairs {
            let Some(chunk) = self
                .store
                .state
                .chunks
                .iter_mut()
                .find(|chunk| chunk.id == *id)
            else {
                errors.push(Value::string(id.clone()));
                continue;
            };
            chunk.summary = Some(summary.clone());
            stored += 1;
        }
        self.store.save("summaries", &Value::Int(stored))?;
        Ok(Value::object(vec![
            ("stored", Value::Int(stored)),
            ("errors", Value::Array(errors)),
            ("total", Value::Int(pairs.len() as i64)),
        ]))
    }

    pub fn chunk_history(&self, chunk_id: Option<&str>, path: Option<&str>, limit: i64) -> Value {
        let mut rows: Vec<&ChunkHist> = self
            .store
            .state
            .chunk_history
            .iter()
            .filter(|row| chunk_id.is_none_or(|id| row.chunk_id == id))
            .filter(|row| path.is_none_or(|path| row.path == path))
            .collect();
        rows.truncate(limit.max(0) as usize);
        Value::Array(
            rows.into_iter()
                .map(|row| {
                    Value::object(vec![
                        ("chunk_id", Value::string(&row.chunk_id)),
                        ("version", Value::Int(row.version as i64)),
                        ("content_hash", Value::string(&row.hash)),
                        ("created_at", Value::Int(row.at)),
                        ("document_path", Value::string(&row.path)),
                    ])
                })
                .collect(),
        )
    }

    pub fn record_change(&mut self, summary: &str, session: &str, reason: &str) {
        self.store.state.change_log.push(ChangeRow {
            at: now_secs(),
            session: session.to_string(),
            summary: summary.to_string(),
            reason: reason.to_string(),
        });
    }

    pub fn record_search_row(
        &mut self,
        session: &str,
        pattern: &str,
        tool: &str,
        checked: i64,
        matched: i64,
    ) {
        let slot = self.session_slot(session);
        slot.searches.push(pattern.to_string());
        slot.search_rows.push(SearchRow {
            pattern: pattern.to_string(),
            tool: tool.to_string(),
            files_checked: checked,
            files_with_matches: matched,
            at: now_secs(),
        });
    }

    pub fn store_paths(&self) -> Vec<String> {
        self.store
            .state
            .documents
            .iter()
            .map(|doc| doc.path.clone())
            .collect()
    }

    pub fn llm_embed(
        &mut self,
        text: &str,
        chunk_id: &str,
        fingerprint: Option<Vec<f64>>,
    ) -> Result<Value, String> {
        if chunk_id.is_empty() {
            return Ok(Value::object(vec![
                ("stored", Value::Bool(false)),
                ("error", Value::string("chunk_id is required")),
            ]));
        }
        let vector = match fingerprint {
            Some(values) if values.len() == 128 => values,
            Some(values) if values.len() == 32 => expand_fingerprint(&values),
            Some(_) => {
                return Ok(Value::object(vec![
                    ("stored", Value::Bool(false)),
                    (
                        "error",
                        Value::string("fingerprint_values must have 32 or 128 numbers"),
                    ),
                ]));
            }
            None => super::fingerprint::embed_text(text),
        };
        if !self
            .store
            .state
            .chunks
            .iter()
            .any(|chunk| chunk.id == chunk_id)
        {
            self.store.state.chunks.push(crate::store::Chunk {
                id: chunk_id.to_string(),
                path: format!("memory:{chunk_id}"),
                start: 0,
                end: text.chars().count(),
                content: text.chars().take(4000).collect(),
                sha256: String::new(),
                signature: String::new(),
                tokens: crate::chunk::estimate_tokens(text).max(1),
                modality: "text".to_string(),
                language: None,
                summary: None,
                notes: None,
                version: 1,
                staleness: 0.0,
                stale_since: None,
            });
            self.store.state.sigs.push([0f32; 128]);
        }
        self.bulk_embeddings(&[(chunk_id.to_string(), vector)])?;
        Ok(Value::object(vec![
            ("stored", Value::Bool(true)),
            ("chunk_id", Value::string(chunk_id)),
        ]))
    }

    pub fn batch_ops(&mut self, args: &Value) -> Result<Value, String> {
        let rows = match field_ops(args, "operations") {
            Some(Value::Array(rows)) => rows.clone(),
            _ => Vec::new(),
        };
        let mut out = Vec::new();
        for (index, row) in rows.iter().enumerate() {
            let method = match field_ops(row, "method") {
                Some(Value::String(text)) => text.clone(),
                _ => String::new(),
            };
            let params = field_ops(row, "params")
                .cloned()
                .unwrap_or(Value::object(vec![]));
            let result = self.batch_one(&method, &params);
            match result {
                Ok(value) => out.push(Value::object(vec![
                    ("index", Value::Int(index as i64)),
                    ("method", Value::string(method)),
                    ("success", Value::Bool(true)),
                    ("result", value),
                ])),
                Err(err) => out.push(Value::object(vec![
                    ("index", Value::Int(index as i64)),
                    ("method", Value::string(method)),
                    ("error", Value::string(err)),
                ])),
            }
        }
        Ok(Value::object(vec![
            ("operations", Value::Array(out)),
            ("total", Value::Int(rows.len() as i64)),
        ]))
    }

    fn batch_one(&mut self, method: &str, params: &Value) -> Result<Value, String> {
        let path = text_ops(params, "path").or_else(|| text_ops(params, "document_path"));
        let session = text_ops(params, "session_id").unwrap_or_else(|| "default".to_string());
        match method {
            "index" | "index_documents" => {
                let paths = match field_ops(params, "paths") {
                    Some(Value::Array(rows)) => rows
                        .iter()
                        .filter_map(|item| match item {
                            Value::String(text) => Some(text.clone()),
                            _ => None,
                        })
                        .collect(),
                    _ => vec![path.unwrap_or_else(|| ".".to_string())],
                };
                self.index(&paths, false)
            }
            "detect_changes" | "detect_changes_and_update" => self.detect(true),
            "search" => Ok(self.search(
                &text_ops(params, "query").unwrap_or_default(),
                "keyword",
                10,
            )),
            "prune_history" => self.prune_history(None, None),
            "list_sessions" => Ok(self.list_sessions(None)),
            "rebuild_symbols" | "rebuild_symbol_graph" => self.rebuild_symbols(),
            "stale_chunks" => Ok(self.stale_chunks(0.3, None)),
            "environment_check" | "check_environment" => Ok(self.environment_check()),
            "clean_bytecache" => Ok(self.clean_bytecache()),
            "get_stats" | "stats" => Ok(self.stats()),
            "doctor_snapshot" | "doctor" => Ok(self.stats()),
            "rollback" => self.rollback(&session, 0),
            _ => Err(format!("batch method '{method}' is not available")),
        }
    }

    pub fn remove_agent_dynamic(&mut self, agent: &str) -> i64 {
        let before = self.store.state.dynamic.len();
        let prefix = format!("runtime:{agent}:");
        self.store
            .state
            .dynamic
            .retain(|sym| sym.agent != agent && !sym.chunk_id.starts_with(&prefix));
        (before - self.store.state.dynamic.len()) as i64
    }

    pub fn store_dynamic_last(&mut self) -> Option<&mut crate::store::Dynamic> {
        self.store.state.dynamic.last_mut()
    }

    pub fn apply_path_summaries(&mut self, pairs: &[(String, String)]) -> Result<(), String> {
        for (path, summary) in pairs {
            for chunk in &mut self.store.state.chunks {
                if &chunk.path == path {
                    chunk.summary = Some(summary.clone());
                }
            }
        }
        self.store
            .save("summaries", &Value::Int(pairs.len() as i64))?;
        Ok(())
    }

    pub fn context_many(
        &mut self,
        paths: &[String],
        session: Option<&str>,
    ) -> Result<Value, String> {
        self.context_paths(paths, session)
    }

    pub fn index_working_tree(&mut self) -> Vec<String> {
        let root = crate::root::detect_project_root(&self.cwd);
        let Some(root) = root else {
            return Vec::new();
        };
        let output = std::process::Command::new("git")
            .arg("-C")
            .arg(&root)
            .args(["status", "--porcelain", "-u"])
            .output();
        let Ok(output) = output else {
            return Vec::new();
        };
        if !output.status.success() {
            return Vec::new();
        }
        let text = String::from_utf8_lossy(&output.stdout);
        let mut paths = Vec::new();
        for line in text.lines() {
            if line.len() < 4 {
                continue;
            }
            let mut path = line[3..].trim();
            if let Some((_, dest)) = path.split_once(" -> ") {
                path = dest;
            }
            let abs = root.join(path);
            if abs.is_file() {
                paths.push(path.replace('\\', "/"));
            }
        }
        if paths.is_empty() {
            return Vec::new();
        }
        let indexed = self.index(&paths, false).ok();
        let _ = indexed;
        paths
    }

    fn session_slot(&mut self, id: &str) -> &mut crate::store::Session {
        if !self.store.state.sessions.iter().any(|item| item.id == id) {
            self.store.state.sessions.push(crate::store::Session {
                id: id.to_string(),
                searches: Vec::new(),
                reads: Vec::new(),
                kv: Vec::new(),
                created_at: now_secs(),
                updated_at: now_secs(),
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

fn egg_info_issue(root: &Path) -> Option<Value> {
    let (proj_name, proj_version) = project_name_version(root);
    let mut issues = Vec::new();
    let Ok(read) = fs::read_dir(root) else {
        return None;
    };
    for entry in read.flatten() {
        let path = entry.path();
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !name.ends_with(".egg-info") || !path.is_dir() {
            continue;
        }
        let info = path.join("PKG-INFO");
        let Ok(text) = fs::read_to_string(&info) else {
            continue;
        };
        let mut egg_name = String::new();
        let mut egg_version = String::new();
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("Name:") {
                egg_name = rest.trim().to_string();
            } else if let Some(rest) = line.strip_prefix("Version:") {
                egg_version = rest.trim().to_string();
            }
            if !egg_name.is_empty() && !egg_version.is_empty() {
                break;
            }
        }
        if egg_name.is_empty() || egg_version.is_empty() {
            continue;
        }
        let dir = path.display().to_string();
        if !proj_name.is_empty() && canonical(&egg_name) != canonical(&proj_name) {
            issues.push(Value::object(vec![
                ("egg_info_dir", Value::string(&dir)),
                ("egg_info_version", Value::string(&egg_version)),
                ("installed_version", Value::Null),
                ("reason", Value::string("orphaned_name")),
                (
                    "warning",
                    Value::string(format!(
                        "'{name}' is for distribution '{egg_name}' but this project is '{proj_name}' — likely left over from a package rename. It provides a phantom distribution to any Python process run from this directory. Delete the directory."
                    )),
                ),
            ]));
            continue;
        }
        if !proj_version.is_empty() && egg_version != proj_version {
            issues.push(Value::object(vec![
                ("egg_info_dir", Value::string(&dir)),
                ("egg_info_version", Value::string(&egg_version)),
                ("installed_version", Value::Null),
                ("reason", Value::string("stale_vs_project")),
                (
                    "warning",
                    Value::string(format!(
                        "'{name}' has version {egg_version} but pyproject.toml declares {proj_version}, and '{egg_name}' is not installed. Python run from this directory reads the stale metadata. Delete the directory or rebuild the package."
                    )),
                ),
            ]));
        }
    }
    if issues.is_empty() {
        return None;
    }
    Some(Value::object(vec![
        ("type", Value::string("stale_egg_info")),
        ("egg_info_issues", Value::Array(issues.clone())),
        ("count", Value::Int(issues.len() as i64)),
    ]))
}

fn project_name_version(root: &Path) -> (String, String) {
    let Ok(text) = fs::read_to_string(root.join("pyproject.toml")) else {
        return (String::new(), String::new());
    };
    let mut name = String::new();
    let mut version = String::new();
    let mut in_project = false;
    for raw in text.lines() {
        let line = raw.trim();
        if line.starts_with('[') && line.ends_with(']') {
            in_project = line == "[project]";
            continue;
        }
        if !in_project {
            continue;
        }
        if let Some(rest) = line.strip_prefix("name") {
            if let Some(value) = toml_string(rest) {
                name = value;
            }
        } else if let Some(rest) = line.strip_prefix("version") {
            if let Some(value) = toml_string(rest) {
                version = value;
            }
        }
    }
    (name, version)
}

fn toml_string(rest: &str) -> Option<String> {
    let rest = rest.trim().strip_prefix('=')?.trim();
    let rest = rest.strip_prefix('"').or_else(|| rest.strip_prefix('\''))?;
    let end = rest.find(['"', '\'']).unwrap_or(rest.len());
    Some(rest[..end].to_string())
}

fn canonical(name: &str) -> String {
    name.replace(['_', '.'], "-").to_ascii_lowercase()
}

fn stale_pycache(root: &Path) -> (i64, Vec<String>) {
    let mut count = 0i64;
    let mut dirs = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(read) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in read.flatten() {
            let path = entry.path();
            if path.is_dir() {
                if path.file_name().and_then(|n| n.to_str()) == Some("__pycache__") {
                    let files = fs::read_dir(&path)
                        .map(|it| {
                            it.flatten()
                                .filter(|e| {
                                    e.path().extension().and_then(|x| x.to_str()) == Some("pyc")
                                })
                                .count()
                        })
                        .unwrap_or(0);
                    if files > 0 {
                        count += files as i64;
                        dirs.push(path.display().to_string());
                    }
                } else if path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|name| !name.starts_with('.'))
                {
                    stack.push(path);
                }
            }
        }
    }
    (count, dirs)
}

fn read_coord_array(cwd: &Path, key: &str) -> Vec<Value> {
    let Some(root) = crate::root::detect_project_root(cwd) else {
        return Vec::new();
    };
    let path = root
        .join(".git")
        .join("stele-context")
        .join("coordination.json");
    let Ok(text) = fs::read_to_string(path) else {
        return Vec::new();
    };
    let Ok(value) = crate::json::parse(&text) else {
        return Vec::new();
    };
    match field(&value, key) {
        Some(Value::Array(rows)) => rows.clone(),
        _ => Vec::new(),
    }
}

fn expand_fingerprint(values: &[f64]) -> Vec<f64> {
    let mut vec = vec![0.0; 128];
    let sigma = 1.2f64;
    for (index, value) in values.iter().take(32).enumerate() {
        let norm = (value + 1.0) / 2.0;
        let base = index * 4;
        for offset in 0..4 {
            let weight = (-((offset * offset) as f64) / (2.0 * sigma * sigma)).exp();
            if base + offset < 128 {
                vec[base + offset] += norm * weight;
            }
        }
    }
    let norm = vec.iter().map(|item| item * item).sum::<f64>().sqrt();
    if norm > 0.0 {
        for item in &mut vec {
            *item /= norm;
        }
    }
    vec
}

fn field_ops<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    field(value, key)
}

fn text_ops(value: &Value, key: &str) -> Option<String> {
    match field(value, key) {
        Some(Value::String(text)) if !text.is_empty() => Some(text.clone()),
        _ => None,
    }
}

fn field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    let Value::Object(pairs) = value else {
        return None;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, item)| item)
}

fn text_of(value: &Value, key: &str) -> String {
    match field(value, key) {
        Some(Value::String(text)) => text.clone(),
        _ => String::new(),
    }
}

fn int_of(value: &Value, key: &str) -> i64 {
    match field(value, key) {
        Some(Value::Int(number)) => *number,
        Some(Value::Float(number)) => *number as i64,
        _ => 0,
    }
}
