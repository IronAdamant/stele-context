//! Argument checks for the MCP and HTTP tool calls.
//!
//! The table is the parameter list of every full-mode tool in 1.5.2 (from
//! `inspect.signature` of the PyPI wheel's callables), so 2.0 accepts and
//! rejects the same argument names. Rows marked 2.2 add the `full` and
//! `limit` switches of the compact defaults (MIGRATION.md). `catalog`
//! builds the tools/list schemas from this table. 1.5.2 let Python raise `TypeError` with
//! the method's internal name (`_IndexMixin.llm_embed() missing 1 required
//! positional argument: 'chunk_id'`). 2.0 answers the same calls with a
//! short error that names the tool, the field, what it expects, and the
//! arguments to retry with, plus a "Did you mean" for a near miss.

struct Sig {
    tool: &'static str,
    /// The tool takes any extra keyword (`**kwargs`).
    var_kw: bool,
    /// (name, required, expected type).
    params: &'static [(&'static str, bool, &'static str)],
}

/// 1.5.2 `WRITE_TOOLS`. The server fills in its own `agent_id` for these
/// when the call has none and the tool takes one.
const WRITE_TOOLS: &[&str] = &[
    "annotations",
    "batch",
    "bulk_store_chunk_agent_notes",
    "bulk_store_embeddings",
    "bulk_store_summaries",
    "detect_changes",
    "document_lock",
    "index",
    "llm_embed",
    "register_dynamic_symbols",
    "remove",
    "remove_dynamic_symbols",
    "save_kv_state",
];

const SIGS: &[Sig] = &[
    Sig {
        tool: "acquire_document_lock",
        var_kw: false,
        params: &[
            ("document_path", true, "string"),
            ("agent_id", true, "string"),
            ("ttl", false, "number"),
            ("force", false, "boolean"),
        ],
    },
    Sig {
        tool: "agent_grep",
        var_kw: false,
        params: &[
            ("pattern", true, "string"),
            ("regex", false, "boolean"),
            ("document_path", false, "string"),
            ("classify", false, "boolean"),
            ("include_scope", false, "boolean"),
            ("group_by", false, "string"),
            ("max_tokens", false, "integer"),
            ("deduplicate", false, "boolean"),
            ("context_lines", false, "integer"),
            ("session_id", false, "string"),
            ("working_tree", false, "boolean"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "annotate",
        var_kw: false,
        params: &[
            ("target", true, "string"),
            ("target_type", true, "string"),
            ("content", true, "string"),
            ("tags", false, "list of strings"),
        ],
    },
    Sig {
        tool: "annotations",
        var_kw: false,
        params: &[
            ("action", true, "string"),
            ("target", false, "string"),
            ("target_type", false, "string"),
            ("content", false, "string"),
            ("tags", false, "list of strings"),
            ("annotation_id", false, "integer"),
            ("query", false, "string"),
            ("items", false, "list of objects"),
        ],
    },
    Sig {
        tool: "batch",
        var_kw: false,
        params: &[("operations", true, "list of objects")],
    },
    Sig {
        tool: "bulk_annotate",
        var_kw: false,
        params: &[("annotations", true, "list of objects")],
    },
    Sig {
        tool: "bulk_store_chunk_agent_notes",
        var_kw: false,
        params: &[("notes", true, "object of chunk_id to string")],
    },
    Sig {
        tool: "bulk_store_embeddings",
        var_kw: false,
        params: &[("embeddings", true, "object of chunk_id to number list")],
    },
    Sig {
        tool: "bulk_store_summaries",
        var_kw: false,
        params: &[("summaries", true, "object of chunk_id to string")],
    },
    Sig {
        tool: "clean_bytecache",
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "coupling",
        var_kw: false,
        params: &[
            ("document_path", true, "string"),
            ("significance_threshold", false, "number"),
            ("exclude_symbols", false, "list of strings"),
            ("mode", false, "string"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "delete_annotation",
        var_kw: false,
        params: &[("annotation_id", true, "integer")],
    },
    Sig {
        tool: "detect_changes",
        var_kw: false,
        params: &[
            ("session_id", false, "string"),
            ("document_paths", false, "list of strings"),
            ("reason", false, "string"),
            ("agent_id", false, "string"),
            ("scan_new", false, "boolean"),
            ("limit", false, "integer"),
            // 2.2 (MIGRATION.md).
            ("full", false, "boolean"),
        ],
    },
    Sig {
        tool: "detect_modality",
        var_kw: true,
        params: &[("path", false, "string")],
    },
    Sig {
        tool: "doctor",
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "document_lock",
        var_kw: false,
        params: &[
            ("action", true, "string"),
            ("document_path", false, "string"),
            ("agent_id", false, "string"),
            ("ttl", false, "number"),
            ("force", false, "boolean"),
            ("limit", false, "integer"),
        ],
    },
    Sig {
        tool: "enrichment_plan",
        var_kw: false,
        params: &[
            ("top_n", false, "integer"),
            ("min_tokens", false, "integer"),
        ],
    },
    Sig {
        tool: "environment_check",
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "find_definition",
        var_kw: false,
        // 2.2: `full` and `limit` (MIGRATION.md).
        params: &[
            ("symbol", true, "string"),
            ("full", false, "boolean"),
            ("limit", false, "integer"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "find_references",
        var_kw: false,
        // 2.3: `all_languages` and `full` (MIGRATION.md).
        params: &[
            ("symbol", true, "string"),
            ("all_languages", false, "boolean"),
            ("full", false, "boolean"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "get_annotations",
        var_kw: false,
        params: &[
            ("target", false, "string"),
            ("target_type", false, "string"),
            ("tags", false, "list of strings"),
        ],
    },
    Sig {
        tool: "get_chunk_history",
        var_kw: false,
        params: &[
            ("chunk_id", false, "string"),
            ("document_path", false, "string"),
            ("limit", false, "integer"),
        ],
    },
    Sig {
        tool: "get_conflicts",
        var_kw: false,
        params: &[
            ("document_path", false, "string"),
            ("agent_id", false, "string"),
            ("limit", false, "integer"),
        ],
    },
    Sig {
        tool: "get_context",
        var_kw: false,
        params: &[
            ("document_paths", true, "list of strings"),
            ("session_id", false, "string"),
            ("include_trust", false, "boolean"),
            ("max_chunk_content_tokens", false, "integer"),
            ("include_diff", false, "boolean"),
            ("max_diff_tokens", false, "integer"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "get_document_lock_status",
        var_kw: false,
        params: &[("document_path", true, "string")],
    },
    Sig {
        tool: "get_dynamic_symbols",
        var_kw: false,
        params: &[("agent_id", false, "string")],
    },
    Sig {
        tool: "get_notifications",
        var_kw: false,
        params: &[
            ("since", false, "number"),
            ("exclude_self", false, "string"),
            ("limit", false, "integer"),
            // 2.2 (MIGRATION.md).
            ("full", false, "boolean"),
        ],
    },
    Sig {
        tool: "get_relevant_kv",
        var_kw: false,
        params: &[
            ("session_id", true, "string"),
            ("query", true, "string"),
            ("top_k", false, "integer"),
        ],
    },
    Sig {
        tool: "get_search_history",
        var_kw: false,
        params: &[("session_id", true, "string")],
    },
    Sig {
        tool: "get_session_read_files",
        var_kw: false,
        params: &[("session_id", true, "string")],
    },
    Sig {
        tool: "get_supported_formats",
        var_kw: true,
        params: &[],
    },
    Sig {
        tool: "history",
        var_kw: false,
        params: &[
            ("limit", false, "integer"),
            ("document_path", false, "string"),
            // 2.2 (MIGRATION.md).
            ("full", false, "boolean"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "impact_radius",
        var_kw: false,
        params: &[
            ("chunk_id", false, "string"),
            ("depth", false, "integer"),
            ("document_path", false, "string"),
            ("symbol", false, "string"),
            ("compact", false, "boolean"),
            ("include_content", false, "boolean"),
            ("path_filter", false, "string"),
            ("summary_mode", false, "boolean"),
            ("top_n_files", false, "integer"),
            ("significance_threshold", false, "number"),
            ("exclude_symbols", false, "list of strings"),
            ("direction", false, "string"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "index",
        var_kw: false,
        params: &[
            ("paths", true, "list of strings"),
            ("force_reindex", false, "boolean"),
            ("agent_id", false, "string"),
            ("expected_versions", false, "object"),
            ("summaries", false, "object"),
            // 2.2 (MIGRATION.md).
            ("full", false, "boolean"),
        ],
    },
    Sig {
        tool: "list_agents",
        var_kw: false,
        params: &[("active_only", false, "boolean")],
    },
    Sig {
        tool: "list_sessions",
        var_kw: false,
        params: &[("agent_id", false, "string")],
    },
    Sig {
        tool: "llm_embed",
        var_kw: false,
        params: &[
            ("text", true, "string"),
            ("chunk_id", true, "string"),
            ("fingerprint_values", false, "list of numbers"),
            ("agent_id", false, "string"),
        ],
    },
    Sig {
        tool: "map",
        var_kw: false,
        params: &[
            ("compact", false, "boolean"),
            ("max_documents", false, "integer"),
            ("max_annotation_chars", false, "integer"),
            ("path_prefix", false, "string"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "project_brief",
        var_kw: false,
        params: &[("top_n", false, "integer")],
    },
    Sig {
        tool: "prune_chunks",
        var_kw: false,
        params: &[
            ("session_id", true, "string"),
            ("max_tokens", true, "integer"),
        ],
    },
    Sig {
        tool: "prune_history",
        var_kw: false,
        params: &[
            ("max_age_seconds", false, "number"),
            ("max_entries", false, "integer"),
        ],
    },
    Sig {
        tool: "query",
        var_kw: false,
        params: &[
            ("query", true, "string"),
            ("top_k", false, "integer"),
            ("path_prefix", false, "string"),
            ("working_tree", false, "boolean"),
            ("session_id", false, "string"),
            ("search_mode", false, "string"),
            ("compact", false, "boolean"),
            ("max_result_tokens", false, "integer"),
            // 2.4
            ("definitions_first", false, "boolean"),
            ("include_edges", false, "boolean"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "reap_expired_locks",
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "rebuild_symbols",
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "refresh_document_lock",
        var_kw: false,
        params: &[
            ("document_path", true, "string"),
            ("agent_id", true, "string"),
            ("ttl", false, "number"),
        ],
    },
    Sig {
        tool: "register_dynamic_symbols",
        var_kw: false,
        params: &[
            ("symbols", true, "list of objects"),
            ("agent_id", true, "string"),
        ],
    },
    Sig {
        tool: "release_agent_locks",
        var_kw: false,
        params: &[("agent_id", true, "string")],
    },
    Sig {
        tool: "release_document_lock",
        var_kw: false,
        params: &[
            ("document_path", true, "string"),
            ("agent_id", true, "string"),
        ],
    },
    Sig {
        tool: "remove",
        var_kw: false,
        params: &[
            ("document_path", true, "string"),
            ("agent_id", false, "string"),
        ],
    },
    Sig {
        tool: "remove_dynamic_symbols",
        var_kw: false,
        params: &[("agent_id", true, "string")],
    },
    Sig {
        tool: "rollback",
        var_kw: false,
        params: &[
            ("session_id", true, "string"),
            ("target_turn", true, "integer"),
        ],
    },
    Sig {
        tool: "save_kv_state",
        var_kw: false,
        params: &[
            ("session_id", true, "string"),
            ("kv_data", true, "object"),
            ("chunk_ids", false, "list of strings"),
            ("agent_id", false, "string"),
        ],
    },
    Sig {
        tool: "search",
        var_kw: false,
        params: &[
            ("query", true, "string"),
            ("top_k", false, "integer"),
            ("search_mode", false, "string"),
            ("max_result_tokens", false, "integer"),
            ("compact", false, "boolean"),
            ("return_response_meta", false, "boolean"),
            ("path_prefix", false, "string"),
            ("working_tree", false, "boolean"),
            ("session_id", false, "string"),
            // 2.4
            ("definitions_first", false, "boolean"),
            ("include_edges", false, "boolean"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "search_annotations",
        var_kw: false,
        params: &[("query", true, "string"), ("target_type", false, "string")],
    },
    Sig {
        tool: "search_text",
        var_kw: false,
        params: &[
            ("pattern", true, "string"),
            ("regex", false, "boolean"),
            ("document_path", false, "string"),
            ("limit", false, "integer"),
            ("session_id", false, "string"),
            ("working_tree", false, "boolean"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "stale_chunks",
        var_kw: false,
        params: &[
            ("threshold", false, "number"),
            ("max_age_seconds", false, "number"),
            // 2.4: answer for this git worktree (base index + overlay).
            ("worktree_root", false, "string"),
        ],
    },
    Sig {
        tool: "stats",
        var_kw: false,
        params: &[("compact", false, "boolean")],
    },
    Sig {
        tool: "store_chunk_agent_notes",
        var_kw: false,
        params: &[("chunk_id", true, "string"), ("notes", true, "string")],
    },
    Sig {
        tool: "store_embedding",
        var_kw: false,
        params: &[
            ("chunk_id", true, "string"),
            ("vector", true, "list of numbers"),
        ],
    },
    Sig {
        tool: "store_semantic_summary",
        var_kw: false,
        params: &[("chunk_id", true, "string"), ("summary", true, "string")],
    },
    Sig {
        tool: "update_annotation",
        var_kw: false,
        params: &[
            ("annotation_id", true, "integer"),
            ("content", false, "string"),
            ("tags", false, "list of strings"),
        ],
    },
];

fn sig(tool: &str) -> Option<&'static Sig> {
    SIGS.iter().find(|row| row.tool == tool)
}

/// (name, required, type) of each argument the tool takes.
#[cfg(test)]
pub(crate) fn params(tool: &str) -> Option<&'static [(&'static str, bool, &'static str)]> {
    sig(tool).map(|row| row.params)
}

/// The server fills `agent_id` for this tool when the caller leaves it out.
pub(crate) fn injects_agent(tool: &str) -> bool {
    WRITE_TOOLS.contains(&tool)
        && sig(tool)
            .map(|row| row.params.iter().any(|(name, _, _)| *name == "agent_id"))
            .unwrap_or(false)
}

/// Check `keys` (the argument names, in call order) against the tool's
/// parameters. `alias` is an extra accepted name (`store_embedding`'s
/// `embedding`, which stands in for `vector`). `inject` is set where the
/// server adds its own `agent_id` (stdio and HTTP, not `batch`).
pub(crate) fn bind(
    tool: &str,
    keys: &[&str],
    alias: Option<&str>,
    inject: bool,
) -> Result<(), String> {
    let Some(row) = sig(tool) else {
        return Ok(());
    };
    let mut bound: Vec<&str> = Vec::new();
    for key in keys {
        if alias == Some(*key) {
            bound.push(if *key == "embedding" { "vector" } else { key });
            continue;
        }
        if row.params.iter().any(|(name, _, _)| name == key) {
            bound.push(key);
            continue;
        }
        if row.var_kw {
            continue;
        }
        let names: Vec<&str> = row.params.iter().map(|(name, _, _)| *name).collect();
        let near = suggestion(&names, key)
            .map(|hint| format!(" Did you mean '{hint}'?"))
            .unwrap_or_default();
        return Err(format!(
            "{tool}: unknown argument '{key}'.{near} Valid arguments: {}.",
            argument_list(row)
        ));
    }
    if inject && injects_agent(tool) && !bound.contains(&"agent_id") {
        bound.push("agent_id");
    }
    let missing: Vec<&(&str, bool, &str)> = row
        .params
        .iter()
        .filter(|(name, required, _)| *required && !bound.contains(name))
        .collect();
    if missing.is_empty() {
        return Ok(());
    }
    let names: Vec<&str> = missing.iter().map(|(name, _, _)| *name).collect();
    let kinds: Vec<String> = missing
        .iter()
        .map(|(name, _, kind)| {
            if missing.len() == 1 {
                (*kind).to_string()
            } else {
                format!("{name}: {kind}")
            }
        })
        .collect();
    let required: Vec<&str> = row
        .params
        .iter()
        .filter(|(_, required, _)| *required)
        .map(|(name, _, _)| *name)
        .collect();
    let optional: Vec<&str> = row
        .params
        .iter()
        .filter(|(_, required, _)| !*required)
        .map(|(name, _, _)| *name)
        .collect();
    let mut message = format!(
        "{tool}: {} {} required ({}). Required: {}.",
        names.join(" and "),
        if names.len() == 1 { "is" } else { "are" },
        kinds.join(", "),
        required.join(", ")
    );
    if !optional.is_empty() {
        message.push_str(&format!(" Optional: {}.", optional.join(", ")));
    }
    Err(message)
}

fn argument_list(row: &Sig) -> String {
    row.params
        .iter()
        .map(|(name, required, _)| {
            if *required {
                format!("{name} (required)")
            } else {
                (*name).to_string()
            }
        })
        .collect::<Vec<_>>()
        .join(", ")
}

const MOVE_COST: usize = 2;
const CASE_COST: usize = 1;
const MAX_STRING_SIZE: usize = 40;

fn substitution_cost(a: u8, b: u8) -> usize {
    if (a & 31) != (b & 31) {
        return MOVE_COST;
    }
    if a == b {
        return 0;
    }
    if a.to_ascii_lowercase() == b.to_ascii_lowercase() {
        CASE_COST
    } else {
        MOVE_COST
    }
}

/// CPython's `suggestions.c` edit distance (the one behind "Did you mean").
fn levenshtein(a: &[u8], b: &[u8], max_cost: usize) -> usize {
    let mut a = a;
    let mut b = b;
    while !a.is_empty() && !b.is_empty() && a[0] == b[0] {
        a = &a[1..];
        b = &b[1..];
    }
    while !a.is_empty() && !b.is_empty() && a[a.len() - 1] == b[b.len() - 1] {
        a = &a[..a.len() - 1];
        b = &b[..b.len() - 1];
    }
    if a.is_empty() || b.is_empty() {
        return (a.len() + b.len()) * MOVE_COST;
    }
    if a.len() > MAX_STRING_SIZE || b.len() > MAX_STRING_SIZE {
        return max_cost + 1;
    }
    if b.len() < a.len() {
        std::mem::swap(&mut a, &mut b);
    }
    if (b.len() - a.len()) * MOVE_COST > max_cost {
        return max_cost + 1;
    }
    let mut buffer: Vec<usize> = (1..=a.len()).map(|i| i * MOVE_COST).collect();
    let mut result = 0usize;
    for (b_index, code) in b.iter().enumerate() {
        let mut distance = b_index * MOVE_COST;
        result = distance;
        let mut minimum = usize::MAX;
        for (index, item) in a.iter().enumerate() {
            let substitute = distance + substitution_cost(*code, *item);
            distance = buffer[index];
            let insert_delete = result.min(distance) + MOVE_COST;
            result = insert_delete.min(substitute);
            buffer[index] = result;
            if result < minimum {
                minimum = result;
            }
        }
        if minimum > max_cost {
            return max_cost + 1;
        }
    }
    result
}

/// The closest parameter name, if it is within a third of the characters.
pub(crate) fn suggestion<'a>(candidates: &[&'a str], name: &str) -> Option<&'a str> {
    let mut best: Option<&'a str> = None;
    let mut best_distance = usize::MAX;
    for item in candidates {
        if *item == name {
            continue;
        }
        let mut max_distance = (name.len() + item.len() + 3) * MOVE_COST / 6;
        max_distance = max_distance.min(best_distance.saturating_sub(1));
        let distance = levenshtein(name.as_bytes(), item.as_bytes(), max_distance);
        if distance > max_distance {
            continue;
        }
        if best.is_none() || distance < best_distance {
            best = Some(item);
            best_distance = distance;
        }
    }
    best
}

#[cfg(test)]
mod tests {
    use super::*;

    /// (tool, argument names). The same names are bound against the 1.5.2
    /// callables: 2.0 must accept exactly what 1.5.2 accepts, and reject
    /// the rest with a message that names the tool.
    const CASES: &[(&str, &[&str])] = &[
        ("llm_embed", &["text"]),
        ("llm_embed", &["text", "chunk_id"]),
        ("llm_embed", &["text", "fingerprint_values"]),
        ("llm_embed", &[]),
        ("agent_grep", &["pattern", "path_prefix"]),
        ("agent_grep", &["pattern", "context_lines", "session_id"]),
        ("query", &["text"]),
        ("query", &["querry"]),
        ("query", &["query", "top_k", "path_prefix", "compact"]),
        ("search", &["query", "serch_mode"]),
        ("get_relevant_kv", &[]),
        ("get_context", &["path"]),
        ("get_context", &["document_paths", "session_id"]),
        ("store_embedding", &["chunk_id", "vector"]),
        ("store_embedding", &["chunk_id"]),
        ("bulk_annotate", &["items"]),
        ("find_definition", &["self"]),
        ("map", &["path_prefix", "compact", "depth"]),
        ("detect_modality", &["path", "anything"]),
        ("index", &["paths", "path"]),
        ("rollback", &[]),
        ("annotate", &["target"]),
        ("doctor", &[]),
    ];

    #[test]
    fn accepts_and_rejects_the_same_calls_as_python_152() {
        let script = r#"
import inspect, json, sys, tempfile
from stele_context.engine import Stele
from stele_context.tool_registry import build_tool_map, get_modality_flags
d = tempfile.mkdtemp()
tm = build_tool_map(Stele(storage_dir=d, project_root=d), get_modality_flags(), mode="full")
out = []
for tool, keys in json.loads(sys.stdin.read()):
    try:
        inspect.signature(tm[tool]).bind(**{k: None for k in keys})
        out.append(True)
    except TypeError:
        out.append(False)
print(json.dumps(out))
"#;
        let cases: Vec<crate::json::Value> = CASES
            .iter()
            .map(|(tool, keys)| {
                crate::json::Value::Array(vec![
                    crate::json::Value::string(*tool),
                    crate::json::Value::Array(
                        keys.iter()
                            .map(|key| crate::json::Value::string(*key))
                            .collect(),
                    ),
                ])
            })
            .collect();
        let mut child = std::process::Command::new(crate::oracle::reference())
            .args(["-c", script])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .expect("python");
        {
            use std::io::Write;
            let body = crate::json::to_string(&crate::json::Value::Array(cases));
            child
                .stdin
                .take()
                .unwrap()
                .write_all(body.as_bytes())
                .unwrap();
        }
        let done = child.wait_with_output().unwrap();
        assert!(
            done.status.success(),
            "{}",
            String::from_utf8_lossy(&done.stderr)
        );
        let parsed = crate::json::parse(String::from_utf8_lossy(&done.stdout).trim()).unwrap();
        let crate::json::Value::Array(rows) = parsed else {
            panic!("list")
        };
        for ((tool, keys), python) in CASES.iter().zip(rows) {
            let accepted = matches!(python, crate::json::Value::Bool(true));
            let ours = bind(tool, keys, None, false);
            assert_eq!(ours.is_ok(), accepted, "{tool} {keys:?}: {ours:?}");
            if let Err(message) = ours {
                assert!(message.starts_with(&format!("{tool}: ")), "{message}");
            }
        }
    }

    #[test]
    fn errors_name_the_field_and_the_retry() {
        assert_eq!(
            bind("llm_embed", &["text"], None, true).unwrap_err(),
            "llm_embed: chunk_id is required (string). Required: text, chunk_id. Optional: fingerprint_values, agent_id."
        );
        let unknown = bind("agent_grep", &["pattern", "path_prefix"], None, true).unwrap_err();
        assert!(unknown.starts_with("agent_grep: unknown argument 'path_prefix'."));
        assert!(unknown.contains("Valid arguments: pattern (required), regex, document_path"));
        assert_eq!(
            bind("query", &["querry"], None, true).unwrap_err(),
            "query: unknown argument 'querry'. Did you mean 'query'? Valid arguments: query (required), top_k, path_prefix, working_tree, session_id, search_mode, compact, max_result_tokens, definitions_first, include_edges, worktree_root."
        );
        // The accepted alias stands in for `vector`.
        assert!(bind(
            "store_embedding",
            &["chunk_id", "embedding"],
            Some("embedding"),
            true
        )
        .is_ok());
        assert!(bind("register_dynamic_symbols", &["symbols"], None, true).is_ok());
        assert!(bind("register_dynamic_symbols", &["symbols"], None, false)
            .unwrap_err()
            .starts_with("register_dynamic_symbols: agent_id is required"));
        assert!(!injects_agent("annotations"));
    }
}
