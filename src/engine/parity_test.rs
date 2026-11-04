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
    let script = r#"
import json, sys
from pathlib import Path
from stele_context.engine import Stele
root = Path(sys.argv[1])
store = Path(sys.argv[2])
engine = Stele(storage_dir=str(store), project_root=str(root))
engine.index_documents([str(root)])
payload = {
    "rebuild": engine.rebuild_symbol_graph(),
    "stale": engine.stale_chunks(),
    "sessions": engine.list_sessions(),
    "agents": engine.list_agents(),
    "notes": engine.get_notifications(),
    "prune": engine.prune_history(),
    "env": engine.check_environment(),
    "dynamic": engine.get_dynamic_symbols(),
    "history": engine.get_chunk_history(limit=5),
}
print(json.dumps(payload, default=str))
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
    let mut engine = Engine::open_at(root.clone(), root.join("rs-store")).unwrap();
    engine.index(&[".".to_string()], false).unwrap();
    let rebuild = engine.rebuild_symbols().unwrap();
    let stale = engine.stale_chunks(0.3, None);
    let sessions = engine.list_sessions(None);
    let agents = engine.list_agents(true);
    let notes = engine.notifications(None, None, 100);
    let prune = engine.prune_history(None, None).unwrap();
    let env = engine.environment_check();
    let dynamic = engine.dynamic_symbols(None);
    let history = engine.chunk_history(None, None, 5);
    assert_eq!(
        crate::store::int_field(&stale, "total_stale"),
        int_path(&py_value, &["stale", "total_stale"])
    );
    assert_eq!(
        array_len(&sessions),
        array_len_path(&py_value, &["sessions"])
    );
    assert_eq!(array_len(&agents), array_len_path(&py_value, &["agents"]));
    assert_eq!(
        crate::store::int_field(&notes, "count"),
        int_path(&py_value, &["notes", "count"])
    );
    assert_eq!(
        crate::store::int_field(&prune, "pruned"),
        int_path(&py_value, &["prune", "pruned"])
    );
    assert_eq!(
        crate::store::int_field(&env, "total_issues"),
        int_path(&py_value, &["env", "total_issues"])
    );
    assert_eq!(array_len(&dynamic), array_len_path(&py_value, &["dynamic"]));
    assert_eq!(array_len(&history), array_len_path(&py_value, &["history"]));
    let py_symbols = int_path(&py_value, &["rebuild", "symbols"]);
    let rs_symbols = crate::store::int_field(&rebuild, "symbols");
    assert_eq!(
        rs_symbols, py_symbols,
        "symbol count rust {rs_symbols} python {py_symbols}"
    );
    eprintln!("parity diffs: 0");
    let _ = fs::remove_dir_all(&root);
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
