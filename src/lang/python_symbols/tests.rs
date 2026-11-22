use super::*;
use crate::json::{self, Value};
use crate::syntax::query::SymbolHit;

#[test]
fn keyword_arguments_and_format_types_are_not_loads() {
    let source = "def test_get_html_style():\n    html_style = Style(\n        reverse=True,\n        color=\"red\",\n    ).get_html_style()\n    text = f\"{count:d} {size:.1f}\"\n";
    let hits = extract(source);
    let names: Vec<_> = hits.iter().map(|hit| hit.name.as_str()).collect();
    assert!(!names.contains(&"reverse"), "{hits:?}");
    assert!(!names.contains(&"color"), "{hits:?}");
    assert!(!names.contains(&"d"), "{hits:?}");
    assert!(!names.contains(&"f"), "{hits:?}");
    assert!(names.contains(&"get_html_style"), "{hits:?}");
    assert!(names.contains(&"count"), "{hits:?}");
    assert!(names.contains(&"size"), "{hits:?}");
    let source = "def encoding(self) -> str:\n    return (getattr(self.file, \"encoding\", \"utf-8\") or \"utf-8\").lower()\n\n@property\ndef is_terminal(self) -> bool:\n    force_color = self._environ.get(\"FORCE_COLOR\")\n    return force_color\n";
    let hits = extract(source);
    let lowers = hits.iter().filter(|hit| hit.name == "lower").count();
    assert_eq!(lowers, 1, "{hits:?}");
    assert!(
        hits.iter()
            .any(|hit| hit.name == "is_terminal" && hit.role == "definition"),
        "{hits:?}"
    );
    assert!(
        hits.iter()
            .all(|hit| !(hit.name == "force_color" && hit.line == 6)),
        "{hits:?}"
    );
    let hits = extract("parser = MarkdownIt().enable(\"strikethrough\").enable(\"table\")\n");
    let enables = hits.iter().filter(|hit| hit.name == "enable").count();
    assert_eq!(enables, 2, "{hits:?}");
    let hits = extract("return replace_link_ids(console.file.getvalue()).replace(\"a\", \"b\")\n");
    assert_eq!(
        hits.iter().filter(|hit| hit.name == "replace").count(),
        1,
        "{hits:?}"
    );
    let source = r#"class Table:
    def _measure_column(
        self,
        console: "Console",
        options: "ConsoleOptions",
        column: Column,
    ) -> Measurement:
        """Get the minimum and maximum width of the column."""
        max_width = options.max_width
        if column.width is not None:
            return Measurement(
                column.width + padding_width, column.width + padding_width
            ).with_maximum(max_width)
        measurement = Measurement(
            max(min_widths) if min_widths else 1,
            max(max_widths) if max_widths else max_width,
        ).with_maximum(max_width)
        measurement = measurement.clamp(
            None if column.min_width is None else column.min_width + padding_width,
            None if column.max_width is None else column.max_width + padding_width,
        )
        return measurement

    def _render(self):
        return self
"#;
    let hits = extract(source);
    assert!(
        hits.iter().any(|hit| {
            hit.name == "_render"
                && hit.role == "definition"
                && hit.container.as_deref() == Some("Table")
        }),
        "{hits:?}"
    );
}

#[test]
fn relative_imports_and_fstring_conversion_match_ast() {
    let source =
        "from ._spinners import SPINNERS\nraise KeyError(f\"no spinner called {name!r}\")\n";
    let hits = extract(source);
    assert!(
        hits.iter()
            .any(|hit| hit.name == "_spinners" && hit.kind == "module"),
        "{hits:?}"
    );
    assert!(
        hits.iter()
            .any(|hit| hit.name == "SPINNERS" && hit.kind == "import"),
        "{hits:?}"
    );
    assert!(
        hits.iter().all(|hit| hit.name != "r"),
        "conversion flag leaked: {hits:?}"
    );
}

#[test]
fn class_methods_after_a_wrapped_signature_stay_in_scope() {
    let source = "\
class Spinner:\n    \
def __rich_console__(\n        \
self, console: \"Console\", options: \"ConsoleOptions\"\n    \
) -> \"RenderResult\":\n        \
yield self.render(console.get_time())\n\n    \
def __rich_measure__(\n        \
self, console: \"Console\", options: \"ConsoleOptions\"\n    \
) -> Measurement:\n        \
text = self.render(0)\n        \
return Measurement.get(console, options, text)\n\n    \
def render(self, time: float) -> \"RenderableType\":\n        \
return self.frames\n";
    let hits = extract(source);
    let defs: Vec<_> = hits
        .iter()
        .filter(|hit| hit.role == "definition")
        .map(|hit| format!("{} @{}", hit.name, hit.container.as_deref().unwrap_or("")))
        .collect();
    assert!(
        defs.iter().any(|name| name.starts_with("render")),
        "{defs:?}\n{hits:?}"
    );
}

#[test]
fn keyword_only_params_still_walk_the_body() {
    let source = "class Spinner:\n    def __init__(self, name: str, *, style: \"T\" = None) -> None:\n        self.style = style\n";
    let hits = extract(source);
    assert!(
        hits.iter()
            .any(|hit| hit.name == "self" && hit.role == "reference"),
        "{hits:?}"
    );
}

#[test]
fn garbled_and_truncated_inputs_yield_symbols() {
    let samples = ["", "\0\u{fffd}", "def (", "((((", "class\n", "def ok(\n"];
    for sample in samples {
        let _ = extract(sample);
    }
    let mut state = 7u64;
    for _ in 0..200 {
        state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
        let len = (state % 40) as usize;
        let mut bytes = Vec::with_capacity(len);
        for _ in 0..len {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            bytes.push(state as u8);
        }
        let text = String::from_utf8_lossy(&bytes);
        let _ = extract(&text);
    }
}

#[test]
fn multiline_calls_use_the_expression_start_line() {
    let source = "def get(self):\n    render_width = (\n        get_console_width(console, options)\n        .normalize()\n        .with_maximum(_max_width)\n    )\n    lines = (\n        Text(\"\\n\")\n        .join(lines)\n        .with_indent_guides(1)\n        .split(\"\\n\")\n    )\n    return Measurement(\n        column.width, column.width\n    ).with_maximum(max_width)\n    html_style = Style(\n        bold=True,\n    ).get_html_style()\n";
    let hits = extract(source);
    let line = |name: &str| {
        hits.iter()
            .find(|hit| hit.name == name && hit.role == "reference")
            .map(|hit| hit.line)
    };
    assert_eq!(line("normalize"), line("get_console_width"), "{hits:?}");
    assert_eq!(line("with_maximum"), line("get_console_width"), "{hits:?}");
    assert_eq!(line("join"), line("Text"), "{hits:?}");
    assert_eq!(line("split"), line("Text"), "{hits:?}");
    assert_eq!(line("with_indent_guides"), line("Text"), "{hits:?}");
    assert_eq!(line("get_html_style"), line("Style"), "{hits:?}");
    let measurement = hits.iter().find(|hit| hit.name == "Measurement").unwrap();
    let maximum = hits
        .iter()
        .find(|hit| hit.name == "with_maximum" && hit.line == measurement.line);
    assert!(maximum.is_some(), "{hits:?}");
}

#[test]
fn python_fixture_symbols_match_the_golden() {
    let golden = std::fs::read_to_string("parity/goldens/fixtures.json").unwrap();
    let value = json::parse(&golden).unwrap();
    let files = object_get(&value, "files").unwrap();
    let Value::Array(files) = files else {
        panic!("files");
    };
    for file in files {
        let path = string_field(file, "path");
        let ext = string_field(file, "extension");
        if ext != "py" && ext != "pyw" && ext != "pyx" {
            continue;
        }
        let source = std::fs::read_to_string(format!("parity/fixtures/{path}")).unwrap();
        // Goldens are per chunk. A multi-chunk file is not one module.
        if crate::chunk::chunk_document(
            &path,
            &source,
            crate::chunk::CHUNK_SIZE,
            crate::chunk::MAX_CHUNK_SIZE,
            0.7,
        )
        .len()
            != 1
        {
            continue;
        }
        let got = extract(&source);
        let expected = symbol_rows(file);
        assert_eq!(
            got.len(),
            expected.len(),
            "{path}\n got {got:?}\n exp {expected:?}"
        );
        for (left, right) in got.iter().zip(expected.iter()) {
            assert_eq!(left, right, "{path}");
        }
    }
}

fn symbol_rows(file: &Value) -> Vec<SymbolHit> {
    let Value::Array(rows) = object_get(file, "symbols").unwrap() else {
        panic!("symbols");
    };
    rows.iter()
        .map(|row| SymbolHit {
            name: string_field(row, "name"),
            kind: intern_kind(&string_field(row, "kind")),
            role: intern_role(&string_field(row, "role")),
            line: match object_get(row, "line") {
                Some(Value::Int(line)) => *line as u32,
                _ => 0,
            },
            container: match object_get(row, "container") {
                Some(Value::String(text)) => Some(text.clone()),
                _ => None,
            },
        })
        .collect()
}

fn string_field(value: &Value, key: &str) -> String {
    match object_get(value, key) {
        Some(Value::String(text)) => text.clone(),
        _ => String::new(),
    }
}

fn object_get<'a>(value: &'a Value, key: &str) -> Option<&'a Value> {
    let Value::Object(pairs) = value else {
        return None;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, item)| item)
}

/// sqlglot renames (A/B run): `exp.StrPosition` used as a dict key, an
/// annotation, the head of an attribute chain and a tuple element is a use
/// the `ast` walk does not record. `extract_all` records it; `extract`
/// stays 1.5.2's.
#[test]
fn extract_all_records_attribute_uses_the_walk_misses() {
    let source = r#"from sqlglot import exp


class Generator:
    TRANSFORMS = {
        exp.StrPosition: lambda self, e: self.func("POS", e.this),
    }

    def build(self, expression: exp.StrPosition) -> exp.StrPosition:
        parser = exp.StrPosition.from_arg_list
        kinds = (exp.Concat, exp.StrPosition)
        return exp.StrPosition(this=parser)  # exp.StrPosition in a comment
        "exp.StrPosition in a string"
"#;
    let lines = |hits: &[SymbolHit], name: &str| -> Vec<u32> {
        let mut lines: Vec<u32> = hits
            .iter()
            .filter(|hit| hit.name == name && hit.role == "reference")
            .map(|hit| hit.line)
            .collect();
        lines.sort();
        lines.dedup();
        lines
    };
    let walk = extract(source);
    assert_eq!(lines(&walk, "StrPosition"), vec![12], "{walk:?}");
    let all = extract_all(source);
    assert_eq!(
        lines(&all, "StrPosition"),
        vec![6, 9, 10, 11, 12],
        "{all:?}"
    );
    // The walk's rows come first, unchanged.
    assert_eq!(&all[..walk.len()], &walk[..]);
    let dict_key = all
        .iter()
        .find(|hit| hit.name == "StrPosition" && hit.line == 6)
        .unwrap();
    assert_eq!(dict_key.kind, "attribute");
    assert_eq!(dict_key.container.as_deref(), Some("Generator"));
    let annotation = all
        .iter()
        .find(|hit| hit.name == "StrPosition" && hit.line == 9)
        .unwrap();
    assert_eq!(annotation.container.as_deref(), Some("Generator.build"));
    assert_eq!(lines(&all, "from_arg_list"), vec![10]);
    // A class attribute is kept; a function's locals, parameters and
    // keyword-argument names are bindings, not uses.
    assert_eq!(lines(&all, "TRANSFORMS"), vec![5]);
    assert!(
        lines(&all, "parser").iter().all(|line| *line == 12),
        "{all:?}"
    );
    assert!(lines(&all, "kinds").is_empty(), "{all:?}");
    assert!(lines(&all, "expression").is_empty(), "{all:?}");
    assert!(lines(&all, "e").iter().all(|line| *line == 6), "{all:?}");
    // `e.this` is an attribute use; `this=` on line 12 is a keyword name.
    assert_eq!(lines(&all, "this"), vec![6], "{all:?}");
    // The definitions are not references to themselves.
    assert!(lines(&all, "Generator").is_empty(), "{all:?}");
    assert!(lines(&all, "build").is_empty(), "{all:?}");
}

#[test]
fn extract_all_reads_names_of_a_chunk_that_is_not_a_module() {
    // A chunk cut inside a dict: 1.5.2's regex fallback keeps no uses.
    let source = "        exp.StrPosition: strposition_sql,\n        exp.Upper: rename_func(\"UPPER\"),\n    }\n";
    assert!(extract(source).is_empty());
    let all = extract_all(source);
    let names: Vec<(&str, u32)> = all
        .iter()
        .map(|hit| (hit.name.as_str(), hit.line))
        .collect();
    for want in [
        ("exp", 1),
        ("StrPosition", 1),
        ("strposition_sql", 1),
        ("Upper", 2),
        ("rename_func", 2),
    ] {
        assert!(names.contains(&want), "{want:?}: {names:?}");
    }
    assert!(!names.iter().any(|(name, _)| *name == "UPPER"), "{names:?}");
}

#[test]
fn extract_all_puts_parenthesized_imports_on_their_own_lines() {
    let source = "from sqlglot.dialects.dialect import (\n    rename_func,\n    strposition_sql as _pos,\n)\nimport os.path\n";
    let all = extract_all(source);
    let at = |name: &str| -> Vec<u32> {
        all.iter()
            .filter(|hit| hit.name == name)
            .map(|hit| hit.line)
            .collect()
    };
    assert_eq!(at("rename_func"), vec![2], "{all:?}");
    assert_eq!(at("strposition_sql"), vec![3], "{all:?}");
    // Module paths and `as` names are not name uses.
    assert!(at("dialects").is_empty(), "{all:?}");
    assert!(at("path").is_empty(), "{all:?}");
    assert!(at("_pos").is_empty(), "{all:?}");
}

#[test]
fn extract_all_keeps_one_line_bodies_and_fstrings_apart() {
    let source =
        "def first(): return exp.Upper\nvalue = exp.Lower\ntext = f\"{value!r:>10} {count:.1f}\"\n";
    let all = extract_all(source);
    let upper = all.iter().find(|hit| hit.name == "Upper").unwrap();
    assert_eq!(upper.container.as_deref(), Some("first"));
    let lower = all.iter().find(|hit| hit.name == "Lower").unwrap();
    assert_eq!(lower.container, None, "{all:?}");
    assert!(
        all.iter().all(|hit| hit.name != "r" && hit.name != "f"),
        "{all:?}"
    );
}
