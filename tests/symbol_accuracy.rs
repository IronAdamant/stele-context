//! 2.3 symbol accuracy (Specula live test B5, B6): file line numbers and
//! the real signature line; Rust references through grouped imports and
//! `::` paths; references kept to the definition's language; a store from
//! before 2.3 converted on open.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const BINARY: &str = env!("CARGO_BIN_EXE_stele-context");

struct Project {
    dir: PathBuf,
}

impl Drop for Project {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn filler(name: &str) -> String {
    // Enough tokens that each function is its own chunk.
    let mut body = format!("pub fn {name}(data: &[u8]) -> u32 {{\n");
    for n in 0..60 {
        body.push_str(&format!("    let value_{n} = data.len() as u32 + {n};\n"));
    }
    body.push_str("    0\n}\n\n");
    body
}

fn project() -> Project {
    let dir = std::env::temp_dir().join(format!(
        "stele-symbols-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    let src = dir.join("src");
    std::fs::create_dir_all(&src).unwrap();
    std::fs::create_dir_all(dir.join("scripts")).unwrap();
    std::fs::write(dir.join(".stele-context.toml"), "").unwrap();
    let checksum = format!(
        "//! Checksums used by zlib and PNG.\n\n{}{}pub fn crc32(data: &[u8]) -> u32 {{\n    crc32_update(0, data)\n}}\n\n{}",
        filler("adler32"),
        filler("crc32_update"),
        filler("adler32_update"),
    );
    std::fs::write(src.join("checksum.rs"), checksum).unwrap();
    let policy = format!(
        "{}pub fn drop_changes(world: &mut dyn WorldWrite) -> u32 {{\n    world.apply()\n}}\n\npub trait WorldWrite {{\n    fn apply(&mut self) -> u32;\n}}\n",
        filler("rules")
    );
    std::fs::write(src.join("policy.rs"), policy).unwrap();
    std::fs::write(
        src.join("png.rs"),
        "use crate::checksum::{crc32, crc32_update};\n\npub fn sum(data: &[u8]) -> u32 {\n    crc32(data) ^ crc32_update(1, data)\n}\n",
    )
    .unwrap();
    std::fs::write(
        src.join("binary.rs"),
        "pub fn tag(data: &[u8]) -> u32 {\n    specula_core::checksum::crc32(data)\n}\n",
    )
    .unwrap();
    std::fs::write(
        src.join("game.rs"),
        "pub fn over(world: &mut dyn crate::policy::WorldWrite) -> u32 {\n    world.apply()\n}\n",
    )
    .unwrap();
    std::fs::write(
        src.join("lex.rs"),
        "use crate::policy::{self, WorldWrite};\n\npub fn run(w: &mut dyn WorldWrite) -> u32 {\n    w.apply()\n}\n",
    )
    .unwrap();
    std::fs::write(
        dir.join("scripts/check.py"),
        "import zlib\n\n\ndef check(data):\n    return zlib.crc32(data)\n",
    )
    .unwrap();
    let out = cli(&dir, &["index", "."]);
    assert!(out.contains("\"ok\":true"), "{out}");
    Project { dir }
}

fn cli(dir: &Path, args: &[&str]) -> String {
    let output = Command::new(BINARY)
        .current_dir(dir)
        .arg("--storage-dir")
        .arg(dir.join(".store"))
        .args(args)
        .output()
        .expect("cli");
    String::from_utf8_lossy(&output.stdout).to_string()
}

/// The 1-based line of `path` that starts with `prefix` (after indent).
fn line_of(dir: &Path, path: &str, prefix: &str) -> i64 {
    let text = std::fs::read_to_string(dir.join(path)).unwrap();
    text.split('\n')
        .position(|line| line.trim_start().starts_with(prefix))
        .map(|at| at as i64 + 1)
        .unwrap()
}

fn files(result: &str) -> Vec<String> {
    let mut out: Vec<String> = result
        .split("\"document_path\":\"")
        .skip(1)
        .filter_map(|rest| rest.split('"').next())
        .map(|path| path.replace("\\\\", "/"))
        .collect();
    out.sort();
    out.dedup();
    out
}

fn number_after(text: &str, key: &str) -> i64 {
    let at = text.find(key).unwrap_or_else(|| panic!("{key} in {text}")) + key.len();
    text[at..]
        .chars()
        .take_while(|ch| ch.is_ascii_digit())
        .collect::<String>()
        .parse()
        .unwrap()
}

#[test]
fn definitions_report_file_lines() {
    let project = project();
    let crc = cli(&project.dir, &["find-definition", "crc32"]);
    let expected = line_of(&project.dir, "src/checksum.rs", "pub fn crc32(");
    assert!(
        expected > 60,
        "the definition is past the first chunk: {expected}"
    );
    assert_eq!(number_after(&crc, "\"line_number\":"), expected, "{crc}");
    let trait_line = line_of(&project.dir, "src/policy.rs", "pub trait WorldWrite");
    let world = cli(&project.dir, &["find-definition", "WorldWrite"]);
    assert_eq!(
        number_after(&world, "\"line_number\":"),
        trait_line,
        "{world}"
    );
    // Every reference row points at a line that names the symbol.
    let refs = cli(&project.dir, &["find-references", "WorldWrite"]);
    for row in refs.split("{\"symbol\":\"WorldWrite\"").skip(1) {
        let Some(path) = row
            .split("\"document_path\":\"")
            .nth(1)
            .and_then(|rest| rest.split('"').next())
        else {
            continue;
        };
        let path = path.replace("\\\\", "/");
        let line = number_after(row, "\"line_number\":") as usize;
        let text = std::fs::read_to_string(project.dir.join(&path)).unwrap();
        let shown = text.split('\n').nth(line - 1).unwrap_or("");
        assert!(shown.contains("WorldWrite"), "{path}:{line} is {shown:?}");
    }
}

#[test]
fn compact_signature_is_the_definition_line() {
    let project = project();
    let mut child = Command::new(BINARY)
        .current_dir(&project.dir)
        .arg("--storage-dir")
        .arg(project.dir.join(".store"))
        .arg("serve-mcp")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    {
        let mut stdin = child.stdin.take().unwrap();
        writeln!(stdin, r#"{{"jsonrpc":"2.0","id":1,"method":"initialize","params":{{"protocolVersion":"2025-06-18","capabilities":{{}},"clientInfo":{{"name":"t","version":"0"}}}}}}"#).unwrap();
        writeln!(stdin, r#"{{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{{"name":"find_definition","arguments":{{"symbol":"WorldWrite"}}}}}}"#).unwrap();
        writeln!(stdin, r#"{{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{{"name":"find_references","arguments":{{"symbol":"crc32"}}}}}}"#).unwrap();
    }
    let lines: Vec<String> = BufReader::new(child.stdout.take().unwrap())
        .lines()
        .map_while(Result::ok)
        .collect();
    let _ = child.wait();
    let reply = &lines[1];
    // The chunk names WorldWrite first in drop_changes; the signature is
    // the trait line the symbol table points at.
    assert!(
        reply.contains(r#"\"signature\":\"pub trait WorldWrite {\""#),
        "{reply}"
    );
    // A reference row shows its own line, not the head of its chunk.
    let refs = &lines[2];
    assert!(
        refs.contains(r#"\"line\":\"use crate::checksum::{crc32, crc32_update};\""#),
        "{refs}"
    );
    assert!(!refs.contains("content_preview"), "{refs}");
}

#[test]
fn rust_references_follow_imports_and_paths_in_one_language() {
    let project = project();
    let crc = cli(&project.dir, &["find-references", "crc32"]);
    assert_eq!(
        files(&crc),
        vec!["src/binary.rs", "src/checksum.rs", "src/png.rs"],
        "{crc}"
    );
    assert_eq!(
        number_after(&crc, "\"other_language_references\":"),
        1,
        "{crc}"
    );
    let every = cli(
        &project.dir,
        &["find-references", "crc32", "--all-languages"],
    );
    assert!(
        files(&every).contains(&"scripts/check.py".to_string()),
        "{every}"
    );
    let world = cli(&project.dir, &["find-references", "WorldWrite"]);
    assert_eq!(
        files(&world),
        vec!["src/game.rs", "src/lex.rs", "src/policy.rs"],
        "{world}"
    );
}

/// A store written before 2.3 has chunk-relative symbol lines, no line
/// maps and none of the new Rust references; opening it converts it.
#[test]
fn a_store_from_before_2_3_is_converted_on_open() {
    let project = project();
    let snapshot = project.dir.join(".store/store/snapshot.json");
    let text = std::fs::read_to_string(&snapshot).unwrap();
    let mut value = stele_context::json::parse(&text).unwrap();
    // Rewrite it as 2.2 wrote it: chunk-relative lines from 1.5.2's
    // extractor, no line_map, no symbol_lines marker.
    let stele_context::json::Value::Object(pairs) = &mut value else {
        panic!("object")
    };
    let mut rows = Vec::new();
    for (key, item) in pairs.iter_mut() {
        if key == "chunks" {
            if let stele_context::json::Value::Array(chunks) = item {
                for chunk in chunks.iter_mut() {
                    let stele_context::json::Value::Object(fields) = chunk else {
                        continue;
                    };
                    fields.retain(|(name, _)| name != "line_map");
                    let get = |name: &str| {
                        fields
                            .iter()
                            .find(|(key, _)| key == name)
                            .map(|(_, value)| value.clone())
                    };
                    let (
                        Some(stele_context::json::Value::String(id)),
                        Some(stele_context::json::Value::String(path)),
                        Some(stele_context::json::Value::String(content)),
                    ) = (get("id"), get("path"), get("content"))
                    else {
                        continue;
                    };
                    let ext = path.rsplit('.').next().unwrap_or("").to_string();
                    for hit in stele_context::symbols::extract(&ext, &content) {
                        rows.push(stele_context::json::Value::object(vec![
                            ("name", stele_context::json::Value::string(&hit.name)),
                            ("kind", stele_context::json::Value::string(&hit.kind)),
                            ("role", stele_context::json::Value::string(&hit.role)),
                            ("line", stele_context::json::Value::Int(hit.line as i64)),
                            ("chunk_id", stele_context::json::Value::string(&id)),
                            ("path", stele_context::json::Value::string(&path)),
                        ]));
                    }
                }
            }
        }
    }
    for (key, item) in pairs.iter_mut() {
        if key == "symbols" {
            *item = stele_context::json::Value::Array(rows.clone());
        }
    }
    pairs.retain(|(key, _)| key != "symbol_lines" && key != "edges");
    std::fs::write(&snapshot, stele_context::json::to_string(&value)).unwrap();
    // The first open converts it.
    let crc = cli(&project.dir, &["find-definition", "crc32"]);
    let expected = line_of(&project.dir, "src/checksum.rs", "pub fn crc32(");
    assert_eq!(number_after(&crc, "\"line_number\":"), expected, "{crc}");
    let refs = cli(&project.dir, &["find-references", "crc32"]);
    assert_eq!(
        files(&refs),
        vec!["src/binary.rs", "src/checksum.rs", "src/png.rs"],
        "{refs}"
    );
    let after = std::fs::read_to_string(&snapshot).unwrap();
    assert!(after.contains("\"symbol_lines\":\"file\""));
    assert!(after.contains("\"line_map\""));
}
