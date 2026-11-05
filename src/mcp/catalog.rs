//! What `tools/list` says about each tool: a one-line description and an
//! `inputSchema` built from the argument table in `signature.rs`, the same
//! table that accepts or rejects a call. The three documents the server
//! sends (`tools_lite.json`, `tools_standard.json`, `tools_full.json`) are
//! generated from here; a test keeps the committed files equal to it, so a
//! schema can never list an argument the server rejects or miss one it
//! takes.

/// One line per tool: what it is for and, where it matters, its default.
const DESCRIPTIONS: &[(&str, &str)] = &[
    ("index", "Index files or directories so search, symbols and get_context can use them. Re-run on files you changed."),
    ("remove", "Remove a document and its chunks, symbols and annotations from the index."),
    ("detect_changes", "Files modified, new or removed since the last index; changed files are re-indexed."),
    ("search", "Ranked search over indexed chunks (BM25; hybrid with Tier-2 summaries). Previews by default; compact=false for full text."),
    ("agent_grep", "Grep indexed files with enclosing scope and classification, within max_tokens (default 1000)."),
    ("search_text", "Exact substring or regex over every indexed chunk, with perfect recall. limit defaults to 20."),
    ("query", "Start here for a question: merges ranked search, symbol lookups and grep in one list."),
    ("find_references", "Definitions and every usage of a symbol, with a verdict (referenced, unreferenced, external)."),
    ("find_definition", "Where a symbol is defined: path, line, signature and a short preview. full=true adds the chunk text."),
    ("impact_radius", "What depends on a symbol, file or chunk, through the symbol graph."),
    ("coupling", "Files coupled to a file through shared symbols."),
    ("get_context", "Cached text of files with unchanged/changed/new status, so unchanged files need no re-read."),
    ("doctor", "Session start: index health, counts, Tier-2 coverage and next steps."),
    ("map", "Project overview: documents by token size (max_documents, default 50), totals, index health."),
    ("document_lock", "Claim a file for one agent: acquire, refresh, release, release_all, status, conflicts, reap."),
    ("annotations", "Notes on files or chunks that persist across sessions and agents: create, get, update, delete, search."),
    ("register_dynamic_symbols", "Register runtime symbols (hooks, handlers) that have no definition in the source."),
    ("remove_dynamic_symbols", "Remove the runtime symbols an agent registered."),
    ("llm_embed", "Store your own text as a searchable memory chunk under chunk_id."),
    ("bulk_store_summaries", "Store one-line summaries for chunks (Tier 2); they improve hybrid search."),
    ("enrichment_plan", "Chunks worth summarizing next: the largest ones without a Tier-2 summary."),
    ("get_search_history", "Searches already run in a session and the files they hit."),
    ("get_session_read_files", "Files already read with get_context in a session."),
    ("detect_modality", "File type of a path: code, text, pdf, image, audio or video."),
    ("get_supported_formats", "Supported file extensions by modality."),
    ("prune_history", "Delete old change-history entries by age or count."),
    ("history", "Indexing history: what each index or detect run changed."),
    ("get_relevant_kv", "Saved KV state of a session that matches a query."),
    ("save_kv_state", "Save per-chunk KV state for a session."),
    ("rollback", "Roll a session back to an earlier turn."),
    ("prune_chunks", "Drop a session's least relevant chunks to fit a token budget."),
    ("list_sessions", "Sessions in the store, optionally for one agent."),
    ("rebuild_symbols", "Rebuild the symbol graph from the stored chunks."),
    ("stale_chunks", "Chunks whose dependencies changed since they were indexed."),
    ("get_dynamic_symbols", "Registered runtime symbols, optionally for one agent."),
    ("list_agents", "Agents registered across worktrees, with heartbeat status."),
    ("get_notifications", "Files other agents changed since you last checked. limit defaults to 20."),
    ("environment_check", "Stale __pycache__, editable-install mismatches and other environment problems."),
    ("clean_bytecache", "Remove orphaned .pyc files from stale __pycache__ directories."),
    ("bulk_store_embeddings", "Store your own embedding vectors for chunks."),
    ("bulk_store_chunk_agent_notes", "Store agent notes on chunks."),
    ("get_chunk_history", "Version history of chunks across indexing runs."),
    ("batch", "Run several tool calls in one request: operations [{method, params}]."),
    ("stats", "Storage statistics (1.x name; doctor covers it)."),
    ("project_brief", "Largest files and extension counts (1.x name; doctor covers it)."),
    ("annotate", "Create one annotation (1.x name; annotations action=create)."),
    ("get_annotations", "Annotations of a target (1.x name; annotations action=get)."),
    ("delete_annotation", "Delete an annotation (1.x name; annotations action=delete)."),
    ("update_annotation", "Update an annotation (1.x name; annotations action=update)."),
    ("search_annotations", "Search annotations (1.x name; annotations action=search)."),
    ("bulk_annotate", "Create many annotations (1.x name; annotations action=bulk_create)."),
    ("acquire_document_lock", "Acquire a lock (1.x name; document_lock action=acquire)."),
    ("refresh_document_lock", "Refresh a lock (1.x name; document_lock action=refresh)."),
    ("release_document_lock", "Release a lock (1.x name; document_lock action=release)."),
    ("get_document_lock_status", "Lock status (1.x name; document_lock action=status)."),
    ("release_agent_locks", "Release an agent's locks (1.x name; document_lock action=release_all)."),
    ("get_conflicts", "Lock conflicts (1.x name; document_lock action=conflicts)."),
    ("reap_expired_locks", "Reap expired locks (1.x name; document_lock action=reap)."),
    ("store_semantic_summary", "Store one chunk summary (1.x name; bulk_store_summaries)."),
    ("store_embedding", "Store one chunk vector (1.x name; bulk_store_embeddings)."),
    ("store_chunk_agent_notes", "Store notes on one chunk (1.x name; bulk_store_chunk_agent_notes)."),
];

/// Closed value sets, as 1.5.2's schemas list them.
const ENUMS: &[(&str, &str, &[&str])] = &[
    (
        "agent_grep",
        "group_by",
        &["file", "scope", "classification"],
    ),
    (
        "annotations",
        "action",
        &["create", "get", "delete", "update", "search", "bulk_create"],
    ),
    ("annotations", "target_type", &["document", "chunk"]),
    ("annotate", "target_type", &["document", "chunk"]),
    ("get_annotations", "target_type", &["document", "chunk"]),
    ("coupling", "mode", &["edges", "co_consumers"]),
    (
        "document_lock",
        "action",
        &[
            "acquire",
            "release",
            "refresh",
            "status",
            "release_all",
            "reap",
            "conflicts",
        ],
    ),
    (
        "impact_radius",
        "direction",
        &["dependents", "dependencies", "both"],
    ),
    ("query", "search_mode", &["hybrid", "keyword"]),
    ("search", "search_mode", &["hybrid", "keyword"]),
];

pub(crate) fn describe(tool: &str) -> &'static str {
    DESCRIPTIONS
        .iter()
        .find(|(name, _)| *name == tool)
        .map(|(_, text)| *text)
        .unwrap_or("")
}

fn enum_of(tool: &str, param: &str) -> Option<&'static [&'static str]> {
    ENUMS
        .iter()
        .find(|(name, key, _)| *name == tool && *key == param)
        .map(|(_, _, values)| *values)
}

/// JSON Schema for one argument type of the table.
fn schema_of(kind: &str) -> crate::json::Value {
    use crate::json::Value;
    let item = |name: &str| {
        Value::object(vec![
            ("type", Value::string("array")),
            ("items", Value::object(vec![("type", Value::string(name))])),
        ])
    };
    match kind {
        "list of strings" => item("string"),
        "list of objects" => item("object"),
        "list of numbers" => item("number"),
        "list" => Value::object(vec![("type", Value::string("array"))]),
        other => Value::object(vec![("type", Value::string(other))]),
    }
}

/// The `tools/list` entry of one tool.
pub(crate) fn entry(tool: &str) -> crate::json::Value {
    use crate::json::Value;
    let params = super::signature::params(tool).unwrap_or(&[]);
    let mut properties = Vec::new();
    let mut required = Vec::new();
    for (name, needed, kind) in params {
        let mut schema = schema_of(kind);
        if let (Some(values), Value::Object(pairs)) = (enum_of(tool, name), &mut schema) {
            pairs.push((
                "enum".to_string(),
                Value::Array(values.iter().map(|value| Value::string(*value)).collect()),
            ));
        }
        properties.push(((*name).to_string(), schema));
        // The server fills in its own agent_id for write tools.
        if *needed && !(*name == "agent_id" && super::signature::injects_agent(tool)) {
            required.push(Value::string(*name));
        }
    }
    let mut schema = vec![
        ("type".to_string(), Value::string("object")),
        ("properties".to_string(), Value::Object(properties)),
    ];
    if !required.is_empty() {
        schema.push(("required".to_string(), Value::Array(required)));
    }
    Value::object(vec![
        ("name", Value::string(tool)),
        ("description", Value::string(describe(tool))),
        ("inputSchema", Value::Object(schema)),
    ])
}

/// The `tools/list` result for these tools.
pub(crate) fn document(tools: &[&str]) -> String {
    use crate::json::Value;
    crate::json::to_string(&Value::object(vec![(
        "tools",
        Value::Array(tools.iter().map(|tool| entry(tool)).collect()),
    )]))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn modes() -> Vec<(&'static str, Vec<&'static str>, &'static str)> {
        vec![
            (
                "tools_lite.json",
                super::super::mode_names("lite"),
                include_str!("tools_lite.json"),
            ),
            (
                "tools_standard.json",
                super::super::mode_names("standard"),
                include_str!("tools_standard.json"),
            ),
            (
                "tools_full.json",
                super::super::mode_names("full"),
                include_str!("tools_full.json"),
            ),
        ]
    }

    /// `STELE_DUMP_TOOLS=1 cargo test catalog` rewrites the three files.
    #[test]
    fn committed_tool_lists_match_the_argument_table() {
        for (file, names, committed) in modes() {
            let generated = document(&names);
            if std::env::var("STELE_DUMP_TOOLS").is_ok() {
                let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("src/mcp")
                    .join(file);
                std::fs::write(path, &generated).unwrap();
                continue;
            }
            assert_eq!(
                committed, generated,
                "{file} is stale: run STELE_DUMP_TOOLS=1 cargo test catalog"
            );
        }
    }

    #[test]
    fn every_tool_has_a_description_and_a_schema() {
        for name in super::super::mode_names("full") {
            assert!(!describe(name).is_empty(), "{name}");
            assert!(super::super::signature::params(name).is_some(), "{name}");
        }
        for (tool, param, _) in ENUMS {
            assert!(
                super::super::signature::params(tool)
                    .unwrap()
                    .iter()
                    .any(|(name, _, _)| name == param),
                "{tool}.{param}"
            );
        }
    }

    #[test]
    fn schemas_name_the_required_arguments() {
        let entry = crate::json::to_string(&entry("find_definition"));
        assert!(entry.contains(r#""required":["symbol"]"#), "{entry}");
        let lock = crate::json::to_string(&entry_of("document_lock"));
        assert!(
            lock.contains(r#""action":{"type":"string","enum":["acquire""#),
            "{lock}"
        );
        // index fills in agent_id itself, so only paths is required.
        let index = crate::json::to_string(&entry_of("index"));
        assert!(index.contains(r#""required":["paths"]"#), "{index}");
    }

    fn entry_of(tool: &str) -> crate::json::Value {
        entry(tool)
    }
}
