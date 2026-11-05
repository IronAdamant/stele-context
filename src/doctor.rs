//! Environment report. Counts come from the native snapshot when it exists.

use crate::config::Config;
use crate::contract::{ErrorBody, Response, EXIT_ERROR};
use crate::json::{self, Value};
use crate::root::{detect_project_root, resolve_storage_dir};
use crate::VERSION;
use std::fs;
use std::path::Path;
pub fn report(storage_flag: Option<&Path>, human: bool) -> Response {
    crate::engine::prefetch_python();
    let cwd = match std::env::current_dir() {
        Ok(cwd) => cwd,
        Err(err) => {
            return Response::failure(
                "doctor",
                EXIT_ERROR,
                ErrorBody::new(
                    "cwd",
                    format!("current directory: {err}"),
                    "run from a real directory",
                ),
            );
        }
    };
    let project_root = detect_project_root(&cwd);
    let config = Config::load_at(project_root.as_deref());
    let storage = match resolve_storage_dir(storage_flag, &config, project_root.as_deref()) {
        Ok(path) => path,
        Err(message) => {
            return Response::failure(
                "doctor",
                EXIT_ERROR,
                ErrorBody::new("storage_dir", message, "pass --storage-dir or set HOME"),
            );
        }
    };
    let legacy = storage.join("stele_context.db").is_file();
    let snapshot_path = storage.join("store").join("snapshot.json");
    if legacy && !snapshot_path.is_file() {
        let _ = crate::engine::Engine::open_at(cwd.clone(), storage.clone());
    }
    let native = snapshot_path.is_file();
    let parsed = read_snapshot(&snapshot_path);
    let mut steps: Vec<String> = Vec::new();
    let migrated = storage.join("store").join("migrated-from.txt").is_file();
    let migrate_error = fs::read_to_string(storage.join("store").join("migrate-error.txt")).ok();
    if let Some(err) = migrate_error.as_ref() {
        steps.push(format!(
            "migrate failed and the original stele_context.db was not modified: {err}"
        ));
    } else if legacy && migrated {
        steps.push(
            "A Python 1.5.x stele_context.db was copied into the native store. The original database was not modified.".to_string(),
        );
    } else if legacy && native {
        steps.push(
            "A Python 1.5.x stele_context.db is present and was left untouched because a native store already has documents.".to_string(),
        );
    } else if legacy {
        steps.push(
            "A Python 1.5.x stele_context.db is present. Opening the store copies it into snapshot.json. The original database is not modified.".to_string(),
        );
    }
    if native && parsed.error.is_none() {
        steps.push("A native store is present. snapshot.json parsed.".to_string());
    } else if parsed.error.is_some() {
        steps.push("snapshot.json did not parse. The native store was left untouched.".to_string());
    } else {
        steps.push("No 2.0 store yet. Index a directory to create the native store.".to_string());
    }
    steps.push(
        "Image, audio, and video chunks are metadata. PDF text is the uncompressed streams."
            .to_string(),
    );
    if human {
        let root = project_root
            .as_ref()
            .map(|path| path.display().to_string())
            .unwrap_or_else(|| "(none)".to_string());
        let text = format!(
            "stele-context {VERSION} (rust)\nproject root: {root}\nstorage: {}\nnative store: {}\nlegacy sqlite: {}\n{}",
            storage.display(),
            if native { "yes" } else { "no" },
            if legacy { "yes" } else { "no" },
            steps.join("\n"),
        );
        return Response::text(0, &text);
    }
    let result = Value::object(vec![
        ("version", Value::string(VERSION)),
        ("implementation", Value::string("rust")),
        (
            "project_root",
            match &project_root {
                Some(path) => Value::string(path.display().to_string()),
                None => Value::Null,
            },
        ),
        ("storage_dir", Value::string(storage.display().to_string())),
        ("config_loaded", Value::Bool(config.loaded)),
        ("native_store_present", Value::Bool(native)),
        ("legacy_sqlite_present", Value::Bool(legacy)),
        ("stele_version", Value::string(VERSION)),
        ("python", crate::engine::host_python()),
        ("document_count", Value::Int(parsed.documents)),
        ("chunk_count", Value::Int(parsed.chunks)),
        ("index_health", parsed.index_health(&storage)),
        ("db_health", parsed.db_health(&storage)),
        ("search_quality", parsed.search_quality(&storage)),
        (
            "environment",
            Value::object(vec![
                ("issues", Value::Array(Vec::new())),
                ("total_issues", Value::Int(0)),
            ]),
        ),
        ("map_preview", parsed.map_preview()),
        ("recommended_mcp_mode", Value::string("lite")),
        (
            "token_savings",
            Value::object(vec![
                ("indexed_tokens", Value::Int(parsed.tokens)),
                ("document_count", Value::Int(parsed.documents)),
                ("chunk_count", Value::Int(parsed.chunks)),
            ]),
        ),
        (
            "next_steps",
            Value::Array(steps.into_iter().map(Value::string).collect()),
        ),
    ]);
    Response::success("doctor", result)
}

struct ParsedStore {
    documents: i64,
    chunks: i64,
    symbols: i64,
    tokens: i64,
    tier2: i64,
    latest: f64,
    error: Option<String>,
    paths: Vec<String>,
}

fn read_snapshot(path: &Path) -> ParsedStore {
    let empty = ParsedStore {
        documents: 0,
        chunks: 0,
        symbols: 0,
        tokens: 0,
        tier2: 0,
        latest: 0.0,
        error: None,
        paths: Vec::new(),
    };
    if !path.is_file() {
        return empty;
    }
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(err) => {
            return ParsedStore {
                error: Some(format!("snapshot.json unreadable: {err}")),
                ..empty
            };
        }
    };
    let value = match json::parse(&text) {
        Ok(value) => value,
        Err(err) => {
            return ParsedStore {
                error: Some(format!("snapshot.json did not parse: {err:?}")),
                ..empty
            };
        }
    };
    let documents = array_len(&value, "documents");
    let mut symbols = array_len(&value, "symbols");
    let chunk_rows = match field(&value, "chunks") {
        Some(Value::Array(rows)) => rows.as_slice(),
        _ => &[],
    };
    let mut tokens = 0i64;
    let mut tier2 = 0i64;
    for chunk in chunk_rows {
        tokens += int_field(chunk, "tokens");
        if field(chunk, "summary").is_some_and(|item| !matches!(item, Value::Null)) {
            tier2 += 1;
        }
    }
    let mut paths = Vec::new();
    let mut latest = 0.0f64;
    if let Some(Value::Array(rows)) = field(&value, "documents") {
        for document in rows {
            if let Some(Value::String(path)) = field(document, "path") {
                paths.push(path.clone());
            }
            latest = latest.max(float_field(document, "indexed_at"));
        }
    }
    if let Some(Value::Array(rows)) = field(&value, "dynamic") {
        symbols += rows.len();
    }
    ParsedStore {
        documents: documents as i64,
        chunks: chunk_rows.len() as i64,
        symbols: symbols as i64,
        tokens,
        tier2,
        latest,
        error: None,
        paths,
    }
}

impl ParsedStore {
    fn index_health(&self, storage: &Path) -> Value {
        let symbol_graph_status = if self.symbols > 0 {
            "ready"
        } else if self.chunks > 0 {
            "empty_with_chunks"
        } else {
            "empty"
        };
        let chunk_store_status = if self.chunks > 0 {
            "ready"
        } else if self.documents > 0 {
            "empty_with_documents"
        } else {
            "empty"
        };
        let mut alerts = Vec::new();
        if let Some(error) = &self.error {
            alerts.push(error.clone());
        }
        if self.documents == 0 && self.error.is_none() {
            alerts.push(
                "No documents indexed; run index on project paths before search or symbols."
                    .to_string(),
            );
        }
        if self.chunks > 0 && self.symbols == 0 {
            alerts.push(
                "Chunk store has data but the symbol graph is empty; index source files then run rebuild_symbols."
                    .to_string(),
            );
        }
        Value::object(vec![
            ("documents", Value::Int(self.documents)),
            ("chunks", Value::Int(self.chunks)),
            ("symbol_rows", Value::Int(self.symbols)),
            ("symbols_ready", Value::Bool(self.symbols > 0)),
            (
                "latest_indexed_at",
                if self.latest > 0.0 {
                    Value::Float(self.latest)
                } else {
                    Value::Null
                },
            ),
            ("storage_dir", Value::string(storage.display().to_string())),
            ("symbol_graph_status", Value::string(symbol_graph_status)),
            ("chunk_store_status", Value::string(chunk_store_status)),
            (
                "alerts",
                Value::Array(alerts.into_iter().map(Value::string).collect()),
            ),
        ])
    }

    fn db_health(&self, storage: &Path) -> Value {
        let store = storage.join("store");
        let snapshot = file_len(&store.join("snapshot.json"));
        let journal = file_len(&store.join("journal.jsonl"));
        let sigs = file_len(&store.join("signatures.bin"));
        let mut alerts = Vec::new();
        if let Some(error) = &self.error {
            alerts.push(Value::string(error));
        }
        Value::object(vec![
            (
                "table_rows",
                Value::object(vec![
                    ("documents", Value::Int(self.documents)),
                    ("chunks", Value::Int(self.chunks)),
                    ("symbols", Value::Int(self.symbols)),
                ]),
            ),
            ("growth_alerts", Value::Array(alerts)),
            ("journal_mode", Value::Null),
            ("synchronous", Value::Null),
            ("page_count", Value::Null),
            ("page_size", Value::Null),
            (
                "database_size_bytes",
                Value::Int((snapshot + journal + sigs) as i64),
            ),
            (
                "wal_size_bytes",
                Value::Int(file_len(&storage.join("stele_context.db-wal")) as i64),
            ),
            ("wal_autocheckpoint", Value::Null),
            ("busy_timeout_ms", Value::Null),
            ("recent_operations_1h", Value::Int(0)),
            ("recent_failures_1h", Value::Int(0)),
            ("busy_ratio", Value::Float(0.0)),
            (
                "recommended_action",
                Value::string(if self.error.is_some() {
                    "repair snapshot.json"
                } else {
                    "idle"
                }),
            ),
        ])
    }

    fn search_quality(&self, storage: &Path) -> Value {
        let coverage = if self.chunks == 0 {
            0.0
        } else {
            (self.tier2 as f64 / self.chunks as f64) * 100.0
        };
        let advice = if coverage < 5.0 {
            Some(
                "Tier-2 coverage is critically low. Most semantic searches will fall back to BM25.",
            )
        } else {
            None
        };
        Value::object(vec![
            ("total_chunks", Value::Int(self.chunks)),
            ("tier2_chunks", Value::Int(self.tier2)),
            ("tier2_coverage_percent", Value::Float(coverage)),
            (
                "hnsw_span",
                Value::Float(hnsw_span(storage, self.chunks as usize)),
            ),
            ("advice", advice.map(Value::string).unwrap_or(Value::Null)),
        ])
    }

    fn map_preview(&self) -> Value {
        let shown: Vec<Value> = self
            .paths
            .iter()
            .take(8)
            .map(|path| Value::object(vec![("path", Value::string(path))]))
            .collect();
        Value::object(vec![
            ("document_count", Value::Int(self.documents)),
            ("documents", Value::Array(shown)),
        ])
    }
}

fn file_len(path: &Path) -> u64 {
    fs::metadata(path).map(|meta| meta.len()).unwrap_or(0)
}

fn hnsw_span(storage: &Path, count: usize) -> f64 {
    let bytes = match std::fs::read(storage.join("store").join("signatures.bin")) {
        Ok(bytes) if bytes.len() >= count * 128 * 4 && count > 0 => bytes,
        _ => return 0.0,
    };
    let mut graph = crate::search::Hnsw::new();
    for index in 0..count {
        let start = index * 128 * 4;
        let mut vector = Vec::with_capacity(128);
        for lane in 0..128 {
            let offset = start + lane * 4;
            let mut buf = [0u8; 4];
            buf.copy_from_slice(&bytes[offset..offset + 4]);
            vector.push(f32::from_le_bytes(buf));
        }
        graph.insert(&index.to_string(), vector);
    }
    graph.probe_span()
}

fn array_len(value: &Value, key: &str) -> usize {
    match field(value, key) {
        Some(Value::Array(rows)) => rows.len(),
        _ => 0,
    }
}

fn int_field(value: &Value, key: &str) -> i64 {
    match field(value, key) {
        Some(Value::Int(number)) => *number,
        _ => 0,
    }
}

fn float_field(value: &Value, key: &str) -> f64 {
    match field(value, key) {
        Some(Value::Float(number)) => *number,
        Some(Value::Int(number)) => *number as f64,
        _ => 0.0,
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

impl Config {
    pub fn load_at(root: Option<&Path>) -> Config {
        crate::config::load(root)
    }
}

#[cfg(test)]
mod tests {
    use super::report;
    use std::fs;

    #[test]
    fn doctor_parses_snapshot_json() {
        let dir = std::env::temp_dir().join(format!("stele-doctor-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("store")).unwrap();
        fs::write(
            dir.join("store/snapshot.json"),
            r#"{"documents":[{"path":"a.py"}],"chunks":[{"tokens":3}],"symbols":[{"role":"definition"}]}"#,
        )
        .unwrap();
        let ok = report(Some(&dir), false);
        let text = String::from_utf8(ok.stdout).unwrap();
        assert!(text.contains("\"document_count\":1"), "{text}");
        assert!(text.contains("snapshot.json parsed"), "{text}");
        fs::write(dir.join("store/snapshot.json"), "not-json").unwrap();
        let bad = report(Some(&dir), false);
        let text = String::from_utf8(bad.stdout).unwrap();
        assert!(text.contains("did not parse"), "{text}");
        let _ = fs::remove_dir_all(&dir);
    }
}
