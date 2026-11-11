//! Worktree overlays (2.4).
//!
//! An agent that works in its own git worktree asks with `worktree_root`
//! (CLI `--worktree`). The answer is the one an index built from that tree
//! would give: the base index of the main project, plus an overlay of the
//! files that differ in the worktree (new, changed and deleted).
//!
//! - The overlay is an in-memory copy of the base state with the differing
//!   files re-indexed and the deleted ones dropped. Its store is ephemeral:
//!   nothing it holds is written to the base index, and it takes no locks.
//! - Differences are found by walking the worktree with the index's ignore
//!   rules, then size and mtime first and the content hash when they differ.
//!   Each call re-checks with that cheap pass, so answers are as current as
//!   grep. A new file counts when it is under a top-level directory the
//!   base index covers (or at the top level, when the base has top-level
//!   files), so an index of `crates/` does not grow with the worktree's
//!   `docs/`.
//! - `overlays/<hash of the worktree path>/overlay.json` under the storage
//!   directory keeps the size, mtime and hash of each file whose stamp
//!   differs from the base, so a new process does not hash the tree again.
//!   Overlays whose worktree is gone are pruned.
//! - Replies gain `worktree` and `overlay_files` (how many files differ from
//!   the base); paths stay relative to the worktree.

use super::Engine;
use crate::json::{self, Value};
use crate::store::Store;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

/// The read and query tools that take `worktree_root`.
pub const WORKTREE_TOOLS: &[&str] = &[
    "search",
    "agent_grep",
    "search_text",
    "query",
    "find_definition",
    "find_references",
    "impact_radius",
    "coupling",
    "get_context",
    "map",
    "stale_chunks",
    "get_search_history",
    "get_session_read_files",
];

/// Overlays kept in memory at once; the least recently used one goes.
const SLOTS: usize = 3;

/// The overlays a server keeps between calls.
#[derive(Default)]
pub struct Overlays {
    slots: Vec<Slot>,
    tick: u64,
    pruned: bool,
}

struct Slot {
    key: String,
    engine: Engine,
    base_generation: u64,
    /// The base index's documents: path -> (sha256, size, mtime).
    base: HashMap<String, (String, u64, i64)>,
    /// Top-level directories (and whether top-level files) the base covers.
    scope: (HashSet<String>, bool),
    /// Paths whose content differs from the base, or that are new or gone.
    differs: BTreeSet<String>,
    used: u64,
    /// `overlay.json` written at least once by this process.
    saved: bool,
}

/// What a worktree call ran against.
pub struct Ran {
    pub worktree: String,
    pub overlay_files: usize,
}

impl Overlays {
    pub fn new() -> Overlays {
        Overlays::default()
    }

    /// Run `body` on the index of the tree at `worktree`: the base engine
    /// itself when it is the base checkout, else its overlay.
    pub fn run<T>(
        &mut self,
        base: &mut Engine,
        worktree: &str,
        body: impl FnOnce(&mut Engine) -> T,
    ) -> Result<(T, Ran), String> {
        let tree = validate(base, worktree)?;
        let shown = tree.display().to_string();
        if same_path(&tree, &base.cwd) {
            return Ok((
                body(base),
                Ran {
                    worktree: shown,
                    overlay_files: 0,
                },
            ));
        }
        if !self.pruned {
            self.pruned = true;
            prune(&base.storage);
        }
        self.tick += 1;
        let key = key_of(&tree);
        let generation = base.store.generation();
        let found = self
            .slots
            .iter()
            .position(|slot| slot.key == key && slot.base_generation == generation);
        let at = match found {
            Some(at) => at,
            None => {
                self.slots.retain(|slot| slot.key != key);
                if self.slots.len() >= SLOTS {
                    if let Some(oldest) = self
                        .slots
                        .iter()
                        .enumerate()
                        .min_by_key(|(_, slot)| slot.used)
                        .map(|(at, _)| at)
                    {
                        self.slots.remove(oldest);
                    }
                }
                self.slots.push(Slot::build(base, &tree, key)?);
                self.slots.len() - 1
            }
        };
        let slot = &mut self.slots[at];
        slot.used = self.tick;
        slot.sync()?;
        let overlay_files = slot.differs.len();
        Ok((
            body(&mut slot.engine),
            Ran {
                worktree: shown,
                overlay_files,
            },
        ))
    }
}

/// Add `worktree` and `overlay_files` to a reply. An array reply becomes
/// `{"results": [...], "worktree", "overlay_files"}`.
pub fn annotate(value: Value, ran: &Ran) -> Value {
    let extra = [
        ("worktree".to_string(), Value::string(&ran.worktree)),
        (
            "overlay_files".to_string(),
            Value::Int(ran.overlay_files as i64),
        ),
    ];
    match value {
        Value::Object(mut pairs) => {
            pairs.extend(extra);
            Value::Object(pairs)
        }
        other => {
            let mut pairs = vec![("results".to_string(), other)];
            pairs.extend(extra);
            Value::Object(pairs)
        }
    }
}

/// The directory to answer for, or a one-line error that says what to do.
/// `worktree` must be an existing absolute path in a checkout of the same
/// project: for a git project, one whose git common directory is the
/// base's. A path inside the worktree means its checkout, at the same
/// offset as the base engine's directory below its project root.
fn validate(base: &Engine, worktree: &str) -> Result<PathBuf, String> {
    let given = Path::new(worktree);
    if worktree.trim().is_empty() || !given.is_absolute() {
        return Err(format!(
            "worktree_root must be an absolute path, got '{worktree}'. Pass the worktree's absolute path, or omit it to query the main index."
        ));
    }
    if !given.is_dir() {
        return Err(format!(
            "worktree_root '{worktree}' does not exist or is not a directory. Pass an existing checkout (git worktree list), or omit it to query the main index."
        ));
    }
    let given = crate::root::resolved(given);
    let tree_root = crate::root::detect_project_root(&given).unwrap_or_else(|| given.clone());
    let base_cwd = crate::root::resolved(&base.cwd);
    let base_root = crate::root::detect_project_root(&base_cwd).unwrap_or_else(|| base_cwd.clone());
    let base_git = crate::root::git_common_dir(&base_root);
    let tree_git = crate::root::git_common_dir(&tree_root);
    match (&base_git, &tree_git) {
        (Some(left), Some(right)) if same_path(left, right) => {}
        (None, None) => {}
        (Some(left), right) => {
            let found = right
                .as_ref()
                .map(|path| format!("git common dir {}", path.display()))
                .unwrap_or_else(|| "no git checkout".to_string());
            return Err(format!(
                "worktree_root '{worktree}' is not a checkout of this project ({found}; the project's is {}). Pass a worktree listed by `git -C {} worktree list`.",
                left.display(),
                base_root.display()
            ));
        }
        (None, Some(_)) => {
            return Err(format!(
                "worktree_root '{worktree}' is a git checkout but this project ({}) is not. Run the server in that checkout instead.",
                base_root.display()
            ));
        }
    }
    let offset = base_cwd.strip_prefix(&base_root).unwrap_or(Path::new(""));
    if offset.as_os_str().is_empty() {
        Ok(tree_root)
    } else {
        Ok(tree_root.join(offset))
    }
}

fn same_path(left: &Path, right: &Path) -> bool {
    let left = crate::root::resolved(left);
    let right = crate::root::resolved(right);
    if cfg!(windows) {
        left.to_string_lossy().to_lowercase() == right.to_string_lossy().to_lowercase()
    } else {
        left == right
    }
}

/// A stable name for the worktree's overlay directory.
fn key_of(tree: &Path) -> String {
    let mut text = crate::root::resolved(tree).display().to_string();
    if cfg!(windows) {
        text = text.to_lowercase();
    }
    crate::hash::hex(&crate::hash::sha256(text.as_bytes()))[..16].to_string()
}

/// Remove the overlay directories whose worktree no longer exists.
fn prune(storage: &Path) {
    let Ok(entries) = fs::read_dir(storage.join("overlays")) else {
        return;
    };
    for entry in entries.flatten() {
        let dir = entry.path();
        let manifest = dir.join("overlay.json");
        let worktree = fs::read_to_string(&manifest)
            .ok()
            .and_then(|text| json::parse(&text).ok())
            .map(|value| crate::store::string_field(&value, "worktree"));
        let gone = match worktree {
            Some(path) if !path.is_empty() => !Path::new(&path).is_dir(),
            _ => !manifest.exists(),
        };
        if gone {
            let _ = fs::remove_dir_all(&dir);
        }
    }
}

impl Slot {
    fn build(base: &mut Engine, tree: &Path, key: String) -> Result<Slot, String> {
        if base.documents_only {
            base.store.reload_full()?;
            base.documents_only = false;
            base.file_lines_once();
        }
        base.ensure_bm25();
        let storage = base.storage.join("overlays").join(&key);
        let store = Store::ephemeral(&storage, base.store.state.clone())?;
        let engine = Engine {
            cwd: tree.to_path_buf(),
            storage,
            store,
            bm25: base.bm25.clone(),
            bm25_ready: base.bm25_ready,
            bm25_excluded: base.bm25_excluded.clone(),
            hnsw: std::cell::RefCell::new(crate::search::Hnsw::new()),
            hnsw_ready: std::cell::Cell::new(false),
            graph_cache: std::cell::Cell::new(None),
            chunk_size: base.chunk_size,
            max_chunk_size: base.max_chunk_size,
            respect_gitignore: base.respect_gitignore,
            skip_dirs: base.skip_dirs.clone(),
            merge_threshold: base.merge_threshold,
            change_threshold: base.change_threshold,
            search_alpha: base.search_alpha,
            max_history_entries: base.max_history_entries,
            documents_only: false,
            rooted: base.rooted,
            root_text: std::cell::OnceCell::new(),
            server_agent: String::new(),
            file_reads: Vec::new(),
            operations: 0,
            failures: 0,
            overlay: true,
        };
        let base_docs: HashMap<String, (String, u64, i64)> = base
            .store
            .state
            .documents
            .iter()
            .map(|doc| {
                (
                    doc.path.clone(),
                    (doc.sha256.clone(), doc.size, doc.mtime_ns),
                )
            })
            .collect();
        let mut dirs = HashSet::new();
        let mut top_files = false;
        for path in base_docs.keys() {
            match path.split(['/', '\\']).collect::<Vec<_>>().as_slice() {
                [_] => top_files = true,
                [first, ..] => {
                    dirs.insert(first.to_string());
                }
                [] => {}
            }
        }
        let mut slot = Slot {
            key,
            engine,
            base_generation: base.store.generation(),
            base: base_docs,
            scope: (dirs, top_files),
            differs: BTreeSet::new(),
            used: 0,
            saved: false,
        };
        slot.apply_manifest();
        Ok(slot)
    }

    fn manifest_path(&self) -> PathBuf {
        self.engine.storage.join("overlay.json")
    }

    /// Take the stamps a previous process recorded for files whose content
    /// matched the base, so they are not hashed again.
    fn apply_manifest(&mut self) {
        let Some(value) = fs::read_to_string(self.manifest_path())
            .ok()
            .and_then(|text| json::parse(&text).ok())
        else {
            return;
        };
        let Some(Value::Array(rows)) = field(&value, "files") else {
            return;
        };
        let mut stamps: HashMap<String, (u64, i64, String)> = HashMap::new();
        for row in rows {
            if let Value::Array(items) = row {
                if let [Value::String(path), Value::Int(size), Value::Int(mtime), Value::String(sha)] =
                    items.as_slice()
                {
                    stamps.insert(path.clone(), (*size as u64, *mtime, sha.clone()));
                }
            }
        }
        for doc in self.engine.store.state.documents.iter_mut() {
            if let Some((size, mtime, sha)) = stamps.get(&doc.path) {
                if *sha == doc.sha256 {
                    doc.size = *size;
                    doc.mtime_ns = *mtime;
                }
            }
        }
    }

    fn in_scope(&self, rel: &str) -> bool {
        let (dirs, top_files) = &self.scope;
        if dirs.is_empty() && !top_files {
            return true;
        }
        let parts: Vec<&str> = rel.split(['/', '\\']).collect();
        match parts.as_slice() {
            [_] => *top_files,
            [first, ..] => dirs.contains(*first),
            [] => false,
        }
    }

    /// Bring the overlay up to date with the worktree: re-index what
    /// differs, drop what is gone. Unchanged files cost one stat.
    fn sync(&mut self) -> Result<(), String> {
        let files = super::collect(
            &self.engine.cwd,
            &[],
            self.engine.respect_gitignore,
            &self.engine.skip_dirs,
            self.engine.rooted,
        )?;
        let known: HashSet<String> = self
            .engine
            .store
            .state
            .documents
            .iter()
            .map(|doc| doc.path.clone())
            .collect();
        let mut seen: HashSet<String> = HashSet::new();
        let mut work: Vec<(String, PathBuf)> = Vec::new();
        for (rel, abs) in files {
            if !known.contains(&rel) && !self.base.contains_key(&rel) && !self.in_scope(&rel) {
                continue;
            }
            seen.insert(rel.clone());
            work.push((rel, abs));
        }
        // Indexed files the walk skips (named explicitly, or ignored) are
        // checked where they are; the ones that are gone are deleted.
        let mut gone = Vec::new();
        for rel in &known {
            if seen.contains(rel) {
                continue;
            }
            let abs = self.engine.cwd.join(rel);
            if abs.is_file() {
                work.push((rel.clone(), abs));
            } else {
                gone.push(rel.clone());
            }
        }
        let mut changed = false;
        let mut stamped = false;
        for rel in gone {
            self.drop_file(&rel);
            if self.base.contains_key(&rel) {
                self.differs.insert(rel);
            } else {
                self.differs.remove(&rel);
            }
            changed = true;
        }
        let mut rows = self.document_rows();
        for (rel, abs) in work {
            let Ok(meta) = fs::metadata(&abs) else {
                continue;
            };
            let mtime = super::mtime_ns(&meta);
            let doc = rows.get(&rel).copied();
            if let Some(at) = doc {
                let row = &self.engine.store.state.documents[at];
                if row.size == meta.len() && row.mtime_ns == mtime {
                    continue;
                }
            }
            let Ok(bytes) = fs::read(&abs) else {
                continue;
            };
            let sha = super::content_sha(&rel, &bytes);
            let base_sha = self.base.get(&rel).map(|(sha, _, _)| sha.as_str());
            if base_sha == Some(sha.as_str()) {
                self.differs.remove(&rel);
            } else {
                self.differs.insert(rel.clone());
            }
            stamped = true;
            if let Some(at) = doc {
                let row = &mut self.engine.store.state.documents[at];
                if row.sha256 == sha {
                    row.size = meta.len();
                    row.mtime_ns = mtime;
                    continue;
                }
            }
            self.reindex(&rel, &abs, &meta)?;
            changed = true;
            // Re-indexing moves the document rows.
            rows = self.document_rows();
        }
        if changed {
            let state = &mut self.engine.store.state;
            state.edges = crate::symbols::build_edges(&state.symbols);
            // Signatures line up with chunks by position; the hex signature
            // of each chunk is read instead (`Store::ensure_sigs`).
            state.sigs.clear();
            self.engine.hnsw_ready.set(false);
            self.engine.graph_cache.set(None);
        }
        if stamped || changed || !self.saved {
            self.save_manifest();
            self.saved = true;
        }
        Ok(())
    }

    fn document_rows(&self) -> HashMap<String, usize> {
        self.engine
            .store
            .state
            .documents
            .iter()
            .enumerate()
            .map(|(at, doc)| (doc.path.clone(), at))
            .collect()
    }

    fn drop_file(&mut self, rel: &str) {
        let ids: Vec<String> = self
            .engine
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == rel)
            .map(|chunk| chunk.id.clone())
            .collect();
        for id in &ids {
            self.engine.bm25.remove(id);
        }
        self.engine.drop_path(rel);
    }

    fn reindex(&mut self, rel: &str, abs: &Path, meta: &fs::Metadata) -> Result<(), String> {
        let old: Vec<String> = self
            .engine
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == rel)
            .map(|chunk| chunk.id.clone())
            .collect();
        for id in &old {
            self.engine.bm25.remove(id);
        }
        self.engine.reindex_file(rel, abs, meta)?;
        let engine = &mut self.engine;
        for chunk in engine
            .store
            .state
            .chunks
            .iter()
            .filter(|chunk| chunk.path == rel)
        {
            // The same chunks `rebuild_bm25` leaves out.
            if matches!(chunk.modality.as_str(), "image" | "audio" | "video")
                || chunk.content.is_empty()
            {
                continue;
            }
            engine.bm25.add(&chunk.id, &chunk.content);
        }
        Ok(())
    }

    /// The stamps of the files whose size or mtime differ from the base.
    fn save_manifest(&self) {
        let mut rows: Vec<Value> = Vec::new();
        for doc in &self.engine.store.state.documents {
            let same = self
                .base
                .get(&doc.path)
                .is_some_and(|(_, size, mtime)| *size == doc.size && *mtime == doc.mtime_ns);
            if same {
                continue;
            }
            rows.push(Value::Array(vec![
                Value::string(&doc.path),
                Value::Int(doc.size as i64),
                Value::Int(doc.mtime_ns),
                Value::string(&doc.sha256),
            ]));
        }
        let body = Value::object(vec![
            (
                "worktree",
                Value::string(self.engine.cwd.display().to_string()),
            ),
            (
                "differs",
                Value::Array(self.differs.iter().map(Value::string).collect()),
            ),
            ("files", Value::Array(rows)),
        ]);
        let path = self.manifest_path();
        let tmp = path.with_extension("json.tmp");
        if fs::write(&tmp, json::to_string(&body)).is_ok() {
            let _ = fs::rename(&tmp, &path);
        }
    }
}

fn field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    match value {
        Value::Object(pairs) => pairs.iter().find(|(name, _)| name == key).map(|(_, v)| v),
        _ => None,
    }
}

/// The worktree an MCP server started inside a linked git worktree answers
/// for automatically: when that worktree has no index of its own and the
/// main checkout has one, the main checkout is the base and read tools
/// answer for the worktree. Returns (main checkout root, worktree root).
/// None when `STELE_CONTEXT_STORAGE_DIR`, a storage flag or a config
/// `storage_dir` names the storage, as those already decide.
pub fn auto_worktree(cwd: &Path, storage_flag: Option<&Path>) -> Option<(PathBuf, PathBuf)> {
    if storage_flag.is_some() || std::env::var_os("STELE_CONTEXT_STORAGE_DIR").is_some() {
        return None;
    }
    let tree = crate::root::detect_project_root(cwd)?;
    if !crate::root::is_linked_worktree(&tree) {
        return None;
    }
    let config = crate::config::load(Some(&tree));
    if config.storage_dir.is_some() {
        return None;
    }
    if tree
        .join(".stele-context")
        .join("store")
        .join("snapshot.json")
        .is_file()
    {
        return None;
    }
    let common = crate::root::git_common_dir(&tree)?;
    if common.file_name().is_none_or(|name| name != ".git") {
        return None;
    }
    let main = common.parent()?.to_path_buf();
    let main_config = crate::config::load(Some(&main));
    let storage = crate::root::resolve_storage_dir(None, &main_config, Some(&main)).ok()?;
    if !storage.join("store").join("snapshot.json").is_file() {
        return None;
    }
    let offset = crate::root::resolved(cwd)
        .strip_prefix(crate::root::resolved(&tree))
        .map(Path::to_path_buf)
        .unwrap_or_default();
    let tree = crate::root::resolved(&tree);
    if offset.as_os_str().is_empty() {
        Some((main, tree))
    } else {
        Some((main.join(&offset), tree.join(offset)))
    }
}

/// Pull `worktree_root` out of a call's arguments: Some(path) when it is a
/// non-empty string.
pub fn requested(args: &Value) -> Option<String> {
    match field(args, "worktree_root") {
        Some(Value::String(text)) if !text.is_empty() => Some(text.clone()),
        _ => None,
    }
}

#[cfg(test)]
mod tests;
