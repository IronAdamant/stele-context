//! Newline-delimited MCP. Speaks 2026-07-28 and the two previous handshakes.
//! Full mode lists the 18 old singleton names and runs them.

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
    serve_with(None)
}

pub fn serve_with(storage: Option<&std::path::Path>) -> Response {
    let stdin = io::stdin();
    let mut stdout = io::stdout();
    let mut engine = Engine::open(storage).ok();
    let agent = format!("stele-context-mcp-{}", std::process::id());
    if let Some(engine) = engine.as_mut() {
        engine.remember_server_agent(&agent);
    }
    let mut registered = false;
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
            "tools/call" => {
                if !registered {
                    if let Some(engine) = engine.as_mut() {
                        engine.register_server_agent();
                    }
                    registered = true;
                }
                tools_call(engine.as_mut(), &params)
            }
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
    // Lite stays the 2.0 `{type:object}` schema. The D13 tools/list gate
    // measures this reply, and a property-level schema pushes it over the
    // base interval. Standard and full keep the property lists. HTTP
    // GET /tools still builds the full lite schema via `tools_list`.
    let mode = std::env::var("STELE_MCP_MODE").unwrap_or_else(|_| "lite".to_string());
    match mode.as_str() {
        "full" => include_str!("tools_full.json"),
        "standard" => include_str!("tools_standard.json"),
        _ => include_str!("tools_lite.json"),
    }
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
            let schema = input_schema(name);
            Value::object(vec![
                ("name", Value::string(name)),
                ("description", Value::string(describe(name))),
                ("inputSchema", schema.clone()),
                ("parameters", schema),
            ])
        })
        .collect();
    Value::object(vec![("tools", Value::Array(tools))])
}

fn attach_hint(name: &str, value: &mut Value) {
    let Value::Object(pairs) = value else {
        return;
    };
    if pairs.iter().any(|(key, _)| key == "hint") {
        return;
    }
    let err = pairs.iter().find_map(|(key, item)| {
        if key == "error" {
            Some(json::to_string(item).to_lowercase())
        } else {
            None
        }
    });
    let hint = if let Some(err) = err.as_deref() {
        healing_hint(name, err)
    } else if name == "impact_radius"
        && pairs
            .iter()
            .any(|(key, item)| key == "affected_chunks" && matches!(item, Value::Int(0)))
    {
        healing_hint(name, "0 affected")
    } else if name == "coupling"
        && pairs.iter().any(|(key, item)| {
            (key == "coupled_files" || key == "files")
                && matches!(item, Value::Array(rows) if rows.is_empty())
                || (key == "count" && matches!(item, Value::Int(0)))
        })
    {
        healing_hint(name, "0 affected")
    } else {
        None
    };
    if let Some(hint) = hint {
        pairs.push(("hint".to_string(), Value::string(hint)));
    }
}

fn healing_hint(name: &str, err: &str) -> Option<&'static str> {
    if err.contains("database is locked")
        || err.contains("busy")
        || err.contains("unable to open database file")
    {
        return Some("SQLite is under load. Run detect_changes or wait a moment and retry.");
    }
    if name == "impact_radius" || name == "coupling" {
        if err.contains("0 affected")
            || err.contains("no seed")
            || err.contains("provide chunk_id")
            || err.contains("no definitions")
            || err.contains("empty")
        {
            return Some(
                "Impact/coupling found no useful seeds or edges. Pass document_path, chunk_id, or symbol; for base classes with high fan-in try find_references on exported symbols and impact_radius(direction='dependents', summary_mode=true).",
            );
        }
    }
    if name == "search" && err.contains('0') && !err.contains("hnsw") {
        return Some(
            "Tier-1 semantic search returned no strong matches. Results may fall back to BM25 keyword ranking. Consider adding Tier-2 summaries for this topic.",
        );
    }
    None
}

fn tools_call(engine: Option<&mut Engine>, params: &Value) -> Value {
    let name = string_field(params, "name");
    let Some(engine) = engine else {
        return tool_text(
            "{\"ok\":false,\"error\":{\"code\":\"storage\",\"message\":\"store did not open\"}}",
        );
    };
    let args = field(params, "arguments")
        .cloned()
        .unwrap_or(Value::object(vec![]));
    let (text, is_error) = match dispatch(engine, &name, &args) {
        Ok(mut value) => {
            attach_hint(&name, &mut value);
            (json::to_string(&value), false)
        }
        Err(message) => {
            engine.note_failure();
            let mut value = if name == "llm_embed" || name == "agent_grep" {
                Value::object(vec![("error", Value::string(message))])
            } else {
                Value::object(vec![
                    ("ok", Value::Bool(false)),
                    ("error", Value::string(message)),
                ])
            };
            attach_hint(&name, &mut value);
            (json::to_string(&value), true)
        }
    };
    engine.note_operation();
    let mut result = tool_text(&text);
    if is_error {
        if let Value::Object(pairs) = &mut result {
            pairs.push(("isError".to_string(), Value::Bool(true)));
        }
    }
    result
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
            let mut paths = string_list(args, "paths");
            if paths.is_empty() {
                paths = if path.is_empty() {
                    vec![".".to_string()]
                } else {
                    vec![path]
                };
            }
            let force = flag(args, "force_reindex") || flag(args, "force");
            let indexed = engine.index(&paths, force)?;
            let summaries = string_pairs(args, "summaries");
            if !summaries.is_empty() {
                engine.apply_path_summaries(&summaries)?;
            }
            Ok(indexed)
        }
        "remove" => engine.remove(&path),
        "detect_changes" => {
            if flag(args, "working_tree") {
                engine.index_working_tree();
            }
            let only = string_list(args, "document_paths");
            let scan = match field(args, "scan_new") {
                Some(Value::Bool(bit)) => *bit,
                _ => true,
            };
            let limit = int_arg(args, "limit").unwrap_or(200) as usize;
            engine.detect_with(
                scan,
                if only.is_empty() { None } else { Some(&only) },
                limit,
                &caller(engine, args),
            )
        }
        "search" => {
            if flag(args, "working_tree") {
                engine.index_working_tree();
            }
            let top = int_arg(args, "top_k").unwrap_or(10).max(1) as usize;
            let mut value = engine.search(
                &query,
                &arg(args, "search_mode").unwrap_or_else(|| "keyword".to_string()),
                top,
            );
            if let Some(prefix) = arg(args, "path_prefix") {
                filter_prefix(&mut value, &prefix);
            }
            if flag(args, "compact") {
                compact_results(&mut value);
            }
            Ok(value)
        }
        "search_text" => engine.text_search(
            &query,
            flag(args, "regex"),
            arg(args, "document_path").as_deref(),
            int_arg(args, "limit").unwrap_or(50).max(1) as usize,
            session.as_deref(),
            flag(args, "working_tree"),
        ),
        "agent_grep" => {
            if field(args, "path_prefix").is_some() {
                return Err(
                    "_SearchMixin.agent_grep() got an unexpected keyword argument 'path_prefix'"
                        .to_string(),
                );
            }
            engine.grep_full(
                &query,
                flag(args, "regex"),
                arg(args, "document_path").as_deref(),
                !flag(args, "no_classify") && flag_default(args, "classify", true),
                flag_default(args, "include_scope", true),
                arg(args, "group_by").as_deref().unwrap_or("file"),
                int_arg(args, "max_tokens").unwrap_or(4000),
                flag_default(args, "deduplicate", true),
                int_arg(args, "context_lines").unwrap_or(0),
                session.as_deref(),
                flag(args, "working_tree"),
            )
        }
        "query" => {
            if flag(args, "working_tree") || session.is_some() {
                engine.index_working_tree();
            }
            engine.query(&query)
        }
        "find_definition" => Ok(engine.find_definition(&symbol)),
        "find_references" => Ok(engine.find_references(&symbol)),
        "impact_radius" => Ok(engine.impact_radius(
            &symbol,
            arg(args, "document_path").as_deref(),
            arg(args, "chunk_id").as_deref(),
            int_arg(args, "depth").unwrap_or(2).max(0) as usize,
            flag(args, "summary_mode"),
        )),
        "coupling" => Ok(engine.coupling_mode(
            &path,
            &arg(args, "mode").unwrap_or_else(|| "edges".to_string()),
            float_arg(args, "significance_threshold").unwrap_or(0.0),
        )),
        "get_context" => {
            let mut many = string_list(args, "document_paths");
            if many.is_empty() {
                many = string_list(args, "paths");
            }
            if many.is_empty() {
                engine.get_context(&path, session.as_deref())
            } else {
                engine.context_many(&many, session.as_deref())
            }
        }
        "doctor" => Ok(engine.doctor_snapshot()),
        "map" => {
            let prefix = arg(args, "path_prefix");
            Ok(engine.map_with(prefix.as_deref(), None, None))
        }
        "enrichment_plan" => Ok(engine.enrichment_plan_with(
            int_arg(args, "top_n").unwrap_or(15).max(0) as usize,
            int_arg(args, "min_tokens").unwrap_or(20),
        )),
        "history" => engine.history(),
        "stats" => Ok(engine.stats()),
        "project_brief" => Ok(engine.project_brief()),
        "annotations" => {
            let action = arg(args, "action").unwrap_or_else(|| "get".to_string());
            let target = arg(args, "target").unwrap_or(path);
            let body = arg(args, "content")
                .or_else(|| arg(args, "body"))
                .or_else(|| arg(args, "query"))
                .unwrap_or_default();
            let tags = string_list(args, "tags");
            let target_type = arg(args, "target_type").unwrap_or_default();
            let id = arg(args, "annotation_id").unwrap_or_else(|| target.clone());
            if action == "delete" || action == "update" {
                engine.annotations_tagged(&action, &id, &body, &tags, &target_type)
            } else if action == "search" {
                engine.annotations_tagged("search", &body, "", &tags, &target_type)
            } else {
                engine.annotations_tagged(&action, &target, &body, &tags, &target_type)
            }
        }
        "document_lock" => engine.document_lock(
            &arg(args, "action").unwrap_or_else(|| "status".to_string()),
            &path,
            &arg(args, "agent_id").unwrap_or_else(|| "default".to_string()),
            flag(args, "force"),
        ),
        "get_search_history" => Ok(engine.search_history(session.as_deref().unwrap_or("default"))),
        "get_session_read_files" => Ok(engine.read_files(session.as_deref().unwrap_or("default"))),
        "get_relevant_kv" => Ok(engine.relevant_kv(
            session.as_deref().unwrap_or("default"),
            &arg(args, "query").unwrap_or_default(),
            int_arg(args, "top_k").unwrap_or(10).max(1) as usize,
        )),
        "save_kv_state" => {
            let session_id = session.unwrap_or_else(|| "default".to_string());
            let kv = field(args, "kv_data")
                .cloned()
                .unwrap_or(Value::object(vec![]));
            let only = string_list(args, "chunk_ids");
            engine.save_kv_state(&session_id, &kv, &only, &caller(engine, args))
        }
        "store_semantic_summary" => {
            let id = arg(args, "chunk_id").unwrap_or_default();
            let summary = arg(args, "summary").unwrap_or_default();
            match engine.store_summary(&id, &summary) {
                Ok(value) => Ok(value),
                Err(_) => Ok(Value::object(vec![
                    ("stored", Value::Bool(false)),
                    ("error", Value::string("chunk not found")),
                ])),
            }
        }
        "bulk_store_summaries" => {
            let mut pairs = string_pairs(args, "summaries");
            if pairs.is_empty() {
                let id = arg(args, "chunk_id").unwrap_or_default();
                let summary = arg(args, "summary").unwrap_or_default();
                if !id.is_empty() {
                    pairs.push((id, summary));
                }
            }
            engine.bulk_summaries(&pairs)
        }
        "store_embedding" => {
            let id = arg(args, "chunk_id").unwrap_or_default();
            let vector = float_list(args, "vector")
                .or(float_list(args, "embedding"))
                .unwrap_or_default();
            let stored = engine
                .bulk_embeddings(&[(id.clone(), vector)])
                .map(|value| crate::store::int_field(&value, "stored") > 0)
                .unwrap_or(false);
            if stored {
                Ok(Value::object(vec![
                    ("stored", Value::Bool(true)),
                    ("chunk_id", Value::string(id)),
                ]))
            } else {
                Ok(Value::object(vec![
                    ("stored", Value::Bool(false)),
                    ("error", Value::string("chunk not found")),
                ]))
            }
        }
        "bulk_store_embeddings" => {
            let mut pairs = float_pairs(args, "embeddings");
            if pairs.is_empty() {
                let id = arg(args, "chunk_id").unwrap_or_default();
                let vector = float_list(args, "vector")
                    .or(float_list(args, "embedding"))
                    .unwrap_or_default();
                pairs.push((id, vector));
            }
            engine.bulk_embeddings(&pairs)
        }
        "store_chunk_agent_notes" => {
            let id = arg(args, "chunk_id").unwrap_or_default();
            let notes = arg(args, "notes").unwrap_or_else(|| arg(args, "body").unwrap_or_default());
            let stored = engine
                .bulk_notes(&[(id.clone(), notes)])
                .map(|value| crate::store::int_field(&value, "stored") > 0)
                .unwrap_or(false);
            Ok(Value::object(vec![
                ("stored", Value::Bool(stored)),
                ("chunk_id", Value::string(id)),
            ]))
        }
        "bulk_store_chunk_agent_notes" => {
            let mut pairs = string_pairs(args, "notes");
            if pairs.is_empty() {
                let id = arg(args, "chunk_id").unwrap_or_default();
                let notes =
                    arg(args, "notes").unwrap_or_else(|| arg(args, "body").unwrap_or_default());
                if !id.is_empty() {
                    pairs.push((id, notes));
                }
            }
            engine.bulk_notes(&pairs)
        }
        "register_dynamic_symbols" => {
            let rows = symbol_rows(args);
            let agent = arg(args, "agent_id").unwrap_or_else(|| "default".to_string());
            if rows.is_empty() {
                return engine.register_dynamic(&symbol, "function", &path);
            }
            let mut stored = 0i64;
            let mut errors = Vec::new();
            for (name, kind, path, line) in rows {
                if name.is_empty() {
                    errors.push(Value::string("missing name"));
                    continue;
                }
                engine.register_dynamic(&name, &kind, &path)?;
                if let Some(sym) = engine.store_dynamic_last() {
                    sym.agent = agent.clone();
                    sym.chunk_id = format!("runtime:{agent}:{name}");
                    sym.line = line as u32;
                }
                stored += 1;
            }
            Ok(Value::object(vec![
                ("stored", Value::Int(stored)),
                ("errors", Value::Array(errors)),
            ]))
        }
        "remove_dynamic_symbols" => {
            let agent = arg(args, "agent_id").unwrap_or_default();
            if symbol.is_empty() && !agent.is_empty() {
                let removed = engine.remove_agent_dynamic(&agent);
                Ok(Value::object(vec![("removed", Value::Int(removed))]))
            } else {
                engine.store_dynamic_remove(&symbol);
                Ok(Value::object(vec![("removed", Value::Int(1))]))
            }
        }
        "detect_modality" => Ok(Value::object(vec![
            ("path", Value::string(&path)),
            ("modality", Value::string(modality_of(&path))),
        ])),
        "get_supported_formats" => Ok(Value::object(vec![(
            "formats",
            Value::object(vec![
                ("text", ext_list(TEXT_EXTS)),
                ("code", ext_list(CODE_EXTS)),
                ("image", ext_list(IMAGE_EXTS)),
                ("pdf", ext_list(PDF_EXTS)),
                ("audio", ext_list(AUDIO_EXTS)),
                ("video", ext_list(VIDEO_EXTS)),
            ]),
        )])),
        "llm_embed" => engine.llm_embed(
            &arg(args, "text").unwrap_or_default(),
            &arg(args, "chunk_id").unwrap_or_default(),
            float_list(args, "fingerprint_values"),
        ),
        "prune_history" => engine.prune_history(
            int_arg(args, "max_age_seconds"),
            int_arg(args, "max_entries"),
        ),
        "rollback" => engine.rollback(
            session.as_deref().unwrap_or("default"),
            int_arg(args, "target_turn").unwrap_or(0),
        ),
        "prune_chunks" => engine.prune_session_chunks(
            session.as_deref().unwrap_or("default"),
            int_arg(args, "max_tokens").unwrap_or(0),
        ),
        "list_sessions" => Ok(engine.list_sessions(arg(args, "agent_id").as_deref())),
        "rebuild_symbols" => engine.rebuild_symbols(),
        "stale_chunks" => Ok(engine.stale_chunks(
            float_arg(args, "threshold").unwrap_or(0.3),
            int_arg(args, "max_age_seconds"),
        )),
        "get_dynamic_symbols" => Ok(engine.dynamic_symbols(arg(args, "agent_id").as_deref())),
        "list_agents" => Ok(engine.list_agents(flag_default(args, "active_only", true))),
        "get_notifications" => Ok(engine.notifications(
            int_arg(args, "since"),
            arg(args, "exclude_self")
                .or_else(|| arg(args, "exclude_agent"))
                .as_deref(),
            int_arg(args, "limit").unwrap_or(100),
        )),
        "environment_check" => Ok(engine.environment_check()),
        "clean_bytecache" => Ok(engine.clean_bytecache()),
        "get_chunk_history" => Ok(engine.chunk_history(
            arg(args, "chunk_id").as_deref(),
            arg(args, "document_path").as_deref(),
            int_arg(args, "limit").unwrap_or(50),
        )),
        "batch" => engine.batch_ops(args),
        "annotate" => engine.annotations_tagged(
            "create",
            &arg(args, "target").unwrap_or(path),
            &arg(args, "content")
                .or_else(|| arg(args, "body"))
                .unwrap_or_default(),
            &string_list(args, "tags"),
            &arg(args, "target_type").unwrap_or_else(|| "document".to_string()),
        ),
        "get_annotations" => {
            let wrapped = engine.annotations_tagged(
                "get",
                &arg(args, "target").unwrap_or(path),
                "",
                &string_list(args, "tags"),
                &arg(args, "target_type").unwrap_or_default(),
            )?;
            Ok(match field(&wrapped, "annotations") {
                Some(Value::Array(rows)) => Value::Array(rows.clone()),
                _ => wrapped,
            })
        }
        "delete_annotation" => {
            let id = arg(args, "annotation_id")
                .or_else(|| arg(args, "id"))
                .unwrap_or(path);
            engine.annotations("delete", &id, "")
        }
        "update_annotation" => {
            let id = arg(args, "annotation_id")
                .or_else(|| arg(args, "id"))
                .unwrap_or(path);
            engine.annotations_tagged(
                "update",
                &id,
                &arg(args, "content").unwrap_or_default(),
                &string_list(args, "tags"),
                "",
            )
        }
        "search_annotations" => {
            let wrapped = engine.annotations("search", &arg(args, "query").unwrap_or(query), "")?;
            Ok(match field(&wrapped, "annotations") {
                Some(Value::Array(rows)) => Value::Array(rows.clone()),
                _ => wrapped,
            })
        }
        "bulk_annotate" => {
            let items = match field(args, "annotations").or_else(|| field(args, "items")) {
                Some(Value::Array(rows)) => rows.clone(),
                _ => Vec::new(),
            };
            let mut created = Vec::new();
            let mut errors = Vec::new();
            for item in items {
                let target = string_field(&item, "target");
                let content = string_field(&item, "content");
                let tags = string_list(&item, "tags");
                match engine.annotations_tagged("create", &target, &content, &tags, "") {
                    Ok(row) => created.push(row),
                    Err(message) => errors.push(Value::string(message)),
                }
            }
            Ok(Value::object(vec![
                ("created", Value::Array(created)),
                ("errors", Value::Array(errors)),
            ]))
        }
        "acquire_document_lock" => {
            engine.document_lock("acquire", &path, &caller(engine, args), flag(args, "force"))
        }
        "refresh_document_lock" => engine.document_lock(
            "refresh",
            &path,
            &arg(args, "agent_id").unwrap_or_else(|| "default".to_string()),
            false,
        ),
        "release_document_lock" => engine.document_lock(
            "release",
            &path,
            &arg(args, "agent_id").unwrap_or_else(|| "default".to_string()),
            false,
        ),
        "get_document_lock_status" => engine.document_lock("status", &path, "default", false),
        "release_agent_locks" => engine.document_lock(
            "release_all",
            "",
            &arg(args, "agent_id").unwrap_or_else(|| "default".to_string()),
            false,
        ),
        "get_conflicts" => {
            let wrapped = engine.document_lock("conflicts", &path, "default", false)?;
            Ok(match field(&wrapped, "conflicts") {
                Some(Value::Array(rows)) => Value::Array(rows.clone()),
                _ => wrapped,
            })
        }
        "reap_expired_locks" => engine.document_lock("reap", "", "default", false),
        _ => Err(format!("unknown tool '{name}'")),
    }
}

fn caller(engine: &Engine, args: &Value) -> String {
    match arg(args, "agent_id") {
        Some(id) if !id.is_empty() => id,
        _ if !engine.server_agent().is_empty() => engine.server_agent().to_string(),
        _ => "default".to_string(),
    }
}

const TEXT_EXTS: &[&str] = &[
    ".txt",
    ".md",
    ".markdown",
    ".rst",
    ".adoc",
    ".log",
    ".csv",
    ".tsv",
];
const CODE_EXTS: &[&str] = &[
    ".py", ".pyw", ".pyx", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".java", ".kt", ".kts",
    ".scala", ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx", ".cs", ".go", ".rs", ".rb",
    ".php", ".swift", ".sh", ".bash", ".zsh", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".sql", ".html", ".htm", ".css", ".scss", ".less",
];
const IMAGE_EXTS: &[&str] = &[
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".ico",
];
const PDF_EXTS: &[&str] = &[".pdf"];
const AUDIO_EXTS: &[&str] = &[".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".wma"];
const VIDEO_EXTS: &[&str] = &[".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"];

fn ext_list(exts: &[&str]) -> Value {
    Value::Array(exts.iter().copied().map(Value::string).collect())
}

fn modality_of(path: &str) -> String {
    let dot = match path.rfind('.') {
        Some(index) => &path[index..],
        None => "",
    };
    let ext = dot.to_ascii_lowercase();
    if CODE_EXTS.contains(&ext.as_str()) {
        "code".to_string()
    } else if IMAGE_EXTS.contains(&ext.as_str()) {
        "image".to_string()
    } else if PDF_EXTS.contains(&ext.as_str()) {
        "pdf".to_string()
    } else if AUDIO_EXTS.contains(&ext.as_str()) {
        "audio".to_string()
    } else if VIDEO_EXTS.contains(&ext.as_str()) {
        "video".to_string()
    } else if TEXT_EXTS.contains(&ext.as_str()) {
        "text".to_string()
    } else {
        "unknown".to_string()
    }
}

fn describe(name: &str) -> String {
    format!("stele-context {name}")
}

fn input_schema(name: &str) -> Value {
    let props: &[(&str, &str)] = match name {
        "index" => &[
            ("paths", "array"),
            ("path", "string"),
            ("force_reindex", "boolean"),
            ("summaries", "object"),
            ("expected_versions", "object"),
            ("agent_id", "string"),
        ],
        "search" | "query" => &[
            ("query", "string"),
            ("top_k", "integer"),
            ("search_mode", "string"),
            ("path_prefix", "string"),
            ("compact", "boolean"),
            ("max_result_tokens", "integer"),
            ("working_tree", "boolean"),
            ("session_id", "string"),
        ],
        "search_text" | "agent_grep" => &[
            ("pattern", "string"),
            ("regex", "boolean"),
            ("document_path", "string"),
            ("limit", "integer"),
            ("working_tree", "boolean"),
            ("session_id", "string"),
            ("group_by", "string"),
            ("max_tokens", "integer"),
            ("classify", "boolean"),
            ("include_scope", "boolean"),
            ("deduplicate", "boolean"),
            ("context_lines", "integer"),
        ],
        "get_context" => &[
            ("path", "string"),
            ("document_paths", "array"),
            ("session_id", "string"),
            ("include_trust", "boolean"),
            ("include_diff", "boolean"),
        ],
        "detect_changes" => &[
            ("document_paths", "array"),
            ("scan_new", "boolean"),
            ("limit", "integer"),
            ("session_id", "string"),
            ("working_tree", "boolean"),
        ],
        "impact_radius" => &[
            ("symbol", "string"),
            ("document_path", "string"),
            ("chunk_id", "string"),
            ("depth", "integer"),
            ("summary_mode", "boolean"),
            ("significance_threshold", "number"),
            ("direction", "string"),
        ],
        "coupling" => &[
            ("document_path", "string"),
            ("mode", "string"),
            ("significance_threshold", "number"),
        ],
        "annotations" | "document_lock" => &[
            ("action", "string"),
            ("path", "string"),
            ("document_path", "string"),
            ("target", "string"),
            ("body", "string"),
            ("content", "string"),
            ("agent_id", "string"),
            ("force", "boolean"),
            ("ttl", "number"),
        ],
        "bulk_store_summaries" | "store_semantic_summary" => &[
            ("summaries", "object"),
            ("chunk_id", "string"),
            ("summary", "string"),
        ],
        "bulk_store_embeddings" | "store_embedding" => &[
            ("embeddings", "object"),
            ("chunk_id", "string"),
            ("vector", "array"),
            ("embedding", "array"),
        ],
        "bulk_store_chunk_agent_notes" | "store_chunk_agent_notes" => &[
            ("notes", "object"),
            ("chunk_id", "string"),
            ("notes_text", "string"),
        ],
        "register_dynamic_symbols" => &[
            ("symbols", "array"),
            ("agent_id", "string"),
            ("symbol", "string"),
            ("path", "string"),
        ],
        "remove_dynamic_symbols" => &[("agent_id", "string"), ("symbol", "string")],
        "llm_embed" => &[
            ("text", "string"),
            ("chunk_id", "string"),
            ("fingerprint_values", "array"),
        ],
        "batch" => &[("operations", "array")],
        "prune_history" => &[("max_age_seconds", "integer"), ("max_entries", "integer")],
        "rollback" => &[("session_id", "string"), ("target_turn", "integer")],
        "prune_chunks" => &[("session_id", "string"), ("max_tokens", "integer")],
        "stale_chunks" => &[("threshold", "number"), ("max_age_seconds", "integer")],
        "get_chunk_history" => &[
            ("chunk_id", "string"),
            ("document_path", "string"),
            ("limit", "integer"),
        ],
        "get_notifications" => &[
            ("since", "number"),
            ("exclude_self", "string"),
            ("limit", "integer"),
        ],
        "list_agents" => &[("active_only", "boolean")],
        "list_sessions" | "get_dynamic_symbols" => &[("agent_id", "string")],
        _ => &[
            ("path", "string"),
            ("query", "string"),
            ("session_id", "string"),
        ],
    };
    let properties = props
        .iter()
        .map(|(name, kind)| {
            (
                (*name).to_string(),
                Value::object(vec![("type", Value::string(*kind))]),
            )
        })
        .collect();
    Value::object(vec![
        ("type", Value::string("object")),
        ("properties", Value::Object(properties)),
    ])
}

pub(crate) fn tools_document() -> Value {
    tools_list()
}

pub(crate) fn tool_known(name: &str) -> bool {
    let mode = std::env::var("STELE_MCP_MODE").unwrap_or_else(|_| "lite".to_string());
    if LITE.contains(&name) {
        return true;
    }
    if (mode == "standard" || mode == "full") && STANDARD_EXTRA.contains(&name) {
        return true;
    }
    mode == "full" && REMOVED.iter().any(|(old, _)| *old == name)
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

#[cfg(test)]
mod schema_dump {
    use super::*;

    #[test]
    fn write_static_schemas_when_asked() {
        if std::env::var("STELE_DUMP_TOOLS").is_err() {
            return;
        }
        for (mode, name) in [
            ("standard", "tools_standard.json"),
            ("full", "tools_full.json"),
        ] {
            // Lite `tools_lite.json` stays the compact `{type:object}` document.
            // Regenerating it from `tools_list` would miss the D13 tools/list gate.
            std::env::set_var("STELE_MCP_MODE", mode);
            let text = json::to_string(&tools_list());
            let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("src/mcp")
                .join(name);
            std::fs::write(path, text).unwrap();
        }
    }
}

fn arg(value: &Value, key: &str) -> Option<String> {
    match field(value, key) {
        Some(Value::String(text)) if !text.is_empty() => Some(text.clone()),
        Some(Value::Int(number)) => Some(number.to_string()),
        Some(Value::Float(number)) => Some(number.to_string()),
        _ => None,
    }
}

fn flag_default(value: &Value, key: &str, default: bool) -> bool {
    match field(value, key) {
        Some(_) => flag(value, key),
        None => default,
    }
}

fn int_arg(value: &Value, key: &str) -> Option<i64> {
    match field(value, key) {
        Some(Value::Int(number)) => Some(*number),
        Some(Value::Float(number)) => Some(*number as i64),
        Some(Value::String(text)) => text.parse().ok(),
        _ => None,
    }
}

fn float_arg(value: &Value, key: &str) -> Option<f64> {
    match field(value, key) {
        Some(Value::Float(number)) => Some(*number),
        Some(Value::Int(number)) => Some(*number as f64),
        Some(Value::String(text)) => text.parse().ok(),
        _ => None,
    }
}

fn string_list(value: &Value, key: &str) -> Vec<String> {
    match field(value, key) {
        Some(Value::Array(rows)) => rows
            .iter()
            .filter_map(|item| match item {
                Value::String(text) => Some(text.clone()),
                _ => None,
            })
            .collect(),
        Some(Value::String(text)) => vec![text.clone()],
        _ => Vec::new(),
    }
}

fn string_pairs(value: &Value, key: &str) -> Vec<(String, String)> {
    match field(value, key) {
        Some(Value::Object(pairs)) => pairs
            .iter()
            .filter_map(|(name, item)| match item {
                Value::String(text) => Some((name.clone(), text.clone())),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn float_list(value: &Value, key: &str) -> Option<Vec<f64>> {
    match field(value, key) {
        Some(Value::Array(rows)) => Some(
            rows.iter()
                .map(|item| match item {
                    Value::Float(number) => *number,
                    Value::Int(number) => *number as f64,
                    _ => 0.0,
                })
                .collect(),
        ),
        _ => None,
    }
}

fn float_pairs(value: &Value, key: &str) -> Vec<(String, Vec<f64>)> {
    match field(value, key) {
        Some(Value::Object(pairs)) => pairs
            .iter()
            .filter_map(|(name, item)| match item {
                Value::Array(rows) => Some((
                    name.clone(),
                    rows.iter()
                        .map(|item| match item {
                            Value::Float(number) => *number,
                            Value::Int(number) => *number as f64,
                            _ => 0.0,
                        })
                        .collect(),
                )),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn symbol_rows(value: &Value) -> Vec<(String, String, String, i64)> {
    match field(value, "symbols") {
        Some(Value::Array(rows)) => rows
            .iter()
            .map(|row| {
                (
                    string_field(row, "name"),
                    {
                        let kind = string_field(row, "kind");
                        if kind.is_empty() {
                            "function".to_string()
                        } else {
                            kind
                        }
                    },
                    string_field(row, "document_path"),
                    int_arg(row, "line_number").unwrap_or(0),
                )
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn filter_prefix(value: &mut Value, prefix: &str) {
    let Some(rows) = result_rows(value) else {
        return;
    };
    rows.retain(|row| crate::store::string_field(row, "document_path").starts_with(prefix));
}

fn compact_results(value: &mut Value) {
    let Some(rows) = result_rows(value) else {
        return;
    };
    for row in rows {
        let Value::Object(pairs) = row else {
            continue;
        };
        let content =
            pairs
                .iter()
                .find(|(key, _)| key == "content")
                .and_then(|(_, item)| match item {
                    Value::String(text) => Some(text.clone()),
                    _ => None,
                });
        let Some(content) = content else {
            continue;
        };
        let preview = if content.chars().count() > 400 {
            let mut text: String = content.chars().take(400).collect();
            text.push('\u{2026}');
            text
        } else {
            content.clone()
        };
        pairs.retain(|(key, _)| key != "content");
        pairs.push(("content_preview".to_string(), Value::string(preview)));
        pairs.push((
            "content_token_estimate".to_string(),
            Value::Int(crate::chunk::estimate_tokens(&content) as i64),
        ));
    }
}

fn result_rows(value: &mut Value) -> Option<&mut Vec<Value>> {
    match value {
        Value::Array(rows) => Some(rows),
        Value::Object(pairs) => pairs.iter_mut().find_map(|(key, item)| {
            if key == "results" {
                match item {
                    Value::Array(rows) => Some(rows),
                    _ => None,
                }
            } else {
                None
            }
        }),
        _ => None,
    }
}
