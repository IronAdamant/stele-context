//! One-way copy from a Python 1.5.x SQLite store into the native snapshot.
//!
//! The original database, its WAL, and its rollback journal are copied and
//! read from the copy. A failure deletes the temp snapshot and leaves both
//! the legacy files and the previous native snapshot unchanged.

use crate::json::{self, Value};
use crate::store::Store;
use std::fs;
use std::path::Path;
use std::process::Command;

pub fn migrate(storage: &Path, store: &mut Store) -> Result<(), String> {
    let db = storage.join("stele_context.db");
    if !db.is_file() {
        return Err("no Python stele_context.db in the storage directory".to_string());
    }
    let scratch = storage.join("store").join("migrate-tmp");
    let _ = fs::remove_dir_all(&scratch);
    fs::create_dir_all(&scratch).map_err(|err| format!("migrate scratch: {err}"))?;
    let copy = scratch.join("stele_context.db");
    fs::copy(&db, &copy).map_err(|err| format!("copy db: {err}"))?;
    for suffix in ["-wal", "-shm", "-journal"] {
        let side = storage.join(format!("stele_context.db{suffix}"));
        if side.is_file() {
            fs::copy(&side, scratch.join(format!("stele_context.db{suffix}")))
                .map_err(|err| format!("copy {suffix}: {err}"))?;
        }
    }
    let script = r#"
import json, sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("pragma query_only=on")
def has(name):
    return con.execute(
        "select 1 from sqlite_master where type='table' and name=?", (name,)
    ).fetchone()
def rows(sql):
    cur = con.execute(sql)
    names = [item[0] for item in cur.description]
    out = []
    for row in cur.fetchall():
        item = {}
        for name, value in zip(names, row):
            if isinstance(value, bytes):
                continue
            item[name] = value
        out.append(item)
    return out
payload = {"documents": [], "chunks": []}
if has("documents"):
    payload["documents"] = rows(
        "select path, file_size, content_hash from documents"
    )
if has("chunks"):
    payload["chunks"] = rows(
        "select chunk_id, document_path, start_pos, end_pos, content, "
        "content_hash, token_count, semantic_summary from chunks"
    )
json.dump(payload, sys.stdout)
"#;
    let output = Command::new("python3")
        .arg("-c")
        .arg(script)
        .arg(&copy)
        .output()
        .map_err(|err| format!("python sqlite reader: {err}"))?;
    if !output.status.success() {
        let _ = fs::remove_dir_all(&scratch);
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(format!(
            "migrate refused; legacy database was not modified: {detail}"
        ));
    }
    let value = json::parse(&String::from_utf8_lossy(&output.stdout))
        .map_err(|err| format!("migrate payload: {}", err.message))?;
    apply(store, &value)?;
    store.save(
        "migrate",
        &Value::object(vec![("source", Value::string("stele_context.db"))]),
    )?;
    let _ = fs::remove_dir_all(&scratch);
    Ok(())
}

fn apply(store: &mut Store, value: &Value) -> Result<(), String> {
    let Value::Object(_) = value else {
        return Err("migrate payload was not an object".to_string());
    };
    // Import is all-or-nothing: build a replacement, then swap.
    let mut next = store.state.clone();
    next.documents.clear();
    next.chunks.clear();
    next.symbols.clear();
    if let Value::Object(pairs) = value {
        if let Some((_, Value::Array(rows))) = pairs.iter().find(|(key, _)| key == "documents") {
            for row in rows {
                let path = text(row, "path").or_else(|| text(row, "document_path"));
                let Some(path) = path else { continue };
                next.documents.push(crate::store::Document {
                    path,
                    mtime_ns: 0,
                    size: num(row, "file_size") as u64,
                    sha256: text(row, "content_hash").unwrap_or_default(),
                });
            }
        }
        if let Some((_, Value::Array(rows))) = pairs.iter().find(|(key, _)| key == "chunks") {
            for row in rows {
                let Some(id) = text(row, "chunk_id") else {
                    continue;
                };
                let content = text(row, "content").unwrap_or_default();
                next.chunks.push(crate::store::Chunk {
                    id,
                    path: text(row, "document_path").unwrap_or_default(),
                    start: num(row, "start_pos") as usize,
                    end: num(row, "end_pos") as usize,
                    sha256: text(row, "content_hash").unwrap_or_default(),
                    signature: crate::chunk::signature_f32_hex(&content),
                    tokens: if num(row, "token_count") == 0 {
                        crate::chunk::estimate_tokens(&content)
                    } else {
                        num(row, "token_count") as usize
                    },
                    modality: "code".to_string(),
                    language: None,
                    summary: text(row, "semantic_summary"),
                    notes: None,
                    content,
                });
            }
        }
    }
    if next.chunks.is_empty() && next.documents.is_empty() {
        return Err(
            "legacy database has no documents or chunks; native store was not replaced".to_string(),
        );
    }
    store.state = next;
    Ok(())
}

fn text(value: &Value, key: &str) -> Option<String> {
    let Value::Object(pairs) = value else {
        return None;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .and_then(|(_, item)| match item {
            Value::String(text) => Some(text.clone()),
            Value::Int(number) => Some(number.to_string()),
            _ => None,
        })
}

fn num(value: &Value, key: &str) -> i64 {
    let Value::Object(pairs) = value else {
        return 0;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .and_then(|(_, item)| match item {
            Value::Int(number) => Some(*number),
            Value::Float(number) => Some(*number as i64),
            _ => None,
        })
        .unwrap_or(0)
}
