//! Newline-delimited MCP. Speaks 2026-07-28 and the two previous handshakes.
//! Full mode lists the 18 old singleton names and runs them.

use crate::contract::Response;
use crate::engine::Engine;
use crate::json::{self, Value};
use crate::VERSION;
use std::io::{self, BufRead, Write};

/// Generates tools_*.json (a test keeps them current).
#[cfg(test)]
mod catalog;
mod compact;
pub(crate) mod groups;
mod signature;

const HANDSHAKES: &[&str] = &["2026-07-28", "2025-11-25", "2025-06-18"];

/// The second line of the initialize instructions (2.4): when Stele beats
/// grep, when grep is the better tool, and how to ask from a worktree.
const USAGE: &str = "Stele beats grep for where a symbol is defined (find_definition), every use of it with a dead-code verdict (find_references) and what a change breaks (impact_radius). Use grep for text in strings or comments and for files outside the index. In a git worktree pass worktree_root=<its absolute path> to read tools.";

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
    // Tool groups: STELE_TOOLS / STELE_DISABLE from the operator, the
    // `agent-tools` block of the project's AGENTS.md from the agent. A bad
    // env value stops the server with one stderr line and exit code 2; a bad
    // AGENTS.md line is a warning in the initialize instructions.
    let project = std::env::current_dir()
        .map(|cwd| crate::root::detect_project_root(&cwd).unwrap_or(cwd))
        .unwrap_or_default();
    let mut surface = match groups::Surface::open(&project) {
        Ok(surface) => surface,
        Err(line) => {
            let _ = writeln!(io::stderr(), "{line}");
            return Response {
                code: 2,
                stdout: Vec::new(),
            };
        }
    };
    let catalogue = mode_tool_names();
    let mut filtered: Option<String> = None;
    let mut initialized = false;
    let stdin = io::stdin();
    let mut stdout = io::stdout();
    // The store opens on a background thread while the client does its
    // handshake: initialize and tools/list answer without touching the disk,
    // and the first tools/call waits for the store only if it is not ready
    // yet. The same thread then finds the host Python version that doctor
    // reports (file reads along PATH, slow on Windows; nothing is started).
    let mut engine: Option<Engine> = None;
    // 2.4: worktree overlays, and the worktree a server started inside a
    // linked git worktree answers for when that tree has no index of its
    // own (crate::engine::overlay::auto_worktree).
    let mut overlays = crate::engine::overlay::Overlays::new();
    let auto = std::env::current_dir()
        .ok()
        .and_then(|cwd| crate::engine::overlay::auto_worktree(&cwd, storage));
    let mut base: Option<Engine> = None;
    let mut base_opened = false;
    let (sender, opened) = std::sync::mpsc::channel::<Option<Engine>>();
    let (warm_sender, warmed) = std::sync::mpsc::channel::<Vec<u8>>();
    let owned = storage.map(std::path::Path::to_path_buf);
    let background = std::thread::Builder::new()
        .name("store-open".to_string())
        .spawn(move || {
            // First, while the client is still on initialize: page in the
            // tools/list document and a reply buffer that holds it, so the
            // first tools/list does not pay those page faults.
            let listed = cached_tools_json().as_bytes();
            let mut reply = Vec::with_capacity(listed.len() + 64);
            reply.extend_from_slice(listed);
            reply.clear();
            let _ = warm_sender.send(reply);
            let mut opened = Engine::open(owned.as_deref()).ok();
            // Idle between calls: the store is free for other processes.
            if let Some(engine) = opened.as_mut() {
                engine.release_store();
            }
            let _ = sender.send(opened);
            let _ = crate::pyver::host_version();
        })
        .is_ok();
    let agent = format!("stele-context-mcp-{}", std::process::id());
    let mut registered = false;
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let Ok(value) = json::parse(&line) else {
            send(&mut stdout, error_line(Value::Null, -32700, "parse error"));
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
        // One stat of AGENTS.md per request. When an agent edits the block
        // mid-session and the exposed tools change, the client hears
        // `notifications/tools/list_changed` before this response.
        if surface.refresh(&catalogue) {
            filtered = None;
            if initialized {
                send(
                    &mut stdout,
                    "{\"jsonrpc\":\"2.0\",\"method\":\"notifications/tools/list_changed\"}"
                        .to_string(),
                );
            }
        }
        let result = match method.as_str() {
            "initialize" => {
                initialized = true;
                let mut result = initialize(&params);
                if let Value::Object(pairs) = &mut result {
                    pairs.push((
                        "instructions".to_string(),
                        Value::string(format!("{}\n{USAGE}", surface.instructions(&catalogue))),
                    ));
                }
                result
            }
            "tools/list" => {
                let selection = surface.selection();
                let listed: &str = if selection.is_default() {
                    cached_tools_json()
                } else {
                    filtered.get_or_insert_with(|| filtered_tools_json(selection))
                };
                let mut reply = warmed.try_recv().unwrap_or_default();
                reply.clear();
                reply.extend_from_slice(b"{\"jsonrpc\":\"2.0\",\"id\":");
                reply.extend_from_slice(json::to_string(&id).as_bytes());
                reply.extend_from_slice(b",\"result\":");
                reply.extend_from_slice(listed.as_bytes());
                reply.extend_from_slice(
                    b"}
",
                );
                let _ = stdout.write_all(&reply);
                let _ = stdout.flush();
                continue;
            }
            "tools/call" => {
                if !registered {
                    engine = if background {
                        opened.recv().ok().flatten()
                    } else {
                        Engine::open(storage).ok()
                    };
                    if let Some(engine) = engine.as_mut() {
                        engine.remember_server_agent(&agent);
                        engine.register_server_agent();
                    }
                    registered = true;
                }
                // The store is held for one call at a time, so two agents'
                // servers (and the CLI) can share a project.
                let reacquired = engine.as_mut().map(Engine::reacquire_store);
                let name = string_field(&params, "name");
                let default_tree = auto
                    .as_ref()
                    .filter(|_| crate::engine::overlay::WORKTREE_TOOLS.contains(&name.as_str()));
                if let Some((main, _)) = default_tree {
                    if !base_opened {
                        base_opened = true;
                        base = Engine::open_from(main.clone(), None).ok();
                        if let Some(base) = base.as_mut() {
                            base.release_store();
                        }
                    }
                }
                let result = match reacquired {
                    Some(Err(message)) => tool_result(
                        &json::to_string(&Value::object(vec![("error", Value::string(message))])),
                        true,
                    ),
                    _ => match (default_tree, base.as_mut()) {
                        (Some((_, tree)), Some(main)) => {
                            let held = main.reacquire_store();
                            let result = match held {
                                Err(message) => tool_result(
                                    &json::to_string(&Value::object(vec![(
                                        "error",
                                        Value::string(message),
                                    )])),
                                    true,
                                ),
                                Ok(()) => tools_call_in(
                                    Some(main),
                                    &params,
                                    &mut overlays,
                                    Some(&tree.display().to_string()),
                                ),
                            };
                            main.release_store();
                            result
                        }
                        _ => tools_call_in(engine.as_mut(), &params, &mut overlays, None),
                    },
                };
                if let Some(engine) = engine.as_mut() {
                    engine.release_store();
                }
                result
            }
            "ping" => Value::object(vec![]),
            _ => {
                send(&mut stdout, error_line(id, -32601, "method not found"));
                continue;
            }
        };
        send(
            &mut stdout,
            json::to_string(&Value::object(vec![
                ("jsonrpc", Value::string("2.0")),
                ("id", id),
                ("result", result),
            ])),
        );
    }
    Response {
        code: 0,
        stdout: Vec::new(),
    }
}

/// One JSON-RPC message, newline-terminated, in a single write: the client
/// gets the whole line in one read instead of a head, a body and a tail.
fn send(stdout: &mut io::Stdout, mut line: String) {
    line.push('\n');
    let _ = stdout.write_all(line.as_bytes());
    let _ = stdout.flush();
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
            Value::object(vec![(
                "tools",
                Value::object(vec![("listChanged", Value::Bool(true))]),
            )]),
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

/// The mode's tools/list with the hidden tools left out.
fn filtered_tools_json(selection: &groups::Selection) -> String {
    let Ok(mut listed) = json::parse(cached_tools_json()) else {
        return cached_tools_json().to_string();
    };
    if let Value::Object(pairs) = &mut listed {
        for (key, value) in pairs.iter_mut() {
            if key != "tools" {
                continue;
            }
            if let Value::Array(tools) = value {
                tools.retain(|tool| selection.hidden(&string_field(tool, "name")).is_none());
            }
        }
    }
    json::to_string(&listed)
}

/// Every tool name in the current `STELE_MCP_MODE`.
fn mode_tool_names() -> Vec<&'static str> {
    let mode = std::env::var("STELE_MCP_MODE").unwrap_or_else(|_| "lite".to_string());
    mode_names(&mode)
}

/// The tool names of one `STELE_MCP_MODE`, in tools/list order.
pub(crate) fn mode_names(mode: &str) -> Vec<&'static str> {
    let mut names: Vec<&'static str> = LITE.to_vec();
    if mode == "standard" || mode == "full" {
        names.extend_from_slice(STANDARD_EXTRA);
    }
    if mode == "full" {
        names.extend(REMOVED.iter().map(|(old, _)| *old));
    }
    names
}

/// The full catalogue, every mode together.
#[cfg(test)]
pub(crate) fn all_tool_names() -> Vec<&'static str> {
    mode_names("full")
}

/// The mode's tools/list result. Every mode lists each tool with a short
/// description and its arguments (generated by `catalog`).
fn cached_tools_json() -> &'static str {
    let mode = std::env::var("STELE_MCP_MODE").unwrap_or_else(|_| "lite".to_string());
    match mode.as_str() {
        "full" => include_str!("tools_full.json"),
        "standard" => include_str!("tools_standard.json"),
        _ => include_str!("tools_lite.json"),
    }
}

fn attach_hint(name: &str, value: &mut Value) {
    let Some(hint) = result_hint(name, value) else {
        return;
    };
    if let Value::Object(pairs) = value {
        pairs.push(("hint".to_string(), Value::string(hint)));
    }
}

/// 1.5.2 `hint_for_tool_result`: a hint for a dict result that carries an
/// error or found nothing. `None` when the result already has a hint.
pub(crate) fn result_hint(name: &str, value: &Value) -> Option<&'static str> {
    let Value::Object(pairs) = value else {
        return None;
    };
    if pairs
        .iter()
        .any(|(key, item)| key == "hint" && truthy(item))
    {
        return None;
    }
    let err = pairs.iter().find_map(|(key, item)| {
        if key == "error" && truthy(item) {
            Some(match item {
                Value::String(text) => text.to_lowercase(),
                other => json::to_string(other).to_lowercase(),
            })
        } else {
            None
        }
    });
    if let Some(err) = err.as_deref() {
        return healing_hint(name, err);
    }
    if name == "impact_radius"
        && pairs
            .iter()
            .any(|(key, item)| key == "affected_chunks" && matches!(item, Value::Int(0)))
    {
        return healing_hint(name, "0 affected");
    }
    if name == "coupling"
        && pairs.iter().any(|(key, item)| {
            (key == "coupled_files" || key == "files")
                && matches!(item, Value::Array(rows) if rows.is_empty())
                || (key == "count" && matches!(item, Value::Int(0)))
        })
    {
        return healing_hint(name, "0 affected");
    }
    None
}

fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(bit) => *bit,
        Value::Int(number) => *number != 0,
        Value::Float(number) => *number != 0.0,
        Value::String(text) => !text.is_empty(),
        Value::Array(rows) => !rows.is_empty(),
        Value::Object(pairs) => !pairs.is_empty(),
    }
}

/// `TypeError` text when 1.5.2's callable would reject these arguments.
pub(crate) fn bind_args(name: &str, args: &Value, inject: bool) -> Result<(), String> {
    let keys: Vec<&str> = match args {
        Value::Object(pairs) => pairs.iter().map(|(key, _)| key.as_str()).collect(),
        _ => Vec::new(),
    };
    // Accepted difference: `store_embedding` also takes `embedding`.
    let alias = if name == "store_embedding" && keys.contains(&"embedding") {
        Some("embedding")
    } else {
        None
    };
    signature::bind(name, &keys, alias, inject)
}

pub(crate) fn healing_hint(name: &str, err: &str) -> Option<&'static str> {
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

/// One tools/call. A read tool with `worktree_root` (or the server's
/// automatic worktree, `default_tree`) runs on that tree's overlay.
fn tools_call_in(
    engine: Option<&mut Engine>,
    params: &Value,
    overlays: &mut crate::engine::overlay::Overlays,
    default_tree: Option<&str>,
) -> Value {
    let name = string_field(params, "name");
    let Some(engine) = engine else {
        return tool_text(
            "{\"ok\":false,\"error\":{\"code\":\"storage\",\"message\":\"store did not open\"}}",
        );
    };
    let args = field(params, "arguments")
        .cloned()
        .unwrap_or(Value::object(vec![]));
    if !tool_known(&name) {
        // 1.5.2: the name is not in this mode's tool map. Nothing runs and
        // the operation log does not see the call.
        let mut value = Value::object(vec![("error", Value::string(unknown_tool(&name)))]);
        attach_hint(&name, &mut value);
        return tool_result(&json::to_string(&value), true);
    }
    let selection = groups::active();
    if let Some(why) = selection.hidden(&name) {
        // The convention's one line, as the text of an isError result.
        return tool_result(&selection.refusal(&name, &why), true);
    }
    let tree = crate::engine::overlay::requested(&args).or_else(|| {
        default_tree
            .filter(|_| crate::engine::overlay::WORKTREE_TOOLS.contains(&name.as_str()))
            .map(str::to_string)
    });
    let called = match tree {
        Some(tree) => bind_args(&name, &args, true).and_then(|()| {
            overlays
                .run(engine, &tree, |tree_engine| {
                    dispatch(tree_engine, &name, &args)
                })
                .and_then(|(value, ran)| {
                    value.map(|value| crate::engine::overlay::annotate(value, &ran))
                })
        }),
        None => call_bound(engine, &name, &args, true),
    };
    let (text, is_error) = match called {
        Ok(mut value) => {
            attach_hint(&name, &mut value);
            if name == "doctor" {
                if let Value::Object(pairs) = &mut value {
                    for (key, line) in groups::doctor_fields(&mode_tool_names()) {
                        pairs.push((key, Value::string(line)));
                    }
                }
            }
            let errored = only_error(&value);
            (json::to_string(&value), errored)
        }
        Err(message) => {
            // 1.5.2 catches the exception, returns `{"error": str(exc)}` plus
            // a self-healing hint, and marks the result an error.
            engine.note_failure();
            let mut pairs = vec![("error".to_string(), Value::string(&message))];
            if let Some(hint) = healing_hint(&name, &message.to_lowercase()) {
                pairs.push(("hint".to_string(), Value::string(hint)));
            }
            (json::to_string(&Value::Object(pairs)), true)
        }
    };
    engine.note_operation();
    tool_result(&text, is_error)
}

/// Bind the arguments the way 1.5.2's callable would, then run the tool.
/// `inject` is true where the server fills in its own `agent_id`.
pub(crate) fn call_bound(
    engine: &mut Engine,
    name: &str,
    args: &Value,
    inject: bool,
) -> Result<Value, String> {
    bind_args(name, args, inject)?;
    dispatch(engine, name, args)
}

/// 1.5.2 marks a dict result an error when its only keys are `error` and
/// `hint`.
fn only_error(value: &Value) -> bool {
    let Value::Object(pairs) = value else {
        return false;
    };
    pairs.iter().any(|(key, _)| key == "error")
        && pairs.iter().all(|(key, _)| key == "error" || key == "hint")
}

fn tool_result(text: &str, is_error: bool) -> Value {
    let mut result = tool_text(text);
    if is_error {
        if let Value::Object(pairs) = &mut result {
            pairs.push(("isError".to_string(), Value::Bool(true)));
        }
    }
    result
}

/// "Unknown tool: X." plus the way forward: the mode that has it, a near
/// name, or where the list is.
fn unknown_tool(name: &str) -> String {
    let mode = std::env::var("STELE_MCP_MODE").unwrap_or_else(|_| "lite".to_string());
    let mut current: Vec<&str> = LITE.to_vec();
    if mode == "standard" || mode == "full" {
        current.extend_from_slice(STANDARD_EXTRA);
    }
    if mode == "full" {
        current.extend(REMOVED.iter().map(|(old, _)| *old));
    }
    if STANDARD_EXTRA.contains(&name) {
        return format!(
            "Unknown tool: {name}. It is available with STELE_MCP_MODE=standard or full (this server runs {mode})."
        );
    }
    if let Some((_, replacement)) = REMOVED.iter().find(|(old, _)| *old == name) {
        return format!(
            "Unknown tool: {name}. Use {replacement}, or set STELE_MCP_MODE=full for the old name."
        );
    }
    match signature::suggestion(&current, name) {
        Some(near) => format!("Unknown tool: {name}. Did you mean '{near}'?"),
        None => format!(
            "Unknown tool: {name}. tools/list shows the {} tools in {mode} mode.",
            current.len()
        ),
    }
}

pub(crate) fn lite_tools() -> &'static [&'static str] {
    LITE
}

/// Run a tool for MCP or HTTP and apply the 2.2 compact defaults.
pub(crate) fn dispatch(engine: &mut Engine, name: &str, args: &Value) -> Result<Value, String> {
    run_tool(engine, name, args).map(|value| compact::apply(engine, name, args, value))
}

fn run_tool(engine: &mut Engine, name: &str, args: &Value) -> Result<Value, String> {
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
            let prefix = match field(args, "path_prefix") {
                Some(Value::String(text)) => Some(text.clone()),
                _ => None,
            };
            Ok(engine.search_ranked(
                &query,
                &arg(args, "search_mode").unwrap_or_else(|| "keyword".to_string()),
                top,
                prefix.as_deref(),
                // 2.2: previews by default; compact=false for full chunk text.
                flag_default(args, "compact", true),
                int_arg(args, "max_result_tokens"),
                flag(args, "return_response_meta"),
                // 2.4: the defining chunk first, and no edge blocks.
                flag_default(args, "definitions_first", true),
                flag(args, "include_edges"),
            ))
        }
        "search_text" => engine.text_search(
            &query,
            flag(args, "regex"),
            arg(args, "document_path").as_deref(),
            // 2.2: 20 chunks by default (1.5.2: 50).
            int_arg(args, "limit").unwrap_or(20).max(1) as usize,
            session.as_deref(),
            flag(args, "working_tree"),
        ),
        "agent_grep" => engine.grep_full(
            &query,
            flag(args, "regex"),
            arg(args, "document_path").as_deref(),
            !flag(args, "no_classify") && flag_default(args, "classify", true),
            flag_default(args, "include_scope", true),
            arg(args, "group_by").as_deref().unwrap_or("file"),
            // 2.2: a 1000-token budget by default (1.5.2: 4000).
            int_arg(args, "max_tokens").unwrap_or(1000),
            flag_default(args, "deduplicate", true),
            int_arg(args, "context_lines").unwrap_or(0),
            session.as_deref(),
            flag(args, "working_tree"),
        ),
        "query" => {
            let prefix = match field(args, "path_prefix") {
                Some(Value::String(text)) => Some(text.clone()),
                _ => None,
            };
            engine.query_ranked(
                &query,
                int_arg(args, "top_k").unwrap_or(10).max(1) as usize,
                prefix.as_deref(),
                flag(args, "working_tree"),
                session.as_deref(),
                &arg(args, "search_mode").unwrap_or_else(|| "keyword".to_string()),
                flag_default(args, "compact", true),
                int_arg(args, "max_result_tokens"),
                flag_default(args, "definitions_first", true),
                flag(args, "include_edges"),
            )
        }
        "find_definition" => Ok(engine.find_definition(&symbol)),
        "find_references" => Ok(engine.find_references_in(&symbol, flag(args, "all_languages"))),
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
            // An empty string is a prefix. `arg` drops empty strings, and
            // 1.5.2 then resolves `""` to `.`, which matches no document.
            let prefix = match field(args, "path_prefix") {
                Some(Value::String(text)) => Some(text.clone()),
                _ => None,
            };
            // 2.2: the 50 largest documents by default; max_documents=0 lists
            // every one (as 1.5.2's no cap).
            let max = match int_arg(args, "max_documents") {
                Some(number) if number > 0 => number as usize,
                Some(_) => usize::MAX,
                None => 50,
            };
            let ann = int_arg(args, "max_annotation_chars").map(|number| number.max(0) as usize);
            Ok(engine.map_with(prefix.as_deref(), Some(max), ann))
        }
        "enrichment_plan" => Ok(engine.enrichment_plan_with(
            int_arg(args, "top_n").unwrap_or(15).max(0) as usize,
            int_arg(args, "min_tokens").unwrap_or(20),
        )),
        "history" => engine.history_with(
            int_arg(args, "limit").unwrap_or(20).max(0) as usize,
            arg(args, "document_path").as_deref(),
        ),
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
            // 1.5.2 `store_dynamic_symbols`: each dict needs a name and a role
            // of definition or reference; kind defaults to function.
            let agent = caller(engine, args);
            let rows = match field(args, "symbols") {
                Some(Value::Array(rows)) => rows.clone(),
                _ => Vec::new(),
            };
            let mut stored = 0i64;
            let mut errors = Vec::new();
            for row in rows {
                let name = match field(&row, "name") {
                    Some(Value::String(text)) if !text.is_empty() => text.clone(),
                    _ => {
                        errors.push(Value::string(format!(
                            "missing name in symbol: {}",
                            crate::json::py_repr(&row)
                        )));
                        continue;
                    }
                };
                let role = match field(&row, "role") {
                    None => "definition".to_string(),
                    Some(Value::String(text)) => text.clone(),
                    Some(other) => crate::json::py_repr(other),
                };
                if role != "definition" && role != "reference" {
                    errors.push(Value::string(format!(
                        "invalid role '{role}' for symbol '{name}'"
                    )));
                    continue;
                }
                let kind = match field(&row, "kind") {
                    None => "function".to_string(),
                    Some(Value::String(text)) => text.clone(),
                    Some(other) => crate::json::py_repr(other),
                };
                let path = string_field(&row, "document_path");
                let line = int_arg(&row, "line_number").unwrap_or(0);
                engine.register_dynamic(&name, &kind, &path)?;
                if let Some(sym) = engine.store_dynamic_last() {
                    sym.agent = agent.clone();
                    sym.role = role;
                    sym.chunk_id = format!("runtime:{agent}:{name}");
                    sym.line = line.max(0) as u32;
                }
                stored += 1;
            }
            Ok(Value::object(vec![
                ("stored", Value::Int(stored)),
                ("errors", Value::Array(errors)),
            ]))
        }
        "remove_dynamic_symbols" => {
            let agent = caller(engine, args);
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
            // 2.2: 20 by default (1.5.2: 100).
            int_arg(args, "limit").unwrap_or(20),
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

/// HTTP `GET /tools`: the lite list, which is what `POST /call` runs, in
/// the HTTP shape (`parameters` where MCP says `inputSchema`).
pub(crate) fn tools_document() -> Value {
    let mut listed = json::parse(include_str!("tools_lite.json")).unwrap_or(Value::Null);
    if let Value::Object(pairs) = &mut listed {
        for (_, tools) in pairs.iter_mut() {
            if let Value::Array(rows) = tools {
                for row in rows.iter_mut() {
                    if let Value::Object(fields) = row {
                        for (key, _) in fields.iter_mut() {
                            if key == "inputSchema" {
                                *key = "parameters".to_string();
                            }
                        }
                    }
                }
            }
        }
    }
    listed
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

/// A value that is not a list is kept as an empty vector, which the store
/// reports as `invalid vector` the way 1.5.2 does.
fn float_pairs(value: &Value, key: &str) -> Vec<(String, Vec<f64>)> {
    match field(value, key) {
        Some(Value::Object(pairs)) => pairs
            .iter()
            .map(|(name, item)| match item {
                Value::Array(rows) => (
                    name.clone(),
                    rows.iter()
                        .map(|item| match item {
                            Value::Float(number) => *number,
                            Value::Int(number) => *number as f64,
                            _ => 0.0,
                        })
                        .collect(),
                ),
                _ => (name.clone(), Vec::new()),
            })
            .collect(),
        _ => Vec::new(),
    }
}
