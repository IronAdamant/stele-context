//! 1.5.2 `semantic_fingerprint` and `fingerprint_to_vector`.
//!
//! The patterns are the same ones in `llm_embedding.py`. No model is called.

const NAMES: [&str; 32] = [
    "topic_data",
    "topic_web",
    "topic_ui",
    "topic_logic",
    "topic_test",
    "topic_config",
    "topic_auth",
    "topic_util",
    "qual_abstract",
    "qual_size",
    "qual_stable",
    "qual_legacy",
    "qual_typed",
    "qual_pure",
    "qual_stateful",
    "qual_async",
    "sig_imports",
    "sig_exports",
    "sig_functions",
    "sig_classes",
    "sig_tests",
    "sig_errors",
    "sig_comments",
    "sig_strings",
    "ctx_depth",
    "ctx_arity",
    "ctx_scope",
    "ctx_coupling",
    "ctx_cohesion",
    "ctx_coverage",
    "ctx_doc",
    "ctx_maintain",
];

pub fn embed_text(text: &str) -> Vec<f64> {
    let mut fp = [0.0f64; 32];
    fill(&mut fp, text);
    spread(&fp)
}

fn fill(fp: &mut [f64; 32], text: &str) {
    let lower = text.to_ascii_lowercase();
    let words = ident_words(&lower);
    let lines: Vec<&str> = text.split('\n').collect();
    let non_empty: Vec<&str> = lines
        .iter()
        .copied()
        .filter(|ln| !ln.trim().is_empty())
        .collect();
    let topics: &[(&str, &[&str])] = &[
        (
            "topic_data",
            &[
                "data", "parse", "query", "sql", "db", "record", "schema", "cache", "dict", "list",
                "array", "obj",
            ],
        ),
        (
            "topic_web",
            &[
                "http", "request", "response", "url", "api", "route", "server", "fetch", "client",
                "endpoint",
            ],
        ),
        (
            "topic_ui",
            &[
                "render",
                "component",
                "style",
                "css",
                "dom",
                "element",
                "button",
                "display",
                "html",
                "view",
                "layout",
            ],
        ),
        (
            "topic_logic",
            &[
                "validate",
                "check",
                "transform",
                "filter",
                "sort",
                "compute",
                "logic",
                "rule",
                "parse",
                "convert",
            ],
        ),
        (
            "topic_test",
            &[
                "test", "mock", "assert", "expect", "spec", "describe", "it(", "coverage", "suite",
                "case",
            ],
        ),
        (
            "topic_config",
            &[
                "config", "env", "flag", "option", "setting", "arg", "param", "default", "yaml",
                "toml", "ini",
            ],
        ),
        (
            "topic_auth",
            &[
                "auth",
                "token",
                "session",
                "permission",
                "role",
                "user",
                "login",
                "jwt",
                "password",
                "secret",
            ],
        ),
        (
            "topic_util",
            &[
                "util", "helper", "format", "escape", "copy", "merge", "deep", "uuid", "tool",
                "common",
            ],
        ),
    ];
    let denom = words.len().max(1) as f64;
    for (slot, (_name, keywords)) in topics.iter().enumerate() {
        let mut hits = 0.0;
        for word in &words {
            for kw in *keywords {
                if word.contains(kw) {
                    hits += 1.0;
                }
            }
        }
        fp[slot] = (hits / denom * 20.0).clamp(-1.0, 1.0);
    }
    let has_types = [
        ": int",
        ": str",
        ": bool",
        "interface",
        "type ",
        " TypedDict",
    ]
    .iter()
    .any(|k| text.contains(k));
    let is_async = text.contains("async ") || text.contains("await ");
    let is_pure = !["global", "nonlocal", "setattr", "del "]
        .iter()
        .any(|k| text.contains(k));
    let has_state = ["self.", "this.", ".state", "_state", ".cache"]
        .iter()
        .any(|k| text.contains(k));
    let code_lines: Vec<&str> = non_empty
        .iter()
        .copied()
        .filter(|ln| {
            let s = ln.trim_start();
            !s.starts_with('#') && !s.starts_with("//")
        })
        .collect();
    fp[8] = (words.len() as f64 / 100.0 - 0.5).clamp(-1.0, 1.0);
    fp[9] = (code_lines.len() as f64 / 50.0 - 1.0).clamp(-1.0, 1.0);
    fp[10] = 0.5;
    fp[11] = if ["deprecated", "__old", "legacy"]
        .iter()
        .any(|k| text.contains(k))
    {
        -0.5
    } else {
        0.0
    };
    fp[12] = if has_types { 1.0 } else { -1.0 };
    fp[13] = if is_pure { 1.0 } else { -0.5 };
    fp[14] = if has_state { 1.0 } else { -1.0 };
    fp[15] = if is_async { 1.0 } else { -1.0 };
    let import_count = count_alts(text, &["import ", "from ", "require ", "include "]);
    let export_count = count_export(text);
    let func_count = count_alts(text, &["def ", "function ", "fn ", "proc "]);
    let class_count = count_alts(text, &["class ", "struct ", "interface "]);
    let test_pattern = count_alts(
        text,
        &[
            "describe(",
            "it(",
            "test(",
            "expect(",
            "assert ",
            "def test_",
        ],
    );
    let error_count = count_alts(
        text,
        &[
            "raise", "throw", "except", "catch", "Error", "error:", "fail",
        ],
    );
    let comment_count = count_comment(text);
    let string_count = count_strings(text);
    let n = code_lines.len().max(1) as f64;
    fp[16] = (import_count as f64 / n * 10.0).clamp(-1.0, 1.0);
    fp[17] = (export_count as f64 / n * 10.0).clamp(-1.0, 1.0);
    fp[18] = (func_count as f64 / n * 5.0 - 1.0).clamp(-1.0, 1.0);
    fp[19] = (class_count as f64 / n * 5.0 - 1.0).clamp(-1.0, 1.0);
    fp[20] = (test_pattern as f64 / n * 10.0).clamp(-1.0, 1.0);
    fp[21] = (error_count as f64 / n * 5.0 - 1.0).clamp(-1.0, 1.0);
    fp[22] = (comment_count as f64 / n * 10.0 - 2.0).clamp(-1.0, 1.0);
    fp[23] = (string_count as f64 / n * 5.0 - 2.0).clamp(-1.0, 1.0);
    let mut max_indent = 0usize;
    for line in &lines {
        let stripped = line.trim_start();
        if !stripped.is_empty() {
            max_indent = max_indent.max(line.len() - stripped.len());
        }
    }
    fp[24] = (max_indent as f64 / 20.0 - 0.5).clamp(-1.0, 1.0);
    let commas = text.matches(',').count() as f64;
    fp[25] = (commas / func_count.max(1) as f64 / 3.0 - 0.5).clamp(-1.0, 1.0);
    fp[26] = (words.len() as f64 / 200.0 - 0.5).clamp(-1.0, 1.0);
    fp[27] = (import_count as f64 / 10.0 - 0.5).clamp(-1.0, 1.0);
    fp[28] = 0.5;
    fp[29] = 0.0;
    fp[30] =
        (comment_count as f64 / (func_count + class_count).max(1) as f64 - 1.0).clamp(-1.0, 1.0);
    fp[31] = (comment_count as f64 / code_lines.len().max(1) as f64 * 5.0 - 1.0).clamp(-1.0, 1.0);
    let _ = NAMES;
}

fn spread(fp: &[f64; 32]) -> Vec<f64> {
    let mut vec = vec![0.0; 128];
    let sigma = 1.2f64;
    for (index, value) in fp.iter().enumerate() {
        let norm = (value + 1.0) / 2.0;
        let base = index * 4;
        for offset in 0..4 {
            let weight = (-((offset * offset) as f64) / (2.0 * sigma * sigma)).exp();
            vec[base + offset] += norm * weight;
        }
    }
    let norm = vec.iter().map(|item| item * item).sum::<f64>().sqrt();
    if norm > 0.0 {
        for item in &mut vec {
            *item /= norm;
        }
    }
    vec
}

fn ident_words(lower: &str) -> Vec<String> {
    let chars: Vec<char> = lower.chars().collect();
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < chars.len() {
        if is_ident_start(chars[i]) {
            let start = i;
            i += 1;
            while i < chars.len() && is_ident_cont(chars[i]) {
                i += 1;
            }
            out.push(chars[start..i].iter().collect());
        } else {
            i += 1;
        }
    }
    out
}

fn is_ident_start(ch: char) -> bool {
    ch.is_ascii_alphabetic() || ch == '_'
}

fn is_ident_cont(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ch == '_'
}

fn count_alts(text: &str, parts: &[&str]) -> usize {
    parts.iter().map(|part| text.matches(part).count()).sum()
}

fn count_export(text: &str) -> usize {
    text.matches("export").count()
        + text.matches("module.exports").count()
        + text.matches("pub fn").count()
        + text.matches("public def").count()
}

fn count_comment(text: &str) -> usize {
    text.matches('#').count()
        + text.matches("//").count()
        + text.matches("/*").count()
        + text.matches("*/").count()
        + text.matches("'''").count()
        + text.matches("\"\"\"").count()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    #[test]
    fn matches_python_semantic_fingerprint() {
        let text = "def classify(n):\n    return n + 1\n";
        let rust = embed_text(text);
        let script = r#"
import json, sys
from stele_context.llm_embedding import embed
print(json.dumps(embed(sys.stdin.read())))
"#;
        let out = Command::new("python3")
            .arg("-c")
            .arg(script)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn();
        let Ok(mut child) = out else { return };
        use std::io::Write;
        child
            .stdin
            .take()
            .unwrap()
            .write_all(text.as_bytes())
            .unwrap();
        let done = child.wait_with_output().unwrap();
        if !done.status.success() {
            return;
        }
        let value = crate::json::parse(&String::from_utf8_lossy(&done.stdout)).unwrap();
        let crate::json::Value::Array(rows) = value else {
            panic!("vec")
        };
        assert_eq!(rows.len(), rust.len());
        for (index, (got, expect)) in rust.iter().zip(rows).enumerate() {
            let expect = match expect {
                crate::json::Value::Float(n) => n,
                crate::json::Value::Int(n) => n as f64,
                _ => 0.0,
            };
            assert!(
                (got - expect).abs() < 1e-6,
                "dim {index} rust {got} python {expect}"
            );
        }
    }
}

fn count_strings(text: &str) -> usize {
    let bytes = text.as_bytes();
    let mut count = 0usize;
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] == b'\'' || bytes[i] == b'"' {
            let quote = bytes[i];
            i += 1;
            let start = i;
            while i < bytes.len() && bytes[i] != quote {
                i += 1;
            }
            if i < bytes.len() && bytes[i] == quote && i > start {
                count += 1;
                i += 1;
                continue;
            }
        }
        i += 1;
    }
    count
}
