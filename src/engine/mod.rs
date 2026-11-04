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
    chunk_size: usize,
    max_chunk_size: usize,
    respect_gitignore: bool,
    skip_dirs: Vec<String>,
    merge_threshold: f64,
    change_threshold: f64,
    search_alpha: f64,
    max_history_entries: i64,
}

impl Engine {
    pub fn open(storage_flag: Option<&Path>) -> Result<Engine, String> {
        let cwd = std::env::current_dir().map_err(|err| format!("cwd: {err}"))?;
        let root = detect_project_root(&cwd);
        let loaded = config::load(root.as_deref());
        let storage = resolve_storage_dir(storage_flag, &loaded, root.as_deref())?;
        Self::finish(cwd, storage, loaded)
    }

    pub fn open_from(cwd: PathBuf, storage_flag: Option<&Path>) -> Result<Engine, String> {
        let root = detect_project_root(&cwd);
        let loaded = config::load(root.as_deref());
        let storage = resolve_storage_dir(storage_flag, &loaded, root.as_deref())?;
        Self::finish(cwd, storage, loaded)
    }

    pub fn open_at(cwd: PathBuf, storage: PathBuf) -> Result<Engine, String> {
        let root = detect_project_root(&cwd);
        let loaded = config::load(root.as_deref());
        Self::finish(cwd, storage, loaded)
    }

    fn finish(cwd: PathBuf, storage: PathBuf, loaded: config::Config) -> Result<Engine, String> {
        let mut store = Store::open(&storage)?;
        let legacy = storage.join("stele_context.db");
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
            chunk_size: loaded.chunk_size.unwrap_or(256).max(1) as usize,
            max_chunk_size: loaded.max_chunk_size.unwrap_or(4096).max(1) as usize,
            respect_gitignore: loaded.respect_gitignore.unwrap_or(true),
            skip_dirs: loaded.skip_dirs.clone().unwrap_or_default(),
            merge_threshold: loaded.merge_threshold.unwrap_or(0.7),
            change_threshold: loaded.change_threshold.unwrap_or(0.85),
            search_alpha: loaded.search_alpha.unwrap_or(0.42),
            max_history_entries: loaded.max_history_entries.unwrap_or(1000),
        })
    }

    pub fn index(&mut self, paths: &[String], force: bool) -> Result<Value, String> {
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
            crate::locks::note_indexed(&self.cwd, &fresh);
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
        self.detect_with(scan_new, None, 200)
    }

    pub fn detect_with(
        &mut self,
        scan_new: bool,
        only: Option<&[String]>,
        limit: usize,
    ) -> Result<Value, String> {
        if !self
            .store
            .state
            .sessions
            .iter()
            .any(|session| session.id == "default")
        {
            let now = now_secs();
            self.store.state.sessions.push(crate::store::Session {
                id: "default".to_string(),
                searches: Vec::new(),
                reads: Vec::new(),
                kv: Vec::new(),
                created_at: now,
                updated_at: now,
                turn: 0,
                tokens: 0,
                agent: String::new(),
                attached: Vec::new(),
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
        let kv_restored = 0i64;
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
                self.store.state.change_log.push(crate::store::ChangeRow {
                    at: now_secs(),
                    session: "default".to_string(),
                    summary: "detect".to_string(),
                    reason: String::new(),
                });
                return Ok(result);
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
            let modality = if media { "media" } else { "code" };
            let threshold = match modality {
                "code" => 0.80,
                "text" | "pdf" => 0.85,
                _ => self.change_threshold,
            };
            if threshold >= 0.0 {
                kv_reprocessed += 1;
            }
            self.reindex_file(rel, &abs, &meta)?;
        }
        if !removed.is_empty() || modified.iter().len() > 0 {
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
        self.store.state.change_log.push(crate::store::ChangeRow {
            at: now_secs(),
            session: "default".to_string(),
            summary: "detect".to_string(),
            reason: String::new(),
        });
        Ok(result)
    }

    pub fn remove(&mut self, path: &str) -> Result<Value, String> {
        let rel = normalize(path);
        let before = self.store.state.chunks.len();
        self.drop_path(&rel);
        let removed = before - self.store.state.chunks.len();
        self.rebuild_bm25();
        self.store.save(
            "remove",
            &Value::object(vec![("path", Value::string(&rel))]),
        )?;
        Ok(Value::object(vec![
            ("path", Value::string(rel)),
            ("chunks_removed", Value::Int(removed as i64)),
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
        let symbols = self.store.state.symbols.len() as i64;
        let ready = !self.store.state.symbols.is_empty();
        Value::object(vec![
            ("version", Value::string(crate::VERSION)),
            ("storage", storage),
            (
                "index",
                Value::object(vec![
                    ("node_count", Value::Int(chunks)),
                    ("max_level", Value::Int(0)),
                    ("avg_connections", Value::Float(0.0)),
                    ("insert_count", Value::Int(chunks)),
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
            ("latest_indexed_at", Value::Float(0.0)),
            (
                "storage_dir",
                Value::string(self.storage.display().to_string()),
            ),
            (
                "symbol_graph_status",
                Value::string(if ready { "ready" } else { "empty" }),
            ),
            ("chunk_store_status", Value::string("ready")),
            ("seconds_since_last_index", Value::Float(0.0)),
            ("alerts", Value::Array(Vec::new())),
        ])
    }

    /// Storage block of Python `get_stats()["storage"]`, for `GET /health`.
    pub fn storage_report(&self) -> Value {
        let state = &self.store.state;
        let tokens: i64 = state.chunks.iter().map(|chunk| chunk.tokens as i64).sum();
        let definitions = state
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
        let kv_bytes: u64 = state
            .sessions
            .iter()
            .flat_map(|session| session.kv.iter())
            .map(|(key, value)| key.len() + value.len())
            .sum::<usize>() as u64;
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
            ("version_count", Value::Int(0)),
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
            ("symbol_count", Value::Int(state.symbols.len() as i64)),
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
        let mut files: Vec<Value> = self
            .store
            .state
            .documents
            .iter()
            .map(|doc| {
                let chunks = self
                    .store
                    .state
                    .chunks
                    .iter()
                    .filter(|chunk| chunk.path == doc.path)
                    .count();
                let symbols = self
                    .store
                    .state
                    .symbols
                    .iter()
                    .filter(|sym| sym.path == doc.path)
                    .count();
                Value::object(vec![
                    ("path", Value::string(&doc.path)),
                    ("chunks", Value::Int(chunks as i64)),
                    ("symbols", Value::Int(symbols as i64)),
                    ("bytes", Value::Int(doc.size as i64)),
                ])
            })
            .collect();
        files.sort_by(|left, right| json_path(left).cmp(&json_path(right)));
        Value::object(vec![
            (
                "documents",
                Value::Int(self.store.state.documents.len() as i64),
            ),
            ("files", Value::Array(files)),
        ])
    }

    pub fn project_brief(&self) -> Value {
        let mut files: Vec<(i64, String, i64)> = Vec::new();
        for doc in &self.store.state.documents {
            let chunks: Vec<_> = self
                .store
                .state
                .chunks
                .iter()
                .filter(|chunk| chunk.path == doc.path)
                .collect();
            let tokens = chunks.iter().map(|chunk| chunk.tokens as i64).sum();
            files.push((tokens, doc.path.clone(), chunks.len() as i64));
        }
        files.sort_by(|left, right| right.0.cmp(&left.0).then(left.1.cmp(&right.1)));
        let largest: Vec<Value> = files
            .into_iter()
            .take(40)
            .map(|(tokens, path, count)| {
                Value::object(vec![
                    ("path", Value::string(path)),
                    ("chunk_count", Value::Int(count)),
                    ("total_tokens", Value::Int(tokens)),
                    ("indexed_at", Value::Float(0.0)),
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
        let pending: Vec<Value> = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.summary.is_none())
            .take(50)
            .map(|chunk| {
                Value::object(vec![
                    ("chunk_id", Value::string(&chunk.id)),
                    ("path", Value::string(&chunk.path)),
                ])
            })
            .collect();
        let missing = self
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.summary.is_none())
            .count();
        Value::object(vec![
            ("chunks_missing_summary", Value::Int(missing as i64)),
            ("next", Value::Array(pending)),
        ])
    }

    pub fn history(&self) -> Result<Value, String> {
        let rows = self.store.history()?;
        Ok(Value::object(vec![
            ("entries", Value::Int(rows.len() as i64)),
            ("history", Value::Array(rows)),
        ]))
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
        self.store.state.chunks.retain(|chunk| chunk.path != rel);
        self.store.state.symbols.retain(|sym| sym.path != rel);
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
    path.strip_prefix(cwd)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn normalize(path: &str) -> String {
    path.replace('\\', "/")
}

fn mtime_ns(meta: &fs::Metadata) -> i64 {
    meta.modified()
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos() as i64)
        .unwrap_or(0)
}

fn strings(values: &[String]) -> Value {
    Value::Array(values.iter().cloned().map(Value::String).collect())
}

fn json_path(value: &Value) -> String {
    crate::store::string_field(value, "path")
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

// The piece signature must be computed from the chunk text, not from a
// moved value. `store_piece` above is replaced by the helper in this impl
// once the borrow is split. See `commit_piece`.
impl Engine {
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
        });
    }
}

fn extension_of(path: &str) -> &str {
    path.rsplit(['/', '\\'])
        .next()
        .and_then(|name| name.rsplit_once('.'))
        .map(|(_, ext)| ext)
        .unwrap_or("")
}
