//! Argument binding of the 1.5.2 tool callables.
//!
//! 1.5.2's stdio and HTTP servers call `tool_map[name](**arguments)`. Python
//! binds the keywords, and a name the function does not take, or a required
//! parameter that is missing, raises `TypeError` before the tool runs. The
//! table below is `inspect.signature` of every full-mode callable in the PyPI
//! 1.5.2 wheel. The messages follow CPython 3.13 (`ceval.c`
//! `initialize_locals` and `suggestions.c`), including the
//! "Did you mean" suggestion for a misspelt keyword.

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    /// Positional-or-keyword.
    P,
    /// Keyword-only (after `*`).
    K,
}

use Kind::{K, P};

struct Sig {
    tool: &'static str,
    qual: &'static str,
    /// Bound method: `self` is the first parameter of the code object.
    method: bool,
    /// The callable takes `**kwargs`.
    var_kw: bool,
    params: &'static [(&'static str, Kind, bool)],
}

/// 1.5.2 `WRITE_TOOLS`. The server injects its agent id into these when the
/// call has no `agent_id` and the callable takes one.
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
        qual: "_LockMixin.acquire_document_lock",
        method: true,
        var_kw: false,
        params: &[
            ("document_path", P, true),
            ("agent_id", P, true),
            ("ttl", P, false),
            ("force", P, false),
        ],
    },
    Sig {
        tool: "agent_grep",
        qual: "_SearchMixin.agent_grep",
        method: true,
        var_kw: false,
        params: &[
            ("pattern", P, true),
            ("regex", P, false),
            ("document_path", P, false),
            ("classify", P, false),
            ("include_scope", P, false),
            ("group_by", P, false),
            ("max_tokens", P, false),
            ("deduplicate", P, false),
            ("context_lines", P, false),
            ("session_id", P, false),
            ("working_tree", P, false),
        ],
    },
    Sig {
        tool: "annotate",
        qual: "_IndexMixin.annotate",
        method: true,
        var_kw: false,
        params: &[
            ("target", P, true),
            ("target_type", P, true),
            ("content", P, true),
            ("tags", P, false),
        ],
    },
    Sig {
        tool: "annotations",
        qual: "_IndexMixin.annotations",
        method: true,
        var_kw: false,
        params: &[
            ("action", P, true),
            ("target", P, false),
            ("target_type", P, false),
            ("content", P, false),
            ("tags", P, false),
            ("annotation_id", P, false),
            ("query", P, false),
            ("items", P, false),
        ],
    },
    Sig {
        tool: "batch",
        qual: "Stele.batch",
        method: true,
        var_kw: false,
        params: &[("operations", P, true)],
    },
    Sig {
        tool: "bulk_annotate",
        qual: "_IndexMixin.bulk_annotate",
        method: true,
        var_kw: false,
        params: &[("annotations", P, true)],
    },
    Sig {
        tool: "bulk_store_chunk_agent_notes",
        qual: "_IndexMixin.bulk_store_chunk_agent_notes",
        method: true,
        var_kw: false,
        params: &[("notes", P, true)],
    },
    Sig {
        tool: "bulk_store_embeddings",
        qual: "_IndexMixin.bulk_store_embeddings",
        method: true,
        var_kw: false,
        params: &[("embeddings", P, true)],
    },
    Sig {
        tool: "bulk_store_summaries",
        qual: "_IndexMixin.bulk_store_summaries",
        method: true,
        var_kw: false,
        params: &[("summaries", P, true)],
    },
    Sig {
        tool: "clean_bytecache",
        qual: "_InfoMixin.clean_bytecache",
        method: true,
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "coupling",
        qual: "_SymbolMixin.coupling",
        method: true,
        var_kw: false,
        params: &[
            ("document_path", P, true),
            ("significance_threshold", P, false),
            ("exclude_symbols", P, false),
            ("mode", P, false),
        ],
    },
    Sig {
        tool: "delete_annotation",
        qual: "_IndexMixin.delete_annotation",
        method: true,
        var_kw: false,
        params: &[("annotation_id", P, true)],
    },
    Sig {
        tool: "detect_changes",
        qual: "_IndexMixin.detect_changes_and_update",
        method: true,
        var_kw: false,
        params: &[
            ("session_id", P, false),
            ("document_paths", P, false),
            ("reason", P, false),
            ("agent_id", P, false),
            ("scan_new", K, false),
            ("limit", K, false),
        ],
    },
    Sig {
        tool: "detect_modality",
        qual: "build_tool_map.<locals>._detect_modality",
        method: false,
        var_kw: true,
        params: &[("path", P, false)],
    },
    Sig {
        tool: "doctor",
        qual: "_InfoMixin.doctor_snapshot",
        method: true,
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "document_lock",
        qual: "_LockMixin.document_lock",
        method: true,
        var_kw: false,
        params: &[
            ("action", P, true),
            ("document_path", P, false),
            ("agent_id", P, false),
            ("ttl", P, false),
            ("force", P, false),
            ("limit", P, false),
        ],
    },
    Sig {
        tool: "enrichment_plan",
        qual: "_InfoMixin.enrichment_plan",
        method: true,
        var_kw: false,
        params: &[("top_n", P, false), ("min_tokens", P, false)],
    },
    Sig {
        tool: "environment_check",
        qual: "_InfoMixin.check_environment",
        method: true,
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "find_definition",
        qual: "_SymbolMixin.find_definition",
        method: true,
        var_kw: false,
        params: &[("symbol", P, true)],
    },
    Sig {
        tool: "find_references",
        qual: "_SymbolMixin.find_references",
        method: true,
        var_kw: false,
        params: &[("symbol", P, true)],
    },
    Sig {
        tool: "get_annotations",
        qual: "_IndexMixin.get_annotations",
        method: true,
        var_kw: false,
        params: &[
            ("target", P, false),
            ("target_type", P, false),
            ("tags", P, false),
        ],
    },
    Sig {
        tool: "get_chunk_history",
        qual: "_InfoMixin.get_chunk_history",
        method: true,
        var_kw: false,
        params: &[
            ("chunk_id", P, false),
            ("document_path", P, false),
            ("limit", P, false),
        ],
    },
    Sig {
        tool: "get_conflicts",
        qual: "_LockMixin.get_conflicts",
        method: true,
        var_kw: false,
        params: &[
            ("document_path", P, false),
            ("agent_id", P, false),
            ("limit", P, false),
        ],
    },
    Sig {
        tool: "get_context",
        qual: "_SearchMixin.get_context",
        method: true,
        var_kw: false,
        params: &[
            ("document_paths", P, true),
            ("session_id", K, false),
            ("include_trust", K, false),
            ("max_chunk_content_tokens", K, false),
            ("include_diff", K, false),
            ("max_diff_tokens", K, false),
        ],
    },
    Sig {
        tool: "get_document_lock_status",
        qual: "_LockMixin.get_document_lock_status",
        method: true,
        var_kw: false,
        params: &[("document_path", P, true)],
    },
    Sig {
        tool: "get_dynamic_symbols",
        qual: "_SymbolMixin.get_dynamic_symbols",
        method: true,
        var_kw: false,
        params: &[("agent_id", P, false)],
    },
    Sig {
        tool: "get_notifications",
        qual: "_LockMixin.get_notifications",
        method: true,
        var_kw: false,
        params: &[
            ("since", P, false),
            ("exclude_self", P, false),
            ("limit", P, false),
        ],
    },
    Sig {
        tool: "get_relevant_kv",
        qual: "Stele.get_relevant_kv",
        method: true,
        var_kw: false,
        params: &[
            ("session_id", P, true),
            ("query", P, true),
            ("top_k", P, false),
        ],
    },
    Sig {
        tool: "get_search_history",
        qual: "_SearchMixin.get_search_history",
        method: true,
        var_kw: false,
        params: &[("session_id", P, true)],
    },
    Sig {
        tool: "get_session_read_files",
        qual: "_SearchMixin.get_session_read_files",
        method: true,
        var_kw: false,
        params: &[("session_id", P, true)],
    },
    Sig {
        tool: "get_supported_formats",
        qual: "build_tool_map.<locals>._get_supported_formats",
        method: false,
        var_kw: true,
        params: &[],
    },
    Sig {
        tool: "history",
        qual: "_InfoMixin.get_history",
        method: true,
        var_kw: false,
        params: &[("limit", P, false), ("document_path", P, false)],
    },
    Sig {
        tool: "impact_radius",
        qual: "_SymbolMixin.impact_radius",
        method: true,
        var_kw: false,
        params: &[
            ("chunk_id", P, false),
            ("depth", P, false),
            ("document_path", P, false),
            ("symbol", P, false),
            ("compact", K, false),
            ("include_content", K, false),
            ("path_filter", K, false),
            ("summary_mode", K, false),
            ("top_n_files", K, false),
            ("significance_threshold", K, false),
            ("exclude_symbols", K, false),
            ("direction", K, false),
        ],
    },
    Sig {
        tool: "index",
        qual: "_IndexMixin.index_documents",
        method: true,
        var_kw: false,
        params: &[
            ("paths", P, true),
            ("force_reindex", P, false),
            ("agent_id", P, false),
            ("expected_versions", P, false),
            ("summaries", P, false),
        ],
    },
    Sig {
        tool: "list_agents",
        qual: "_LockMixin.list_agents",
        method: true,
        var_kw: false,
        params: &[("active_only", P, false)],
    },
    Sig {
        tool: "list_sessions",
        qual: "_InfoMixin.list_sessions",
        method: true,
        var_kw: false,
        params: &[("agent_id", P, false)],
    },
    Sig {
        tool: "llm_embed",
        qual: "_IndexMixin.llm_embed",
        method: true,
        var_kw: false,
        params: &[
            ("text", P, true),
            ("chunk_id", P, true),
            ("fingerprint_values", P, false),
            ("agent_id", P, false),
        ],
    },
    Sig {
        tool: "map",
        qual: "_InfoMixin.get_map",
        method: true,
        var_kw: false,
        params: &[
            ("compact", K, false),
            ("max_documents", K, false),
            ("max_annotation_chars", K, false),
            ("path_prefix", K, false),
        ],
    },
    Sig {
        tool: "project_brief",
        qual: "_InfoMixin.get_project_brief",
        method: true,
        var_kw: false,
        params: &[("top_n", P, false)],
    },
    Sig {
        tool: "prune_chunks",
        qual: "Stele.prune_chunks",
        method: true,
        var_kw: false,
        params: &[("session_id", P, true), ("max_tokens", P, true)],
    },
    Sig {
        tool: "prune_history",
        qual: "_IndexMixin.prune_history",
        method: true,
        var_kw: false,
        params: &[("max_age_seconds", P, false), ("max_entries", P, false)],
    },
    Sig {
        tool: "query",
        qual: "Stele.query",
        method: true,
        var_kw: false,
        params: &[
            ("query", P, true),
            ("top_k", P, false),
            ("path_prefix", P, false),
            ("working_tree", P, false),
            ("session_id", P, false),
            ("search_mode", P, false),
            ("compact", P, false),
            ("max_result_tokens", P, false),
        ],
    },
    Sig {
        tool: "reap_expired_locks",
        qual: "_LockMixin.reap_expired_locks",
        method: true,
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "rebuild_symbols",
        qual: "_SymbolMixin.rebuild_symbol_graph",
        method: true,
        var_kw: false,
        params: &[],
    },
    Sig {
        tool: "refresh_document_lock",
        qual: "_LockMixin.refresh_document_lock",
        method: true,
        var_kw: false,
        params: &[
            ("document_path", P, true),
            ("agent_id", P, true),
            ("ttl", P, false),
        ],
    },
    Sig {
        tool: "register_dynamic_symbols",
        qual: "_SymbolMixin.register_dynamic_symbols",
        method: true,
        var_kw: false,
        params: &[("symbols", P, true), ("agent_id", P, true)],
    },
    Sig {
        tool: "release_agent_locks",
        qual: "_LockMixin.release_agent_locks",
        method: true,
        var_kw: false,
        params: &[("agent_id", P, true)],
    },
    Sig {
        tool: "release_document_lock",
        qual: "_LockMixin.release_document_lock",
        method: true,
        var_kw: false,
        params: &[("document_path", P, true), ("agent_id", P, true)],
    },
    Sig {
        tool: "remove",
        qual: "_IndexMixin.remove_document",
        method: true,
        var_kw: false,
        params: &[("document_path", P, true), ("agent_id", P, false)],
    },
    Sig {
        tool: "remove_dynamic_symbols",
        qual: "_SymbolMixin.remove_dynamic_symbols",
        method: true,
        var_kw: false,
        params: &[("agent_id", P, true)],
    },
    Sig {
        tool: "rollback",
        qual: "Stele.rollback",
        method: true,
        var_kw: false,
        params: &[("session_id", P, true), ("target_turn", P, true)],
    },
    Sig {
        tool: "save_kv_state",
        qual: "Stele.save_kv_state",
        method: true,
        var_kw: false,
        params: &[
            ("session_id", P, true),
            ("kv_data", P, true),
            ("chunk_ids", P, false),
            ("agent_id", P, false),
        ],
    },
    Sig {
        tool: "search",
        qual: "_SearchMixin.search",
        method: true,
        var_kw: false,
        params: &[
            ("query", P, true),
            ("top_k", P, false),
            ("search_mode", K, false),
            ("max_result_tokens", K, false),
            ("compact", K, false),
            ("return_response_meta", K, false),
            ("path_prefix", K, false),
            ("working_tree", K, false),
            ("session_id", K, false),
        ],
    },
    Sig {
        tool: "search_annotations",
        qual: "_IndexMixin.search_annotations",
        method: true,
        var_kw: false,
        params: &[("query", P, true), ("target_type", P, false)],
    },
    Sig {
        tool: "search_text",
        qual: "_SearchMixin.search_text",
        method: true,
        var_kw: false,
        params: &[
            ("pattern", P, true),
            ("regex", P, false),
            ("document_path", P, false),
            ("limit", P, false),
            ("session_id", P, false),
            ("working_tree", P, false),
        ],
    },
    Sig {
        tool: "stale_chunks",
        qual: "Stele.stale_chunks",
        method: true,
        var_kw: false,
        params: &[("threshold", P, false), ("max_age_seconds", P, false)],
    },
    Sig {
        tool: "stats",
        qual: "_InfoMixin.get_stats",
        method: true,
        var_kw: false,
        params: &[("compact", K, false)],
    },
    Sig {
        tool: "store_chunk_agent_notes",
        qual: "_IndexMixin.store_chunk_agent_notes",
        method: true,
        var_kw: false,
        params: &[("chunk_id", P, true), ("notes", P, true)],
    },
    Sig {
        tool: "store_embedding",
        qual: "_IndexMixin.store_embedding",
        method: true,
        var_kw: false,
        params: &[("chunk_id", P, true), ("vector", P, true)],
    },
    Sig {
        tool: "store_semantic_summary",
        qual: "_IndexMixin.store_semantic_summary",
        method: true,
        var_kw: false,
        params: &[("chunk_id", P, true), ("summary", P, true)],
    },
    Sig {
        tool: "update_annotation",
        qual: "_IndexMixin.update_annotation",
        method: true,
        var_kw: false,
        params: &[
            ("annotation_id", P, true),
            ("content", P, false),
            ("tags", P, false),
        ],
    },
];

fn sig(tool: &str) -> Option<&'static Sig> {
    SIGS.iter().find(|row| row.tool == tool)
}

/// The server fills `agent_id` for this tool when the caller leaves it out.
pub(crate) fn injects_agent(tool: &str) -> bool {
    WRITE_TOOLS.contains(&tool)
        && sig(tool)
            .map(|row| row.params.iter().any(|(name, _, _)| *name == "agent_id"))
            .unwrap_or(false)
}

/// Bind `keys` (the argument names, in call order) the way Python binds
/// `func(**arguments)`. `Err` is the `TypeError` text. `alias` names a key
/// that is accepted on top of the 1.5.2 signature. `inject` is set where the
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
        if row.method && *key == "self" {
            return Err(format!(
                "{}() got multiple values for argument 'self'",
                row.qual
            ));
        }
        if alias == Some(*key) {
            continue;
        }
        if row.params.iter().any(|(name, _, _)| name == key) {
            bound.push(key);
            continue;
        }
        if row.var_kw {
            continue;
        }
        let mut candidates: Vec<&str> = Vec::new();
        if row.method {
            candidates.push("self");
        }
        candidates.extend(row.params.iter().map(|(name, _, _)| *name));
        return Err(match suggestion(&candidates, key) {
            Some(hint) => format!(
                "{}() got an unexpected keyword argument '{}'. Did you mean '{}'?",
                row.qual, key, hint
            ),
            None => format!(
                "{}() got an unexpected keyword argument '{}'",
                row.qual, key
            ),
        });
    }
    if inject && injects_agent(tool) && !bound.contains(&"agent_id") {
        bound.push("agent_id");
    }
    for (kind, label) in [(P, "positional"), (K, "keyword-only")] {
        let missing: Vec<&str> = row
            .params
            .iter()
            .filter(|(name, which, required)| {
                *which == kind && *required && !bound.contains(name) && alias_fills(alias, name)
            })
            .map(|(name, _, _)| *name)
            .collect();
        if !missing.is_empty() {
            return Err(format!(
                "{}() missing {} required {} argument{}: {}",
                row.qual,
                missing.len(),
                label,
                if missing.len() == 1 { "" } else { "s" },
                name_list(&missing)
            ));
        }
    }
    Ok(())
}

/// The accepted `store_embedding` alias stands in for `vector`.
fn alias_fills(alias: Option<&str>, name: &str) -> bool {
    !(alias == Some("embedding") && name == "vector")
}

fn name_list(names: &[&str]) -> String {
    let quoted: Vec<String> = names.iter().map(|name| format!("'{name}'")).collect();
    match quoted.len() {
        0 => String::new(),
        1 => quoted[0].clone(),
        2 => format!("{} and {}", quoted[0], quoted[1]),
        n => format!("{}, and {}", quoted[..n - 1].join(", "), quoted[n - 1]),
    }
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

/// `levenshtein_distance` from CPython `Python/suggestions.c`.
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

/// `_Py_CalculateSuggestions` over the parameter names.
fn suggestion<'a>(candidates: &[&'a str], name: &str) -> Option<&'a str> {
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

    /// (tool, argument names). Each one is also called on the 1.5.2 tool map
    /// and the TypeError text must be the same.
    const CASES: &[(&str, &[&str])] = &[
        ("llm_embed", &["text"]),
        ("llm_embed", &["text", "fingerprint_values"]),
        ("llm_embed", &[]),
        ("agent_grep", &["pattern", "path_prefix"]),
        ("query", &["text"]),
        ("query", &["querry"]),
        ("search", &["query", "serch_mode"]),
        ("get_relevant_kv", &[]),
        ("get_context", &["path"]),
        ("store_embedding", &["chunk_id", "embedding"]),
        ("store_embedding", &["chunk_id"]),
        ("bulk_annotate", &["items"]),
        ("find_definition", &["self"]),
        ("map", &["path_prefix", "compact", "depth"]),
        ("detect_modality", &["path", "anything"]),
        ("index", &["paths", "path"]),
        ("rollback", &[]),
        ("annotate", &["target"]),
    ];

    #[test]
    fn type_errors_match_python_152() {
        let script = r#"
import json, sys, tempfile
from stele_context.engine import Stele
from stele_context.tool_registry import build_tool_map, get_modality_flags
d = tempfile.mkdtemp()
tm = build_tool_map(Stele(storage_dir=d, project_root=d), get_modality_flags(), mode="full")
out = []
for tool, keys in json.loads(sys.stdin.read()):
    # Values never reach the body: binding fails first, or the call is skipped.
    try:
        import inspect
        inspect.signature(tm[tool]).bind(**{k: None for k in keys})
        out.append(None)
        continue
    except TypeError:
        pass
    try:
        tm[tool](**{k: None for k in keys})
        out.append(None)
    except TypeError as exc:
        out.append(str(exc))
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
            // The accepted alias is the one place the binding differs.
            let alias = if *tool == "store_embedding" && keys.contains(&"embedding") {
                Some("embedding")
            } else {
                None
            };
            let ours = bind(tool, keys, alias, false).err();
            let theirs = match python {
                crate::json::Value::String(text) => Some(text),
                _ => None,
            };
            if alias.is_some() {
                assert!(ours.is_none(), "{tool} {keys:?}: alias must bind");
                assert!(
                    theirs
                        .as_deref()
                        .unwrap_or("")
                        .contains("unexpected keyword argument 'embedding'"),
                    "{tool}: 1.5.2 rejects the alias"
                );
                continue;
            }
            assert_eq!(ours, theirs, "{tool} {keys:?}");
        }
    }

    #[test]
    fn server_injects_agent_id_for_write_tools() {
        assert!(bind("register_dynamic_symbols", &["symbols"], None, true).is_ok());
        assert_eq!(
            bind("register_dynamic_symbols", &["symbols"], None, false).unwrap_err(),
            "_SymbolMixin.register_dynamic_symbols() missing 1 required positional argument: 'agent_id'"
        );
        assert!(!injects_agent("annotations"));
    }
}
