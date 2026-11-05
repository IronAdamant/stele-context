//! Time Rust and Python 1.5.2 on one corpus, in one process.
//!
//! The corpus is a copy of parity/fixtures. Each row uses a fresh storage
//! directory. Warm rows index once, then time the command. A row's time is
//! the median of `--runs` wall-clock runs. Rust must be strictly faster on
//! every row. Python is the installed PyPI package stele-context 1.5.2, found
//! as `python::find_oracle` describes.

use crate::proc;
use crate::python::{self, find_oracle, OracleError, TempDir, ORACLE_VERSION};
use crate::stats;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Duration;

/// `subprocess.run(..., timeout=120)` in the script.
const TIMEOUT_SECS: u64 = 120;

pub(crate) struct VersusArgs {
    pub binary: PathBuf,
    pub runs: i64,
}

#[derive(PartialEq)]
enum Store {
    /// Global flags only: no storage directory.
    None,
    /// A fresh, empty storage directory.
    Cold,
    /// A fresh storage directory indexed once before timing.
    Warm,
}

pub(crate) fn run(args: VersusArgs) -> Result<i32, String> {
    let repo = Path::new(env!("CARGO_MANIFEST_DIR"));
    let binary = std::path::absolute(&args.binary)
        .map_err(|err| format!("{}: {err}", args.binary.display()))?;
    let python = match find_oracle() {
        Ok(python) => python,
        Err(OracleError::Version(found)) => {
            return Err(format!(
                "need PyPI stele-context {ORACLE_VERSION}, found {found}"
            ))
        }
        Err(OracleError::Missing(why)) => return Err(why),
    };
    let corpus = TempDir::new("stele-versus-")?;
    copy_tree(&repo.join("parity").join("fixtures"), corpus.path())?;
    let py_file = first_py(corpus.path())?;
    let rel_py = posix_relative(&py_file, corpus.path())?;
    let symbol = "def";
    let rows: Vec<(&str, Vec<&str>, Store)> = vec![
        ("cli --version", vec!["--version"], Store::None),
        ("cli --help", vec!["--help"], Store::None),
        ("cli index . (cold)", vec!["index", "."], Store::Cold),
        ("cli index . (warm)", vec!["index", "."], Store::Warm),
        ("cli detect", vec!["detect"], Store::Warm),
        ("cli stats", vec!["stats"], Store::Warm),
        ("cli doctor", vec!["doctor"], Store::Warm),
        ("cli map", vec!["map"], Store::Warm),
        ("cli project-brief", vec!["project-brief"], Store::Warm),
        (
            "cli search keyword",
            vec!["search", symbol, "--search-mode", "keyword"],
            Store::Warm,
        ),
        (
            "cli search hybrid",
            vec!["search", symbol, "--search-mode", "hybrid"],
            Store::Warm,
        ),
        ("cli search-text", vec!["search-text", "def "], Store::Warm),
        ("cli agent-grep", vec!["agent-grep", "def "], Store::Warm),
        ("cli query", vec!["query", "def"], Store::Warm),
        (
            "cli find-definition",
            vec!["find-definition", "main"],
            Store::Warm,
        ),
        (
            "cli find-references",
            vec!["find-references", "main"],
            Store::Warm,
        ),
        (
            "cli impact-radius",
            vec!["impact-radius", "--symbol", "main"],
            Store::Warm,
        ),
        (
            "cli coupling",
            vec!["coupling", rel_py.as_str()],
            Store::Warm,
        ),
        ("cli enrichment-plan", vec!["enrichment-plan"], Store::Warm),
        ("cli history", vec!["history"], Store::Warm),
    ];
    let env = child_env();
    let binary = binary.display().to_string();
    let py_cli = [python.as_str(), "-m", "stele_context.cli"];
    let rs_cli = [binary.as_str()];
    println!("{:<28} {:>10} {:>10} result", "row", "python ms", "rust ms");
    let mut failed = false;
    for (name, argv, store) in rows {
        let py_store = TempDir::new("stele-vs-py-")?;
        let rs_store = TempDir::new("stele-vs-rs-")?;
        let py = with_store(&py_cli, py_store.path(), &argv);
        let rs = with_store(&rs_cli, rs_store.path(), &argv);
        if store == Store::Warm {
            let index = ["index", "."];
            let py_index = with_store(&py_cli, py_store.path(), &index);
            let rs_index = with_store(&rs_cli, rs_store.path(), &index);
            check_run(&py_index, corpus.path(), &env)?;
            check_run(&rs_index, corpus.path(), &env)?;
        }
        let py_ms = time_command(&py, corpus.path(), &env, args.runs)?;
        let rs_ms = time_command(&rs, corpus.path(), &env, args.runs)?;
        drop(py_store);
        drop(rs_store);
        let ok = stats::beats_floor(rs_ms, py_ms);
        if !ok {
            failed = true;
        }
        println!(
            "{name:<28} {py_ms:10.1} {rs_ms:10.1} {}",
            if ok { "rust" } else { "FAIL" }
        );
    }
    Ok(if failed { 1 } else { 0 })
}

/// `--storage-dir` goes in front of a subcommand, never in front of a
/// global flag such as `--version`.
fn with_store(command: &[&str], store: &Path, args: &[&str]) -> Vec<String> {
    let mut argv: Vec<String> = command.iter().map(|part| part.to_string()).collect();
    if !args[0].starts_with("--") {
        argv.push("--storage-dir".to_string());
        argv.push(store.display().to_string());
    }
    argv.extend(args.iter().map(|arg| arg.to_string()));
    argv
}

/// The parent environment without `PYTHONPATH` and `STELE_CONTEXT_STORAGE_DIR`.
fn child_env() -> HashMap<String, String> {
    let dropped = ["PYTHONPATH", "STELE_CONTEXT_STORAGE_DIR"];
    std::env::vars()
        .filter(|(key, _)| {
            !dropped.iter().any(|name| {
                if cfg!(windows) {
                    key.eq_ignore_ascii_case(name)
                } else {
                    key == name
                }
            })
        })
        .collect()
}

fn time_command(
    argv: &[String],
    cwd: &Path,
    env: &HashMap<String, String>,
    runs: i64,
) -> Result<f64, String> {
    let mut samples = Vec::new();
    for _ in 0..runs.max(0) {
        let done = finish(argv, cwd, env)?;
        if !done.status.success() {
            let err = String::from_utf8_lossy(&done.stderr);
            let skip = err.chars().count().saturating_sub(400);
            let tail: String = err.chars().skip(skip).collect();
            return Err(format!(
                "{} exited {}\n{tail}",
                python::repr_list(argv),
                python::returncode(done.status)
            ));
        }
        samples.push(done.wall_ms);
    }
    stats::median(&samples).map_err(|_| "no median for empty data".to_string())
}

/// `subprocess.run(argv, check=True, capture_output=True, timeout=120)`.
fn check_run(argv: &[String], cwd: &Path, env: &HashMap<String, String>) -> Result<(), String> {
    let done = finish(argv, cwd, env)?;
    if done.status.success() {
        return Ok(());
    }
    Err(format!(
        "Command '{}' returned non-zero exit status {}.",
        python::repr_list(argv),
        python::returncode(done.status)
    ))
}

fn finish(
    argv: &[String],
    cwd: &Path,
    env: &HashMap<String, String>,
) -> Result<proc::Captured, String> {
    proc::capture(argv, cwd, env, Some(Duration::from_secs(TIMEOUT_SECS)))?.ok_or_else(|| {
        format!(
            "Command '{}' timed out after {TIMEOUT_SECS} seconds",
            python::repr_list(argv)
        )
    })
}

/// `shutil.copytree(src, dest, dirs_exist_ok=True)`: symlinks are followed,
/// and files and directories keep their access and modification times.
fn copy_tree(src: &Path, dest: &Path) -> Result<(), String> {
    let fail = |path: &Path, err: std::io::Error| format!("copy {}: {err}", path.display());
    fs::create_dir_all(dest).map_err(|err| fail(dest, err))?;
    for entry in fs::read_dir(src).map_err(|err| fail(src, err))? {
        let entry = entry.map_err(|err| fail(src, err))?;
        let from = entry.path();
        let to = dest.join(entry.file_name());
        let meta = fs::metadata(&from).map_err(|err| fail(&from, err))?;
        if meta.is_dir() {
            copy_tree(&from, &to)?;
        } else {
            fs::copy(&from, &to).map_err(|err| fail(&from, err))?;
            set_times(&to, &meta).map_err(|err| fail(&to, err))?;
        }
    }
    if let Ok(meta) = fs::metadata(src) {
        // Directory times are best effort, as some file systems refuse them.
        let _ = set_times(dest, &meta);
    }
    Ok(())
}

/// The owner may set times through a read-only handle on Unix. Windows needs
/// FILE_WRITE_ATTRIBUTES, and FILE_FLAG_BACKUP_SEMANTICS to open a directory.
fn set_times(path: &Path, meta: &fs::Metadata) -> std::io::Result<()> {
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.access_mode(0x0100).custom_flags(0x0200_0000);
    }
    let file = options.open(path)?;
    let mut times = fs::FileTimes::new();
    if let Ok(accessed) = meta.accessed() {
        times = times.set_accessed(accessed);
    }
    if let Ok(modified) = meta.modified() {
        times = times.set_modified(modified);
    }
    file.set_times(times)
}

/// `next(corpus.rglob("*.py"))` as CPython 3.13 walks it: matches directly in
/// the root, then a stack walk that lists each directory's matches when the
/// directory is first seen. Directory order is the OS order, as with
/// `os.scandir`. The match ignores case on Windows, like pathlib there.
fn first_py(root: &Path) -> Result<PathBuf, String> {
    if let Some(found) = match_in(root) {
        return Ok(found);
    }
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            if entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false) {
                let path = entry.path();
                if let Some(found) = match_in(&path) {
                    return Ok(found);
                }
                stack.push(path);
            }
        }
    }
    Err(format!("StopIteration: no *.py under {}", root.display()))
}

fn match_in(dir: &Path) -> Option<PathBuf> {
    let entries = fs::read_dir(dir).ok()?;
    entries.flatten().map(|entry| entry.path()).find(|path| {
        let name = path
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .unwrap_or_default();
        name.len() >= 3 && {
            let suffix = &name.as_bytes()[name.len() - 3..];
            if cfg!(windows) {
                suffix.eq_ignore_ascii_case(b".py")
            } else {
                suffix == b".py"
            }
        }
    })
}

fn posix_relative(path: &Path, base: &Path) -> Result<String, String> {
    let rel = path
        .strip_prefix(base)
        .map_err(|err| format!("{} is not under {}: {err}", path.display(), base.display()))?;
    let parts: Vec<String> = rel
        .components()
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect();
    Ok(parts.join("/"))
}
