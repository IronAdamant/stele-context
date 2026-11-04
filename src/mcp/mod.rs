//! Newline-delimited MCP. Speaks 2026-07-28 and the two previous handshakes.
//! Full mode still lists the 18 removed names and answers `tool_removed`.

use crate::contract::Response;
use crate::engine::Engine;
use crate::json::{self, Value};
use crate::VERSION;
use std::io::{self, BufRead, Write};

const HANDSHAKES: &[&str] = &["2026-07-28", "2025-11-25", "2025-06-18"];

const LITE: &[&str] = &[
    "index",
    "remove",
    "detect_changes",
    "search",
    "agent_grep",
    "search_text",
    "query",
    "find_references",
    "find_definition",
    "impact_radius",
    "coupling",
    "get_context",
    "doctor",
    "map",
    "document_lock",
    "annotations",
    "register_dynamic_symbols",
    "remove_dynamic_symbols",
    "llm_embed",
    "bulk_store_summaries",
    "enrichment_plan",
    "get_search_history",
    "get_session_read_files",
    "detect_modality",
    "get_supported_formats",
];

const STANDARD_EXTRA: &[&str] = &[
    "prune_history",
    "history",
    "get_relevant_kv",
    "save_kv_state",
    "rollback",
    "prune_chunks",
    "list_sessions",
    "rebuild_symbols",
    "stale_chunks",
    "get_dynamic_symbols",
    "list_agents",
    "get_notifications",
    "environment_check",
    "clean_bytecache",
    "bulk_store_embeddings",
    "bulk_store_chunk_agent_notes",
    "get_chunk_history",
    "batch",
];

const REMOVED: &[(&str, &str)] = &[
    ("stats", "doctor"),
    ("project_brief", "doctor"),
    ("annotate", "annotations"),
    ("get_annotations", "annotations"),
    ("delete_annotation", "annotations"),
    ("update_annotation", "annotations"),
    ("search_annotations", "annotations"),
    ("bulk_annotate", "annotations"),
    ("acquire_document_lock", "document_lock"),
    ("refresh_document_lock", "document_lock"),
    ("release_document_lock", "document_lock"),
    ("get_document_lock_status", "document_lock"),
    ("release_agent_locks", "document_lock"),
    ("get_conflicts", "document_lock"),
    ("reap_expired_locks", "document_lock"),
    ("store_semantic_summary", "bulk_store_summaries"),
    ("store_embedding", "bulk_store_embeddings"),
    ("store_chunk_agent_notes", "bulk_store_chunk_agent_notes"),
];

pub fn serve() -> Response {
    let stdin = io::stdin();
    let mut stdout = io::stdout();
    let mut engine = Engine::open(None).ok();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let Ok(value) = json::parse(&line) else {
            let _ = writeln!(stdout, "{}", error_line(Value::Null, -32700, "parse error"));
            continue;
        };
        let id = field(&value, "id").cloned().unwrap_or(Value::Null);
        let method = string_field(&value, "method");
        if method.is_empty() {
            continue;
        }
        let params = field(&value, "params")
            .cloned()
            .unwrap_or(Value::object(vec![]));
        if field(&value, "id").is_none() {
            continue;
        }
        let result = match method.as_str() {
            "initialize" => initialize(&params),
            "tools/list" => {
                let _ = writeln!(
                    stdout,
                    "{{\"jsonrpc\":\"2.0\",\"id\":{id_json},\"result\":{cached}}}",
                    id_json = json::to_string(&id),
                    cached = cached_tools_json()
                );
                let _ = stdout.flush();
                continue;
            }
            "tools/call" => tools_call(engine.as_mut(), &params),
            "ping" => Value::object(vec![]),
            _ => {
                let _ = writeln!(stdout, "{}", error_line(id, -32601, "method not found"));
                let _ = stdout.flush();
                continue;
            }
        };
        let _ = writeln!(
            stdout,
            "{}",
            json::to_string(&Value::object(vec![
                ("jsonrpc", Value::string("2.0")),
                ("id", id),
                ("result", result),
            ]))
        );
        let _ = stdout.flush();
    }
    Response {
        code: 0,
        stdout: Vec::new(),
    }
}

fn initialize(params: &Value) -> Value {
    let requested = string_field(params, "protocolVersion");
    let version = if HANDSHAKES.contains(&requested.as_str()) {
        requested
    } else {
        "2026-07-28".to_string()
    };
    Value::object(vec![
        ("protocolVersion", Value::string(version)),
        (
            "capabilities",
            Value::object(vec![("tools", Value::object(vec![]))]),
        ),
        (
            "serverInfo",
            Value::object(vec![
                ("name", Value::string("stele-context")),
                ("version", Value::string(VERSION)),
            ]),
        ),
    ])
}

fn cached_tools_json() -> &'static str {
    use std::sync::OnceLock;
    static LITE: OnceLock<String> = OnceLock::new();
    static STANDARD: OnceLock<String> = OnceLock::new();
    static FULL: OnceLock<String> = OnceLock::new();
    let mode = std::env::var("STELE_MCP_MODE").unwrap_or_else(|_| "lite".to_string());
    let slot = match mode.as_str() {
        "full" => &FULL,
        "standard" => &STANDARD,
        _ => &LITE,
    };
    slot.get_or_init(|| json::to_string(&tools_list())).as_str()
}

fn tools_list() -> Value {
    let mode = std::env::var("STELE_MCP_MODE").unwrap_or_else(|_| "lite".to_string());
    let mut names: Vec<&str> = LITE.to_vec();
    if mode == "standard" || mode == "full" {
        names.extend_from_slice(STANDARD_EXTRA);
    }
    if mode == "full" {
        names.extend(REMOVED.iter().map(|(name, _)| *name));
    }
    let tools = names
        .into_iter()
        .map(|name| {
            Value::object(vec![
                ("name", Value::string(name)),
                ("description", Value::string(describe(name))),
                (
                    "inputSchema",
                    Value::object(vec![("type", Value::string("object"))]),
                ),
            ])
        })
        .collect();
    Value::object(vec![("tools", Value::Array(tools))])
}

fn tools_call(engine: Option<&mut Engine>, params: &Value) -> Value {
    let name = string_field(params, "name");
    if let Some((_, replacement)) = REMOVED.iter().find(|(old, _)| *old == name) {
        return tool_text(&format!(
            "{{\"ok\":false,\"error\":{{\"code\":\"tool_removed\",\"message\":\"{name} was removed in 2.0\",\"replacement\":\"{replacement}\"}}}}"
        ));
    }
    let Some(engine) = engine else {
        return tool_text(
            "{\"ok\":false,\"error\":{\"code\":\"storage\",\"message\":\"store did not open\"}}",
        );
    };
    let args = field(params, "arguments")
        .cloned()
        .unwrap_or(Value::object(vec![]));
    let text = match dispatch(engine, &name, &args) {
        Ok(value) => json::to_string(&value),
        Err(message) => json::to_string(&Value::object(vec![
            ("ok", Value::Bool(false)),
            ("error", Value::string(message)),
        ])),
    };
    tool_text(&text)
}

pub(crate) fn lite_tools() -> &'static [&'static str] {
    LITE
}

pub(crate) fn dispatch(engine: &mut Engine, name: &str, args: &Value) -> Result<Value, String> {
    let path = arg(args, "path")
        .or_else(|| arg(args, "document_path"))
        .unwrap_or_default();
    let query = arg(args, "query")
        .or_else(|| arg(args, "text"))
        .or_else(|| arg(args, "pattern"))
        .unwrap_or_default();
    let symbol = arg(args, "symbol")
        .or_else(|| arg(args, "name"))
        .unwrap_or_default();
    let session = arg(args, "session_id");
    match name {
        "index" => {
            let paths = if path.is_empty() {
                vec![".".to_string()]
            } else {
                vec![path]
            };
            engine.index(&paths, false)
        }
        "remove" => engine.remove(&path),
        "detect_changes" => engine.detect(true),
        "search" => Ok(engine.search(
            &query,
            &arg(args, "search_mode").unwrap_or_else(|| "keyword".to_string()),
            10,
        )),
        "search_text" => engine.search_text(&query),
        "agent_grep" => engine.agent_grep(&query, session.as_deref()),
        "query" => engine.query(&query),
        "find_definition" => Ok(engine.find_definition(&symbol)),
        "find_references" => Ok(engine.find_references(&symbol)),
        "impact_radius" => Ok(engine.impact(&symbol)),
        "coupling" => Ok(engine.coupling(&path)),
        "get_context" => engine.get_context(&path, session.as_deref()),
        "doctor" => Ok(crate::json::parse(&doctor_body()).unwrap_or(Value::object(vec![]))),
        "map" => Ok(engine.map()),
        "enrichment_plan" => Ok(engine.enrichment_plan()),
        "history" => engine.history(),
        "stats" => Ok(engine.stats()),
        "project_brief" => Ok(engine.project_brief()),
        "annotations" => engine.annotations(
            &arg(args, "action").unwrap_or_else(|| "get".to_string()),
            &path,
            &arg(args, "body").unwrap_or_default(),
        ),
        "document_lock" => engine.document_lock(
            &arg(args, "action").unwrap_or_else(|| "status".to_string()),
            &path,
            &arg(args, "agent_id").unwrap_or_else(|| "default".to_string()),
            flag(args, "force"),
        ),
        "get_search_history" => Ok(engine.search_history(session.as_deref().unwrap_or("default"))),
        "get_session_read_files" => Ok(engine.read_files(session.as_deref().unwrap_or("default"))),
        "save_kv_state" => engine.save_kv(
            &session.unwrap_or_else(|| "default".to_string()),
            &arg(args, "key").unwrap_or_default(),
            &arg(args, "value").unwrap_or_default(),
        ),
        "get_relevant_kv" => Ok(engine.get_kv(session.as_deref().unwrap_or("default"))),
        "bulk_store_summaries" => engine.store_summary(
            &arg(args, "chunk_id").unwrap_or_default(),
            &arg(args, "summary").unwrap_or_default(),
        ),
        "register_dynamic_symbols" => engine.register_dynamic(&symbol, "function", &path),
        "remove_dynamic_symbols" => {
            engine.store_dynamic_remove(&symbol);
            Ok(Value::object(vec![("removed", Value::string(symbol))]))
        }
        "detect_modality" => Ok(Value::object(vec![
            ("path", Value::string(&path)),
            ("modality", Value::string(modality_of(&path))),
        ])),
        "get_supported_formats" => Ok(Value::object(vec![(
            "formats",
            Value::object(vec![
                (
                    "text",
                    Value::string("txt md markdown rst adoc log csv tsv"),
                ),
                (
                    "code",
                    Value::string("py js ts java go rs c cpp rb php swift cs html css"),
                ),
                (
                    "image",
                    Value::string("png jpg jpeg gif webp bmp tiff ico header metadata"),
                ),
                (
                    "audio",
                    Value::string(
                        "wav duration and 30s segments; other types are one metadata line",
                    ),
                ),
                ("video", Value::string("container brand and byte length")),
                (
                    "pdf",
                    Value::string("uncompressed and FlateDecode page text"),
                ),
            ]),
        )])),
        "llm_embed" => Ok(Value::object(vec![
            ("embedding", Value::string("caller-supplied")),
            (
                "note",
                Value::string("2.0 does not bundle a model; pass vectors to bulk_store_embeddings"),
            ),
        ])),
        _ => Ok(Value::object(vec![
            ("ok", Value::Bool(true)),
            ("tool", Value::string(name)),
            ("note", Value::string("accepted")),
        ])),
    }
}

fn doctor_body() -> String {
    let response = crate::doctor::report(None, false);
    String::from_utf8_lossy(&response.stdout).trim().to_string()
}

fn modality_of(path: &str) -> String {
    let ext = path.rsplit('.').next().unwrap_or("");
    if matches!(ext, "png" | "jpg" | "jpeg" | "gif" | "webp") {
        "image".to_string()
    } else if matches!(ext, "wav" | "mp3" | "flac") {
        "audio".to_string()
    } else if matches!(ext, "mp4" | "mov" | "mkv") {
        "video".to_string()
    } else if ext == "pdf" {
        "pdf".to_string()
    } else if matches!(
        ext,
        "txt" | "md" | "markdown" | "rst" | "adoc" | "log" | "csv" | "tsv"
    ) {
        "text".to_string()
    } else {
        "code".to_string()
    }
}

fn describe(name: &str) -> String {
    if let Some((_, replacement)) = REMOVED.iter().find(|(old, _)| *old == name) {
        return format!("Removed in 2.0. Use {replacement}.");
    }
    format!("stele-context {name}")
}

fn tool_text(text: &str) -> Value {
    Value::object(vec![(
        "content",
        Value::Array(vec![Value::object(vec![
            ("type", Value::string("text")),
            ("text", Value::string(text)),
        ])]),
    )])
}

fn error_line(id: Value, code: i64, message: &str) -> String {
    json::to_string(&Value::object(vec![
        ("jsonrpc", Value::string("2.0")),
        ("id", id),
        (
            "error",
            Value::object(vec![
                ("code", Value::Int(code)),
                ("message", Value::string(message)),
            ]),
        ),
    ]))
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

fn string_field(value: &Value, key: &str) -> String {
    match field(value, key) {
        Some(Value::String(text)) => text.clone(),
        _ => String::new(),
    }
}

fn flag(value: &Value, key: &str) -> bool {
    match field(value, key) {
        Some(Value::Bool(bit)) => *bit,
        Some(Value::String(text)) => text == "true" || text == "1",
        _ => false,
    }
}

fn arg(value: &Value, key: &str) -> Option<String> {
    let text = string_field(value, key);
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}
