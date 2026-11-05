//! 2.2 compact defaults for tool results that ran past 10 KB on a real
//! project (MIGRATION.md lists each one). The MCP and HTTP tools return the
//! short form unless the call asks for `full: true`; the CLI and the engine
//! keep the 1.5.2 shape. Defaults that are plain argument values (search
//! `compact`, search_text `limit`, agent_grep `max_tokens`, map
//! `max_documents`, get_notifications `limit`) are set where the tool is
//! dispatched. agent_grep matches grouped by file drop the repeated
//! `file`, which is the group key.

use crate::json::Value;

/// find_definition rows returned without `limit`.
pub(crate) const DEFINITIONS: usize = 10;
/// index rows listed before `indexed_count` takes over.
pub(crate) const INDEXED_ROWS: usize = 20;
/// Lines and characters of a find_definition preview.
const PREVIEW_LINES: usize = 6;
const PREVIEW_CHARS: usize = 400;
const SIGNATURE_CHARS: usize = 200;

pub(crate) fn wants_full(args: &Value) -> bool {
    matches!(field(args, "full"), Some(Value::Bool(true)))
}

/// Trim the result of `tool` unless the call asked for `full`.
pub(crate) fn apply(tool: &str, args: &Value, value: Value) -> Value {
    let full = wants_full(args);
    match tool {
        "find_definition" => definitions(value, full, limit(args)),
        "index" if !full => index(value),
        "detect_changes" if !full => drop_key(value, "unchanged"),
        "history" if !full => match value {
            Value::Array(rows) => Value::Array(
                rows.into_iter()
                    .map(|row| map_key(row, "summary", |summary| drop_key(summary, "unchanged")))
                    .collect(),
            ),
            other => other,
        },
        "agent_grep" => map_key(value, "groups", |groups| match groups {
            Value::Array(items) => Value::Array(items.into_iter().map(grep_group).collect()),
            other => other,
        }),
        "get_notifications" if !full => map_key(value, "notifications", |rows| match rows {
            Value::Array(items) => Value::Array(
                items
                    .into_iter()
                    .map(|row| drop_key(row, "worktree_root"))
                    .collect(),
            ),
            other => other,
        }),
        _ => value,
    }
}

fn limit(args: &Value) -> Option<usize> {
    match field(args, "limit") {
        Some(Value::Int(number)) if *number > 0 => Some(*number as usize),
        _ => None,
    }
}

/// find_definition: each row keeps where the symbol is and gains its
/// `signature` line and a short `preview`; the chunk `content` (often the
/// whole impl block) only comes back with `full`. Without `full` at most
/// `DEFINITIONS` rows are listed; `limit` sets the cap either way.
fn definitions(value: Value, full: bool, limit: Option<usize>) -> Value {
    let cap = limit.or(if full { None } else { Some(DEFINITIONS) });
    map_object(value, |pairs| {
        let symbol = match pairs.iter().find(|(key, _)| key == "symbol") {
            Some((_, Value::String(text))) => text.clone(),
            _ => String::new(),
        };
        let mut omitted = 0usize;
        for (key, rows) in pairs.iter_mut() {
            if key != "definitions" {
                continue;
            }
            let Value::Array(items) = rows else {
                continue;
            };
            if let Some(cap) = cap {
                if items.len() > cap {
                    omitted = items.len() - cap;
                    items.truncate(cap);
                }
            }
            if full {
                continue;
            }
            for row in items.iter_mut() {
                let Value::Object(fields) = row else {
                    continue;
                };
                let content = match fields.iter().find(|(key, _)| key == "content") {
                    Some((_, Value::String(text))) => text.clone(),
                    _ => String::new(),
                };
                let (signature, preview) = sketch(&content, &symbol);
                fields.retain(|(key, _)| key != "content");
                let at = fields
                    .iter()
                    .position(|(key, _)| key == "line_number")
                    .map(|index| index + 1)
                    .unwrap_or(fields.len());
                fields.insert(at, ("signature".to_string(), Value::string(signature)));
                fields.insert(at + 1, ("preview".to_string(), Value::string(preview)));
            }
        }
        if omitted > 0 {
            pairs.push(("omitted".to_string(), Value::Int(omitted as i64)));
            pairs.push((
                "hint".to_string(),
                Value::string(
                    "More definitions: pass limit (or full=true for all, with chunk text).",
                ),
            ));
        }
    })
}

/// The definition line of `symbol` in a chunk and up to `PREVIEW_LINES`
/// lines from there. The line is the first one that names the symbol as a
/// word outside a comment; a chunk that never names it (a runtime symbol)
/// gives its first line.
pub(crate) fn sketch(content: &str, symbol: &str) -> (String, String) {
    let lines: Vec<&str> = content.lines().collect();
    let at = lines
        .iter()
        .position(|line| !is_comment(line) && names(line, symbol))
        .or_else(|| lines.iter().position(|line| !line.trim().is_empty()))
        .unwrap_or(0);
    let signature = cut(
        lines.get(at).map(|line| line.trim()).unwrap_or(""),
        SIGNATURE_CHARS,
    );
    let preview = cut(
        &lines
            .iter()
            .skip(at)
            .take(PREVIEW_LINES)
            .copied()
            .collect::<Vec<_>>()
            .join("\n"),
        PREVIEW_CHARS,
    );
    (signature, preview)
}

fn is_comment(line: &str) -> bool {
    let text = line.trim_start();
    ["//", "#", "/*", "*", "--", "\"\"\"", "'''", "<!--", ";"]
        .iter()
        .any(|lead| text.starts_with(lead))
}

/// `symbol` appears in `line` with no word character on either side.
fn names(line: &str, symbol: &str) -> bool {
    if symbol.is_empty() {
        return false;
    }
    let word = |ch: Option<char>| ch.is_some_and(|ch| ch.is_alphanumeric() || ch == '_');
    let mut from = 0;
    while let Some(found) = line[from..].find(symbol) {
        let start = from + found;
        let end = start + symbol.len();
        if !word(line[..start].chars().next_back()) && !word(line[end..].chars().next()) {
            return true;
        }
        from = start + symbol.len().max(1);
        if from >= line.len() {
            break;
        }
    }
    false
}

fn cut(text: &str, max: usize) -> String {
    if text.chars().count() <= max {
        return text.to_string();
    }
    let mut out: String = text.chars().take(max).collect();
    out.push('\u{2026}');
    out
}

/// agent_grep grouped by file: a match does not repeat the group's file.
fn grep_group(group: Value) -> Value {
    let key = match field(&group, "key") {
        Some(Value::String(text)) => text.clone(),
        _ => return group,
    };
    map_key(group, "matches", |matches| match matches {
        Value::Array(items) => Value::Array(
            items
                .into_iter()
                .map(|row| {
                    if matches!(field(&row, "file"), Some(Value::String(file)) if *file == key) {
                        drop_key(row, "file")
                    } else {
                        row
                    }
                })
                .collect(),
        ),
        other => other,
    })
}

/// index: the files it indexed (first `INDEXED_ROWS`) and the skipped files
/// that need attention. Unchanged files are a count.
fn index(value: Value) -> Value {
    map_object(value, |pairs| {
        let mut indexed = 0usize;
        let mut unchanged = 0usize;
        let mut skipped = 0usize;
        for (key, rows) in pairs.iter_mut() {
            let Value::Array(items) = rows else {
                continue;
            };
            if key == "indexed" {
                indexed = items.len();
                items.truncate(INDEXED_ROWS);
            } else if key == "skipped" {
                skipped = items.len();
                items.retain(|row| {
                    !matches!(field(row, "reason"), Some(Value::String(reason)) if reason == "Unchanged")
                });
                unchanged = skipped - items.len();
            }
        }
        pairs.push(("indexed_count".to_string(), Value::Int(indexed as i64)));
        pairs.push(("skipped_count".to_string(), Value::Int(skipped as i64)));
        pairs.push(("unchanged_count".to_string(), Value::Int(unchanged as i64)));
        if indexed > INDEXED_ROWS {
            pairs.push((
                "hint".to_string(),
                Value::string("indexed lists the first 20 files; full=true lists every file."),
            ));
        }
    })
}

fn map_object(value: Value, edit: impl FnOnce(&mut Vec<(String, Value)>)) -> Value {
    match value {
        Value::Object(mut pairs) => {
            edit(&mut pairs);
            Value::Object(pairs)
        }
        other => other,
    }
}

fn drop_key(value: Value, key: &str) -> Value {
    map_object(value, |pairs| pairs.retain(|(name, _)| name != key))
}

fn map_key(value: Value, key: &str, edit: impl FnOnce(Value) -> Value) -> Value {
    map_object(value, |pairs| {
        if let Some(slot) = pairs.iter_mut().find(|(name, _)| name == key) {
            let inner = std::mem::replace(&mut slot.1, Value::Null);
            slot.1 = edit(inner);
        }
    })
}

fn field<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    match value {
        Value::Object(pairs) => pairs
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, item)| item),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sketch_finds_the_definition_line() {
        let content = "/// Calls detect_with on every file.\nimpl Engine {\n    pub fn detect_with(&mut self) -> bool {\n        true\n    }\n}";
        let (signature, preview) = sketch(content, "detect_with");
        assert_eq!(signature, "pub fn detect_with(&mut self) -> bool {");
        assert!(preview.starts_with("    pub fn detect_with"));
        assert_eq!(preview.lines().count(), 4);
        // Not a word match: detect_without.
        let (signature, _) = sketch("fn detect_without() {}\nfn detect_with() {}", "detect_with");
        assert_eq!(signature, "fn detect_with() {}");
        let (signature, preview) = sketch("", "x");
        assert_eq!((signature.as_str(), preview.as_str()), ("", ""));
    }

    #[test]
    fn find_definition_is_compact_unless_full() {
        let row = |n: i64| {
            Value::object(vec![
                ("symbol", Value::string("run")),
                ("document_path", Value::string("a.rs")),
                ("line_number", Value::Int(n)),
                ("content", Value::string("x".repeat(5000) + "\nfn run() {}")),
            ])
        };
        let value = || {
            Value::object(vec![
                ("symbol", Value::string("run")),
                ("definitions", Value::Array((1..=12).map(row).collect())),
                ("count", Value::Int(12)),
            ])
        };
        let compact =
            crate::json::to_string(&apply("find_definition", &Value::object(vec![]), value()));
        assert!(!compact.contains("\"content\""));
        assert!(compact.contains("\"signature\":\"fn run() {}\""));
        assert!(compact.contains("\"omitted\":2"));
        assert!(compact.len() < 2_000, "{}", compact.len());
        let full = crate::json::to_string(&apply(
            "find_definition",
            &Value::object(vec![("full", Value::Bool(true))]),
            value(),
        ));
        assert!(full.contains("\"content\""));
        assert!(!full.contains("omitted"));
        let three = crate::json::to_string(&apply(
            "find_definition",
            &Value::object(vec![("limit", Value::Int(3))]),
            value(),
        ));
        assert!(three.contains("\"omitted\":9"));
    }

    #[test]
    fn index_lists_counts_and_the_skips_that_matter() {
        let skipped: Vec<Value> = (0..30)
            .map(|n| {
                Value::object(vec![
                    ("path", Value::string(format!("f{n}"))),
                    (
                        "reason",
                        Value::string(if n == 0 { "Too large" } else { "Unchanged" }),
                    ),
                ])
            })
            .collect();
        let value = Value::object(vec![
            ("indexed", Value::Array(vec![])),
            ("skipped", Value::Array(skipped)),
        ]);
        let out = crate::json::to_string(&apply("index", &Value::object(vec![]), value));
        assert!(
            out.contains("\"skipped\":[{\"path\":\"f0\",\"reason\":\"Too large\"}]"),
            "{out}"
        );
        assert!(
            out.contains("\"skipped_count\":30,\"unchanged_count\":29"),
            "{out}"
        );
    }
}
