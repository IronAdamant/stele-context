//! Tool and migrate comparison against the Python tree on `python-legacy`.

use super::Engine;
use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};

fn python_src() -> PathBuf {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let dest = repo.join("target").join("python-legacy-src");
    if dest.join("stele_context").join("engine.py").is_file() {
        return dest;
    }
    let archived = Command::new("git")
        .current_dir(&repo)
        .args(["archive", "origin/python-legacy", "stele_context"])
        .output()
        .expect("git archive");
    assert!(
        archived.status.success(),
        "git archive origin/python-legacy: {}",
        String::from_utf8_lossy(&archived.stderr)
    );
    let _ = fs::remove_dir_all(&dest);
    fs::create_dir_all(&dest).unwrap();
    let mut tar = Command::new("tar")
        .current_dir(&dest)
        .arg("-x")
        .stdin(Stdio::piped())
        .spawn()
        .expect("tar");
    tar.stdin
        .take()
        .unwrap()
        .write_all(&archived.stdout)
        .unwrap();
    let status = tar.wait().unwrap();
    assert!(status.success(), "tar extract failed");
    dest
}

fn python_bin() -> &'static str {
    if Command::new("python3").arg("--version").output().is_ok() {
        "python3"
    } else {
        "python"
    }
}

#[test]
fn migrate_reads_a_database_written_by_python_152() {
    let py = python_src();
    let root = std::env::temp_dir().join(format!("stele-mig-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    fs::write(root.join("sample.py"), "def classify(n):\n    return n\n").unwrap();
    let script = r#"
import json, sys
from pathlib import Path
from stele_context.engine import Stele
root = Path(sys.argv[1])
store = Path(sys.argv[2])
engine = Stele(storage_dir=str(store), project_root=str(root))
indexed = engine.index_documents([str(root / "sample.py")])
print(json.dumps({"chunks": indexed.get("total_chunks", 0)}))
"#;
    let store = root.join("py-store");
    let ran = Command::new(python_bin())
        .env("PYTHONPATH", &py)
        .arg("-c")
        .arg(script)
        .arg(&root)
        .arg(&store)
        .output()
        .expect("python");
    assert!(
        ran.status.success(),
        "python index failed\n{}",
        String::from_utf8_lossy(&ran.stderr)
    );
    assert!(
        store.join("stele_context.db").is_file(),
        "python did not write stele_context.db"
    );
    let native = root.join("native");
    fs::create_dir_all(&native).unwrap();
    for name in [
        "stele_context.db",
        "stele_context.db-wal",
        "stele_context.db-shm",
        "stele_context.db-journal",
    ] {
        let from = store.join(name);
        if from.is_file() {
            fs::copy(&from, native.join(name)).unwrap();
        }
    }
    let engine = Engine::open_at(root.clone(), native.clone()).unwrap();
    let stats = engine.stats();
    let err =
        fs::read_to_string(native.join("store").join("migrate-error.txt")).unwrap_or_default();
    assert!(
        crate::store::int_field(&stats, "documents") >= 1,
        "migrate did not load documents: {} error: {err}",
        crate::json::to_string(&stats)
    );
    assert!(
        crate::store::int_field(&stats, "chunks") >= 1,
        "migrate did not load chunks"
    );
    let defs = engine.find_definition("classify");
    let rendered = crate::json::to_string(&defs);
    assert!(rendered.contains("classify"), "{rendered}");
    let marker =
        fs::read_to_string(native.join("store").join("migrated-from.txt")).unwrap_or_default();
    assert!(marker.contains("stele_context.db"), "{marker}");
    let _ = fs::remove_dir_all(&root);
}

#[test]
fn standard_tools_match_python_on_a_one_file_project() {
    let py = python_src();
    let root = std::env::temp_dir().join(format!("stele-par-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(root.join("src")).unwrap();
    fs::write(
        root.join("src").join("sample.py"),
        "def classify(n):\n    return n\n",
    )
    .unwrap();
    fs::write(root.join(".gitignore"), "skip.py\n").unwrap();
    fs::write(root.join("skip.py"), "def skip():\n    return 0\n").unwrap();
    fs::create_dir_all(root.join("vendor")).unwrap();
    fs::write(
        root.join("vendor").join("hidden.py"),
        "def hidden():\n    return 1\n",
    )
    .unwrap();
    fs::write(
        root.join("src").join("app.js"),
        "function greet(){ return 1 }\n",
    )
    .unwrap();
    fs::write(root.join("notes.md"), "# notes\n\nclassify the input\n").unwrap();
    fs::write(
        root.join(".stele-context.toml"),
        "[stele-context]\nchunk_size = 256\nskip_dirs = [\"vendor\"]\nrespect_gitignore = true\nmerge_threshold = 0.7\nchange_threshold = 0.85\nsearch_alpha = 0.42\nmax_history_entries = 1000\n",
    )
    .unwrap();
    fs::write(
        root.join("pyproject.toml"),
        "[project]\nname = \"stele-fixture\"\nversion = \"9.9.9\"\n",
    )
    .unwrap();
    fs::create_dir_all(root.join("Other.egg-info")).unwrap();
    fs::write(
        root.join("Other.egg-info").join("PKG-INFO"),
        "Name: other\nVersion: 0.1.0\n",
    )
    .unwrap();
    fs::copy(
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("parity/fixtures/media/note.pdf"),
        root.join("note.pdf"),
    )
    .unwrap();
    fs::create_dir_all(root.join(".git")).unwrap();
    let script = r#"
import json, sys
from pathlib import Path
from stele_context.engine import Stele
from stele_context.llm_embedding import embed
root = Path(sys.argv[1])
store = Path(sys.argv[2])
engine = Stele(storage_dir=str(store), project_root=str(root))
indexed = engine.index_documents([str(root)])
paths = sorted(p for p in (d["document_path"] for d in engine.storage.get_all_documents()) if not p.endswith("PKG-INFO") and Path(p).name not in {"pyproject.toml", ".stele-context.toml", ".gitignore"})
text = (root / "src" / "sample.py").read_text()
payload = {
    "index": indexed,
    "paths": paths,
    "search": engine.search("classify", search_mode="keyword"),
    "define": engine.find_definition("classify"),
    "refs": engine.find_references("classify"),
    "grep": engine.agent_grep("classify"),
    "text": engine.search_text("classify", regex=False),
    "context": engine.get_context(["src/sample.py"]),
    "detect": engine.detect_changes_and_update(),
    "env": engine.check_environment(),
    "rebuild_symbols": engine.rebuild_symbol_graph()["symbols"],
    "stale": engine.stale_chunks()["total_stale"],
    "notes": engine.get_notifications()["count"],
    "embed0": round(embed(text)[0], 6),
}
reg = engine.register_dynamic_symbols([{"name": "on_hook", "document_path": "src/sample.py"}], "agent-a")
payload["registered"] = reg.get("stored", 0)
payload["removed_dyn"] = engine.remove_dynamic_symbols("agent-a").get("removed", 0)
ann = engine.annotate("src/sample.py", "document", "note", None)
payload["annotated"] = 1 if ann.get("id") else 0
lock = engine.acquire_document_lock("src/sample.py", "agent-a")
payload["locked"] = 1 if lock.get("acquired") else 0
engine.release_document_lock("src/sample.py", "agent-a")
print(json.dumps(payload))
"#;
    let py_store = root.join("py-store");
    let ran = Command::new(python_bin())
        .env("PYTHONPATH", &py)
        .arg("-c")
        .arg(script)
        .arg(&root)
        .arg(&py_store)
        .output()
        .expect("python");
    assert!(
        ran.status.success(),
        "{}",
        String::from_utf8_lossy(&ran.stderr)
    );
    let py_text = String::from_utf8_lossy(&ran.stdout);
    let py_value = crate::json::parse(py_text.trim()).expect("python json");
    let rs_store = std::env::temp_dir().join(format!("stele-par-rs-{}", std::process::id()));
    let _ = fs::remove_dir_all(&rs_store);
    let mut engine = Engine::open_at(root.clone(), rs_store).unwrap();
    let indexed = engine.index(&[".".to_string()], false).unwrap();
    let mut paths: Vec<String> = engine
        .store_paths()
        .into_iter()
        .filter(|path| {
            let name = path.rsplit('/').next().unwrap_or(path);
            !path.ends_with("PKG-INFO")
                && name != "pyproject.toml"
                && name != ".stele-context.toml"
                && name != ".gitignore"
        })
        .collect();
    paths.sort();
    let notes = crate::store::int_field(&engine.notifications(None, None, 100), "count");
    let text = fs::read_to_string(root.join("src/sample.py")).unwrap();
    let embed = super::fingerprint::embed_text(&text);
    let reg = engine.register_dynamic("on_hook", "function", "src/sample.py");
    if let Some(sym) = engine.store_dynamic_last() {
        sym.agent = "agent-a".to_string();
        sym.chunk_id = "runtime:agent-a:on_hook".to_string();
    }
    let removed_dyn = engine.remove_agent_dynamic("agent-a");
    let ann = engine
        .annotations("create", "src/sample.py", "note")
        .unwrap();
    let lock = engine
        .document_lock("acquire", "src/sample.py", "agent-a", false)
        .unwrap();
    let _ = engine.document_lock("release", "src/sample.py", "agent-a", false);
    let search = engine.search("classify", "keyword", 10);
    let defs = engine.find_definition("classify");
    let refs = engine.find_references("classify");
    let grep = engine
        .grep_full(
            "classify", false, None, true, true, "file", 4000, true, 0, None, false,
        )
        .unwrap();
    let text_hit = engine
        .text_search("classify", false, None, 50, None, false)
        .unwrap();
    let context = engine
        .get_context("src/sample.py", None)
        .unwrap_or(crate::json::Value::Null);
    let detect = engine.detect(true).unwrap();
    let env = engine.environment_check();
    let root_text = root.display().to_string();
    let mut diffs = 0i64;
    let py_paths = array_len_path(&py_value, &["paths"]) as i64;
    note_diff(&mut diffs, "paths", paths.len() as i64, py_paths);
    let rebuild = engine.rebuild_symbols().unwrap();
    note_diff(
        &mut diffs,
        "rebuild_symbols",
        crate::store::int_field(&rebuild, "symbols"),
        int_path(&py_value, &["rebuild_symbols"]),
    );
    note_diff(
        &mut diffs,
        "stale",
        crate::store::int_field(&engine.stale_chunks(0.3, None), "total_stale"),
        int_path(&py_value, &["stale"]),
    );
    note_diff(&mut diffs, "notes", notes, int_path(&py_value, &["notes"]));
    let embed_py = match object_get(&py_value, "embed0") {
        Some(crate::json::Value::Float(n)) => *n,
        Some(crate::json::Value::Int(n)) => *n as f64,
        _ => -1.0,
    };
    note_diff(
        &mut diffs,
        "embed0",
        if (embed[0] - embed_py).abs() > 1e-5 {
            1
        } else {
            0
        },
        0,
    );
    for (name, rust_value) in [
        ("index", &indexed),
        ("search", &search),
        ("define", &defs),
        ("refs", &refs),
        ("grep", &grep),
        ("text", &text_hit),
        ("context", &context),
        ("detect", &detect),
        ("env", &env),
    ] {
        let Some(py_item) = object_get(&py_value, name) else {
            eprintln!("parity diff {name}: missing from python");
            diffs += 1;
            continue;
        };
        let left = canonical(rust_value, &root_text, name);
        let right = canonical(py_item, &root_text, name);
        if left != right {
            eprintln!(
                "parity diff {name}:\n rust {}\n python {}",
                crate::json::to_string(&left),
                crate::json::to_string(&right)
            );
            diffs += 1;
        }
    }
    note_diff(
        &mut diffs,
        "registered",
        if reg.is_ok() { 1 } else { 0 },
        int_path(&py_value, &["registered"]),
    );
    note_diff(
        &mut diffs,
        "removed_dyn",
        removed_dyn,
        int_path(&py_value, &["removed_dyn"]),
    );
    note_diff(
        &mut diffs,
        "annotated",
        if crate::json::to_string(&ann).contains("id") {
            1
        } else {
            0
        },
        int_path(&py_value, &["annotated"]),
    );
    note_diff(
        &mut diffs,
        "locked",
        if crate::json::to_string(&lock).contains("\"acquired\":true") {
            1
        } else {
            0
        },
        int_path(&py_value, &["locked"]),
    );
    assert!(
        !paths.iter().any(|p| p.contains("skip.py")),
        "gitignored file indexed"
    );
    assert!(
        !paths.iter().any(|p| p.contains("vendor")),
        "skip_dirs file indexed"
    );
    eprintln!("parity diffs: {diffs}");
    assert_eq!(diffs, 0, "parity diffs: {diffs}");
    let _ = fs::remove_dir_all(&root);
}

fn note_diff(diffs: &mut i64, name: &str, rust: i64, python: i64) {
    if rust != python {
        eprintln!("parity diff {name}: rust {rust} python {python}");
        *diffs += 1;
    }
}

fn int_path(value: &crate::json::Value, keys: &[&str]) -> i64 {
    let mut cur = value;
    for key in keys {
        cur = object_get(cur, key).unwrap_or(&crate::json::Value::Null);
    }
    match cur {
        crate::json::Value::Int(number) => *number,
        crate::json::Value::Float(number) => *number as i64,
        _ => panic!(
            "missing int at {keys:?} in {}",
            crate::json::to_string(value)
        ),
    }
}

fn canonical(value: &crate::json::Value, root: &str, key: &str) -> crate::json::Value {
    use crate::json::Value::{Array, Float, Int, Object, String};
    match value {
        Object(pairs) => {
            let mut out = Vec::new();
            for (name, item) in pairs {
                let child = if matches!(
                    name.as_str(),
                    "document_indexed_at"
                        | "stored_last_modified"
                        | "file_mtime"
                        | "created_at"
                        | "updated_at"
                        | "latest_timestamp"
                        | "at"
                        | "until"
                ) {
                    Int(0)
                } else {
                    canonical(item, root, name)
                };
                out.push((name.clone(), child));
            }
            out.sort_by(|left, right| left.0.cmp(&right.0));
            Object(out)
        }
        Array(rows) => {
            let mut rows: Vec<_> = rows.iter().map(|row| canonical(row, root, key)).collect();
            if matches!(
                key,
                "indexed"
                    | "skipped"
                    | "unchanged"
                    | "files_with_matches"
                    | "paths"
                    | "new"
                    | "removed"
            ) {
                rows.sort_by(|left, right| sort_key(left).cmp(&sort_key(right)));
            }
            Array(rows)
        }
        String(text) => {
            let text = text.replace(root, "").replace("\r\n", "\n");
            String(text)
        }
        Float(number) => Float((number * 1_000_000.0).round() / 1_000_000.0),
        other => other.clone(),
    }
}

fn sort_key(value: &crate::json::Value) -> String {
    if let crate::json::Value::String(text) = value {
        return text.clone();
    }
    for key in ["path", "document_path", "key"] {
        if let Some(crate::json::Value::String(text)) = object_get(value, key) {
            return text.clone();
        }
    }
    crate::json::to_string(value)
}

fn array_len(value: &crate::json::Value) -> usize {
    match value {
        crate::json::Value::Array(rows) => rows.len(),
        _ => 0,
    }
}

fn array_len_path(value: &crate::json::Value, keys: &[&str]) -> usize {
    let mut cur = value;
    for key in keys {
        cur = object_get(cur, key).unwrap_or(&crate::json::Value::Null);
    }
    array_len(cur)
}

fn object_get<'a>(value: &'a crate::json::Value, key: &str) -> Option<&'a crate::json::Value> {
    let crate::json::Value::Object(pairs) = value else {
        return None;
    };
    pairs
        .iter()
        .find(|(name, _)| name == key)
        .map(|(_, item)| item)
}
