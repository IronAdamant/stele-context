//! Symbol extraction for non-Python languages, plus the Python dispatcher.
//!
//! Patterns follow `stele_context/symbols.py`, `symbol_patterns.py`,
//! `symbol_csharp.py`, and `symbol_swift.py` from package 1.5.2.

mod csharp;
mod edges;
mod javascript;
mod javascript_prepass;
mod source;
mod swift;
mod util;
mod web;

/// One definition or reference inside a chunk.
///
/// `line` is 1-based within the chunk text. HTML and CSS extraction does not
/// record a line; those hits use `0`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Hit {
    pub name: String,
    pub kind: String,
    pub role: String,
    pub line: u32,
    pub container: Option<String>,
}

pub(crate) use edges::build as build_edges;

pub fn extract(extension: &str, content: &str) -> Vec<Hit> {
    let ext = extension
        .strip_prefix('.')
        .unwrap_or(extension)
        .to_ascii_lowercase();
    match ext.as_str() {
        // Valid Python chunks are labeled "python" by the chunker. The file
        // extension remains py, pyw, or pyx, which is what SymbolExtractor sees.
        "py" | "pyw" | "pyx" | "python" => from_python(content),
        "js" | "jsx" | "mjs" | "cjs" | "ts" | "tsx" => javascript::extract(content),
        "html" | "htm" => web::extract_html(content),
        "css" | "scss" | "less" => web::extract_css(content),
        "java" | "kt" | "kts" | "scala" => source::extract_java(content),
        "go" => source::extract_go(content),
        "rs" => source::extract_rust(content),
        "cs" | "csharp" => csharp::extract(content),
        "swift" => swift::extract(content),
        "c" | "cpp" | "cc" | "cxx" | "h" | "hpp" | "hxx" => source::extract_c(content),
        "rb" => source::extract_ruby(content),
        "php" => source::extract_php(content),
        _ => Vec::new(),
    }
}

fn from_python(content: &str) -> Vec<Hit> {
    crate::lang::python_symbols::extract(content)
        .into_iter()
        .map(|hit| Hit {
            name: hit.name,
            kind: hit.kind.to_string(),
            role: hit.role.to_string(),
            line: hit.line,
            container: hit.container,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chunk::{self, Produced};
    use crate::json::{self, Value};
    use std::path::Path;

    #[derive(Debug, PartialEq, Eq)]
    struct Row {
        chunk_index: usize,
        name: String,
        kind: String,
        role: String,
        line: u32,
        container: Option<String>,
    }

    #[test]
    fn fixture_symbols_match_python_goldens() {
        let golden = std::fs::read_to_string("parity/goldens/fixtures.json").unwrap();
        let value = json::parse(&golden).unwrap();
        let files = array_field(&value, "files");
        let mut mismatches = Vec::new();
        for file in files {
            let rel = string_field(file, "path");
            let bytes = std::fs::read(Path::new("parity/fixtures").join(&rel))
                .unwrap_or_else(|error| panic!("read {rel}: {error}"));
            let source = chunk::read_python_text(&bytes);
            let produced =
                chunk::chunk_document(&rel, &source, chunk::CHUNK_SIZE, chunk::MAX_CHUNK_SIZE, 0.7);
            let got = flatten(&rel, &produced);
            let expected = golden_rows(file);
            if got != expected {
                mismatches.push(format!(
                    "{rel}\n  got:\n{}\n  expected:\n{}",
                    render(&got),
                    render(&expected)
                ));
            }
        }
        assert!(
            mismatches.is_empty(),
            "{} fixture(s) differ\n{}",
            mismatches.len(),
            mismatches.join("\n")
        );
    }

    fn flatten(path: &str, produced: &[Produced]) -> Vec<Row> {
        let fallback = file_extension(path);
        let mut rows = Vec::new();
        for chunk in produced {
            let extension = chunk.language.as_deref().unwrap_or(fallback.as_str());
            for hit in extract(extension, &chunk.content) {
                rows.push(Row {
                    chunk_index: chunk.index,
                    name: hit.name,
                    kind: hit.kind,
                    role: hit.role,
                    line: hit.line,
                    container: hit.container,
                });
            }
        }
        rows
    }

    fn golden_rows(file: &Value) -> Vec<Row> {
        array_field(file, "symbols")
            .iter()
            .map(|row| Row {
                chunk_index: int_field(row, "chunk_index") as usize,
                name: string_field(row, "name"),
                kind: string_field(row, "kind"),
                role: string_field(row, "role"),
                // HTML and CSS leave line_number unset. Hit.line is 0 there.
                line: match field(row, "line") {
                    Some(Value::Int(line)) => *line as u32,
                    Some(Value::Null) | None => 0,
                    other => panic!("line {other:?}"),
                },
                container: match field(row, "container") {
                    Some(Value::String(text)) => Some(text.clone()),
                    Some(Value::Null) | None => None,
                    other => panic!("container {other:?}"),
                },
            })
            .collect()
    }

    fn render(rows: &[Row]) -> String {
        if rows.is_empty() {
            return "    (none)".to_string();
        }
        rows.iter()
            .map(|row| {
                format!(
                    "    #{:<2} {:<10} {:<12} L{:<4} {:?} {}",
                    row.chunk_index, row.role, row.kind, row.line, row.container, row.name
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn file_extension(path: &str) -> String {
        path.rsplit(['/', '\\'])
            .next()
            .and_then(|name| name.rsplit_once('.'))
            .map(|(_, ext)| ext.to_ascii_lowercase())
            .unwrap_or_default()
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

    fn int_field(value: &Value, key: &str) -> i64 {
        match field(value, key) {
            Some(Value::Int(number)) => *number,
            _ => 0,
        }
    }

    fn array_field<'a>(value: &'a Value, key: &str) -> &'a [Value] {
        match field(value, key) {
            Some(Value::Array(items)) => items,
            _ => panic!("missing array {key}"),
        }
    }
}
