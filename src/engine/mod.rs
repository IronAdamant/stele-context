//! Index, detect, and the commands that read the native store.
//! Public methods take a project-relative path and honor mtime+size.

mod fingerprint;
mod migrate;
mod ops;
#[cfg(test)]
mod parity_test;
mod query;

use crate::chunk::{self, Produced};
use crate::config;
use crate::hash::{hex, sha256};
use crate::json::Value;
use crate::root::{detect_project_root, resolve_storage_dir};
use crate::search::Bm25;
use crate::store::{Chunk, Document, Store, Symbol};
use crate::symbols;
use crate::walk;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub struct Engine {
    pub cwd: PathBuf,
    pub storage: PathBuf,
    store: Store,
    bm25: Bm25,
    bm25_ready: bool,
    hnsw: crate::search::Hnsw,
    chunk_size: usize,
    max_chunk_size: usize,
    respect_gitignore: bool,
    skip_dirs: Vec<String>,
    merge_threshold: f64,
    change_threshold: f64,
    search_alpha: f64,
    max_history_entries: i64,
    /// Detect can answer from document mtime and size. Chunk bodies stay
    /// unloaded until a file actually differs.
    documents_only: bool,
    server_agent: String,
    file_reads: Vec<(String, String, Vec<String>, f64)>,
    operations: u64,
    failures: u64,
}

impl Engine {
    pub fn open(storage_flag: Option<&Path>) -> Result<Engine, String> {
        let cwd = std::env::current_dir().map_err(|err| format!("cwd: {err}"))?;
        let root = detect_project_root(&cwd);
        let loaded = config::load(root.as_deref());
        let storage = resolve_storage_dir(storage_flag, &loaded, root.as_deref())?;
        Self::finish(cwd, storage, loaded, false)
    }

    pub fn open_from(cwd: PathBuf, storage_flag: Option<&Path>) -> Result<Engine, String> {
        let root = detect_project_root(&cwd);
        let loaded = config::load(root.as_deref());
        let storage = resolve_storage_dir(storage_flag, &loaded, root.as_deref())?;
        Self::finish(cwd, storage, loaded, false)
    }

    /// Open for `detect`. Document rows are enough while every indexed file
    /// still matches mtime and size.
    pub fn open_detect(cwd: PathBuf, storage_flag: Option<&Path>) -> Result<Engine, String> {
        let root = detect_project_root(&cwd);
        let loaded = config::load(root.as_deref());
        let storage = resolve_storage_dir(storage_flag, &loaded, root.as_deref())?;
        Self::finish(cwd, storage, loaded, true)
    }

    pub fn open_at(cwd: PathBuf, storage: PathBuf) -> Result<Engine, String> {
        let root = detect_project_root(&cwd);
        let loaded = config::load(root.as_deref());
        Self::finish(cwd, storage, loaded, false)
    }

    fn finish(
        cwd: PathBuf,
        storage: PathBuf,
        loaded: config::Config,
        documents_only: bool,
    ) -> Result<Engine, String> {
        let legacy = storage.join("stele_context.db");
        let documents_only = documents_only && !legacy.is_file();
        let mut store = if documents_only {
            Store::open_documents(&storage)?
        } else {
            Store::open(&storage)?
        };
        if legacy.is_file() {
            let err_path = storage.join("store").join("migrate-error.txt");
            if let Err(err) = migrate::migrate_if_needed(&storage, &cwd, &mut store) {
                let _ = fs::write(&err_path, &err);
            } else if err_path.is_file() {
                let _ = fs::remove_file(&err_path);
            }
        }
        Ok(Engine {
            cwd,
            storage,
            store,
            bm25: Bm25::new(),
            bm25_ready: false,
            hnsw: crate::search::Hnsw::new(),
            chunk_size: loaded.chunk_size.unwrap_or(256).max(1) as usize,
            max_chunk_size: loaded.max_chunk_size.unwrap_or(4096).max(1) as usize,
            respect_gitignore: loaded.respect_gitignore.unwrap_or(true),
            skip_dirs: loaded.skip_dirs.clone().unwrap_or_default(),
            merge_threshold: loaded.merge_threshold.unwrap_or(0.7),
            change_threshold: loaded.change_threshold.unwrap_or(0.85),
            search_alpha: loaded.search_alpha.unwrap_or(0.42),
            max_history_entries: loaded.max_history_entries.unwrap_or(1000),
            documents_only,
            server_agent: String::new(),
            file_reads: Vec::new(),
            operations: 0,
            failures: 0,
        })
        .map(|mut engine| {
            if !engine.documents_only {
                engine.rebuild_hnsw();
                // 1.5.2 reloads the graph with insert_count = len(nodes).
                engine.store.state.inserts = engine.store.state.chunks.len() as u64;
            }
            engine
        })
    }

    /// MCP stdio registers this id, the same way 1.5.2 registers
    /// `stele-context-mcp-<pid>`, and index takes its document locks.
    pub(crate) fn server_agent(&self) -> &str {
        &self.server_agent
    }

    pub fn remember_server_agent(&mut self, id: &str) {
        self.server_agent = id.to_string();
    }

    pub fn register_server_agent(&self) {
        if self.server_agent.is_empty() {
            return;
        }
        let root = self.cwd.display().to_string();
        crate::locks::register_agent(&self.cwd, &self.server_agent, &root);
    }

    pub fn set_server_agent(&mut self, id: &str) {
        self.remember_server_agent(id);
        self.register_server_agent();
    }

    pub fn index(&mut self, paths: &[String], force: bool) -> Result<Value, String> {
        if self.documents_only && !force && !self.store.state.documents.is_empty() {
            if let Some(result) = self.index_if_unchanged(paths)? {
                return Ok(result);
            }
            self.store.reload_full()?;
            self.documents_only = false;
        }
        let files = collect(&self.cwd, paths, self.respect_gitignore, &self.skip_dirs)?;
        let mut indexed_rows = Vec::new();
        let mut skipped_rows = Vec::new();
        let mut seen = Vec::new();
        let mut fresh = Vec::new();
        let mut total_chunks = 0i64;
        let mut total_tokens = 0i64;
        let known: std::collections::HashMap<String, (u64, i64, i64)> = self
            .store
            .state
            .documents
            .iter()
            .map(|doc| {
                (
                    doc.path.clone(),
                    (doc.size, doc.mtime_ns, doc.chunk_count as i64),
                )
            })
            .collect();
        for (rel, abs) in &files {
            seen.push(rel.clone());
            let meta = fs::metadata(abs).map_err(|err| format!("{rel}: {err}"))?;
            if !force {
                if let Some((size, mtime, count)) = known.get(rel) {
                    if *size == meta.len() && *mtime == mtime_ns(&meta) {
                        let count = *count;
                        skipped_rows.push(Value::object(vec![
                            ("path", Value::string(rel)),
                            ("reason", Value::string("Unchanged")),
                            ("chunk_count", Value::Int(count)),
                        ]));
                        continue;
                    }
                }
            }
            let (chunk_count, tokens, modality) = self.reindex_file(rel, abs, &meta)?;
            total_chunks += chunk_count;
            total_tokens += tokens;
            fresh.push(rel.clone());
            indexed_rows.push(Value::object(vec![
                ("path", Value::string(rel)),
                ("chunk_count", Value::Int(chunk_count)),
                ("total_tokens", Value::Int(tokens)),
                ("modality", Value::string(modality)),
            ]));
        }
        let before = self.store.state.documents.len();
        self.drop_unseen(paths, &seen);
        let removed = before as i64 - self.store.state.documents.len() as i64;
        if !indexed_rows.is_empty() || removed > 0 || force {
            self.rebuild_bm25();
            self.store.save(
                "index",
                &Value::object(vec![
                    ("indexed", Value::Int(indexed_rows.len() as i64)),
                    ("skipped", Value::Int(skipped_rows.len() as i64)),
                    ("removed", Value::Int(removed.max(0))),
                ]),
            )?;
            if self.max_history_entries > 0
                && self.store.state.change_log.len() > self.max_history_entries as usize
            {
                let _ = self.prune_history(None, Some(self.max_history_entries));
            }
            crate::locks::note_indexed(&self.cwd, &fresh, &self.server_agent);
            if !self.server_agent.is_empty() {
                let now = now_secs();
                let agent = self.server_agent.clone();
                for path in &fresh {
                    crate::locks::hold_shared(&self.cwd, path, &agent, now);
                }
            }
        }
        Ok(Value::object(vec![
            ("indexed", Value::Array(indexed_rows)),
            ("skipped", Value::Array(skipped_rows)),
            ("errors", Value::Array(Vec::new())),
            ("conflicts", Value::Array(Vec::new())),
            ("total_chunks", Value::Int(total_chunks)),
            ("total_tokens", Value::Int(total_tokens)),
        ]))
    }

    pub fn detect(&mut self, scan_new: bool) -> Result<Value, String> {
        self.detect_with(scan_new, None, 200, "")
    }

    pub fn detect_with(
        &mut self,
        scan_new: bool,
        only: Option<&[String]>,
        limit: usize,
        agent: &str,
    ) -> Result<Value, String> {
        let agent = if agent.is_empty() {
            self.server_agent.clone()
        } else {
            agent.to_string()
        };
        if let Some(session) = self
            .store
            .state
            .sessions
            .iter_mut()
            .find(|session| session.id == "default")
        {
            if !agent.is_empty() {
                session.agent = agent.clone();
            }
        } else {
            self.store.state.sessions.push(crate::store::Session {
                id: "default".to_string(),
                searches: Vec::new(),
                reads: Vec::new(),
                kv: Vec::new(),
                created_at: now_us(),
                updated_at: now_us(),
                turn: 0,
                tokens: 0,
                agent: agent.clone(),
                attached: Vec::new(),
                kv_state: Vec::new(),
                search_rows: Vec::new(),
            });
        }
        let files = collect(
            &self.cwd,
            &[".".to_string()],
            self.respect_gitignore,
            &self.skip_dirs,
        )?;
        let mut unchanged = Vec::new();
        let mut modified = Vec::new();
        let mut new_rows = Vec::new();
        let mut removed = Vec::new();
        let mut kv_restored = 0i64;
        let mut kv_reprocessed = 0i64;
        if only.is_none() {
            let mut aligned = true;
            unchanged.reserve(self.store.state.documents.len());
            for doc in &self.store.state.documents {
                let abs = self.cwd.join(&doc.path);
                let Ok(meta) = fs::metadata(&abs) else {
                    aligned = false;
                    break;
                };
                if doc.size == meta.len() && doc.mtime_ns == mtime_ns(&meta) {
                    unchanged.push(doc.path.clone());
                } else {
                    aligned = false;
                    break;
                }
            }
            if aligned {
                if scan_new {
                    let have: std::collections::HashSet<&str> = self
                        .store
                        .state
                        .documents
                        .iter()
                        .map(|doc| doc.path.as_str())
                        .collect();
                    for (rel, _) in &files {
                        if !have.contains(rel.as_str()) {
                            new_rows.push(Value::object(vec![
                                ("path", Value::string(rel)),
                                ("reason", Value::string("New file (scan)")),
                            ]));
                        }
                    }
                }
                let mut result = Value::object(vec![
                    ("unchanged", strings(&unchanged)),
                    ("modified", Value::Array(Vec::new())),
                    ("new", Value::Array(new_rows)),
                    ("removed", Value::Array(Vec::new())),
                    ("conflicts", Value::Array(Vec::new())),
                    ("kv_restored", Value::Int(0)),
                    ("kv_reprocessed", Value::Int(0)),
                ]);
                if limit > 0 {
                    truncate_detect(&mut result, limit);
                }
                self.remember_change(&result);
                return Ok(result);
            }
            if self.documents_only {
                self.store.reload_full()?;
                self.documents_only = false;
                self.rebuild_hnsw();
            }
            unchanged.clear();
        }
        let docs: std::collections::HashMap<String, (u64, i64)> = self
            .store
            .state
            .documents
            .iter()
            .map(|doc| (doc.path.clone(), (doc.size, doc.mtime_ns)))
            .collect();
        let indexed: Vec<String> = if let Some(only) = only {
            if only.is_empty() {
                docs.keys().cloned().collect()
            } else {
                only.to_vec()
            }
        } else {
            docs.keys().cloned().collect()
        };
        if scan_new {
            for (rel, _) in &files {
                if !docs.contains_key(rel) {
                    new_rows.push(Value::object(vec![
                        ("path", Value::string(rel)),
                        ("reason", Value::string("New file (scan)")),
                    ]));
                }
            }
        }
        for rel in &indexed {
            let abs = self.cwd.join(rel);
            if !abs.is_file() {
                removed.push(rel.clone());
                self.drop_path(rel);
                continue;
            }
            let meta = fs::metadata(&abs).map_err(|err| format!("{rel}: {err}"))?;
            let unchanged_fast = docs
                .get(rel.as_str())
                .is_some_and(|(size, mtime)| *size == meta.len() && *mtime == mtime_ns(&meta));
            if unchanged_fast {
                unchanged.push(rel.clone());
                continue;
            }
            let bytes = fs::read(&abs).map_err(|err| format!("{rel}: {err}"))?;
            let media = crate::media::is_media(crate::chunk::extension_of(rel));
            let sha = if media {
                hex(&sha256(&bytes))
            } else {
                hex(&sha256(chunk::read_python_text(&bytes).as_bytes()))
            };
            let old = self
                .store
                .state
                .documents
                .iter()
                .find(|doc| &doc.path == rel)
                .map(|doc| doc.sha256.clone())
                .unwrap_or_default();
            if old == sha {
                unchanged.push(rel.clone());
                continue;
            }
            let old_short: String = old.chars().take(16).collect();
            let new_short: String = sha.chars().take(16).collect();
            modified.push(Value::object(vec![
                ("path", Value::string(rel)),
                ("old_hash", Value::string(old_short)),
                ("new_hash", Value::string(new_short)),
            ]));
            let produced = chunk::chunk_bytes(
                rel,
                &bytes,
                self.chunk_size,
                self.max_chunk_size,
                self.merge_threshold,
            );
            let old_chunks: Vec<(usize, usize, String, String)> = self
                .store
                .state
                .chunks
                .iter()
                .filter(|chunk| chunk.path == *rel)
                .map(|chunk| {
                    (
                        chunk.start,
                        chunk.end,
                        chunk.sha256.clone(),
                        chunk.content.clone(),
                    )
                })
                .collect();
            let all_sigs: Vec<[f64; 128]> = self
                .store
                .state
                .chunks
                .iter()
                .map(|chunk| chunk::signature(&chunk.content))
                .collect();
            let (restored, reprocessed) =
                classify_kv(&old_chunks, &all_sigs, &produced, self.change_threshold);
            kv_restored += restored;
            kv_reprocessed += reprocessed;
            self.reindex_file(rel, &abs, &meta)?;
        }
        if !modified.is_empty() {
            let paths: Vec<String> = modified
                .iter()
                .map(|row| crate::store::string_field(row, "path"))
                .filter(|path| !path.is_empty())
                .collect();
            crate::locks::note_change(&self.cwd, &paths, &agent, "modified");
        }
        if !removed.is_empty() {
            crate::locks::note_change(&self.cwd, &removed, &agent, "removed");
        }
        if !removed.is_empty() || !modified.is_empty() {
            self.rebuild_bm25();
            self.store.save("detect", &Value::object(vec![]))?;
        }
        let mut result = Value::object(vec![
            ("unchanged", strings(&unchanged)),
            ("modified", Value::Array(modified)),
            ("new", Value::Array(new_rows)),
            ("removed", strings(&removed)),
            ("conflicts", Value::Array(Vec::new())),
            ("kv_restored", Value::Int(kv_restored)),
            ("kv_reprocessed", Value::Int(kv_reprocessed)),
        ]);
        if limit > 0 {
            truncate_detect(&mut result, limit);
        }
        self.remember_change(&result);
        Ok(result)
    }

    pub fn remove(&mut self, path: &str) -> Result<Value, String> {
        let rel = normalize(path);
        let known = self.store.state.documents.iter().any(|doc| doc.path == rel);
        if !known {
            return Ok(Value::object(vec![("removed", Value::Bool(false))]));
        }
        let chunk_ids: Vec<String> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == rel)
            .map(|chunk| chunk.id.clone())
            .collect();
        let annotations_removed = self
            .store
            .state
            .annotations
            .iter()
            .filter(|note| note.path == rel)
            .count();
        let chunks_removed = chunk_ids.len() as i64;
        self.drop_path(&rel);
        self.rebuild_bm25();
        crate::locks::note_change(&self.cwd, &[rel.clone()], &self.server_agent, "removed");
        self.store.save(
            "remove",
            &Value::object(vec![("path", Value::string(&rel))]),
        )?;
        Ok(Value::object(vec![
            ("removed", Value::Bool(true)),
            (
                "chunk_ids",
                Value::Array(chunk_ids.into_iter().map(Value::String).collect()),
            ),
            ("chunks_removed", Value::Int(chunks_removed)),
            (
                "annotations_removed",
                Value::Int(annotations_removed as i64),
            ),
        ]))
    }

    pub fn stats(&self) -> Value {
        let mut storage = self.storage_report();
        if let Value::Object(pairs) = &mut storage {
            if let Some((_, item)) = pairs.iter_mut().find(|(key, _)| key == "history_count") {
                *item = Value::Int(self.store.state.change_log.len() as i64);
            }
        }
        let chunks = self.store.state.chunks.len() as i64;
        let symbols = (self.store.state.symbols.len() + self.store.state.dynamic.len()) as i64;
        let ready = symbols > 0;
        Value::object(vec![
            ("version", Value::string(crate::VERSION)),
            ("storage", storage),
            (
                "index",
                Value::object(vec![
                    ("node_count", Value::Int(chunks)),
                    ("max_level", Value::Int(self.hnsw.max_level())),
                    ("avg_connections", Value::Float(self.hnsw.avg_connections())),
                    ("insert_count", Value::Int(self.store.state.inserts as i64)),
                    ("chunk_count", Value::Int(chunks)),
                ]),
            ),
            (
                "config",
                Value::object(vec![
                    ("chunk_size", Value::Int(self.chunk_size as i64)),
                    ("max_chunk_size", Value::Int(self.max_chunk_size as i64)),
                    ("merge_threshold", Value::Float(self.merge_threshold)),
                    ("change_threshold", Value::Float(self.change_threshold)),
                    ("search_alpha", Value::Float(self.search_alpha)),
                ]),
            ),
            ("index_health", self.index_health(chunks, symbols, ready)),
            (
                "project_root",
                Value::string(self.cwd.display().to_string()),
            ),
        ])
    }

    fn index_health(&self, chunks: i64, symbols: i64, ready: bool) -> Value {
        Value::object(vec![
            (
                "documents",
                Value::Int(self.store.state.documents.len() as i64),
            ),
            ("chunks", Value::Int(chunks)),
            ("symbol_rows", Value::Int(symbols)),
            ("symbols_ready", Value::Bool(ready)),
            ("latest_indexed_at", self.indexed_stamp()),
            (
                "storage_dir",
                Value::string(self.storage.display().to_string()),
            ),
            (
                "symbol_graph_status",
                Value::string(if ready { "ready" } else { "empty" }),
            ),
            ("chunk_store_status", Value::string("ready")),
            (
                "seconds_since_last_index",
                Value::Float(self.seconds_since_last_index()),
            ),
            ("alerts", Value::Array(Vec::new())),
        ])
    }

    /// Storage block of Python `get_stats()["storage"]`, for `GET /health`.
    pub fn storage_report(&self) -> Value {
        let state = &self.store.state;
        let tokens: i64 = state.chunks.iter().map(|chunk| chunk.tokens as i64).sum();
        let definitions = state.dynamic.len() as i64
            + state
                .symbols
                .iter()
                .filter(|symbol| symbol.role == "definition")
                .count() as i64;
        let references = state
            .symbols
            .iter()
            .filter(|symbol| symbol.role == "reference")
            .count() as i64;
        let now = now_secs();
        let expired = state.locks.iter().filter(|lock| lock.until <= now).count() as i64;
        let mut agents = Vec::new();
        for lock in &state.locks {
            if !agents.iter().any(|agent| agent == &lock.agent) {
                agents.push(lock.agent.clone());
            }
        }
        let last_conflict = state.conflicts.iter().map(|row| row.at).max();
        let kv_bytes = kv_cache_bytes(&self.storage);
        let store_dir = self.storage.join("store");
        let database_size = file_len(&store_dir.join("snapshot.json"))
            + file_len(&store_dir.join("journal.jsonl"))
            + file_len(&store_dir.join("signatures.bin"))
            + file_len(&store_dir.join("bm25.bin"));
        Value::object(vec![
            ("chunk_count", Value::Int(state.chunks.len() as i64)),
            ("document_count", Value::Int(state.documents.len() as i64)),
            ("session_count", Value::Int(state.sessions.len() as i64)),
            ("total_tokens", Value::Int(tokens)),
            (
                "version_count",
                Value::Int(state.chunk_history.len() as i64),
            ),
            (
                "annotation_count",
                Value::Int(state.annotations.len() as i64),
            ),
            (
                "history_count",
                Value::Int(journal_lines(&store_dir.join("journal.jsonl")) as i64),
            ),
            ("kv_cache_size_bytes", Value::Int(kv_bytes as i64)),
            ("database_size_bytes", Value::Int(database_size as i64)),
            (
                "storage_dir",
                Value::string(self.storage.display().to_string()),
            ),
            (
                "symbol_count",
                Value::Int((state.symbols.len() + state.dynamic.len()) as i64),
            ),
            ("definition_count", Value::Int(definitions)),
            ("reference_count", Value::Int(references)),
            ("edge_count", Value::Int(state.edges.len() as i64)),
            (
                "runtime_symbol_count",
                Value::Int(state.dynamic.len() as i64),
            ),
            ("locked_documents", Value::Int(state.locks.len() as i64)),
            ("expired_locks", Value::Int(expired)),
            ("active_lock_agents", Value::Int(agents.len() as i64)),
            ("total_conflicts", Value::Int(state.conflicts.len() as i64)),
            (
                "last_conflict_at",
                last_conflict.map(Value::Int).unwrap_or(Value::Null),
            ),
        ])
    }

    pub fn map(&self) -> Value {
        self.map_with(None, None, None)
    }

    pub fn map_with(
        &self,
        prefix: Option<&str>,
        max_documents: Option<usize>,
        max_ann_chars: Option<usize>,
    ) -> Value {
        let mut docs: Vec<Value> = self
            .store
            .state
            .documents
            .iter()
            .filter(|doc| path_under(&self.cwd, prefix, &doc.path))
            .map(|doc| {
                let chunks: Vec<_> = self
                    .store
                    .state
                    .chunks
                    .iter()
                    .filter(|chunk| chunk.path == doc.path)
                    .collect();
                let tokens: i64 = chunks.iter().map(|chunk| chunk.tokens as i64).sum();
                let notes: Vec<Value> = self
                    .store
                    .state
                    .annotations
                    .iter()
                    .filter(|note| note.path == doc.path && note.target_type == "document")
                    .map(|note| {
                        Value::object(vec![
                            ("id", Value::Int(note.id.parse::<i64>().unwrap_or(0))),
                            (
                                "content",
                                Value::string(trim_ann(&note.body, max_ann_chars)),
                            ),
                            (
                                "tags",
                                Value::Array(
                                    note.tags.iter().cloned().map(Value::String).collect(),
                                ),
                            ),
                        ])
                    })
                    .collect();
                Value::object(vec![
                    ("path", Value::string(&doc.path)),
                    ("chunk_count", Value::Int(chunks.len() as i64)),
                    ("total_tokens", Value::Int(tokens)),
                    ("indexed_at", Value::Float(doc.indexed_at)),
                    ("annotations", Value::Array(notes)),
                ])
            })
            .collect();
        docs.sort_by(|left, right| {
            crate::store::int_field(right, "total_tokens")
                .cmp(&crate::store::int_field(left, "total_tokens"))
                .then(
                    crate::store::string_field(left, "path")
                        .cmp(&crate::store::string_field(right, "path")),
                )
        });
        let total_before_cap = docs.len();
        if let Some(max) = max_documents {
            if docs.len() > max {
                docs.truncate(max);
            }
        }
        let shown = docs.len() as i64;
        let total_tokens: i64 = docs
            .iter()
            .map(|doc| crate::store::int_field(doc, "total_tokens"))
            .sum();
        let total = total_before_cap as i64;
        let chunks = self.store.state.chunks.len() as i64;
        let symbols = self.store.state.symbols.len() as i64;
        let mut out = Value::object(vec![
            ("documents", Value::Array(docs)),
            ("total_documents", Value::Int(total)),
            ("total_tokens", Value::Int(total_tokens)),
            (
                "index_health",
                self.index_health(chunks, symbols, !self.store.state.symbols.is_empty()),
            ),
            ("compact", Value::Bool(true)),
            (
                "project_root",
                Value::string(self.cwd.display().to_string()),
            ),
        ]);
        if max_documents.is_some() {
            if let Value::Object(pairs) = &mut out {
                pairs.push(("documents_omitted".to_string(), Value::Int(total - shown)));
            }
        }
        out
    }

    pub fn project_brief(&self) -> Value {
        let mut files: Vec<(i64, String, i64, f64)> = Vec::new();
        for doc in &self.store.state.documents {
            let chunks: Vec<_> = self
                .store
                .state
                .chunks
                .iter()
                .filter(|chunk| chunk.path == doc.path)
                .collect();
            let tokens = chunks.iter().map(|chunk| chunk.tokens as i64).sum();
            files.push((
                tokens,
                doc.path.clone(),
                chunks.len() as i64,
                doc.indexed_at,
            ));
        }
        files.sort_by(|left, right| right.0.cmp(&left.0).then(left.1.cmp(&right.1)));
        let largest: Vec<Value> = files
            .into_iter()
            .take(40)
            .map(|(tokens, path, count, indexed_at)| {
                Value::object(vec![
                    ("path", Value::string(path)),
                    ("chunk_count", Value::Int(count)),
                    ("total_tokens", Value::Int(tokens)),
                    ("indexed_at", Value::Float(indexed_at)),
                ])
            })
            .collect();
        let mut extensions: Vec<(String, i64)> = Vec::new();
        for doc in &self.store.state.documents {
            let ext = Path::new(&doc.path)
                .extension()
                .and_then(|ext| ext.to_str())
                .map(|ext| format!(".{ext}"))
                .unwrap_or_default();
            if let Some(slot) = extensions.iter_mut().find(|(name, _)| name == &ext) {
                slot.1 += 1;
            } else {
                extensions.push((ext, 1));
            }
        }
        extensions.sort_by(|left, right| left.0.cmp(&right.0));
        let tokens: i64 = self
            .store
            .state
            .chunks
            .iter()
            .map(|chunk| chunk.tokens as i64)
            .sum();
        let chunks = self.store.state.chunks.len() as i64;
        let symbols = self.store.state.symbols.len() as i64;
        Value::object(vec![
            (
                "purpose",
                Value::string("One-screen orientation for LLM agents; use before deep search."),
            ),
            (
                "totals",
                Value::object(vec![
                    (
                        "documents",
                        Value::Int(self.store.state.documents.len() as i64),
                    ),
                    ("chunks", Value::Int(chunks)),
                    ("total_tokens", Value::Int(tokens)),
                ]),
            ),
            (
                "extensions",
                Value::Object(
                    extensions
                        .into_iter()
                        .map(|(ext, count)| (ext, Value::Int(count)))
                        .collect(),
                ),
            ),
            ("largest_files_by_tokens", Value::Array(largest)),
            (
                "index_health",
                self.index_health(chunks, symbols, !self.store.state.symbols.is_empty()),
            ),
            (
                "project_root",
                Value::string(self.cwd.display().to_string()),
            ),
        ])
    }

    pub fn enrichment_plan(&self) -> Value {
        self.enrichment_plan_with(15, 20)
    }

    pub fn enrichment_plan_with(&self, top_n: usize, min_tokens: i64) -> Value {
        let mut missing: Vec<Value> = Vec::new();
        let mut tier2_tokens = 0i64;
        let mut total_tokens = 0i64;
        for chunk in &self.store.state.chunks {
            let tokens = chunk.tokens as i64;
            total_tokens += tokens;
            if chunk.summary.is_some() {
                tier2_tokens += tokens;
                continue;
            }
            if tokens < min_tokens {
                continue;
            }
            let preview: String = chunk.content.chars().take(120).collect();
            missing.push(Value::object(vec![
                ("chunk_id", Value::string(&chunk.id)),
                ("document_path", Value::string(&chunk.path)),
                ("token_count", Value::Int(tokens)),
                ("preview", Value::string(preview)),
            ]));
        }
        missing.sort_by(|left, right| {
            crate::store::int_field(right, "token_count")
                .cmp(&crate::store::int_field(left, "token_count"))
                .then(
                    crate::store::string_field(left, "document_path")
                        .cmp(&crate::store::string_field(right, "document_path")),
                )
        });
        let uncovered: i64 = missing
            .iter()
            .map(|row| crate::store::int_field(row, "token_count"))
            .sum();
        let total_missing = missing.len() as i64;
        let selected: Vec<Value> = missing.into_iter().take(top_n).collect();
        let pct = if total_tokens == 0 {
            0.0
        } else {
            (tier2_tokens as f64 / total_tokens as f64) * 100.0
        };
        let pct = (pct * 100.0).round() / 100.0;
        Value::object(vec![
            ("top_n", Value::Int(top_n as i64)),
            ("candidates", Value::Array(selected)),
            ("candidates_total", Value::Int(total_missing)),
            ("uncovered_tokens", Value::Int(uncovered)),
            ("tier2_token_coverage_percent", Value::Float(pct)),
            ("total_tokens", Value::Int(total_tokens)),
            (
                "how_to_enrich",
                Value::string(
                    "Call bulk_store_summaries with {chunk_id: short purpose/exports summary} for the candidates below. Prefer hot paths agents already read.",
                ),
            ),
        ])
    }

    fn remember_change(&mut self, result: &crate::json::Value) {
        let row = crate::store::ChangeRow {
            at: now_us(),
            session: "default".to_string(),
            summary: crate::json::to_string(result),
            reason: String::new(),
        };
        self.store.state.change_log.push(row.clone());
        let path = self.storage.join("store").join("changes.jsonl");
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
        {
            use std::io::Write;
            let _ = writeln!(file, "{}", crate::store::ChangeRow::to_line(&row));
        }
    }

    pub fn note_operation(&mut self) {
        self.operations += 1;
    }

    pub fn note_failure(&mut self) {
        self.failures += 1;
    }

    /// The 1.5.2 `doctor_snapshot` object. There is no SQLite file, so the
    /// pragma fields are null. Byte sizes and the operation counters are
    /// measured. `recommended_action` uses the 1.5.2 busy-ratio bands.
    pub fn doctor_snapshot(&self) -> Value {
        let documents = self.store.state.documents.len() as i64;
        let chunks = self.store.state.chunks.len() as i64;
        let symbols = self.store.state.symbols.len() as i64;
        let tokens: i64 = self
            .store
            .state
            .chunks
            .iter()
            .map(|chunk| chunk.tokens as i64)
            .sum();
        let tier2_chunks = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.summary.is_some())
            .count() as i64;
        let chunk_pct = if chunks == 0 {
            0.0
        } else {
            (tier2_chunks as f64 / chunks as f64) * 100.0
        };
        let chunk_pct = (chunk_pct * 100.0).round() / 100.0;
        let advice = if chunk_pct < 5.0 {
            Some(
                "Tier-2 coverage is critically low. Most semantic searches will fall back to BM25. Run llm_embed on representative chunks or populate semantic_summary fields to enable Tier-2 ranking.",
            )
        } else if chunk_pct < 80.0 {
            Some("below")
        } else {
            None
        };
        let advice = match advice {
            Some("below") => Value::string(format!(
                "Tier-2 coverage is {chunk_pct:.1}% — below the 80% target for sharp semantic search. Run llm_embed or populate semantic_summary on the remaining chunks."
            )),
            Some(text) => Value::string(text),
            None => Value::Null,
        };
        let ready = symbols > 0;
        let mut health = self.index_health(chunks, symbols, ready);
        if let Value::Object(pairs) = &mut health {
            pairs.retain(|(key, _)| key != "latest_indexed_at");
            pairs.push(("latest_indexed_at".to_string(), self.indexed_stamp()));
            pairs.push((
                "seconds_since_last_index".to_string(),
                Value::Float(self.seconds_since_last_index()),
            ));
        }
        let avg = if documents == 0 {
            0.0
        } else {
            (tokens as f64 / documents as f64 * 10.0).round() / 10.0
        };
        let ritual = "doctor → query (session_id) → agent_grep / find_definition|find_references as needed → get_context (trust/diff) → detect_changes after edits";
        let mut steps = vec![Value::object(vec![
            ("action", Value::string("query")),
            (
                "detail",
                Value::string(
                    "Start broad questions with `query` (pass session_id for auto working_tree). Prefer agent_grep / find_* for exact identifiers.",
                ),
            ),
        ])];
        if chunk_pct < 5.0
            || crate::store::int_field(&self.enrichment_plan(), "candidates_total") > 0
        {
            steps.push(Value::object(vec![
                ("action", Value::string("enrich_tier2")),
                (
                    "detail",
                    Value::string(format!(
                        "Tier-2 coverage is {chunk_pct:.1}% (target ≥20% on hot paths). Use enrichment_plan / doctor enrichment_preview, then bulk_store_summaries on top candidates."
                    )),
                ),
            ]));
        }
        steps.push(Value::object(vec![
            ("action", Value::string("ritual")),
            ("detail", Value::string(ritual)),
        ]));
        let mut preview = self.map_with(None, Some(8), Some(120));
        if let Value::Object(pairs) = &mut preview {
            if let Some((_, health_slot)) = pairs.iter_mut().find(|(key, _)| key == "index_health")
            {
                if let Value::Object(health_pairs) = health_slot {
                    health_pairs.retain(|(key, _)| key != "latest_indexed_at");
                    health_pairs.push(("latest_indexed_at".to_string(), self.indexed_stamp()));
                    health_pairs.push((
                        "seconds_since_last_index".to_string(),
                        Value::Float(self.seconds_since_last_index()),
                    ));
                }
            }
        }
        Value::object(vec![
            ("stele_version", Value::string(crate::VERSION)),
            ("python", host_python()),
            (
                "project_root",
                Value::string(self.cwd.display().to_string()),
            ),
            (
                "storage_dir",
                Value::string(self.storage.display().to_string()),
            ),
            ("document_count", Value::Int(documents)),
            ("chunk_count", Value::Int(chunks)),
            ("index_health", health),
            (
                "db_health",
                Value::object(vec![
                    (
                        "table_rows",
                        Value::object(vec![
                            (
                                "change_history",
                                Value::Int(self.store.state.change_log.len() as i64),
                            ),
                            (
                                "chunk_history",
                                Value::Int(self.store.state.chunk_history.len() as i64),
                            ),
                            ("operation_log", Value::Int(self.operations as i64)),
                            (
                                "sessions",
                                Value::Int(self.store.state.sessions.len() as i64),
                            ),
                        ]),
                    ),
                    ("growth_alerts", Value::Array(Vec::new())),
                    ("journal_mode", Value::Null),
                    ("synchronous", Value::Null),
                    ("page_count", Value::Null),
                    ("page_size", Value::Null),
                    ("database_size_bytes", Value::Int(store_bytes(&self.storage))),
                    ("wal_size_bytes", Value::Int(wal_bytes(&self.storage))),
                    ("wal_autocheckpoint", Value::Null),
                    ("busy_timeout_ms", Value::Null),
                    (
                        "recent_operations_1h",
                        Value::Int(self.operations as i64),
                    ),
                    ("recent_failures_1h", Value::Int(self.failures as i64)),
                    (
                        "busy_ratio",
                        Value::Float(busy_ratio(self.failures, self.operations)),
                    ),
                    (
                        "recommended_action",
                        Value::string(busy_action(self.failures, self.operations)),
                    ),
                ]),
            ),
            (
                "search_quality",
                Value::object(vec![
                    ("total_chunks", Value::Int(chunks)),
                    ("tier2_chunks", Value::Int(tier2_chunks)),
                    ("tier2_coverage_percent", Value::Float(chunk_pct)),
                    ("hnsw_span", Value::Float(self.hnsw_span())),
                    ("advice", advice),
                ]),
            ),
            ("environment", self.environment_check()),
            ("map_preview", preview),
            ("recommended_mcp_mode", Value::string("lite")),
            ("recommended_ritual", Value::string(ritual)),
            (
                "mcp_config",
                Value::string(
                    "{\n  \"mcpServers\": {\n    \"stele-context\": {\n      \"command\": \"stele-context\",\n      \"args\": [\"serve-mcp\"],\n      \"env\": {\n        \"STELE_MCP_MODE\": \"lite\"\n      }\n    }\n  }\n}",
                ),
            ),
            ("next_steps", Value::Array(steps)),
            (
                "token_savings",
                Value::object(vec![
                    ("indexed_tokens", Value::Int(tokens)),
                    ("document_count", Value::Int(documents)),
                    ("chunk_count", Value::Int(chunks)),
                    ("avg_tokens_per_document", Value::Float(avg)),
                    ("avoided_reread_tokens_if_cache_hit", Value::Int(tokens)),
                    (
                        "note",
                        Value::string(format!(
                            "If the agent reuses the index instead of re-reading every file, up to ~{tokens} tokens of full-file re-read can be avoided (use get_context + diff_since_cache for deltas)."
                        )),
                    ),
                ]),
            ),
            ("enrichment_preview", enrichment_preview(&self.enrichment_plan())),
        ])
    }

    pub fn history(&self) -> Result<Value, String> {
        let logged = if self.store.state.change_log.is_empty() {
            crate::store::ChangeRow::load_file(&self.storage)
        } else {
            self.store.state.change_log.clone()
        };
        let mut rows: Vec<Value> = logged
            .iter()
            .enumerate()
            .map(|(index, row)| {
                let summary =
                    crate::json::parse(&row.summary).unwrap_or(Value::string(&row.summary));
                Value::object(vec![
                    ("id", Value::Int(index as i64 + 1)),
                    (
                        "reason",
                        if row.reason.is_empty() {
                            Value::Null
                        } else {
                            Value::string(&row.reason)
                        },
                    ),
                    ("session_id", Value::string(&row.session)),
                    ("summary", summary),
                    ("timestamp", Value::Float(session_time(row.at))),
                ])
            })
            .collect();
        rows.reverse();
        Ok(Value::Array(rows))
    }

    pub fn clear(&mut self) -> Result<Value, String> {
        self.store.state = crate::store::State::default();
        self.bm25 = Bm25::new();
        self.bm25_ready = true;
        self.store.save("clear", &Value::object(vec![]))?;
        Ok(Value::object(vec![("cleared", Value::Bool(true))]))
    }

    pub fn migrate(&mut self) -> Result<Value, String> {
        let outcome = migrate::migrate_if_needed(&self.storage, &self.cwd, &mut self.store)?;
        self.bm25_ready = false;
        self.rebuild_bm25();
        match outcome {
            migrate::Legacy::Absent => {
                Err("no Python stele_context.db in the storage directory".to_string())
            }
            migrate::Legacy::Kept => Ok(Value::object(vec![
                ("migrated", Value::Bool(false)),
                (
                    "reason",
                    Value::string(
                        "native store already has documents; legacy database was left untouched",
                    ),
                ),
                ("legacy_untouched", Value::Bool(true)),
            ])),
            migrate::Legacy::Copied { documents, chunks } => Ok(Value::object(vec![
                ("migrated", Value::Bool(true)),
                ("documents", Value::Int(documents)),
                ("chunks", Value::Int(chunks)),
                ("symbols", Value::Int(self.store.state.symbols.len() as i64)),
                ("legacy_untouched", Value::Bool(true)),
            ])),
        }
    }

    /// `Ok(Some)` when every indexed file still matches mtime and size.
    /// The caller has not loaded chunk bodies, so a miss must reload first.
    fn index_if_unchanged(&self, paths: &[String]) -> Result<Option<Value>, String> {
        let files = collect(&self.cwd, paths, self.respect_gitignore, &self.skip_dirs)?;
        let known: std::collections::HashMap<&str, (u64, i64, i64)> = self
            .store
            .state
            .documents
            .iter()
            .map(|doc| {
                (
                    doc.path.as_str(),
                    (doc.size, doc.mtime_ns, doc.chunk_count as i64),
                )
            })
            .collect();
        let mut skipped_rows = Vec::new();
        let mut seen = std::collections::HashSet::new();
        for (rel, abs) in &files {
            seen.insert(rel.as_str());
            let Some((size, mtime, count)) = known.get(rel.as_str()).copied() else {
                return Ok(None);
            };
            let meta = fs::metadata(abs).map_err(|err| format!("{rel}: {err}"))?;
            if size != meta.len() || mtime != mtime_ns(&meta) {
                return Ok(None);
            }
            skipped_rows.push(Value::object(vec![
                ("path", Value::string(rel)),
                ("reason", Value::string("Unchanged")),
                ("chunk_count", Value::Int(count)),
            ]));
        }
        let whole = paths.is_empty() || paths.iter().any(|path| path == "." || path == "./");
        if whole
            && self
                .store
                .state
                .documents
                .iter()
                .any(|doc| !seen.contains(doc.path.as_str()))
        {
            return Ok(None);
        }
        Ok(Some(Value::object(vec![
            ("indexed", Value::Array(Vec::new())),
            ("skipped", Value::Array(skipped_rows)),
            ("errors", Value::Array(Vec::new())),
            ("conflicts", Value::Array(Vec::new())),
            ("total_chunks", Value::Int(0)),
            ("total_tokens", Value::Int(0)),
        ])))
    }

    fn reindex_file(
        &mut self,
        rel: &str,
        abs: &Path,
        meta: &fs::Metadata,
    ) -> Result<(i64, i64, String), String> {
        let bytes = fs::read(abs).map_err(|err| format!("{rel}: {err}"))?;
        let media = crate::media::is_media(crate::chunk::extension_of(rel));
        let sha = if media {
            hex(&sha256(&bytes))
        } else {
            hex(&sha256(chunk::read_python_text(&bytes).as_bytes()))
        };
        let version = self
            .store
            .state
            .documents
            .iter()
            .find(|doc| doc.path == rel)
            .map(|doc| doc.doc_version)
            .unwrap_or(0)
            + 1;
        self.drop_path(rel);
        let produced = chunk::chunk_bytes(
            rel,
            &bytes,
            self.chunk_size,
            self.max_chunk_size,
            self.merge_threshold,
        );
        for piece in &produced {
            self.commit_piece(rel, piece);
        }
        self.store.state.documents.push(Document {
            path: rel.to_string(),
            mtime_ns: mtime_ns(meta),
            size: meta.len(),
            sha256: sha,
            doc_version: version,
            chunk_count: produced.len() as u32,
            indexed_at: now_f64(),
        });
        let mine: Vec<_> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == rel)
            .collect();
        let tokens = mine.iter().map(|chunk| chunk.tokens as i64).sum();
        let modality = mine
            .first()
            .map(|chunk| chunk.modality.clone())
            .unwrap_or_else(|| "code".to_string());
        Ok((mine.len() as i64, tokens, modality))
    }

    fn drop_unseen(&mut self, paths: &[String], seen: &[String]) {
        let whole = paths.is_empty() || paths.iter().any(|path| path == "." || path == "./");
        let prefixes: Vec<String> = paths
            .iter()
            .filter_map(|path| {
                let abs = self.cwd.join(path);
                abs.is_dir()
                    .then(|| normalize(path).trim_end_matches('/').to_string())
            })
            .filter(|prefix| prefix != "." && !prefix.is_empty())
            .collect();
        let drop_doc = |path: &str| {
            if seen.iter().any(|item| item == path) {
                return false;
            }
            if whole {
                return true;
            }
            prefixes
                .iter()
                .any(|prefix| path == prefix || path.starts_with(&format!("{prefix}/")))
        };
        self.store
            .state
            .documents
            .retain(|doc| !drop_doc(&doc.path));
        self.store
            .state
            .chunks
            .retain(|chunk| !drop_doc(&chunk.path));
        self.store.state.symbols.retain(|sym| !drop_doc(&sym.path));
    }

    fn drop_path(&mut self, rel: &str) {
        self.store.state.documents.retain(|doc| doc.path != rel);
        let gone: Vec<String> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == rel)
            .map(|chunk| chunk.id.clone())
            .collect();
        self.store.state.chunks.retain(|chunk| chunk.path != rel);
        self.store.state.symbols.retain(|sym| sym.path != rel);
        for id in gone {
            self.hnsw.remove(&id);
        }
    }

    fn rebuild_bm25(&mut self) {
        let fingerprint = bm25_fingerprint(&self.store.state.chunks);
        let path = self.storage.join("store").join("bm25.bin");
        let Engine {
            store,
            bm25,
            bm25_ready,
            ..
        } = self;
        store.state.edges = crate::symbols::build_edges(&store.state.symbols);
        *bm25 = Bm25::new();
        for chunk in &store.state.chunks {
            // Image, audio, and video bytes are not keyword text. PDF text is.
            if matches!(chunk.modality.as_str(), "image" | "audio" | "video") {
                continue;
            }
            // 1.5.2 skips a chunk whose content is empty. Whitespace still counts.
            if chunk.content.is_empty() {
                continue;
            }
            bm25.add(&chunk.id, &chunk.content);
        }
        *bm25_ready = true;
        let _ = crate::search::save_bm25(bm25, &path, fingerprint);
    }

    fn ensure_bm25(&mut self) {
        if self.bm25_ready {
            return;
        }
        let fingerprint = bm25_fingerprint(&self.store.state.chunks);
        let path = self.storage.join("store").join("bm25.bin");
        if let Some(index) = crate::search::load_bm25(&path, fingerprint) {
            self.bm25 = index;
            self.bm25_ready = true;
            return;
        }
        self.rebuild_bm25();
    }
}

fn busy_ratio(failures: u64, operations: u64) -> f64 {
    if operations == 0 {
        return 0.0;
    }
    let ratio = failures as f64 / operations as f64;
    (ratio * 10_000.0).round() / 10_000.0
}

fn busy_action(failures: u64, operations: u64) -> &'static str {
    let ratio = if operations == 0 {
        0.0
    } else {
        failures as f64 / operations as f64
    };
    if ratio < 0.01 {
        "idle"
    } else if ratio < 0.1 {
        "vacuum"
    } else {
        "rebuild_symbols"
    }
}

fn path_under(cwd: &Path, prefix: Option<&str>, path: &str) -> bool {
    let Some(prefix) = prefix else {
        return true;
    };
    // `""` and `./` resolve to `.`, which matches no stored relative path.
    let prefix = normalize_prefix(cwd, prefix);
    if prefix == "." {
        return false;
    }
    let path = path.replace('\\', "/");
    path == prefix || path.starts_with(&format!("{prefix}/"))
}

fn normalize_prefix(cwd: &Path, prefix: &str) -> String {
    let joined = if Path::new(prefix).is_absolute() {
        PathBuf::from(prefix)
    } else {
        cwd.join(prefix)
    };
    let mut cleaned = PathBuf::new();
    for part in joined.components() {
        match part {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                cleaned.pop();
            }
            other => cleaned.push(other.as_os_str()),
        }
    }
    match cleaned.strip_prefix(cwd) {
        Ok(rel) => {
            let text = rel.to_string_lossy().replace('\\', "/");
            if text.is_empty() {
                ".".into()
            } else {
                text
            }
        }
        Err(_) => prefix.replace('\\', "/"),
    }
}

fn trim_ann(text: &str, max_chars: Option<usize>) -> String {
    let Some(max) = max_chars else {
        return text.to_string();
    };
    let count = text.chars().count();
    if count <= max {
        return text.to_string();
    }
    let mut out: String = text.chars().take(max).collect();
    out.push('…');
    out
}

fn file_len(path: &Path) -> u64 {
    fs::metadata(path).map(|meta| meta.len()).unwrap_or(0)
}

fn journal_lines(path: &Path) -> usize {
    let Ok(text) = fs::read_to_string(path) else {
        return 0;
    };
    text.lines().filter(|line| !line.is_empty()).count()
}

fn bm25_fingerprint(chunks: &[Chunk]) -> u64 {
    let mut hash = 0xcbf29ce484222325u64;
    for chunk in chunks {
        for byte in chunk.id.bytes().chain(chunk.sha256.bytes()) {
            hash ^= byte as u64;
            hash = hash.wrapping_mul(0x100000001b3);
        }
        hash ^= 0xff;
    }
    hash ^ chunks.len() as u64
}

fn truncate_detect(value: &mut Value, limit: usize) {
    let Value::Object(pairs) = value else {
        return;
    };
    let keys = ["unchanged", "modified", "new", "removed", "conflicts"];
    let mut totals = Vec::new();
    let mut truncated = false;
    for key in keys {
        let len = pairs
            .iter()
            .find(|(name, _)| name == key)
            .and_then(|(_, item)| match item {
                Value::Array(rows) => Some(rows.len()),
                _ => None,
            })
            .unwrap_or(0);
        totals.push((key, len));
        if len > limit {
            truncated = true;
        }
    }
    if !truncated {
        return;
    }
    for (key, _) in &totals {
        if let Some((_, item)) = pairs.iter_mut().find(|(name, _)| name == *key) {
            if let Value::Array(rows) = item {
                rows.truncate(limit);
            }
        }
    }
    let mut total_pairs = Vec::new();
    for (key, len) in totals {
        total_pairs.push((key.to_string(), Value::Int(len as i64)));
    }
    total_pairs.push(("truncated".to_string(), Value::Bool(true)));
    total_pairs.push(("limit".to_string(), Value::Int(limit as i64)));
    pairs.push(("totals".to_string(), Value::Object(total_pairs)));
}

fn collect(
    cwd: &Path,
    paths: &[String],
    respect_gitignore: bool,
    extra_skip: &[String],
) -> Result<Vec<(String, PathBuf)>, String> {
    let inputs: Vec<PathBuf> = if paths.is_empty() {
        vec![cwd.to_path_buf()]
    } else {
        paths.iter().map(|path| cwd.join(path)).collect()
    };
    let mut out = Vec::new();
    for input in inputs {
        if input.is_file() {
            out.push((rel_of(cwd, &input), input));
            continue;
        }
        if input.is_dir() {
            for rel in walk::expand(&[input.clone()], respect_gitignore, extra_skip)
                .map_err(|err| err.to_string())?
            {
                out.push((normalize(&rel), input.join(&rel)));
            }
        }
    }
    out.sort_by(|left, right| left.0.cmp(&right.0));
    out.dedup_by(|left, right| left.0 == right.0);
    Ok(out)
}

fn rel_of(cwd: &Path, path: &Path) -> String {
    crate::walk::native_sep(&path.strip_prefix(cwd).unwrap_or(path).to_string_lossy())
}

fn normalize(path: &str) -> String {
    crate::walk::native_sep(path)
}

fn mtime_ns(meta: &fs::Metadata) -> i64 {
    meta.modified()
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos() as i64)
        .unwrap_or(0)
}

fn store_bytes(storage: &std::path::Path) -> i64 {
    let store = storage.join("store");
    [
        "snapshot.json",
        "journal.jsonl",
        "signatures.bin",
        "bm25.bin",
    ]
    .iter()
    .map(|name| {
        std::fs::metadata(store.join(name))
            .map(|meta| meta.len())
            .unwrap_or(0)
    })
    .sum::<u64>() as i64
}

fn kv_cache_bytes(storage: &Path) -> u64 {
    let root = storage.join("kv_cache");
    let mut total = 0u64;
    let mut stack = vec![root];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if let Ok(meta) = entry.metadata() {
                total += meta.len();
            }
        }
    }
    total
}

pub(crate) fn write_kv_file(
    storage: &Path,
    session: &str,
    chunk_id: &str,
    turn: i64,
    json_bytes: &[u8],
) {
    let dir = storage.join("kv_cache").join(session);
    let _ = std::fs::create_dir_all(&dir);
    let path = dir.join(format!("{chunk_id}_turn{turn}.kv"));
    let _ = std::fs::write(path, crate::store::compress(json_bytes));
}

fn wal_bytes(storage: &Path) -> i64 {
    file_len(&storage.join("stele_context.db-wal")) as i64
}

/// Start reading `python3`'s version before `doctor` runs. The doctor call
/// only waits for a lookup that is already in flight, and a later call does
/// not start a process.
pub fn prefetch_python() {
    let _ = std::thread::Builder::new()
        .name("python-version".to_string())
        .spawn(|| {
            let _ = python_version();
        });
}

fn python_version() -> &'static str {
    static READY: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    READY.get_or_init(|| {
        for bin in ["python3", "python"] {
            let Ok(output) = std::process::Command::new(bin)
                .arg("-c")
                .arg("import sys; print(sys.version.split()[0], end='')")
                .output()
            else {
                continue;
            };
            if !output.status.success() {
                continue;
            }
            let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if !text.is_empty() {
                return text;
            }
        }
        String::new()
    })
}

pub fn host_python() -> crate::json::Value {
    let text = python_version();
    if text.is_empty() {
        crate::json::Value::Null
    } else {
        crate::json::Value::string(text)
    }
}

fn plan_field<'a>(plan: &'a Value, key: &str) -> Option<&'a Value> {
    let Value::Object(pairs) = plan else {
        return None;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, item)| item)
}

fn enrichment_preview(plan: &Value) -> Value {
    let candidates = match plan_field(plan, "candidates") {
        Some(Value::Array(rows)) => rows.iter().take(8).cloned().collect(),
        _ => Vec::new(),
    };
    let pct = match plan_field(plan, "tier2_token_coverage_percent") {
        Some(Value::Float(number)) => *number,
        Some(Value::Int(number)) => *number as f64,
        _ => 0.0,
    };
    Value::object(vec![
        (
            "candidates_total",
            Value::Int(crate::store::int_field(plan, "candidates_total")),
        ),
        (
            "uncovered_tokens",
            Value::Int(crate::store::int_field(plan, "uncovered_tokens")),
        ),
        ("tier2_token_coverage_percent", Value::Float(pct)),
        (
            "how_to_enrich",
            Value::string(crate::store::string_field(plan, "how_to_enrich")),
        ),
        ("top_candidates", Value::Array(candidates)),
    ])
}

fn strings(values: &[String]) -> Value {
    Value::Array(values.iter().cloned().map(Value::String).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn index_then_search_matches_a_fixture_symbol() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!("stele-index-{stamp}"));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        fs::write(root.join("sample.py"), "def classify(n):\n    return n\n").unwrap();
        let store = root.join("store-dir");
        let mut engine = Engine::open_at(root.clone(), store).unwrap();
        let first = engine.index(&[".".to_string()], false).unwrap();
        assert!(crate::json::to_string(&first).contains("sample.py"));
        let second = engine.index(&[".".to_string()], false).unwrap();
        let skipped = match &second {
            Value::Object(pairs) => pairs.iter().find(|(key, _)| key == "skipped"),
            _ => None,
        };
        let skipped_n = match skipped.map(|(_, value)| value) {
            Some(Value::Array(rows)) => rows.len(),
            _ => 0,
        };
        assert_eq!(skipped_n, 1);
        let defs = engine.find_definition("classify");
        let rendered = crate::json::to_string(&defs);
        assert!(rendered.contains("classify"), "{rendered}");
        let found = engine.search("classify", "keyword", 5);
        assert!(crate::json::to_string(&found).contains("classify"));
        let _ = fs::remove_dir_all(&root);
    }
}

#[cfg(test)]
mod media_fixture;

pub fn now_secs() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs() as i64)
        .unwrap_or(0)
}

pub fn now_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs_f64())
        .unwrap_or(0.0)
}

pub fn now_us() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_micros() as i64)
        .unwrap_or(0)
}

pub fn session_time(value: i64) -> f64 {
    if value > 1_000_000_000_000 {
        value as f64 / 1_000_000.0
    } else {
        value as f64
    }
}

// The piece signature must be computed from the chunk text, not from a
// moved value. `store_piece` above is replaced by the helper in this impl
// once the borrow is split. See `commit_piece`.
impl Engine {
    fn note_vector_replace(&mut self) {
        // remove + add. The first node in an empty index is not counted.
        if self.store.state.chunks.len() > 1 {
            self.store.state.inserts += 1;
        }
    }

    fn rebuild_hnsw(&mut self) {
        self.hnsw.clear();
        self.store.ensure_sigs();
        let pairs: Vec<(String, Vec<f32>)> = self
            .store
            .state
            .chunks
            .iter()
            .enumerate()
            .map(|(index, chunk)| {
                let vector = if chunk.embedding.len() == 128 {
                    chunk.embedding.clone()
                } else {
                    self.store
                        .state
                        .sigs
                        .get(index)
                        .map(|sig| sig.to_vec())
                        .unwrap_or_else(|| vec![0.0; 128])
                };
                (chunk.id.clone(), vector)
            })
            .collect();
        for (id, vector) in pairs {
            self.hnsw.insert(&id, vector);
        }
    }

    fn hnsw_span(&self) -> f64 {
        self.hnsw.probe_span()
    }

    fn take_symbol_id(&mut self) -> i64 {
        if self.store.state.next_symbol_id <= 0 {
            let from_dynamic = self
                .store
                .state
                .dynamic
                .iter()
                .map(|sym| sym.id)
                .max()
                .unwrap_or(0);
            self.store.state.next_symbol_id =
                from_dynamic.max(self.store.state.symbols.len() as i64) + 1;
        }
        let id = self.store.state.next_symbol_id;
        self.store.state.next_symbol_id += 1;
        id
    }

    fn indexed_stamp(&self) -> Value {
        let latest = self
            .store
            .state
            .documents
            .iter()
            .map(|doc| doc.indexed_at)
            .fold(0.0, f64::max);
        if latest <= 0.0 {
            Value::Null
        } else {
            Value::Float(latest)
        }
    }

    fn seconds_since_last_index(&self) -> f64 {
        let Value::Float(latest) = self.indexed_stamp() else {
            return 0.0;
        };
        (now_f64() - latest).max(0.0)
    }

    pub fn store_dynamic_remove(&mut self, name: &str) {
        self.store.state.dynamic.retain(|sym| sym.name != name);
    }

    pub fn commit_piece(&mut self, rel: &str, piece: &Produced) {
        let digest = chunk::content_sha256(&piece.content);
        let id = chunk::chunk_id(rel, piece.start, piece.end, &digest);
        let ext = piece
            .language
            .clone()
            .unwrap_or_else(|| extension_of(rel).to_string());
        for hit in symbols::extract(&ext, &piece.content) {
            let _id = self.take_symbol_id();
            self.store.state.symbols.push(Symbol {
                name: hit.name,
                kind: hit.kind,
                role: hit.role,
                line: hit.line,
                container: hit.container,
                chunk_id: id.clone(),
                path: rel.to_string(),
                chunk_index: piece.index,
            });
        }
        let vector: Vec<f32> = chunk::signature(&piece.content)
            .iter()
            .map(|value| *value as f32)
            .collect();
        self.hnsw.insert(&id, vector);
        self.store.state.chunks.push(Chunk {
            id,
            path: rel.to_string(),
            start: piece.start,
            end: piece.end,
            content: piece.content.clone(),
            sha256: digest,
            signature: chunk::signature_f32_hex(&piece.content),
            tokens: chunk::estimate_tokens(&piece.content),
            modality: piece.modality.clone(),
            language: piece.language.clone(),
            summary: None,
            notes: None,
            version: 1,
            staleness: 0.0,
            stale_since: None,
            embedding: Vec::new(),
        });
        // 1.5.2 HNSW increments only after the first node. The first insert
        // returns before the counter moves. Replacing an existing id does not
        // count either; commit_piece always adds a new id.
        if self.store.state.chunks.len() > 1 {
            self.store.state.inserts += 1;
        }
    }
}

fn classify_kv(
    old: &[(usize, usize, String, String)],
    all_sigs: &[[f64; 128]],
    produced: &[crate::chunk::Produced],
    change_threshold: f64,
) -> (i64, i64) {
    let mut restored = 0i64;
    let mut reprocessed = 0i64;
    for piece in produced {
        let limit = match piece.modality.as_str() {
            "code" => 0.80,
            "text" | "pdf" => 0.85,
            _ => change_threshold,
        };
        let digest = chunk::content_sha256(&piece.content);
        if let Some((_, _, hash, content)) = old
            .iter()
            .find(|(start, end, _, _)| *start == piece.start && *end == piece.end)
        {
            if hash == &digest {
                restored += 1;
            } else {
                let sim = cosine(
                    &chunk::signature(content),
                    &chunk::signature(&piece.content),
                );
                if sim >= limit {
                    restored += 1;
                } else {
                    reprocessed += 1;
                }
            }
            continue;
        }
        let new_sig = chunk::signature(&piece.content);
        let best = all_sigs
            .iter()
            .map(|sig| cosine(sig, &new_sig))
            .fold(0.0, f64::max);
        if !all_sigs.is_empty() && best >= limit {
            restored += 1;
        } else {
            reprocessed += 1;
        }
    }
    (restored, reprocessed)
}

fn cosine(left: &[f64; 128], right: &[f64; 128]) -> f64 {
    left.iter().zip(right).map(|(a, b)| a * b).sum()
}

fn extension_of(path: &str) -> &str {
    path.rsplit(['/', '\\'])
        .next()
        .and_then(|name| name.rsplit_once('.'))
        .map(|(_, ext)| ext)
        .unwrap_or("")
}
