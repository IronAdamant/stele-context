//! One-way copy from a Python 1.5.x SQLite store into the native snapshot.
//!
//! The original database, its WAL, and its rollback journal are only read.
//! A failure leaves the previous native snapshot unchanged.

use crate::chunk::{self, signature_f32_hex};
use crate::json::Value;
use crate::store::Store;
use crate::store::{
    sql_blob, sql_number, sql_text, Chunk, Conflict, Document, Edge, Session, SqliteDb, State,
    Symbol,
};
use std::fs;
use std::path::Path;

pub enum Legacy {
    Absent,
    Kept,
    Copied { documents: i64, chunks: i64 },
}

pub fn migrate_if_needed(storage: &Path, cwd: &Path, store: &mut Store) -> Result<Legacy, String> {
    let db = storage.join("stele_context.db");
    if !db.is_file() {
        return Ok(Legacy::Absent);
    }
    if !store.state.documents.is_empty() || !store.state.chunks.is_empty() {
        return Ok(Legacy::Kept);
    }
    let copied = read_db(&db, cwd)?;
    let documents = copied.documents.len() as i64;
    let chunks = copied.chunks.len() as i64;
    if documents == 0 && chunks == 0 {
        return Err(
            "legacy database has no documents or chunks; native store was not replaced".to_string(),
        );
    }
    store.state = copied;
    let mut counts = std::collections::HashMap::<&str, u32>::new();
    for chunk in &store.state.chunks {
        *counts.entry(chunk.path.as_str()).or_default() += 1;
    }
    for doc in &mut store.state.documents {
        doc.chunk_count = counts.get(doc.path.as_str()).copied().unwrap_or(0);
    }
    store.save(
        "migrate",
        &Value::object(vec![("source", Value::string("stele_context.db"))]),
    )?;
    let _ = fs::write(
        storage.join("store").join("migrated-from.txt"),
        "stele_context.db\n",
    );
    Ok(Legacy::Copied { documents, chunks })
}

fn read_db(db: &Path, cwd: &Path) -> Result<State, String> {
    let opened = SqliteDb::open(db)?;
    let mut state = State::default();
    for row in opened
        .table("documents")
        .map_err(|err| format!("documents: {err}"))?
    {
        let path = sql_text(&row, "document_path");
        if path.is_empty() {
            continue;
        }
        let size = sql_number(&row, "file_size") as u64;
        let (mtime_ns, size) = live_stat(cwd, &path, size);
        state.documents.push(Document {
            path,
            mtime_ns,
            size,
            sha256: sql_text(&row, "content_hash"),
            doc_version: sql_number(&row, "doc_version") as u64,
            chunk_count: 0,
        });
    }
    for row in opened
        .table("chunks")
        .map_err(|err| format!("chunks: {err}"))?
    {
        let id = sql_text(&row, "chunk_id");
        if id.is_empty() {
            continue;
        }
        let content = sql_text(&row, "content");
        let blob = sql_blob(&row, "semantic_signature");
        let signature = if blob.len() == 128 * 4 {
            signature_f32_hex_from_blob(&blob)
        } else {
            signature_f32_hex(&content)
        };
        let mut tokens = sql_number(&row, "token_count") as usize;
        if tokens == 0 {
            tokens = chunk::estimate_tokens(&content).max(1);
        }
        state.chunks.push(Chunk {
            id,
            path: sql_text(&row, "document_path"),
            start: sql_number(&row, "start_pos") as usize,
            end: sql_number(&row, "end_pos") as usize,
            content,
            sha256: sql_text(&row, "content_hash"),
            signature,
            tokens,
            modality: "code".to_string(),
            language: None,
            summary: empty_none(sql_text(&row, "semantic_summary")),
            notes: empty_none(sql_text(&row, "agent_notes")),
            version: sql_number(&row, "version") as u64,
            staleness: sql_number(&row, "staleness_score"),
            stale_since: nonzero_opt(sql_number(&row, "stale_since")),
        });
    }
    for row in opened.table("symbols").unwrap_or_default() {
        let name = sql_text(&row, "name");
        if name.is_empty() {
            continue;
        }
        let chunk_id = sql_text(&row, "chunk_id");
        if chunk_id.starts_with("runtime:") {
            state.dynamic.push(crate::store::Dynamic {
                name: name.clone(),
                kind: sql_text(&row, "kind"),
                role: sql_text(&row, "role"),
                path: sql_text(&row, "document_path"),
                agent: runtime_agent(&chunk_id),
                line: sql_number(&row, "line_number") as u32,
                chunk_id,
            });
            continue;
        }
        state.symbols.push(Symbol {
            name,
            kind: sql_text(&row, "kind"),
            role: sql_text(&row, "role"),
            line: sql_number(&row, "line_number") as u32,
            container: empty_none(sql_text(&row, "container")),
            chunk_id,
            path: sql_text(&row, "document_path"),
            chunk_index: 0,
        });
    }
    for row in opened.table("symbol_edges").unwrap_or_default() {
        state.edges.push(Edge {
            source: sql_text(&row, "source_chunk_id"),
            target: sql_text(&row, "target_chunk_id"),
            kind: sql_text(&row, "edge_type"),
            name: sql_text(&row, "symbol_name"),
        });
    }
    for row in opened.table("annotations").unwrap_or_default() {
        let id = sql_number(&row, "id") as i64;
        state.next_annotation = state.next_annotation.max(id);
        state.annotations.push(crate::store::Annotation {
            id: id.to_string(),
            path: sql_text(&row, "target"),
            body: sql_text(&row, "content"),
            tags: split_tags(&sql_text(&row, "tags")),
            target_type: sql_text(&row, "target_type"),
            created_at: unix_us(sql_number(&row, "created_at")),
            updated_at: unix_us(sql_number(&row, "updated_at")),
        });
    }
    for row in opened.table("sessions").unwrap_or_default() {
        state.sessions.push(Session {
            id: sql_text(&row, "session_id"),
            searches: Vec::new(),
            reads: Vec::new(),
            kv: Vec::new(),
            created_at: unix_us(sql_number(&row, "created_at")),
            updated_at: unix_us(sql_number(&row, "last_updated")),
            turn: sql_number(&row, "turn_count") as i64,
            tokens: sql_number(&row, "total_tokens") as i64,
            agent: sql_text(&row, "agent_id"),
            attached: Vec::new(),
            search_rows: Vec::new(),
        });
    }
    for row in opened.table("document_conflicts").unwrap_or_default() {
        state.conflicts.push(Conflict {
            path: sql_text(&row, "document_path"),
            holder: sql_text(&row, "agent_a"),
            requester: sql_text(&row, "agent_b"),
            at: unix_us(sql_number(&row, "created_at")),
            kind: sql_text(&row, "conflict_type"),
        });
    }
    for row in opened.table("chunk_history").unwrap_or_default() {
        state.chunk_history.push(crate::store::ChunkHist {
            chunk_id: sql_text(&row, "chunk_id"),
            version: sql_number(&row, "version") as u64,
            hash: sql_text(&row, "content_hash"),
            at: sql_number(&row, "created_at") as i64,
            path: String::new(),
        });
    }
    Ok(state)
}

fn live_stat(cwd: &Path, path: &str, recorded: u64) -> (i64, u64) {
    let abs = cwd.join(path);
    if let Ok(meta) = fs::metadata(&abs) {
        if recorded == 0 || meta.len() == recorded {
            return (mtime_ns(&meta), meta.len());
        }
    }
    (0, recorded)
}

/// Keep a 1.5.2 `time.time()` float as microseconds so session_time
/// can print the fractional seconds again.
fn unix_us(value: f64) -> i64 {
    if value.abs() > 1.0e12 {
        value.round() as i64
    } else {
        (value * 1_000_000.0).round() as i64
    }
}

fn mtime_ns(meta: &fs::Metadata) -> i64 {
    meta.modified()
        .ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos() as i64)
        .unwrap_or(0)
}

fn signature_f32_hex_from_blob(blob: &[u8]) -> String {
    let mut hex = String::with_capacity(blob.len() * 2);
    for byte in blob {
        hex.push_str(&format!("{byte:02x}"));
    }
    hex
}

fn empty_none(text: String) -> Option<String> {
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

fn nonzero_opt(value: f64) -> Option<i64> {
    if value == 0.0 {
        None
    } else {
        Some(value as i64)
    }
}

fn split_tags(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    if let Some(rest) = text.strip_prefix('[') {
        return rest
            .trim_end_matches(']')
            .split(',')
            .map(|part| part.trim().trim_matches('"').trim_matches('\'').to_string())
            .filter(|part| !part.is_empty())
            .collect();
    }
    text.split(',')
        .map(|part| part.trim().to_string())
        .filter(|part| !part.is_empty())
        .collect()
}

fn runtime_agent(chunk_id: &str) -> String {
    chunk_id
        .strip_prefix("runtime:")
        .and_then(|rest| rest.split(':').next())
        .unwrap_or("")
        .to_string()
}
