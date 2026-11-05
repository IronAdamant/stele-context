//! Interleaved base-versus-head timings of the Rust binaries.
//!
//! At least 15 runs of each tracked command are taken on one runner. CI uses
//! 16 so each side leads exactly half the pairs. A benchmark fails only when
//! the median intervals do not overlap and Mann-Whitney agrees. tools/list
//! reply size is recorded and is not a failure by itself.

use crate::proc::{self, base_env};
use crate::stats::{self, Verdict, MIN_RUNS};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

const BENCHES: &[&str] = &[
    "cli-version",
    "cli-help",
    "mcp-initialize",
    "mcp-tools-list",
    "index-cold",
    "index-warm",
    "detect",
    "search-keyword",
];

struct Side {
    binary: PathBuf,
    mcp: PathBuf,
    project: PathBuf,
}

struct Row {
    name: String,
    baseline_median: f64,
    candidate_median: f64,
    baseline_ci: (f64, f64),
    candidate_ci: (f64, f64),
    mannwhitney_p: f64,
    ci_slower: bool,
    mw_slower: bool,
    reject: bool,
}

pub(crate) struct Report {
    pub base: String,
    pub head: String,
    pub failed: bool,
    rows: Vec<Row>,
    list_base: f64,
    list_head: f64,
}

pub(crate) fn run(
    repo: &Path,
    base: &str,
    head: &str,
    runs: usize,
    inject_detect_ms: f64,
) -> Result<Report, String> {
    let base_sha = git(repo, &["rev-parse", base])?;
    let head_sha = git(repo, &["rev-parse", head])?;
    let root = std::env::temp_dir().join(format!(
        "stele-gate-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    fs::create_dir_all(&root).map_err(|err| err.to_string())?;
    let mut cleanup = Cleanup {
        repo: repo.to_path_buf(),
        worktrees: Vec::new(),
        root: Some(root.clone()),
    };
    let base_dir = root.join("base");
    let head_dir = root.join("head");
    prepare(repo, &base_sha, &base_dir)?;
    cleanup.worktrees.push(base_dir.clone());
    let same = base_sha == head_sha;
    if !same {
        prepare(repo, &head_sha, &head_dir)?;
        cleanup.worktrees.push(head_dir.clone());
    }
    println!("building {base_sha}");
    let (base_bin, base_mcp) = cargo_build(&base_dir)?;
    let (head_bin, head_mcp) = if same {
        (base_bin.clone(), base_mcp.clone())
    } else {
        println!("building {head_sha}");
        cargo_build(&head_dir)?
    };
    let head_src = if same { &base_dir } else { &head_dir };
    let shared = head_src.join("parity").join("fixtures");
    let sides = [
        (
            "base",
            Side {
                binary: base_bin,
                mcp: base_mcp,
                project: isolate_from(&shared, &root.join("proj-base"))?,
            },
        ),
        (
            "head",
            Side {
                binary: head_bin,
                mcp: head_mcp,
                project: isolate_from(&shared, &root.join("proj-head"))?,
            },
        ),
    ];
    let mut samples: Vec<(Vec<f64>, Vec<f64>)> =
        BENCHES.iter().map(|_| (Vec::new(), Vec::new())).collect();
    let mut list_base = Vec::new();
    let mut list_head = Vec::new();
    for index in 0..runs {
        let order = if index % 2 == 0 { [0, 1] } else { [1, 0] };
        println!(
            "run {}/{} order {} {}",
            index + 1,
            runs,
            sides[order[0]].0,
            sides[order[1]].0
        );
        for slot in order {
            let (label, side) = &sides[slot];
            let delay = if *label == "head" {
                Duration::from_secs_f64(inject_detect_ms / 1000.0)
            } else {
                Duration::ZERO
            };
            let (measured, list_bytes) = measure(side, delay)?;
            let bucket = if *label == "base" {
                &mut list_base
            } else {
                &mut list_head
            };
            bucket.push(list_bytes as f64);
            for (name_index, _) in BENCHES.iter().enumerate() {
                let value = measured[name_index];
                if *label == "base" {
                    samples[name_index].0.push(value);
                } else {
                    samples[name_index].1.push(value);
                }
            }
        }
    }
    let report = verdict(&base_sha, &head_sha, &samples, &list_base, &list_head)?;
    drop(cleanup);
    Ok(report)
}

fn measure(side: &Side, detect_delay: Duration) -> Result<([f64; 8], usize), String> {
    let env = base_env();
    let empty = std::env::temp_dir().join(format!(
        "stele-gate-cli-{}-{}",
        std::process::id(),
        unique_suffix()
    ));
    fs::create_dir_all(&empty).map_err(|err| err.to_string())?;
    let version = proc::run_ok(
        &[side.binary.display().to_string(), "--version".to_string()],
        &empty,
        &env,
        Duration::ZERO,
    );
    let help = proc::run_ok(
        &[side.binary.display().to_string(), "--help".to_string()],
        &empty,
        &env,
        Duration::ZERO,
    );
    let _ = fs::remove_dir_all(&empty);
    let version = version?;
    let help = help?;
    let (initialize_ms, list_ms, list_bytes) = proc::mcp_handshake(&side.mcp)?;
    let indexed = index_suite(&side.binary, &side.project, detect_delay)?;
    Ok((
        [
            version,
            help,
            initialize_ms,
            list_ms,
            indexed[0],
            indexed[1],
            indexed[2],
            indexed[3],
        ],
        list_bytes,
    ))
}

fn index_suite(binary: &Path, project: &Path, detect_delay: Duration) -> Result<[f64; 4], String> {
    let storage = std::env::temp_dir().join(format!("stele-gate-index-{}", unique_suffix()));
    let _ = fs::remove_dir_all(&storage);
    fs::create_dir_all(&storage).map_err(|err| err.to_string())?;
    let env = base_env();
    let base = [
        binary.display().to_string(),
        "--storage-dir".to_string(),
        storage.display().to_string(),
    ];
    let cold = proc::run_ok(
        &[
            base[0].clone(),
            base[1].clone(),
            base[2].clone(),
            "index".into(),
            ".".into(),
        ],
        project,
        &env,
        Duration::ZERO,
    );
    let warm = proc::run_ok(
        &[
            base[0].clone(),
            base[1].clone(),
            base[2].clone(),
            "index".into(),
            ".".into(),
        ],
        project,
        &env,
        Duration::ZERO,
    );
    let detect = proc::run_ok(
        &[
            base[0].clone(),
            base[1].clone(),
            base[2].clone(),
            "detect".into(),
        ],
        project,
        &env,
        detect_delay,
    );
    let search = proc::run_ok(
        &[
            base[0].clone(),
            base[1].clone(),
            base[2].clone(),
            "search".into(),
            "index_documents".into(),
            "--search-mode".into(),
            "keyword".into(),
            "--json".into(),
        ],
        project,
        &env,
        Duration::ZERO,
    );
    let _ = fs::remove_dir_all(&storage);
    Ok([cold?, warm?, detect?, search?])
}

fn isolate_from(src: &Path, dest: &Path) -> Result<PathBuf, String> {
    if !src.is_dir() {
        return Err(format!("missing fixtures in {}", src.display()));
    }
    copy_dir(src, dest)?;
    // Mark the copy as a project root, as a checkout with `.git` is. Without
    // a marker 2.1 (like 1.5.2) has no project root and stores absolute
    // paths, which is not the case the gate is meant to time.
    fs::write(dest.join(".stele-context.toml"), "").map_err(|err| err.to_string())?;
    Ok(dest.to_path_buf())
}

fn copy_dir(src: &Path, dest: &Path) -> Result<(), String> {
    fs::create_dir_all(dest).map_err(|err| err.to_string())?;
    for entry in fs::read_dir(src).map_err(|err| err.to_string())? {
        let entry = entry.map_err(|err| err.to_string())?;
        let to = dest.join(entry.file_name());
        let kind = entry.file_type().map_err(|err| err.to_string())?;
        if kind.is_dir() {
            copy_dir(&entry.path(), &to)?;
        } else if kind.is_file() {
            fs::copy(entry.path(), &to).map_err(|err| err.to_string())?;
        }
    }
    Ok(())
}

fn cargo_build(dir: &Path) -> Result<(PathBuf, PathBuf), String> {
    let target = dir.join("target");
    let output = Command::new("cargo")
        .current_dir(dir)
        .args([
            "build",
            "--release",
            "--bin",
            "stele-context",
            "--bin",
            "stele-context-mcp",
        ])
        .env("CARGO_TARGET_DIR", &target)
        .output()
        .map_err(|err| format!("cargo: {err}"))?;
    if !output.status.success() {
        let err = String::from_utf8_lossy(&output.stderr);
        let start = err.len().saturating_sub(2000);
        let start = (start..=err.len())
            .find(|index| err.is_char_boundary(*index))
            .unwrap_or(err.len());
        return Err(format!(
            "cargo build failed in {}\n{}",
            dir.display(),
            &err[start..]
        ));
    }
    let suffix = std::env::consts::EXE_SUFFIX;
    let release = target.join("release");
    let bin = release.join(format!("stele-context{suffix}"));
    let mcp = release.join(format!("stele-context-mcp{suffix}"));
    if !bin.is_file() || !mcp.is_file() {
        return Err(format!("missing release binaries in {}", release.display()));
    }
    Ok((bin, mcp))
}

fn prepare(repo: &Path, sha: &str, dest: &Path) -> Result<(), String> {
    git(
        repo,
        &[
            "worktree",
            "add",
            "--detach",
            &dest.display().to_string(),
            sha,
        ],
    )?;
    Ok(())
}

fn git(repo: &Path, args: &[&str]) -> Result<String, String> {
    let output = Command::new("git")
        .args(args)
        .current_dir(repo)
        .output()
        .map_err(|err| format!("git: {err}"))?;
    if !output.status.success() {
        return Err(format!(
            "git {args:?} failed: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
}

fn verdict(
    base: &str,
    head: &str,
    samples: &[(Vec<f64>, Vec<f64>)],
    list_base: &[f64],
    list_head: &[f64],
) -> Result<Report, String> {
    let mut rows = Vec::new();
    let mut failed = false;
    for (index, name) in BENCHES.iter().enumerate() {
        let (base_sample, head_sample) = &samples[index];
        if base_sample.len() < MIN_RUNS || head_sample.len() < MIN_RUNS {
            rows.push(Row {
                name: (*name).to_string(),
                baseline_median: stats::median(base_sample)?,
                candidate_median: stats::median(head_sample)?,
                baseline_ci: (
                    base_sample.iter().copied().fold(f64::INFINITY, f64::min),
                    base_sample
                        .iter()
                        .copied()
                        .fold(f64::NEG_INFINITY, f64::max),
                ),
                candidate_ci: (
                    head_sample.iter().copied().fold(f64::INFINITY, f64::min),
                    head_sample
                        .iter()
                        .copied()
                        .fold(f64::NEG_INFINITY, f64::max),
                ),
                mannwhitney_p: 1.0,
                ci_slower: false,
                mw_slower: false,
                reject: false,
            });
            continue;
        }
        let outcome: Verdict = stats::regression(base_sample, head_sample)?;
        if outcome.reject {
            failed = true;
        }
        rows.push(Row {
            name: (*name).to_string(),
            baseline_median: outcome.baseline_median,
            candidate_median: outcome.candidate_median,
            baseline_ci: outcome.baseline_ci,
            candidate_ci: outcome.candidate_ci,
            mannwhitney_p: outcome.mannwhitney_p,
            ci_slower: outcome.ci_slower,
            mw_slower: outcome.mw_slower,
            reject: outcome.reject,
        });
    }
    Ok(Report {
        base: base.to_string(),
        head: head.to_string(),
        failed,
        rows,
        list_base: stats::median(list_base).unwrap_or(0.0),
        list_head: stats::median(list_head).unwrap_or(0.0),
    })
}

pub(crate) fn print_report(report: &Report) {
    println!("base {}", report.base);
    println!("head {}", report.head);
    println!(
        "{:<18} {:>10} {:>10} {:>8} {:>8}",
        "benchmark", "base ms", "head ms", "p", "verdict"
    );
    for row in &report.rows {
        let verdict = if row.reject { "FAIL" } else { "pass" };
        println!(
            "{:<18} {:10.1} {:10.1} {:8.3} {:>8}",
            row.name, row.baseline_median, row.candidate_median, row.mannwhitney_p, verdict
        );
        if row.reject {
            println!(
                "  base CI {:?} head CI {:?} ci_slower={} mw_slower={}",
                row.baseline_ci, row.candidate_ci, row.ci_slower, row.mw_slower
            );
        }
    }
    println!(
        "tools/list bytes base {} head {} (reported, not gated)",
        report.list_base, report.list_head
    );
}

pub(crate) fn write_summary(report: &Report) {
    let Some(path) = std::env::var_os("GITHUB_STEP_SUMMARY") else {
        return;
    };
    let mut lines = vec![
        "## Benchmark gate (D13)".to_string(),
        String::new(),
        format!("Base `{}` versus head `{}`.", report.base, report.head),
        String::new(),
        "| Benchmark | Base median ms | Head median ms | Mann-Whitney p | Result |".to_string(),
        "|---|---:|---:|---:|---|".to_string(),
    ];
    for row in &report.rows {
        let result = if row.reject { "fail" } else { "pass" };
        lines.push(format!(
            "| {} | {:.1} | {:.1} | {:.3} | {result} |",
            row.name, row.baseline_median, row.candidate_median, row.mannwhitney_p
        ));
    }
    lines.push(String::new());
    lines.push(format!(
        "tools/list median bytes: base {}, head {}.",
        report.list_base, report.list_head
    ));
    let mut body = lines.join("\n");
    body.push('\n');
    if let Ok(existing) = fs::read_to_string(&path) {
        let _ = fs::write(&path, existing + &body);
    } else {
        let _ = fs::write(&path, body);
    }
}

fn unique_suffix() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

struct Cleanup {
    repo: PathBuf,
    worktrees: Vec<PathBuf>,
    root: Option<PathBuf>,
}

impl Drop for Cleanup {
    fn drop(&mut self) {
        for dir in &self.worktrees {
            let _ = Command::new("git")
                .args(["worktree", "remove", "--force"])
                .arg(dir)
                .current_dir(&self.repo)
                .status();
            let _ = fs::remove_dir_all(dir);
        }
        if let Some(root) = &self.root {
            let _ = fs::remove_dir_all(root);
        }
    }
}
