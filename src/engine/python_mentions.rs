//! Python reference rows `find_references` reads at query time (2.6).
//!
//! The index keeps 1.5.2's Python rows: a name load and a called attribute.
//! `exp.StrPosition` as a dict key, an annotation, `exp.StrPosition.attr`
//! and the names of a chunk that is not a valid module are not rows. Every
//! command loads the whole store, so storing those uses made each one
//! slower (keyword search by 3% on the gate's fixture tree). Instead,
//! `find_references` reads each Python chunk that holds the name with
//! `lang::python_symbols::extract_all` and uses its rows for that chunk in
//! place of the stored ones.

use super::query::language_of;
use super::Engine;
use crate::json::Value;
use std::collections::{HashMap, HashSet};

/// The Python scope (`container`) of each reference row `python_mentions`
/// gave, by `(path, line)`.
pub(super) type Scopes = HashMap<(String, i64), Option<String>>;

impl Engine {
    /// Replaces the rows of `refs` from Python chunks that hold `name` with
    /// that chunk's rows from `extract_all`, and returns their scopes.
    pub(super) fn python_mentions(&self, name: &str, refs: &mut Vec<Value>) -> Scopes {
        let mut scopes = Scopes::new();
        if name.is_empty() || !name.chars().all(|ch| ch.is_alphanumeric() || ch == '_') {
            return scopes;
        }
        let mut read: HashSet<&str> = HashSet::new();
        let mut rows = Vec::new();
        for chunk in &self.store.state.chunks {
            if !chunk.content.contains(name) || language_of(&chunk.path) != "python" {
                continue;
            }
            read.insert(chunk.id.as_str());
            for hit in crate::lang::python_symbols::extract_all(&chunk.content) {
                if hit.name != name || hit.role != "reference" {
                    continue;
                }
                let line = crate::chunk::lines::absolute(&chunk.line_map, hit.line) as i64;
                scopes.insert((chunk.path.clone(), line), hit.container);
                rows.push(Value::object(vec![
                    ("symbol", Value::string(name)),
                    ("kind", Value::string(hit.kind)),
                    ("chunk_id", Value::string(&chunk.id)),
                    ("document_path", Value::string(&chunk.path)),
                    ("line_number", Value::Int(line)),
                    (
                        "content_preview",
                        Value::string(chunk.content.chars().take(200).collect::<String>()),
                    ),
                    ("dynamic", Value::Bool(false)),
                ]));
            }
        }
        if !read.is_empty() {
            refs.retain(|row| !read.contains(crate::store::string_field(row, "chunk_id").as_str()));
            refs.extend(rows);
        }
        scopes
    }
}
