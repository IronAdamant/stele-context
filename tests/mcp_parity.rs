//! Full MCP and CLI compare of Rust against Python 1.5.2.
//!
//! Speaks newline JSON-RPC tools/call to both stdio servers. Rust is
//! `stele-context serve-mcp`. Python is the installed PyPI 1.5.2 command
//! `python -m stele_context.cli serve-mcp`, unmodified. The fixture has Python,
//! JavaScript, Markdown, a one-page PDF with two text lines, a gitignored file,
//! and a skip_dirs directory. Normalisation is temp paths and CRLF. Timestamp
//! values may differ by the gap between the two processes; their type stays a
//! float with a fractional part.
//!
//! The Python is `STELE_PARITY_PYTHON` when set, else the first of `python3`
//! and `python` that runs stele-context 1.5.2, preferring one with pymupdf.
//! The test fails when there is none.
//!
//! Replies are read the way Python's `json.loads` reads them, because 1.5.2
//! writes them with `json.dumps`: ASCII escapes with surrogate pairs, `NaN`
//! and `Infinity`, and integers of any size. Equality is Python's: `1 == 1.0`
//! and `True == 1`. Diff lines print values with Python's `repr`.

#[path = "../src/bin/stele-bench/python.rs"]
mod python;

use python::{find_oracle, repr_list, repr_str, returncode, OracleError, TempDir, ORACLE_VERSION};
use std::collections::BTreeSet;
use std::fs;
use std::io::{Read, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender};
use std::thread;
use std::time::{Duration, Instant};

const PDF: &[u8] = concat!(
    "%PDF-1.4\n",
    "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
    "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
    "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents [4 0 R 5 0 R] >> endobj\n",
    "4 0 obj << /Length 24 >>\n",
    "stream\n",
    "BT (Hello line) Tj ET\n",
    "endstream\n",
    "endobj\n",
    "5 0 obj << /Length 24 >>\n",
    "stream\n",
    "BT (Second line) Tj ET\n",
    "endstream\n",
    "endobj\n",
    "trailer << /Root 1 0 R >>\n",
    "%%EOF\n",
)
.as_bytes();

/// Chunk tools are appended once a chunk id is known.
const LATER: &[&str] = &[
    "save_kv_state",
    "llm_embed",
    "bulk_store_summaries",
    "bulk_store_embeddings",
    "bulk_store_chunk_agent_notes",
    "store_semantic_summary",
    "store_chunk_agent_notes",
];

const REQUIRED: &[&str] = &[
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
    "stats",
    "project_brief",
    "annotate",
    "get_annotations",
    "delete_annotation",
    "update_annotation",
    "search_annotations",
    "acquire_document_lock",
    "refresh_document_lock",
    "release_document_lock",
    "get_document_lock_status",
    "release_agent_locks",
    "get_conflicts",
    "reap_expired_locks",
    "store_semantic_summary",
    "store_chunk_agent_notes",
];

/// Values compared as "within the run" rather than exactly.
const TIME_KEYS: &[&str] = &[
    "created_at",
    "updated_at",
    "last_updated",
    "locked_at",
    "expires_at",
    "latest_timestamp",
    "indexed_at",
    "at",
    "last_heartbeat",
    "file_mtime",
    "document_indexed_at",
    "stored_last_modified",
    "started_at",
    "latest_indexed_at",
    "seconds_since_last_index",
    "read_at",
    "timestamp",
];

/// 2.0 has no on-disk HNSW file and no SQLite database file.
const GRAPH_KEYS: &[&str] = &[
    "max_level",
    "avg_connections",
    "hnsw_span",
    "database_size_bytes",
    "page_count",
    "wal_size_bytes",
];

/// Fields a JSON snapshot cannot measure. Listed in MIGRATION.md.
const SQLITE_FIELDS: &[&str] = &[
    "journal_mode",
    "synchronous",
    "page_count",
    "page_size",
    "wal_autocheckpoint",
    "busy_timeout_ms",
    "database_size_bytes",
    "wal_size_bytes",
];

#[test]
fn mcp_full_output_matches_python_152() {
    let binary = env!("CARGO_BIN_EXE_stele-context");
    let python = match find_oracle() {
        Ok(python) => python,
        Err(OracleError::Version(found)) => {
            println!(
                "parity diff python package: need PyPI stele-context {ORACLE_VERSION}, found {found}"
            );
            panic!("need PyPI stele-context {ORACLE_VERSION}, found {found}");
        }
        Err(OracleError::Missing(why)) => panic!("{why}"),
    };
    let Some(diffs) = parity(binary, &python) else {
        panic!("mcp parity: tool coverage does not match the required list");
    };
    println!("undocumented diffs: {diffs}");
    assert_eq!(diffs, 0, "mcp parity: undocumented diffs: {diffs}");
}

// ---------------------------------------------------------------------------
// The run.
// ---------------------------------------------------------------------------

fn parity(binary: &str, python: &str) -> Option<usize> {
    let calls: Vec<(&str, Py)> = vec![
        ("index", args(r#"{"paths": ["."]}"#)),
        ("remove", args(r#"{"document_path": "docs/guide.md"}"#)),
        (
            "search",
            args(r#"{"query": "classify", "search_mode": "keyword", "top_k": 5}"#),
        ),
        (
            "search_text",
            args(r#"{"pattern": "classify", "regex": false}"#),
        ),
        (
            "agent_grep",
            args(r#"{"pattern": "classify", "group_by": "file"}"#),
        ),
        ("query", args(r#"{"query": "classify"}"#)),
        ("find_definition", args(r#"{"symbol": "classify"}"#)),
        ("find_references", args(r#"{"symbol": "classify"}"#)),
        ("impact_radius", args(r#"{"symbol": "classify"}"#)),
        (
            "coupling",
            args(r#"{"document_path": "src/main.py", "mode": "edges"}"#),
        ),
        (
            "get_context",
            args(r#"{"document_paths": ["src/calc.py"], "session_id": "s1"}"#),
        ),
        ("doctor", args("{}")),
        ("map", args(r#"{"compact": true, "path_prefix": "src"}"#)),
        (
            "document_lock",
            args(r#"{"action": "status", "document_path": "src/calc.py"}"#),
        ),
        (
            "annotations",
            args(r#"{"action": "get", "target": "src/calc.py", "target_type": "document"}"#),
        ),
        (
            "register_dynamic_symbols",
            args(
                r#"{"symbols": [{"name": "on_hook", "kind": "function", "document_path": "src/calc.py"}], "agent_id": "dyn-agent"}"#,
            ),
        ),
        ("get_dynamic_symbols", args(r#"{"agent_id": "dyn-agent"}"#)),
        (
            "remove_dynamic_symbols",
            args(r#"{"agent_id": "dyn-agent"}"#),
        ),
        ("detect_modality", args(r#"{"path": "docs/report.pdf"}"#)),
        ("get_supported_formats", args("{}")),
        ("enrichment_plan", args(r#"{"top_n": 5, "min_tokens": 1}"#)),
        ("get_search_history", args(r#"{"session_id": "s1"}"#)),
        ("get_session_read_files", args(r#"{"session_id": "s1"}"#)),
        ("detect_changes", args("{}")),
        ("prune_history", args(r#"{"max_entries": 1000}"#)),
        ("history", args(r#"{"limit": 20}"#)),
        (
            "get_relevant_kv",
            args(r#"{"session_id": "s1", "query": "classify", "top_k": 3}"#),
        ),
        ("list_sessions", args("{}")),
        ("rebuild_symbols", args("{}")),
        ("stale_chunks", args(r#"{"threshold": 0.5}"#)),
        ("list_agents", args(r#"{"active_only": true}"#)),
        ("get_notifications", args("{}")),
        ("environment_check", args("{}")),
        ("clean_bytecache", args("{}")),
        ("get_chunk_history", args(r#"{"limit": 5}"#)),
        (
            "batch",
            args(
                r#"{"operations": [{"method": "find_definition", "params": {"symbol": "classify"}}]}"#,
            ),
        ),
        ("stats", args("{}")),
        ("project_brief", args("{}")),
        (
            "annotate",
            args(
                r#"{"target": "src/calc.py", "target_type": "document", "content": "note", "tags": ["t1"]}"#,
            ),
        ),
        (
            "get_annotations",
            args(r#"{"target": "src/calc.py", "target_type": "document"}"#),
        ),
        ("search_annotations", args(r#"{"query": "note"}"#)),
        (
            "update_annotation",
            args(r#"{"annotation_id": 1, "content": "edited", "tags": ["t2"]}"#),
        ),
        ("delete_annotation", args(r#"{"annotation_id": 1}"#)),
        (
            "acquire_document_lock",
            args(r#"{"document_path": "src/calc.py", "agent_id": "other-agent"}"#),
        ),
        (
            "refresh_document_lock",
            args(r#"{"document_path": "src/calc.py", "agent_id": "other-agent"}"#),
        ),
        (
            "get_document_lock_status",
            args(r#"{"document_path": "src/calc.py"}"#),
        ),
        ("get_conflicts", args("{}")),
        (
            "release_document_lock",
            args(r#"{"document_path": "src/calc.py", "agent_id": "other-agent"}"#),
        ),
        (
            "release_agent_locks",
            args(r#"{"agent_id": "other-agent"}"#),
        ),
        ("reap_expired_locks", args("{}")),
        (
            "rollback",
            args(r#"{"session_id": "missing", "target_turn": 0}"#),
        ),
        (
            "prune_chunks",
            args(r#"{"session_id": "missing", "max_tokens": 10}"#),
        ),
    ];
    let covered: BTreeSet<&str> = calls.iter().map(|(name, _)| *name).collect();
    let required: BTreeSet<&str> = REQUIRED.iter().copied().collect();
    let known: BTreeSet<&str> = covered.iter().chain(LATER).copied().collect();
    if known != required {
        let missing: Vec<&str> = required.difference(&known).copied().collect();
        let extra: Vec<&str> = known.difference(&required).copied().collect();
        println!(
            "parity diff tool coverage missing={} extra={}",
            repr_list(&missing),
            repr_list(&extra)
        );
        return None;
    }

    let root = TempDir::new("stele-mcp-").expect("mkdtemp");
    fixture(root.path());
    let rs_store = TempDir::new("stele-rs-").expect("mkdtemp");
    let py_store = TempDir::new("stele-py-").expect("mkdtemp");
    let root_s = root.path().display().to_string();
    let rs_s = rs_store.path().display().to_string();
    let py_s = py_store.path().display().to_string();
    let rust_serve = vec![
        binary.to_string(),
        "--storage-dir".to_string(),
        rs_s.clone(),
        "serve-mcp".to_string(),
    ];
    let python_serve = vec![
        python.to_string(),
        "-m".to_string(),
        "stele_context.cli".to_string(),
        "--storage-dir".to_string(),
        py_s.clone(),
        "serve-mcp".to_string(),
    ];
    let mut rust = Server::start(&rust_serve, root.path());
    let mut python_server = Server::start(&python_serve, root.path());
    let mut diffs = 0usize;
    let init = args(
        r#"{"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "parity", "version": "0"}}"#,
    );
    rust.call("initialize", init.clone());
    python_server.call("initialize", init.clone());
    rust.notify("notifications/initialized");
    python_server.notify("notifications/initialized");

    let mut pending: Vec<(String, Py)> = calls
        .into_iter()
        .map(|(name, arguments)| (name.to_string(), arguments))
        .collect();
    let mut seen_chunk = false;
    let mut saved_cid = String::new();
    let mut invoked: BTreeSet<String> = BTreeSet::new();
    while !pending.is_empty() {
        let (name, arguments) = pending.remove(0);
        invoked.insert(name.clone());
        let left = tool(&mut rust, &name, arguments.clone());
        let right = tool(&mut python_server, &name, arguments);
        let mut bad = Vec::new();
        let mut left_n = walk(&left, &root_s, &rs_s, "");
        let mut right_n = walk(&right, &root_s, &py_s, "");
        blank_graph(&mut left_n);
        blank_graph(&mut right_n);
        let (mut left_n, mut right_n) = timestamps_ok(&left_n, &right_n, &mut bad);
        let (note, skip) = documented(&name, &mut left_n, &mut right_n);
        if !note.is_empty() {
            println!("documented {name}: {note}");
        }
        if !bad.is_empty() {
            println!("parity diff {name} timestamp type: {}", repr_list(&bad));
            diffs += 1;
        }
        if !skip && !py_eq(&left_n, &right_n) {
            println!(
                "parity diff {name}: {}",
                diff_text(first_diff(&left_n, &right_n, "$"))
            );
            diffs += 1;
        }
        if !seen_chunk && name == "get_context" {
            let cid = first_chunk(&left);
            if !cid.is_empty() {
                seen_chunk = true;
                saved_cid = cid.clone();
                let vec = floats(0.1, 128);
                let more: Vec<(&str, Py)> = vec![
                    (
                        "save_kv_state",
                        obj(vec![
                            ("session_id", s("s1")),
                            (
                                "kv_data",
                                obj(vec![
                                    (&cid, obj(vec![("note", s("alpha")), ("n", int(1))])),
                                    ("missing-chunk", obj(vec![("note", s("absent"))])),
                                ]),
                            ),
                        ]),
                    ),
                    (
                        "get_relevant_kv",
                        args(r#"{"session_id": "s1", "query": "classify", "top_k": 3}"#),
                    ),
                    (
                        "get_relevant_kv",
                        args(
                            r#"{"session_id": "s1", "query": "zzzz-unrelated-token", "top_k": 2}"#,
                        ),
                    ),
                    (
                        "save_kv_state",
                        obj(vec![
                            ("session_id", s("s2")),
                            (
                                "kv_data",
                                obj(vec![(&cid, obj(vec![("note", s("beta")), ("n", int(2))]))]),
                            ),
                        ]),
                    ),
                    (
                        "get_relevant_kv",
                        args(r#"{"session_id": "s2", "query": "return", "top_k": 2}"#),
                    ),
                    (
                        "llm_embed",
                        obj(vec![
                            ("text", s("def classify(n):\n    return n\n")),
                            ("chunk_id", s(&cid)),
                        ]),
                    ),
                    (
                        "bulk_store_summaries",
                        obj(vec![(
                            "summaries",
                            obj(vec![(&cid, s("classifies numbers"))]),
                        )]),
                    ),
                    (
                        "store_semantic_summary",
                        obj(vec![("chunk_id", s(&cid)), ("summary", s("hello"))]),
                    ),
                    (
                        "bulk_store_embeddings",
                        obj(vec![("embeddings", obj(vec![(&cid, vec)]))]),
                    ),
                    (
                        "bulk_store_chunk_agent_notes",
                        obj(vec![("notes", obj(vec![(&cid, s("keep this"))]))]),
                    ),
                    (
                        "store_chunk_agent_notes",
                        obj(vec![("chunk_id", s(&cid)), ("notes", s("note"))]),
                    ),
                ];
                let mut rest = std::mem::take(&mut pending);
                pending = more
                    .into_iter()
                    .map(|(name, arguments)| (name.to_string(), arguments))
                    .collect();
                pending.append(&mut rest);
            }
        }
    }
    let missed: Vec<&str> = required
        .iter()
        .copied()
        .filter(|name| !invoked.contains(*name))
        .collect();
    if !missed.is_empty() {
        println!("parity diff tools not called: {}", repr_list(&missed));
        diffs += 1;
    }

    let mut probes: Vec<(&str, Py)> = vec![
        ("map", args(r#"{"path_prefix": ""}"#)),
        ("map", args(r#"{"path_prefix": "./src"}"#)),
        (
            "annotations",
            args(
                r#"{"action": "create", "target": "src/calc.py", "target_type": "document", "content": "either tag", "tags": ["q", "x"]}"#,
            ),
        ),
        (
            "annotations",
            args(
                r#"{"action": "get", "target": "src/calc.py", "target_type": "document", "tags": ["q", "missing"]}"#,
            ),
        ),
        ("llm_embed", args(r#"{"text": "hello"}"#)),
        (
            "agent_grep",
            args(r#"{"pattern": "classify", "path_prefix": "src"}"#),
        ),
    ];
    if !saved_cid.is_empty() {
        probes.push((
            "bulk_store_embeddings",
            obj(vec![(
                "embeddings",
                obj(vec![(&saved_cid, floats(0.25, 64))]),
            )]),
        ));
    }
    for (name, arguments) in probes {
        let left = tool(&mut rust, name, arguments.clone());
        let right = tool(&mut python_server, name, arguments);
        let mut left_n = walk(&left, &root_s, &rs_s, "");
        let mut right_n = walk(&right, &root_s, &py_s, "");
        blank_graph(&mut left_n);
        blank_graph(&mut right_n);
        let (left_n, right_n) = timestamps_ok(&left_n, &right_n, &mut Vec::new());
        if !py_eq(&left_n, &right_n) {
            println!(
                "parity diff probe {name}: {}",
                diff_text(first_diff(&left_n, &right_n, "$"))
            );
            diffs += 1;
        }
    }
    rust.close();
    python_server.close();

    // CLI behaviour, not just --help. The server must release the store lock first.
    let cli = |store: &str, argv: &[&str]| -> (i64, String) {
        let mut full = vec![
            binary.to_string(),
            "--storage-dir".to_string(),
            store.to_string(),
        ];
        full.extend(argv.iter().map(|arg| arg.to_string()));
        run_cli(&full, root.path())
    };
    // Deep-compare CLI stdout before the Rust-only flag checks mutate the store.
    let py_cli = |store: &str, argv: &[&str]| -> (i64, String) {
        let mut full = vec![
            python.to_string(),
            "-m".to_string(),
            "stele_context.cli".to_string(),
            "--storage-dir".to_string(),
            store.to_string(),
        ];
        full.extend(argv.iter().map(|arg| arg.to_string()));
        run_cli(&full, root.path())
    };
    let comparable = [
        "search classify --path-prefix src --search-mode keyword --json",
        "search-text classify --limit 5 --json",
        "agent-grep classify --group-by file --json",
        "find-definition classify --json",
        "find-references classify --json",
        "query classify --json",
        "impact-radius --symbol classify --json",
        "coupling src/main.py --json",
        "map --json",
        "project-brief --json",
        "enrichment-plan --json",
        "stats --compact",
        "history --json",
    ];
    for line in comparable {
        let argv = words(line);
        let (rc_r, out_r) = cli(&rs_s, &argv);
        let (rc_p, out_p) = py_cli(&py_s, &argv);
        if rc_r != 0 || rc_p != 0 {
            println!(
                "parity diff cli {}: rust {rc_r} python {rc_p} {} {}",
                repr_list(&argv),
                repr_str(&head(&out_r, 120)),
                repr_str(&head(&out_p, 120))
            );
            diffs += 1;
            continue;
        }
        let mut left_n = walk(&unwrap(&out_r), &root_s, &rs_s, "");
        let mut right_n = walk(&unwrap(&out_p), &root_s, &py_s, "");
        blank_graph(&mut left_n);
        blank_graph(&mut right_n);
        let mut bad = Vec::new();
        let (left_n, right_n) = timestamps_ok(&left_n, &right_n, &mut bad);
        if !py_eq(&left_n, &right_n) {
            println!(
                "parity diff cli {}: {}",
                repr_list(&argv),
                diff_text(first_diff(&left_n, &right_n, "$"))
            );
            diffs += 1;
        }
    }
    let (rc_r, _) = cli(&rs_s, &words("search-text classify --path-prefix src"));
    let (rc_p, _) = py_cli(
        &py_s,
        &words("search-text classify --path-prefix src --json"),
    );
    if rc_r == 0 || rc_p == 0 {
        println!("parity diff cli search-text --path-prefix accepted: rust {rc_r} python {rc_p}");
        diffs += 1;
    }
    // Same annotation on both stores, then compare get-annotations.
    cli(
        &rs_s,
        &words("annotations --action create src/calc.py tagged --tags t1 --json"),
    );
    let (rc_b, out_b) = py_cli(
        &py_s,
        &words("annotate --type document --content tagged --tags t1 --json src/calc.py"),
    );
    if rc_b != 0 {
        println!(
            "parity diff cli annotate: {rc_b} {}",
            repr_str(&head(&out_b, 300))
        );
        diffs += 1;
    }
    let get_annotations = words("get-annotations --target src/calc.py --type document --json");
    let (_, out_r) = cli(&rs_s, &get_annotations);
    let (_, out_p) = py_cli(&py_s, &get_annotations);
    let mut left_n = walk(&unwrap(&out_r), &root_s, &rs_s, "");
    let right_n = walk(&unwrap(&out_p), &root_s, &py_s, "");
    if let Some(inner) = left_n.get("annotations") {
        left_n = inner.clone();
    }
    let (left_n, right_n) = timestamps_ok(&left_n, &right_n, &mut Vec::new());
    if !py_eq(&left_n, &right_n) {
        println!(
            "parity diff cli get-annotations: {}",
            diff_text(first_diff(&left_n, &right_n, "$"))
        );
        diffs += 1;
    }
    let (rc_r, out_r) = cli(
        &rs_s,
        &words("annotate --content two --tags q x --json src/calc.py"),
    );
    let (rc_p, out_p) = py_cli(
        &py_s,
        &words("annotate --type document --content two --tags q x --json src/calc.py"),
    );
    if rc_r != 0 || rc_p != 0 {
        println!(
            "parity diff cli --tags: rust {rc_r} {} python {rc_p} {}",
            repr_str(&head(&out_r, 180)),
            repr_str(&head(&out_p, 180))
        );
        diffs += 1;
    } else {
        let (_, out_r) = cli(&rs_s, &get_annotations);
        let (_, out_p) = py_cli(&py_s, &get_annotations);
        let mut left_n = walk(&unwrap(&out_r), &root_s, &rs_s, "");
        let mut right_n = walk(&unwrap(&out_p), &root_s, &py_s, "");
        if let Some(inner) = left_n.get("annotations") {
            left_n = inner.clone();
        }
        if let Some(inner) = right_n.get("annotations") {
            right_n = inner.clone();
        }
        let (left_n, right_n) = timestamps_ok(&left_n, &right_n, &mut Vec::new());
        if !py_eq(&left_n, &right_n) {
            println!(
                "parity diff cli --tags stored: {}",
                diff_text(first_diff(&left_n, &right_n, "$"))
            );
            diffs += 1;
        }
    }
    // Schema names the 1.5.2 functions do not accept. Observed after the
    // CLI compare so a Rust-only write does not change the shared stores.
    if saved_cid.is_empty() {
        println!("parity diff chunk id missing for alias calls");
        diffs += 1;
    } else {
        let mut rust_b = Server::start(&rust_serve, root.path());
        let mut python_b = Server::start(&python_serve, root.path());
        rust_b.call("initialize", init.clone());
        python_b.call("initialize", init.clone());
        rust_b.notify("notifications/initialized");
        python_b.notify("notifications/initialized");
        let alias_calls: Vec<(&str, Py)> = vec![
            (
                "store_embedding",
                obj(vec![
                    ("chunk_id", s(&saved_cid)),
                    ("embedding", floats(0.3, 128)),
                ]),
            ),
            (
                "bulk_annotate",
                args(
                    r#"{"items": [{"target": "src/main.py", "target_type": "document", "content": "bulk", "tags": ["b"]}]}"#,
                ),
            ),
        ];
        for (name, arguments) in alias_calls {
            let left = tool(&mut rust_b, name, arguments.clone());
            let right = tool(&mut python_b, name, arguments);
            let err = if right.is_dict() {
                right.get("error").cloned().unwrap_or(Py::None)
            } else {
                right.clone()
            };
            let err_s = match &err {
                Py::Str(text) => text.clone(),
                other => dumps(other, false),
            };
            let rust_ran = left.is_dict()
                && (left.get("stored").is_some_and(truthy)
                    || !matches!(left.get("created"), None | Some(Py::None)));
            if truthy(&err) && rust_ran {
                println!(
                    "documented {name}: 1.5.2 stdio rejects the schema name ({}); rust runs it",
                    head(&err_s, 180)
                );
            } else {
                println!(
                    "parity diff {name} alias: rust {} python {}",
                    py_str(&left),
                    py_str(&right)
                );
                diffs += 1;
            }
        }
        rust_b.close();
        python_b.close();
    }
    let (_, out) = cli(&rs_s, &["clear"]);
    if !out.contains("cancelled") {
        println!("parity diff cli --confirm missing: {}", head(&out, 200));
        diffs += 1;
    }
    let behaviour = [
        "search classify --path-prefix src --search-mode keyword --json",
        "search-text classif. --regex --limit 5 --json",
        "search classify --compact --top-k 2 --json",
        "agent-grep classify --group-by file --json",
        "detect --no-scan-new",
        "search classify --working-tree --json",
        "annotations --action create src/calc.py tagged --tags t1 --json",
    ];
    for line in behaviour {
        let argv = words(line);
        let (code, out) = cli(&rs_s, &argv);
        if code != 0 || py_strip(&out).is_empty() {
            println!(
                "parity diff cli {}: {code} {}",
                repr_list(&argv),
                head(&out, 200)
            );
            diffs += 1;
        }
    }
    let help = Command::new(binary)
        .arg("--help")
        .stdin(Stdio::inherit())
        .output()
        .unwrap_or_else(|err| panic!("spawn {binary}: {err}"));
    if !String::from_utf8_lossy(&help.stdout).contains("annotations") {
        println!("parity diff cli help missing annotations");
        diffs += 1;
    }
    Some(diffs)
}

/// `Path.write_text` opens in text mode, so "\n" is written as `os.linesep`.
fn write_text(path: &Path, text: &str) {
    let body = if cfg!(windows) {
        text.replace('\n', "\r\n")
    } else {
        text.to_string()
    };
    fs::write(path, body).unwrap_or_else(|err| panic!("write {}: {err}", path.display()));
}

fn write_bytes(path: &Path, bytes: &[u8]) {
    fs::write(path, bytes).unwrap_or_else(|err| panic!("write {}: {err}", path.display()));
}

fn mkdir(path: &Path) {
    fs::create_dir(path).unwrap_or_else(|err| panic!("mkdir {}: {err}", path.display()));
}

fn fixture(root: &Path) {
    mkdir(&root.join("src"));
    mkdir(&root.join("web"));
    mkdir(&root.join("docs"));
    mkdir(&root.join("vendor"));
    write_text(
        &root.join("src").join("calc.py"),
        "def classify(n):\n    return n\n",
    );
    write_text(
        &root.join("src").join("main.py"),
        "from calc import classify\n\ndef main():\n    return classify(1)\n",
    );
    write_text(
        &root.join("web").join("app.js"),
        "function greet(){ return 1 }\n",
    );
    write_text(
        &root.join("docs").join("guide.md"),
        "# guide\n\nclassify the input\n",
    );
    let media = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("parity")
        .join("fixtures")
        .join("media");
    let note = media.join("note.pdf");
    if note.is_file() {
        let bytes = fs::read(&note).unwrap_or_else(|err| panic!("read note.pdf: {err}"));
        write_bytes(&root.join("docs").join("report.pdf"), &bytes);
    } else {
        write_bytes(&root.join("docs").join("report.pdf"), PDF);
    }
    for name in ["small3.pdf", "big2.pdf"] {
        let src = media.join(name);
        if src.is_file() {
            let bytes = fs::read(&src).unwrap_or_else(|err| panic!("read {name}: {err}"));
            write_bytes(&root.join("docs").join(name), &bytes);
        }
    }
    write_text(&root.join("secret.py"), "def secret():\n    return 1\n");
    write_text(
        &root.join("vendor").join("hidden.py"),
        "def hidden():\n    return 1\n",
    );
    write_text(&root.join(".gitignore"), "secret.py\n");
    write_text(
        &root.join(".stele-context.toml"),
        "[stele-context]\nchunk_size = 256\nskip_dirs = [\"vendor\"]\nrespect_gitignore = true\n",
    );
    mkdir(&root.join(".git"));
}

// ---------------------------------------------------------------------------
// Child processes.
// ---------------------------------------------------------------------------

/// One stdio MCP server. Its stdout is read on a thread, so a reply can be
/// awaited with a timeout on every platform.
struct Server {
    child: Child,
    stdin: Option<ChildStdin>,
    lines: Receiver<Result<String, String>>,
    n: i64,
    closed: bool,
}

impl Server {
    fn start(argv: &[String], cwd: &Path) -> Server {
        let mut child = Command::new(&argv[0])
            .args(&argv[1..])
            .current_dir(cwd)
            .env("STELE_MCP_MODE", "full")
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1")
            .env_remove("PYTHONPATH")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap_or_else(|err| panic!("spawn {}: {err}", argv[0]));
        let stdout = child.stdout.take().expect("server stdout");
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || pump_lines(stdout, tx));
        Server {
            stdin: child.stdin.take(),
            child,
            lines: rx,
            n: 0,
            closed: false,
        }
    }

    /// A text-mode pipe writes "\n" as `os.linesep`.
    fn send(&mut self, message: &Py) {
        let newline = if cfg!(windows) { "\r\n" } else { "\n" };
        let line = dumps(message, false) + newline;
        let stdin = self.stdin.as_mut().expect("server stdin is open");
        stdin
            .write_all(line.as_bytes())
            .and_then(|()| stdin.flush())
            .unwrap_or_else(|err| panic!("write to server: {err}"));
    }

    fn read_line(&self, seconds: u64, label: &str) -> String {
        match self.lines.recv_timeout(Duration::from_secs(seconds)) {
            Ok(Ok(line)) => line,
            Ok(Err(err)) => panic!("{err}"),
            Err(RecvTimeoutError::Timeout) => panic!("timeout waiting for {label}"),
            Err(RecvTimeoutError::Disconnected) => String::new(),
        }
    }

    fn call(&mut self, method: &str, params: Py) -> Py {
        self.n += 1;
        let message = obj(vec![
            ("jsonrpc", s("2.0")),
            ("id", int(self.n)),
            ("method", s(method)),
            ("params", params),
        ]);
        self.send(&message);
        let mut body = None;
        for _ in 0..8 {
            let line = self.read_line(20, method);
            if line.is_empty() {
                panic!("server closed: ");
            }
            let reply = loads(&line)
                .unwrap_or_else(|err| panic!("JSONDecodeError: {err} in {}", repr_str(&line)));
            if !reply.is_dict() {
                panic!("AttributeError: reply is not an object: {}", repr(&reply));
            }
            let notice = reply.get("method").map(py_str).unwrap_or_default();
            body = Some(reply);
            if !notice.starts_with("notifications/") {
                break;
            }
        }
        let body = body.unwrap_or_else(|| panic!("no reply for {method}"));
        if let Some(error) = body.get("error") {
            return obj(vec![("ok", Py::Bool(false)), ("error", error.clone())]);
        }
        let result = match body.get("result") {
            Some(value) if truthy(value) => value.clone(),
            _ => Py::Dict(Vec::new()),
        };
        if !result.is_dict() {
            panic!("AttributeError: result is not an object: {}", repr(&result));
        }
        let content = match result.get("content") {
            Some(value) if truthy(value) => value.clone(),
            _ => Py::List(Vec::new()),
        };
        if let Some(first) = match &content {
            Py::List(items) => items.first(),
            _ => None,
        } {
            if !first.is_dict() {
                panic!(
                    "AttributeError: content[0] is not an object: {}",
                    repr(first)
                );
            }
            let text = match first.get("text") {
                None => String::new(),
                Some(Py::Str(text)) => text.clone(),
                Some(other) => panic!("TypeError: text is {}", repr(other)),
            };
            return loads(&text).unwrap_or_else(|_| obj(vec![("text", Py::Str(text))]));
        }
        result
    }

    fn notify(&mut self, method: &str) {
        self.send(&obj(vec![("jsonrpc", s("2.0")), ("method", s(method))]));
    }

    /// Close stdin, wait five seconds, then kill.
    fn close(&mut self) {
        if self.closed {
            return;
        }
        self.closed = true;
        self.stdin.take();
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
                _ => break,
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.close();
    }
}

/// Text-mode `readline`: "\n", "\r\n" and a lone "\r" all end a line, and
/// each comes back as "\n". A final line without one comes back as is.
fn pump_lines(mut pipe: impl Read, tx: Sender<Result<String, String>>) {
    let mut chunk = [0u8; 8192];
    let mut line = Vec::new();
    let mut cr = false;
    let emit = |line: &mut Vec<u8>, newline: bool| {
        let mut bytes = std::mem::take(line);
        if newline {
            bytes.push(b'\n');
        }
        let text = String::from_utf8(bytes).map_err(|err| format!("UnicodeDecodeError: {err}"));
        tx.send(text).is_ok()
    };
    loop {
        let read = match pipe.read(&mut chunk) {
            Ok(0) | Err(_) => break,
            Ok(read) => read,
        };
        for &byte in &chunk[..read] {
            if cr {
                cr = false;
                if !emit(&mut line, true) {
                    return;
                }
                if byte == b'\n' {
                    continue;
                }
            }
            match byte {
                b'\r' => cr = true,
                b'\n' => {
                    if !emit(&mut line, true) {
                        return;
                    }
                }
                _ => line.push(byte),
            }
        }
    }
    if cr {
        emit(&mut line, true);
    } else if !line.is_empty() {
        emit(&mut line, false);
    }
}

fn tool(server: &mut Server, name: &str, arguments: Py) -> Py {
    server.call(
        "tools/call",
        obj(vec![("name", s(name)), ("arguments", arguments)]),
    )
}

/// `subprocess.run(argv, cwd=root, capture_output=True, text=True,
/// encoding="utf-8")`: strict UTF-8, universal newlines, stdin inherited.
fn run_cli(argv: &[String], cwd: &Path) -> (i64, String) {
    let output = Command::new(&argv[0])
        .args(&argv[1..])
        .current_dir(cwd)
        .stdin(Stdio::inherit())
        .output()
        .unwrap_or_else(|err| panic!("spawn {}: {err}", argv[0]));
    let text = |bytes: Vec<u8>, pipe: &str| {
        String::from_utf8(bytes)
            .unwrap_or_else(|err| panic!("UnicodeDecodeError in {pipe} of {argv:?}: {err}"))
            .replace("\r\n", "\n")
            .replace('\r', "\n")
    };
    let stdout = text(output.stdout, "stdout");
    text(output.stderr, "stderr");
    (returncode(output.status), stdout)
}

fn unwrap(text: &str) -> Py {
    let Ok(value) = loads(text) else {
        return Py::Str(text.to_string());
    };
    if value.get("schema_version").is_some() {
        if let Some(result) = value.get("result") {
            return result.clone();
        }
    }
    value
}

// ---------------------------------------------------------------------------
// Normalisation and compare.
// ---------------------------------------------------------------------------

fn walk(value: &Py, root: &str, other_root: &str, key: &str) -> Py {
    match value {
        Py::Dict(pairs) => {
            let mut sorted: Vec<&(String, Py)> = pairs.iter().collect();
            sorted.sort_by(|left, right| left.0.cmp(&right.0));
            Py::Dict(
                sorted
                    .into_iter()
                    .map(|(name, item)| (name.clone(), walk(item, root, other_root, name)))
                    .collect(),
            )
        }
        Py::List(items) => {
            let rows: Vec<Py> = items
                .iter()
                .map(|item| walk(item, root, other_root, key))
                .collect();
            // `unchanged` order follows PYTHONHASHSEED. Other lists keep their order.
            if key == "unchanged" {
                let mut keyed: Vec<(String, Py)> = rows
                    .into_iter()
                    .map(|row| (dumps(&row, true), row))
                    .collect();
                keyed.sort_by(|left, right| left.0.cmp(&right.0));
                return Py::List(keyed.into_iter().map(|(_, row)| row).collect());
            }
            Py::List(rows)
        }
        Py::Str(text)
            if (key == "version" || key == "stele_version")
                && matches!(
                    text.chars().take(3).collect::<String>().as_str(),
                    "1.5" | "2.0"
                ) =>
        {
            s("version")
        }
        Py::Str(text) => Py::Str(walk_text(text, root, other_root)),
        Py::Int(_) | Py::Bool(_) if key == "pid" => int(0),
        // Signature bytes match 1.5.2. Cosine of the f64 query against the f32
        // vector still moves in the 8th digit. Five decimal places is the compare.
        Py::Float(number) if key == "relevance_score" => Py::Float(round5(*number)),
        other => other.clone(),
    }
}

fn walk_text(value: &str, root: &str, other_root: &str) -> String {
    let mut text = value.replace("\r\n", "\n").replace('\\', "/");
    for prefix in [root, other_root] {
        if !prefix.is_empty() {
            text = text.replace(&prefix.replace('\\', "/"), "");
        }
    }
    let marker: Vec<char> = "stele-mcp-".chars().collect();
    let lowered: Vec<char> = text.to_lowercase().chars().collect();
    if let Some(start) = lowered
        .windows(marker.len())
        .position(|window| window == marker.as_slice())
    {
        let chars: Vec<char> = text.chars().collect();
        let mut end = start + marker.len();
        while end < chars.len() && chars[end] != '/' {
            end += 1;
        }
        text = chars
            .get(end..)
            .map(|rest| rest.iter().collect())
            .unwrap_or_default();
    }
    const AGENT: &str = "stele-context-mcp-";
    while let Some(start) = text.find(AGENT) {
        let rest = &text[start + AGENT.len()..];
        let digits = rest.len()
            - rest
                .trim_start_matches(|ch: char| ch.is_ascii_digit())
                .len();
        text = format!("{}AGENT{}", &text[..start], &rest[digits..]);
    }
    text
}

/// `round(value, 5)`: the correctly rounded five-place decimal, read back.
fn round5(value: f64) -> f64 {
    if !value.is_finite() {
        return value;
    }
    format!("{value:.5}").parse().unwrap_or(value)
}

fn blank_graph(value: &mut Py) {
    match value {
        Py::Dict(pairs) => {
            for (key, item) in pairs.iter_mut() {
                if GRAPH_KEYS.contains(&key.as_str()) {
                    *item = s("store");
                }
            }
            for (_, item) in pairs.iter_mut() {
                blank_graph(item);
            }
        }
        Py::List(items) => items.iter_mut().for_each(blank_graph),
        _ => {}
    }
}

/// Floats within the run match. An int against a float is a real diff.
/// Keys missing on one side come back as None on both, as `dict.get` does.
fn timestamps_ok(left: &Py, right: &Py, bad: &mut Vec<String>) -> (Py, Py) {
    match (left, right) {
        (Py::Dict(lp), Py::Dict(rp)) => {
            let keys: BTreeSet<&str> = lp.iter().chain(rp).map(|(key, _)| key.as_str()).collect();
            let mut out_l = Vec::new();
            let mut out_r = Vec::new();
            for key in keys {
                let lv = lookup(lp, key).cloned().unwrap_or(Py::None);
                let rv = lookup(rp, key).cloned().unwrap_or(Py::None);
                let (a, b) = if TIME_KEYS.contains(&key) && lv.is_number() && rv.is_number() {
                    let gap = (lv.as_float() - rv.as_float()).abs();
                    if key == "seconds_since_last_index" {
                        if gap <= 5.0 {
                            (s("fresh"), s("fresh"))
                        } else {
                            (lv, rv)
                        }
                    } else if matches!(lv, Py::Bool(_)) || matches!(rv, Py::Bool(_)) {
                        (lv, rv)
                    } else if matches!(lv, Py::Int(_)) || matches!(rv, Py::Int(_)) {
                        bad.push(key.to_string());
                        (lv, rv)
                    } else if gap <= 180.0 {
                        (s("time"), s("time"))
                    } else {
                        (lv, rv)
                    }
                } else {
                    timestamps_ok(&lv, &rv, bad)
                };
                out_l.push((key.to_string(), a));
                out_r.push((key.to_string(), b));
            }
            (Py::Dict(out_l), Py::Dict(out_r))
        }
        (Py::List(li), Py::List(ri)) if li.len() == ri.len() => {
            let (out_l, out_r) = li
                .iter()
                .zip(ri)
                .map(|(a, b)| timestamps_ok(a, b, bad))
                .unzip();
            (Py::List(out_l), Py::List(out_r))
        }
        _ => (left.clone(), right.clone()),
    }
}

fn first_chunk(value: &Py) -> String {
    fn visit(item: &Py, found: &mut Vec<String>) {
        match item {
            Py::Dict(pairs) => {
                if let Some(Py::Str(cid)) = lookup(pairs, "chunk_id") {
                    if !cid.is_empty() {
                        found.push(cid.clone());
                    }
                }
                for (_, child) in pairs {
                    visit(child, found);
                }
            }
            Py::List(items) => items.iter().for_each(|child| visit(child, found)),
            _ => {}
        }
    }
    let mut found = Vec::new();
    visit(value, &mut found);
    found.into_iter().next().unwrap_or_default()
}

/// Narrow adjustments named in MIGRATION.md. Returns (note, skip_compare).
fn documented(name: &str, left: &mut Py, right: &mut Py) -> (String, bool) {
    let mut notes: Vec<String> = Vec::new();
    let mut skip = false;
    if name == "doctor" && left.is_dict() && right.is_dict() {
        for side in [&mut *left, &mut *right] {
            if let Some(Py::Dict(health)) = side.get_mut("db_health") {
                health.retain(|(key, _)| !SQLITE_FIELDS.contains(&key.as_str()));
            }
        }
        notes.push("sqlite pragma and database file fields".to_string());
    }
    // stats.kv_cache_size_bytes and index.insert_count are compared.
    if name == "get_supported_formats" && left.is_dict() && right.is_dict() {
        let mut spare_left = Vec::new();
        let mut spare_right = Vec::new();
        let lf = match left.get_mut("formats") {
            Some(Py::Dict(pairs)) => pairs,
            _ => &mut spare_left,
        };
        let rf = match right.get_mut("formats") {
            Some(Py::Dict(pairs)) => pairs,
            _ => &mut spare_right,
        };
        let mut extra: Vec<String> = lf
            .iter()
            .filter(|(key, _)| matches!(lookup(rf, key), None | Some(Py::None)))
            .map(|(key, _)| key.clone())
            .collect();
        extra.sort();
        rf.retain(|(_, value)| !matches!(value, Py::None));
        if !extra.is_empty() {
            lf.retain(|(key, _)| !extra.contains(key));
            notes.push(format!("rust also lists {}", extra.join(",")));
        }
    }
    if name == "get_session_read_files" {
        if let Some(Py::Str(err)) = right.get("error") {
            if err.contains("staleness_score") {
                notes.push("1.5.2 query errors; rust returns the read list".to_string());
                skip = true;
            }
        }
    }
    if (name == "store_embedding" || name == "bulk_annotate") && right.is_dict() {
        let err = right.get("error").map(py_str).unwrap_or_default();
        if err.contains("unexpected keyword") || err.contains("missing") {
            notes.push("1.5.2 rejects the alias argument; rust accepts it".to_string());
            skip = true;
        }
    }
    (notes.join("; "), skip)
}

fn first_diff(left: &Py, right: &Py, path: &str) -> Option<(String, Py, Py)> {
    if py_eq(left, right) {
        return None;
    }
    match (left, right) {
        (Py::Dict(lp), Py::Dict(rp)) => {
            let keys: BTreeSet<&str> = lp.iter().chain(rp).map(|(key, _)| key.as_str()).collect();
            for key in keys {
                let lv = lookup(lp, key).cloned().unwrap_or(Py::None);
                let rv = lookup(rp, key).cloned().unwrap_or(Py::None);
                if let Some(found) = first_diff(&lv, &rv, &format!("{path}.{key}")) {
                    return Some(found);
                }
            }
            None
        }
        (Py::List(li), Py::List(ri)) if li.len() == ri.len() => {
            for (index, (item, other)) in li.iter().zip(ri).enumerate() {
                if let Some(found) = first_diff(item, other, &format!("{path}[{index}]")) {
                    return Some(found);
                }
            }
            None
        }
        _ => Some((path.to_string(), left.clone(), right.clone())),
    }
}

/// `str(first_diff(...))`: a tuple repr, or `None`.
fn diff_text(found: Option<(String, Py, Py)>) -> String {
    match found {
        None => "None".to_string(),
        Some((path, left, right)) => {
            format!("({}, {}, {})", repr_str(&path), repr(&left), repr(&right))
        }
    }
}

/// The CLI argument lists here have no token with a space in it.
fn words(line: &str) -> Vec<&str> {
    line.split_whitespace().collect()
}

fn head(text: &str, count: usize) -> String {
    text.chars().take(count).collect()
}

/// `str.strip()`: Python also counts U+001C..U+001F as whitespace.
fn py_strip(text: &str) -> &str {
    text.trim_matches(|ch: char| ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch))
}

// ---------------------------------------------------------------------------
// Python values: json.loads, json.dumps, ==, repr, truthiness.
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
enum Py {
    None,
    Bool(bool),
    /// Decimal digits with an optional minus sign. Python ints have no size limit.
    Int(String),
    Float(f64),
    Str(String),
    List(Vec<Py>),
    /// Insertion order, keys unique, as a Python dict.
    Dict(Vec<(String, Py)>),
}

impl Py {
    fn is_dict(&self) -> bool {
        matches!(self, Py::Dict(_))
    }

    fn is_number(&self) -> bool {
        matches!(self, Py::Bool(_) | Py::Int(_) | Py::Float(_))
    }

    /// `float(x)` for a number.
    fn as_float(&self) -> f64 {
        match self {
            Py::Bool(flag) => f64::from(u8::from(*flag)),
            Py::Int(digits) => digits.parse().unwrap_or(f64::NAN),
            Py::Float(number) => *number,
            _ => f64::NAN,
        }
    }

    fn get(&self, key: &str) -> Option<&Py> {
        match self {
            Py::Dict(pairs) => lookup(pairs, key),
            _ => None,
        }
    }

    fn get_mut(&mut self, key: &str) -> Option<&mut Py> {
        match self {
            Py::Dict(pairs) => pairs
                .iter_mut()
                .find(|(name, _)| name == key)
                .map(|(_, value)| value),
            _ => None,
        }
    }
}

fn lookup<'a>(pairs: &'a [(String, Py)], key: &str) -> Option<&'a Py> {
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, value)| value)
}

fn set_item(pairs: &mut Vec<(String, Py)>, key: String, value: Py) {
    match pairs.iter_mut().find(|(name, _)| *name == key) {
        Some(slot) => slot.1 = value,
        None => pairs.push((key, value)),
    }
}

fn s(text: &str) -> Py {
    Py::Str(text.to_string())
}

fn int(number: i64) -> Py {
    Py::Int(number.to_string())
}

fn floats(value: f64, count: usize) -> Py {
    Py::List(vec![Py::Float(value); count])
}

/// A dict literal: a repeated key keeps its first place and its last value.
fn obj(pairs: Vec<(&str, Py)>) -> Py {
    let mut out = Vec::new();
    for (key, value) in pairs {
        set_item(&mut out, key.to_string(), value);
    }
    Py::Dict(out)
}

fn args(text: &str) -> Py {
    loads(text).unwrap_or_else(|err| panic!("bad literal {text}: {err}"))
}

fn truthy(value: &Py) -> bool {
    match value {
        Py::None => false,
        Py::Bool(flag) => *flag,
        Py::Int(digits) => digits != "0",
        Py::Float(number) => *number != 0.0,
        Py::Str(text) => !text.is_empty(),
        Py::List(items) => !items.is_empty(),
        Py::Dict(pairs) => !pairs.is_empty(),
    }
}

/// `==`. Numbers compare by value across bool, int and float; NaN equals nothing.
fn py_eq(left: &Py, right: &Py) -> bool {
    match (left, right) {
        (Py::None, Py::None) => true,
        (Py::Str(a), Py::Str(b)) => a == b,
        (Py::List(a), Py::List(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(x, y)| py_eq(x, y))
        }
        (Py::Dict(a), Py::Dict(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(key, value)| lookup(b, key).is_some_and(|other| py_eq(value, other)))
        }
        (Py::Float(a), Py::Float(b)) => a == b,
        (Py::Float(float), other) | (other, Py::Float(float)) if other.is_number() => {
            float.is_finite() && float.fract() == 0.0 && integral_text(*float) == int_text(other)
        }
        (a, b) if a.is_number() && b.is_number() => int_text(a) == int_text(b),
        _ => false,
    }
}

fn int_text(value: &Py) -> String {
    match value {
        Py::Bool(flag) => u8::from(*flag).to_string(),
        Py::Int(digits) => digits.clone(),
        _ => String::new(),
    }
}

/// The exact integer an integral float holds.
fn integral_text(value: f64) -> String {
    let text = format!("{value:.0}");
    if text == "-0" {
        "0".to_string()
    } else {
        text
    }
}

fn py_str(value: &Py) -> String {
    match value {
        Py::Str(text) => text.clone(),
        other => repr(other),
    }
}

fn repr(value: &Py) -> String {
    match value {
        Py::None => "None".to_string(),
        Py::Bool(true) => "True".to_string(),
        Py::Bool(false) => "False".to_string(),
        Py::Int(digits) => digits.clone(),
        Py::Float(number) => float_repr(*number, false),
        Py::Str(text) => repr_str(text),
        Py::List(items) => {
            let parts: Vec<String> = items.iter().map(repr).collect();
            format!("[{}]", parts.join(", "))
        }
        Py::Dict(pairs) => {
            let parts: Vec<String> = pairs
                .iter()
                .map(|(key, item)| format!("{}: {}", repr_str(key), repr(item)))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

/// `float.__repr__`: the shortest round-trip digits, in exponent form when
/// the decimal point sits before -4 or after 16 places. `json` spells the
/// non-finite values as `json.dumps` does.
fn float_repr(value: f64, json: bool) -> String {
    if value.is_nan() {
        return if json { "NaN" } else { "nan" }.to_string();
    }
    if value.is_infinite() {
        let text = match (json, value > 0.0) {
            (true, true) => "Infinity",
            (true, false) => "-Infinity",
            (false, true) => "inf",
            (false, false) => "-inf",
        };
        return text.to_string();
    }
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .to_string();
    }
    // `{:e}` gives the shortest round-trip length, but on an exact tie in the
    // last digit it rounds up where Python's dtoa rounds to even. The exact
    // formatter at that length rounds the true value to even.
    let shortest = format!("{value:e}");
    let length = shortest
        .split('e')
        .next()
        .map(|mantissa| mantissa.chars().filter(char::is_ascii_digit).count())
        .unwrap_or(1);
    let exact = format!("{value:.prec$e}", prec = length.saturating_sub(1));
    let (mantissa, exponent) = exact.split_once('e').expect("exponent form");
    let exponent: i32 = exponent.parse().expect("exponent");
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let point = exponent + 1;
    let mut out = String::new();
    if negative {
        out.push('-');
    }
    if !(-4 < point && point <= 16) {
        out.push_str(&digits[..1]);
        if digits.len() > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let sign = if exponent < 0 { '-' } else { '+' };
        out.push_str(&format!("e{sign}{:02}", exponent.abs()));
    } else if point <= 0 {
        out.push_str("0.");
        out.push_str(&"0".repeat((-point) as usize));
        out.push_str(&digits);
    } else if point as usize >= digits.len() {
        out.push_str(&digits);
        out.push_str(&"0".repeat(point as usize - digits.len()));
        out.push_str(".0");
    } else {
        out.push_str(&digits[..point as usize]);
        out.push('.');
        out.push_str(&digits[point as usize..]);
    }
    out
}

/// `json.dumps(value, sort_keys=...)` with the default `", "` and `": "`
/// separators and ASCII escapes.
fn dumps(value: &Py, sort_keys: bool) -> String {
    let mut out = String::new();
    dump(&mut out, value, sort_keys);
    out
}

fn dump(out: &mut String, value: &Py, sort_keys: bool) {
    match value {
        Py::None => out.push_str("null"),
        Py::Bool(true) => out.push_str("true"),
        Py::Bool(false) => out.push_str("false"),
        Py::Int(digits) => out.push_str(digits),
        Py::Float(number) => out.push_str(&float_repr(*number, true)),
        Py::Str(text) => dump_str(out, text),
        Py::List(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                dump(out, item, sort_keys);
            }
            out.push(']');
        }
        Py::Dict(pairs) => {
            let mut ordered: Vec<&(String, Py)> = pairs.iter().collect();
            if sort_keys {
                ordered.sort_by(|left, right| left.0.cmp(&right.0));
            }
            out.push('{');
            for (index, (key, item)) in ordered.into_iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                dump_str(out, key);
                out.push_str(": ");
                dump(out, item, sort_keys);
            }
            out.push('}');
        }
    }
}

fn dump_str(out: &mut String, text: &str) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(ch),
            _ => {
                let mut units = [0u16; 2];
                for unit in ch.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

/// `json.loads`: surrogate pairs join, `NaN` and `Infinity` parse, integers
/// keep every digit, whitespace is space, tab, CR and LF only. A lone
/// surrogate, which a Rust string cannot hold, becomes U+FFFD.
fn loads(text: &str) -> Result<Py, String> {
    if text.starts_with('\u{feff}') {
        return Err("Unexpected UTF-8 BOM".to_string());
    }
    let mut reader = Loads {
        bytes: text.as_bytes(),
        at: 0,
    };
    reader.space();
    let value = reader.value()?;
    reader.space();
    if reader.at != reader.bytes.len() {
        return Err(format!("Extra data at {}", reader.at));
    }
    Ok(value)
}

struct Loads<'a> {
    bytes: &'a [u8],
    at: usize,
}

impl Loads<'_> {
    fn fail<T>(&self, message: &str) -> Result<T, String> {
        Err(format!("{message} at {}", self.at))
    }

    fn peek(&self, offset: usize) -> Option<u8> {
        self.bytes.get(self.at + offset).copied()
    }

    fn space(&mut self) {
        while matches!(self.peek(0), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.at += 1;
        }
    }

    fn word(&mut self, word: &str) -> bool {
        if self.bytes[self.at..].starts_with(word.as_bytes()) {
            self.at += word.len();
            true
        } else {
            false
        }
    }

    fn value(&mut self) -> Result<Py, String> {
        match self.peek(0) {
            Some(b'"') => Ok(Py::Str(self.string()?)),
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'n') if self.word("null") => Ok(Py::None),
            Some(b't') if self.word("true") => Ok(Py::Bool(true)),
            Some(b'f') if self.word("false") => Ok(Py::Bool(false)),
            Some(b'N') if self.word("NaN") => Ok(Py::Float(f64::NAN)),
            Some(b'I') if self.word("Infinity") => Ok(Py::Float(f64::INFINITY)),
            Some(b'-') if self.word("-Infinity") => Ok(Py::Float(f64::NEG_INFINITY)),
            _ => self.number(),
        }
    }

    fn digits(&self, mut at: usize) -> usize {
        while matches!(self.bytes.get(at), Some(b'0'..=b'9')) {
            at += 1;
        }
        at
    }

    fn number(&mut self) -> Result<Py, String> {
        let start = self.at;
        let mut at = start;
        if self.bytes.get(at) == Some(&b'-') {
            at += 1;
        }
        match self.bytes.get(at) {
            Some(b'0') => at += 1,
            Some(b'1'..=b'9') => at = self.digits(at + 1),
            _ => return self.fail("Expecting value"),
        }
        let mut float = false;
        if self.bytes.get(at) == Some(&b'.') && matches!(self.bytes.get(at + 1), Some(b'0'..=b'9'))
        {
            float = true;
            at = self.digits(at + 2);
        }
        if matches!(self.bytes.get(at), Some(b'e' | b'E')) {
            let mut exp = at + 1;
            if matches!(self.bytes.get(exp), Some(b'+' | b'-')) {
                exp += 1;
            }
            if matches!(self.bytes.get(exp), Some(b'0'..=b'9')) {
                float = true;
                at = self.digits(exp);
            }
        }
        let lexeme = std::str::from_utf8(&self.bytes[start..at]).expect("ascii number");
        self.at = at;
        if float {
            return lexeme
                .parse()
                .map(Py::Float)
                .map_err(|err| format!("float {lexeme}: {err}"));
        }
        let negative = lexeme.starts_with('-');
        let digits = lexeme.trim_start_matches('-');
        Ok(Py::Int(if negative && digits != "0" {
            lexeme.to_string()
        } else {
            digits.to_string()
        }))
    }

    fn hex4(&mut self) -> Result<u32, String> {
        let Some(chunk) = self.bytes.get(self.at..self.at + 4) else {
            return self.fail("Invalid \\uXXXX escape");
        };
        let text = std::str::from_utf8(chunk).map_err(|_| "Invalid \\uXXXX escape".to_string())?;
        if !text.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return self.fail("Invalid \\uXXXX escape");
        }
        self.at += 4;
        u32::from_str_radix(text, 16).map_err(|err| err.to_string())
    }

    fn string(&mut self) -> Result<String, String> {
        self.at += 1;
        let mut out = String::new();
        loop {
            let rest = &self.bytes[self.at..];
            let Some(stop) = rest
                .iter()
                .position(|&byte| byte == b'"' || byte == b'\\' || byte < 0x20)
            else {
                return self.fail("Unterminated string");
            };
            out.push_str(std::str::from_utf8(&rest[..stop]).expect("utf-8 input"));
            self.at += stop;
            match self.bytes[self.at] {
                b'"' => {
                    self.at += 1;
                    return Ok(out);
                }
                b'\\' => {
                    let Some(escape) = self.peek(1) else {
                        return self.fail("Unterminated string");
                    };
                    self.at += 2;
                    match escape {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let mut code = self.hex4()?;
                            if (0xd800..=0xdbff).contains(&code)
                                && self.at + 6 < self.bytes.len()
                                && self.peek(0) == Some(b'\\')
                                && self.peek(1) == Some(b'u')
                            {
                                let back = self.at;
                                self.at += 2;
                                let low = self.hex4()?;
                                if (0xdc00..=0xdfff).contains(&low) {
                                    code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
                                } else {
                                    self.at = back;
                                }
                            }
                            out.push(char::from_u32(code).unwrap_or('\u{fffd}'));
                        }
                        _ => return self.fail("Invalid \\escape"),
                    }
                }
                _ => return self.fail("Invalid control character"),
            }
        }
    }

    fn array(&mut self) -> Result<Py, String> {
        self.at += 1;
        let mut items = Vec::new();
        self.space();
        if self.peek(0) == Some(b']') {
            self.at += 1;
            return Ok(Py::List(items));
        }
        loop {
            items.push(self.value()?);
            self.space();
            match self.peek(0) {
                Some(b']') => {
                    self.at += 1;
                    return Ok(Py::List(items));
                }
                Some(b',') => {
                    self.at += 1;
                    self.space();
                }
                _ => return self.fail("Expecting ',' delimiter"),
            }
        }
    }

    fn object(&mut self) -> Result<Py, String> {
        self.at += 1;
        let mut pairs = Vec::new();
        self.space();
        if self.peek(0) == Some(b'}') {
            self.at += 1;
            return Ok(Py::Dict(pairs));
        }
        loop {
            if self.peek(0) != Some(b'"') {
                return self.fail("Expecting property name enclosed in double quotes");
            }
            let key = self.string()?;
            self.space();
            if self.peek(0) != Some(b':') {
                return self.fail("Expecting ':' delimiter");
            }
            self.at += 1;
            self.space();
            let value = self.value()?;
            set_item(&mut pairs, key, value);
            self.space();
            match self.peek(0) {
                Some(b'}') => {
                    self.at += 1;
                    return Ok(Py::Dict(pairs));
                }
                Some(b',') => {
                    self.at += 1;
                    self.space();
                }
                _ => return self.fail("Expecting ',' delimiter"),
            }
        }
    }
}
