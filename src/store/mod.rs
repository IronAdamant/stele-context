//! Native snapshot plus an append-only journal.
//!
//! The snapshot is the readable state. The journal records each successful
//! mutation. A crash while writing the temp snapshot leaves the previous
//! snapshot in place. The legacy SQLite file is not opened by the snapshot
//! loader. `sqlite` reads a 1.5 database when `migrate` asks it to.

mod sqlite;

pub use sqlite::{blob as sql_blob, number as sql_number, text as sql_text, SqliteDb};

use crate::json::{self, Value};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write as _};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub struct Document {
    pub path: String,
    pub mtime_ns: i64,
    pub size: u64,
    pub sha256: String,
    pub doc_version: u64,
    pub chunk_count: u32,
}

#[derive(Clone, Debug)]
pub struct Chunk {
    pub id: String,
    pub path: String,
    pub start: usize,
    pub end: usize,
    pub content: String,
    pub sha256: String,
    pub signature: String,
    pub tokens: usize,
    pub modality: String,
    pub language: Option<String>,
    pub summary: Option<String>,
    pub notes: Option<String>,
    pub version: u64,
    pub staleness: f64,
    pub stale_since: Option<i64>,
}

#[derive(Clone, Debug)]
pub struct Symbol {
    pub name: String,
    pub kind: String,
    pub role: String,
    pub line: u32,
    pub container: Option<String>,
    pub chunk_id: String,
    pub path: String,
    pub chunk_index: usize,
}

#[derive(Clone, Debug)]
pub struct Annotation {
    pub id: String,
    pub path: String,
    pub body: String,
    pub tags: Vec<String>,
    pub target_type: String,
    pub created_at: i64,
    pub updated_at: i64,
}

#[derive(Clone, Debug)]
pub struct Lock {
    pub path: String,
    pub agent: String,
    pub until: i64,
}

#[derive(Clone, Debug)]
pub struct Conflict {
    pub path: String,
    pub holder: String,
    pub requester: String,
    pub at: i64,
    pub kind: String,
}

#[derive(Clone, Debug)]
pub struct Edge {
    pub source: String,
    pub target: String,
    pub kind: String,
    pub name: String,
}

#[derive(Clone, Debug)]
pub struct Session {
    pub id: String,
    pub searches: Vec<String>,
    pub reads: Vec<String>,
    pub kv: Vec<(String, String)>,
    pub created_at: i64,
    pub updated_at: i64,
    pub turn: i64,
    pub tokens: i64,
    pub agent: String,
    pub attached: Vec<(String, i64, f64)>,
    pub search_rows: Vec<SearchRow>,
}

#[derive(Clone, Debug)]
pub struct SearchRow {
    pub pattern: String,
    pub tool: String,
    pub files_checked: i64,
    pub files_with_matches: i64,
    pub at: i64,
}

#[derive(Clone, Debug)]
pub struct ChunkHist {
    pub chunk_id: String,
    pub version: u64,
    pub hash: String,
    pub at: i64,
    pub path: String,
}

#[derive(Clone, Debug)]
pub struct ChangeRow {
    pub at: i64,
    pub session: String,
    pub summary: String,
    pub reason: String,
}

#[derive(Clone, Debug)]
pub struct Dynamic {
    pub name: String,
    pub kind: String,
    pub role: String,
    pub path: String,
    pub agent: String,
    pub line: u32,
    pub chunk_id: String,
}

#[derive(Clone, Debug, Default)]
pub struct State {
    pub documents: Vec<Document>,
    pub chunks: Vec<Chunk>,
    pub symbols: Vec<Symbol>,
    pub annotations: Vec<Annotation>,
    pub locks: Vec<Lock>,
    pub conflicts: Vec<Conflict>,
    pub edges: Vec<Edge>,
    pub sessions: Vec<Session>,
    pub dynamic: Vec<Dynamic>,
    pub chunk_history: Vec<ChunkHist>,
    pub change_log: Vec<ChangeRow>,
    pub next_annotation: i64,
    pub seq: u64,
    /// Parallel to `chunks`. Not part of the JSON snapshot; `signatures.bin`.
    pub sigs: Vec<[f32; 128]>,
}

pub struct Store {
    dir: PathBuf,
    pub state: State,
    _lock: File,
}

impl Store {
    pub fn open(storage: &Path) -> Result<Store, String> {
        let dir = storage.join("store");
        fs::create_dir_all(&dir).map_err(|err| format!("create store: {err}"))?;
        let lock_path = dir.join("lock");
        let lock = File::options()
            .read(true)
            .write(true)
            .create(true)
            .open(&lock_path)
            .map_err(|err| format!("open lock: {err}"))?;
        lock.lock().map_err(|err| format!("lock store: {err}"))?;
        let state = load_snapshot(&dir.join("snapshot.json")).unwrap_or_default();
        Ok(Store {
            dir,
            state,
            _lock: lock,
        })
    }

    /// Document rows only. `detect` uses this while mtime and size still match,
    /// so it does not parse chunk bodies or the signature file.
    pub fn open_documents(storage: &Path) -> Result<Store, String> {
        let dir = storage.join("store");
        fs::create_dir_all(&dir).map_err(|err| format!("create store: {err}"))?;
        let lock_path = dir.join("lock");
        let lock = File::options()
            .read(true)
            .write(true)
            .create(true)
            .open(&lock_path)
            .map_err(|err| format!("open lock: {err}"))?;
        lock.lock().map_err(|err| format!("lock store: {err}"))?;
        let state = load_documents_only(&dir.join("snapshot.json"))
            .unwrap_or_else(|| load_snapshot(&dir.join("snapshot.json")).unwrap_or_default());
        Ok(Store {
            dir,
            state,
            _lock: lock,
        })
    }

    pub fn reload_full(&mut self) -> Result<(), String> {
        self.state = load_snapshot(&self.dir.join("snapshot.json")).unwrap_or_default();
        Ok(())
    }

    /// Keyword search does not read signatures. Load the side file on the
    /// first hybrid query or save, and keep a hex signature if the file is absent.
    pub fn ensure_sigs(&mut self) {
        if self.state.sigs.len() == self.state.chunks.len() {
            return;
        }
        let loaded = read_sigs(&self.dir.join("signatures.bin"), self.state.chunks.len());
        if loaded.len() == self.state.chunks.len() {
            self.state.sigs = loaded;
            return;
        }
        self.state.sigs = self
            .state
            .chunks
            .iter()
            .map(|chunk| decode_hex_sig(&chunk.signature))
            .collect();
    }

    pub fn save(&mut self, op: &str, detail: &Value) -> Result<(), String> {
        self.state.seq = self.state.seq.saturating_add(1);
        self.ensure_sigs();
        write_sigs(&self.dir.join("signatures.bin"), &self.state)?;
        let body = json::to_vec(&self.state.to_json());
        let tmp = self.dir.join("snapshot.json.tmp");
        let final_path = self.dir.join("snapshot.json");
        {
            let mut file = File::create(&tmp).map_err(|err| format!("write snapshot: {err}"))?;
            file.write_all(&body)
                .map_err(|err| format!("write snapshot: {err}"))?;
            file.sync_all().ok();
        }
        fs::rename(&tmp, &final_path).map_err(|err| format!("rename snapshot: {err}"))?;
        let mut line = json::to_string(&Value::object(vec![
            ("seq", Value::Int(self.state.seq as i64)),
            ("op", Value::string(op)),
            ("detail", detail.clone()),
        ]));
        line.push('\n');
        let mut journal = File::options()
            .create(true)
            .append(true)
            .open(self.dir.join("journal.jsonl"))
            .map_err(|err| format!("journal: {err}"))?;
        journal
            .write_all(line.as_bytes())
            .map_err(|err| format!("journal: {err}"))?;
        Ok(())
    }

    pub fn history(&self) -> Result<Vec<Value>, String> {
        let path = self.dir.join("journal.jsonl");
        if !path.is_file() {
            return Ok(Vec::new());
        }
        let file = File::open(path).map_err(|err| format!("history: {err}"))?;
        let mut rows = Vec::new();
        for line in BufReader::new(file).lines() {
            let line = line.map_err(|err| format!("history: {err}"))?;
            if line.trim().is_empty() {
                continue;
            }
            if let Ok(value) = json::parse(&line) {
                rows.push(value);
            }
        }
        Ok(rows)
    }

    pub fn legacy_db(&self) -> Option<PathBuf> {
        let path = self.dir.parent()?.join("stele_context.db");
        path.is_file().then_some(path)
    }
}

impl State {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("version", Value::Int(1)),
            ("seq", Value::Int(self.seq as i64)),
            (
                "documents",
                Value::Array(self.documents.iter().map(Document::to_json).collect()),
            ),
            (
                "chunks",
                Value::Array(self.chunks.iter().map(Chunk::to_json).collect()),
            ),
            (
                "symbols",
                Value::Array(self.symbols.iter().map(Symbol::to_json).collect()),
            ),
            (
                "annotations",
                Value::Array(self.annotations.iter().map(Annotation::to_json).collect()),
            ),
            (
                "locks",
                Value::Array(self.locks.iter().map(Lock::to_json).collect()),
            ),
            (
                "conflicts",
                Value::Array(self.conflicts.iter().map(Conflict::to_json).collect()),
            ),
            (
                "edges",
                Value::Array(self.edges.iter().map(Edge::to_json).collect()),
            ),
            (
                "sessions",
                Value::Array(self.sessions.iter().map(Session::to_json).collect()),
            ),
            (
                "dynamic",
                Value::Array(self.dynamic.iter().map(Dynamic::to_json).collect()),
            ),
        ])
    }
}

fn load_snapshot(path: &Path) -> Option<State> {
    let text = fs::read_to_string(path).ok()?;
    let value = json::parse(&text).ok()?;
    Some(State::from_json(&value))
}

fn load_documents_only(path: &Path) -> Option<State> {
    let text = fs::read_to_string(path).ok()?;
    let slice = documents_array(&text)?;
    let value = json::parse(slice).ok()?;
    let crate::json::Value::Array(rows) = value else {
        return None;
    };
    let mut state = State::default();
    state.documents = rows.iter().map(Document::from_json).collect();
    Some(state)
}

/// The top-level `documents` array. It is written before chunk bodies, so the
/// first match is the key rather than a string inside a chunk.
fn documents_array(text: &str) -> Option<&str> {
    let key = "\"documents\":";
    let start = text.find(key)? + key.len();
    let bytes = text.as_bytes();
    let mut index = start;
    while index < bytes.len() && bytes[index].is_ascii_whitespace() {
        index += 1;
    }
    if bytes.get(index) != Some(&b'[') {
        return None;
    }
    let begin = index;
    let mut depth = 0i32;
    let mut in_string = false;
    let mut escape = false;
    while index < bytes.len() {
        let byte = bytes[index];
        if in_string {
            if escape {
                escape = false;
            } else if byte == b'\\' {
                escape = true;
            } else if byte == b'"' {
                in_string = false;
            }
        } else if byte == b'"' {
            in_string = true;
        } else if byte == b'[' {
            depth += 1;
        } else if byte == b']' {
            depth -= 1;
            if depth == 0 {
                return Some(&text[begin..=index]);
            }
        }
        index += 1;
    }
    None
}

fn write_sigs(path: &Path, state: &State) -> Result<(), String> {
    let mut bytes = Vec::with_capacity(state.chunks.len() * 128 * 4);
    for (index, chunk) in state.chunks.iter().enumerate() {
        let values = if state.sigs.len() == state.chunks.len() {
            state.sigs[index]
        } else {
            decode_hex_sig(&chunk.signature)
        };
        for value in values {
            bytes.extend_from_slice(&value.to_le_bytes());
        }
    }
    fs::write(path, bytes).map_err(|err| format!("signatures: {err}"))
}

fn read_sigs(path: &Path, count: usize) -> Vec<[f32; 128]> {
    let Ok(bytes) = fs::read(path) else {
        return Vec::new();
    };
    if bytes.len() < count * 128 * 4 {
        return Vec::new();
    }
    let mut out = Vec::with_capacity(count);
    for index in 0..count {
        let mut sig = [0f32; 128];
        let start = index * 128 * 4;
        for lane in 0..128 {
            let offset = start + lane * 4;
            let mut buf = [0u8; 4];
            buf.copy_from_slice(&bytes[offset..offset + 4]);
            sig[lane] = f32::from_le_bytes(buf);
        }
        out.push(sig);
    }
    out
}

fn decode_hex_sig(hex: &str) -> [f32; 128] {
    let mut out = [0f32; 128];
    let bytes = hex.as_bytes();
    for lane in 0..128 {
        let start = lane * 8;
        if start + 8 > bytes.len() {
            break;
        }
        let mut le = [0u8; 4];
        for (slot, pair) in le.iter_mut().enumerate() {
            let offset = start + slot * 2;
            *pair = (nibble(bytes[offset]) << 4) | nibble(bytes[offset + 1]);
        }
        out[lane] = f32::from_le_bytes(le);
    }
    out
}

fn nibble(byte: u8) -> u8 {
    match byte {
        b'0'..=b'9' => byte - b'0',
        b'a'..=b'f' => byte - b'a' + 10,
        b'A'..=b'F' => byte - b'A' + 10,
        _ => 0,
    }
}

impl State {
    fn from_json(value: &Value) -> State {
        State {
            seq: int_field(value, "seq") as u64,
            documents: array(value, "documents").map(Document::from_json).collect(),
            chunks: array(value, "chunks").map(Chunk::from_json).collect(),
            symbols: array(value, "symbols").map(Symbol::from_json).collect(),
            annotations: array(value, "annotations")
                .map(Annotation::from_json)
                .collect(),
            locks: array(value, "locks").map(Lock::from_json).collect(),
            conflicts: array(value, "conflicts").map(Conflict::from_json).collect(),
            edges: array(value, "edges").map(Edge::from_json).collect(),
            sessions: array(value, "sessions").map(Session::from_json).collect(),
            dynamic: array(value, "dynamic").map(Dynamic::from_json).collect(),
            chunk_history: Vec::new(),
            change_log: Vec::new(),
            next_annotation: int_field(value, "next_annotation"),
            sigs: Vec::new(),
        }
    }
}

impl Document {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("path", Value::string(&self.path)),
            ("mtime_ns", Value::Int(self.mtime_ns)),
            ("size", Value::Int(self.size as i64)),
            ("sha256", Value::string(&self.sha256)),
            ("doc_version", Value::Int(self.doc_version as i64)),
            ("chunk_count", Value::Int(self.chunk_count as i64)),
        ])
    }
    fn from_json(value: &Value) -> Document {
        Document {
            path: string_field(value, "path"),
            mtime_ns: int_field(value, "mtime_ns"),
            size: int_field(value, "size") as u64,
            sha256: string_field(value, "sha256"),
            doc_version: int_field(value, "doc_version") as u64,
            chunk_count: int_field(value, "chunk_count") as u32,
        }
    }
}

impl Chunk {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("id", Value::string(&self.id)),
            ("path", Value::string(&self.path)),
            ("start", Value::Int(self.start as i64)),
            ("end", Value::Int(self.end as i64)),
            ("content", Value::string(&self.content)),
            ("sha256", Value::string(&self.sha256)),
            ("tokens", Value::Int(self.tokens as i64)),
            ("modality", Value::string(&self.modality)),
            ("language", opt_string(&self.language)),
            ("summary", opt_string(&self.summary)),
            ("notes", opt_string(&self.notes)),
        ])
    }
    fn from_json(value: &Value) -> Chunk {
        Chunk {
            id: string_field(value, "id"),
            path: string_field(value, "path"),
            start: int_field(value, "start") as usize,
            end: int_field(value, "end") as usize,
            content: string_field(value, "content"),
            sha256: string_field(value, "sha256"),
            signature: string_field(value, "signature"),
            tokens: int_field(value, "tokens") as usize,
            modality: string_field(value, "modality"),
            language: opt_string_field(value, "language"),
            summary: opt_string_field(value, "summary"),
            notes: opt_string_field(value, "notes"),
            version: match int_field(value, "version") {
                0 => 1,
                number => number as u64,
            },
            staleness: float_field(value, "staleness"),
            stale_since: opt_int_field(value, "stale_since"),
        }
    }
}

impl Symbol {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("name", Value::string(&self.name)),
            ("kind", Value::string(&self.kind)),
            ("role", Value::string(&self.role)),
            ("line", Value::Int(self.line as i64)),
            ("container", opt_string(&self.container)),
            ("chunk_id", Value::string(&self.chunk_id)),
            ("path", Value::string(&self.path)),
            ("chunk_index", Value::Int(self.chunk_index as i64)),
        ])
    }
    fn from_json(value: &Value) -> Symbol {
        Symbol {
            name: string_field(value, "name"),
            kind: string_field(value, "kind"),
            role: string_field(value, "role"),
            line: int_field(value, "line") as u32,
            container: opt_string_field(value, "container"),
            chunk_id: string_field(value, "chunk_id"),
            path: string_field(value, "path"),
            chunk_index: int_field(value, "chunk_index") as usize,
        }
    }
}

impl Annotation {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("id", Value::string(&self.id)),
            ("path", Value::string(&self.path)),
            ("body", Value::string(&self.body)),
            (
                "tags",
                Value::Array(self.tags.iter().cloned().map(Value::String).collect()),
            ),
            ("target_type", Value::string(&self.target_type)),
            ("created_at", Value::Int(self.created_at)),
            ("updated_at", Value::Int(self.updated_at)),
        ])
    }
    fn from_json(value: &Value) -> Annotation {
        Annotation {
            id: string_field(value, "id"),
            path: string_field(value, "path"),
            body: string_field(value, "body"),
            tags: array(value, "tags")
                .filter_map(|item| match item {
                    Value::String(text) => Some(text.clone()),
                    _ => None,
                })
                .collect(),
            target_type: string_field(value, "target_type"),
            created_at: int_field(value, "created_at"),
            updated_at: int_field(value, "updated_at"),
        }
    }
}

impl Conflict {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("path", Value::string(&self.path)),
            ("holder", Value::string(&self.holder)),
            ("requester", Value::string(&self.requester)),
            ("at", Value::Int(self.at)),
            ("kind", Value::string(&self.kind)),
        ])
    }
    fn from_json(value: &Value) -> Conflict {
        Conflict {
            path: string_field(value, "path"),
            holder: string_field(value, "holder"),
            requester: string_field(value, "requester"),
            at: int_field(value, "at"),
            kind: {
                let kind = string_field(value, "kind");
                if kind.is_empty() {
                    "lock_stolen".to_string()
                } else {
                    kind
                }
            },
        }
    }
}

impl Lock {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("path", Value::string(&self.path)),
            ("agent", Value::string(&self.agent)),
            ("until", Value::Int(self.until)),
        ])
    }
    fn from_json(value: &Value) -> Lock {
        Lock {
            path: string_field(value, "path"),
            agent: string_field(value, "agent"),
            until: int_field(value, "until"),
        }
    }
}

impl Edge {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("source", Value::string(&self.source)),
            ("target", Value::string(&self.target)),
            ("kind", Value::string(&self.kind)),
            ("name", Value::string(&self.name)),
        ])
    }
    fn from_json(value: &Value) -> Edge {
        Edge {
            source: string_field(value, "source"),
            target: string_field(value, "target"),
            kind: string_field(value, "kind"),
            name: string_field(value, "name"),
        }
    }
}

impl Session {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("id", Value::string(&self.id)),
            (
                "searches",
                Value::Array(self.searches.iter().cloned().map(Value::String).collect()),
            ),
            (
                "reads",
                Value::Array(self.reads.iter().cloned().map(Value::String).collect()),
            ),
            (
                "kv",
                Value::Array(
                    self.kv
                        .iter()
                        .map(|(key, value)| {
                            Value::object(vec![
                                ("k", Value::string(key)),
                                ("v", Value::string(value)),
                            ])
                        })
                        .collect(),
                ),
            ),
            ("created_at", Value::Int(self.created_at)),
            ("updated_at", Value::Int(self.updated_at)),
            ("turn", Value::Int(self.turn)),
            ("tokens", Value::Int(self.tokens)),
            ("agent", Value::string(&self.agent)),
        ])
    }
    fn from_json(value: &Value) -> Session {
        Session {
            id: string_field(value, "id"),
            searches: strings(value, "searches"),
            reads: strings(value, "reads"),
            kv: array(value, "kv")
                .map(|item| (string_field(item, "k"), string_field(item, "v")))
                .collect(),
            created_at: int_field(value, "created_at"),
            updated_at: int_field(value, "updated_at"),
            turn: int_field(value, "turn"),
            tokens: int_field(value, "tokens"),
            agent: string_field(value, "agent"),
            attached: Vec::new(),
            search_rows: Vec::new(),
        }
    }
}

impl Dynamic {
    fn to_json(&self) -> Value {
        Value::object(vec![
            ("name", Value::string(&self.name)),
            ("kind", Value::string(&self.kind)),
            ("role", Value::string(&self.role)),
            ("path", Value::string(&self.path)),
            ("agent", Value::string(&self.agent)),
            ("line", Value::Int(self.line as i64)),
            ("chunk_id", Value::string(&self.chunk_id)),
        ])
    }
    fn from_json(value: &Value) -> Dynamic {
        Dynamic {
            name: string_field(value, "name"),
            kind: string_field(value, "kind"),
            role: {
                let role = string_field(value, "role");
                if role.is_empty() {
                    "definition".to_string()
                } else {
                    role
                }
            },
            path: string_field(value, "path"),
            agent: string_field(value, "agent"),
            line: int_field(value, "line") as u32,
            chunk_id: string_field(value, "chunk_id"),
        }
    }
}

fn float_field(value: &Value, key: &str) -> f64 {
    match field(value, key) {
        Some(Value::Float(number)) => *number,
        Some(Value::Int(number)) => *number as f64,
        _ => 0.0,
    }
}

fn opt_int_field(value: &Value, key: &str) -> Option<i64> {
    match field(value, key) {
        Some(Value::Int(number)) => Some(*number),
        Some(Value::Float(number)) => Some(*number as i64),
        _ => None,
    }
}

fn opt_string(value: &Option<String>) -> Value {
    match value {
        Some(text) => Value::string(text),
        None => Value::Null,
    }
}

fn array<'a>(value: &'a Value, key: &str) -> impl Iterator<Item = &'a Value> {
    let items = match value {
        Value::Object(pairs) => pairs
            .iter()
            .find(|(name, _)| name == key)
            .and_then(|(_, item)| {
                if let Value::Array(items) = item {
                    Some(items)
                } else {
                    None
                }
            }),
        _ => None,
    };
    items.into_iter().flatten()
}

fn strings(value: &Value, key: &str) -> Vec<String> {
    array(value, key)
        .filter_map(|item| match item {
            Value::String(text) => Some(text.clone()),
            _ => None,
        })
        .collect()
}

pub fn string_field(value: &Value, key: &str) -> String {
    match field(value, key) {
        Some(Value::String(text)) => text.clone(),
        _ => String::new(),
    }
}

pub fn int_field(value: &Value, key: &str) -> i64 {
    match field(value, key) {
        Some(Value::Int(number)) => *number,
        _ => 0,
    }
}

fn opt_string_field(value: &Value, key: &str) -> Option<String> {
    match field(value, key) {
        Some(Value::String(text)) => Some(text.clone()),
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
