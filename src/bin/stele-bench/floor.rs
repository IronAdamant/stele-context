//! Hard floor against the recorded Python 1.5.1 medians.

use crate::jsonutil::{as_array, as_f64, as_str, object_get};
use crate::proc::{self, Sample};
use crate::stats::{self, beats_floor};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Duration;
use stele_context::json;

struct Command {
    name: &'static str,
    args: &'static [&'static str],
    group: &'static str,
    floor: &'static str,
    fresh_store: bool,
}

const COMMANDS: &[Command] = &[
    Command {
        name: "cli --version (startup)",
        args: &["--version"],
        group: "b1",
        floor: "cli --version (startup)",
        fresh_store: false,
    },
    Command {
        name: "cli --help",
        args: &["--help"],
        group: "b1",
        floor: "cli --help",
        fresh_store: false,
    },
    Command {
        name: "cli index . (cold, empty store)",
        args: &["index", "."],
        group: "b4",
        floor: "cli index . (cold, empty store)",
        fresh_store: true,
    },
    Command {
        name: "cli index . (warm, nothing changed)",
        args: &["index", "."],
        group: "b5",
        floor: "cli index . (warm, nothing changed)",
        fresh_store: false,
    },
    Command {
        name: "cli detect (nothing changed)",
        args: &["detect"],
        group: "b5",
        floor: "cli detect (nothing changed)",
        fresh_store: false,
    },
    Command {
        name: "cli stats",
        args: &["stats"],
        group: "b6",
        floor: "cli stats",
        fresh_store: false,
    },
    Command {
        name: "cli doctor",
        args: &["doctor"],
        group: "b6",
        floor: "cli doctor",
        fresh_store: false,
    },
    Command {
        name: "cli map --json",
        args: &["map", "--json"],
        group: "b6",
        floor: "cli map --json",
        fresh_store: false,
    },
    Command {
        name: "cli project-brief --json",
        args: &["project-brief", "--json"],
        group: "b6",
        floor: "cli project-brief --json",
        fresh_store: false,
    },
    Command {
        name: "cli search hybrid --json",
        args: &[
            "search",
            "console markup",
            "--search-mode",
            "hybrid",
            "--json",
        ],
        group: "b6",
        floor: "cli search hybrid --json",
        fresh_store: false,
    },
    Command {
        name: "cli search keyword --json",
        args: &[
            "search",
            "console markup",
            "--search-mode",
            "keyword",
            "--json",
        ],
        group: "b6",
        floor: "cli search keyword --json",
        fresh_store: false,
    },
    Command {
        name: "cli search-text --json",
        args: &["search-text", "def ", "--json"],
        group: "b6",
        floor: "cli search-text --json",
        fresh_store: false,
    },
    Command {
        name: "cli agent-grep --json",
        args: &["agent-grep", "class Console", "--json"],
        group: "b6",
        floor: "cli agent-grep --json",
        fresh_store: false,
    },
    Command {
        name: "cli query --json",
        args: &["query", "console markup rendering", "--json"],
        group: "b6",
        floor: "cli query --json",
        fresh_store: false,
    },
    Command {
        name: "cli find-definition --json",
        args: &["find-definition", "Console", "--json"],
        group: "b6",
        floor: "cli find-definition --json",
        fresh_store: false,
    },
    Command {
        name: "cli find-references --json",
        args: &["find-references", "Console", "--json"],
        group: "b6",
        floor: "cli find-references --json",
        fresh_store: false,
    },
    Command {
        name: "cli impact-radius --json",
        args: &[
            "impact-radius",
            "--symbol",
            "Console",
            "--summary-mode",
            "--json",
        ],
        group: "b6",
        floor: "cli impact-radius --json",
        fresh_store: false,
    },
    Command {
        name: "cli coupling --json",
        args: &["coupling", "rich/console.py", "--json"],
        group: "b6",
        floor: "cli coupling --json",
        fresh_store: false,
    },
    Command {
        name: "cli enrichment-plan --json",
        args: &["enrichment-plan", "--json"],
        group: "b6",
        floor: "cli enrichment-plan --json",
        fresh_store: false,
    },
    Command {
        name: "cli history --json",
        args: &["history", "--json"],
        group: "b6",
        floor: "cli history --json",
        fresh_store: false,
    },
];

struct Summary {
    name: String,
    runs: usize,
    median_ms: f64,
    min_ms: f64,
    max_ms: f64,
    stdev_ms: f64,
    out_bytes: f64,
    rc: Vec<i32>,
    error: Option<String>,
}

pub(crate) struct FloorArgs {
    pub binary: PathBuf,
    pub groups: Vec<String>,
    pub runs: usize,
    pub cwd: PathBuf,
    pub storage_dir: Option<PathBuf>,
    pub check_floor: bool,
    pub json_out: Option<PathBuf>,
}

pub(crate) fn run(args: FloorArgs) -> Result<i32, String> {
    if args.runs < 1 {
        return Err("runs must be >= 1".to_string());
    }
    if !args.binary.is_file() {
        return Err(format!("binary not found: {}", args.binary.display()));
    }
    let selected: Vec<&Command> = if args.groups.is_empty() {
        COMMANDS.iter().collect()
    } else {
        COMMANDS
            .iter()
            .filter(|spec| args.groups.iter().any(|group| group == spec.group))
            .collect()
    };
    if selected.is_empty() {
        return Err(format!("no commands in groups {:?}", args.groups));
    }
    let floor = load_floor(Path::new("bench/baseline/summary.json"))?;
    let missing: Vec<&str> = selected
        .iter()
        .filter(|spec| !floor.contains_key(spec.floor))
        .map(|spec| spec.floor)
        .collect();
    if !missing.is_empty() {
        return Err(format!("floor table is missing {missing:?}"));
    }
    let mut env = proc::base_env();
    if let Some(dir) = &args.storage_dir {
        env.insert(
            "STELE_CONTEXT_STORAGE_DIR".to_string(),
            dir.display().to_string(),
        );
    }
    let mut summaries = Vec::new();
    for spec in &selected {
        let mut samples = Vec::new();
        for _ in 0..args.runs {
            if args.storage_dir.is_some() && spec.fresh_store {
                reset_store(args.storage_dir.as_ref().unwrap())?;
            }
            let mut argv = vec![args.binary.display().to_string()];
            if let Some(dir) = &args.storage_dir {
                argv.push("--storage-dir".to_string());
                argv.push(dir.display().to_string());
            }
            argv.extend(spec.args.iter().map(|arg| (*arg).to_string()));
            samples.push(proc::run_command(&argv, &args.cwd, &env, Duration::ZERO)?);
        }
        let summary = summarize(spec.name, &samples);
        println!(
            "{}: median {:.1} ms rc {:?}",
            summary.name, summary.median_ms, summary.rc
        );
        summaries.push(summary);
    }
    if let Some(path) = &args.json_out {
        let body = summaries
            .iter()
            .map(summary_json)
            .collect::<Vec<_>>()
            .join(",\n");
        std::fs::write(path, format!("[\n{body}\n]\n")).map_err(|err| err.to_string())?;
    }
    if summaries.iter().any(|row| row.error.is_some()) {
        return Ok(1);
    }
    if args.check_floor {
        let mut failed = Vec::new();
        for (spec, row) in selected.iter().zip(summaries.iter()) {
            let python = floor[spec.floor];
            if !beats_floor(row.median_ms, python) {
                failed.push(format!(
                    "{}: {:.1} ms >= python {python:.1} ms",
                    spec.name, row.median_ms
                ));
            }
        }
        if !failed.is_empty() {
            eprintln!("{}", failed.join("\n"));
            return Ok(1);
        }
    }
    Ok(0)
}

fn load_floor(path: &Path) -> Result<HashMap<String, f64>, String> {
    let text =
        std::fs::read_to_string(path).map_err(|err| format!("read {}: {err}", path.display()))?;
    let value = json::parse(&text).map_err(|err| format!("floor json: {}", err.message))?;
    let rows = object_get(&value, "results")
        .and_then(as_array)
        .ok_or_else(|| "floor table has no results".to_string())?;
    let mut map = HashMap::new();
    for row in rows {
        let Some(name) = object_get(row, "name").and_then(as_str) else {
            continue;
        };
        let Some(median_ms) = object_get(row, "median_ms").and_then(as_f64) else {
            continue;
        };
        map.insert(name.to_string(), median_ms);
    }
    Ok(map)
}

fn summarize(name: &str, samples: &[Sample]) -> Summary {
    let walls: Vec<f64> = samples.iter().map(|sample| sample.wall_ms).collect();
    let outs: Vec<f64> = samples
        .iter()
        .map(|sample| sample.out_bytes as f64)
        .collect();
    let mut rc: Vec<i32> = samples.iter().map(|sample| sample.rc).collect();
    rc.sort_unstable();
    rc.dedup();
    let error = samples
        .iter()
        .find(|sample| sample.rc != 0)
        .map(|sample| sample.stderr_tail.clone());
    Summary {
        name: name.to_string(),
        runs: samples.len(),
        median_ms: stats::median(&walls).unwrap_or(0.0),
        min_ms: walls.iter().copied().fold(f64::INFINITY, f64::min),
        max_ms: walls.iter().copied().fold(f64::NEG_INFINITY, f64::max),
        stdev_ms: pstdev(&walls),
        out_bytes: stats::median(&outs).unwrap_or(0.0),
        rc,
        error,
    }
}

fn pstdev(values: &[f64]) -> f64 {
    if values.len() < 2 {
        return 0.0;
    }
    let mean = values.iter().sum::<f64>() / values.len() as f64;
    let var = values
        .iter()
        .map(|value| (value - mean).powi(2))
        .sum::<f64>()
        / values.len() as f64;
    var.sqrt()
}

fn summary_json(row: &Summary) -> String {
    format!(
        "  {{\"name\":{},\"runs\":{},\"median_ms\":{:.4},\"min_ms\":{:.4},\"max_ms\":{:.4},\"stdev_ms\":{:.4},\"out_bytes\":{}}}",
        json::to_string(&json::Value::string(row.name.as_str())),
        row.runs,
        row.median_ms,
        row.min_ms,
        row.max_ms,
        row.stdev_ms,
        row.out_bytes
    )
}

fn reset_store(path: &Path) -> Result<(), String> {
    if path.exists() {
        std::fs::remove_dir_all(path).map_err(|err| err.to_string())?;
    }
    std::fs::create_dir_all(path).map_err(|err| err.to_string())
}
