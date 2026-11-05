//! Host-side benchmark tool. It is not part of the release archives.
//! `floor` checks a binary against the recorded Python 1.5.1 medians.
//! `gate` compares two commits on one machine.
//! `versus` times a binary against PyPI stele-context 1.5.2 on one corpus.

mod floor;
mod gate;
mod jsonutil;
mod proc;
mod python;
mod stats;
mod versus;

use floor::FloorArgs;
use std::env;
use std::path::PathBuf;
use versus::VersusArgs;

fn main() {
    let code = match dispatch() {
        Ok(code) => code,
        Err(message) => {
            eprintln!("{message}");
            1
        }
    };
    std::process::exit(code);
}

fn dispatch() -> Result<i32, String> {
    let mut args = env::args().skip(1);
    let Some(command) = args.next() else {
        return Err(usage());
    };
    match command.as_str() {
        "floor" => floor::run(parse_floor(&mut args)?),
        "gate" => run_gate(&mut args),
        "versus" => versus::run(parse_versus(&mut args)?),
        "-h" | "--help" | "help" => {
            println!("{}", usage());
            Ok(0)
        }
        other => Err(format!("unknown command '{other}'\n{}", usage())),
    }
}

fn run_gate(args: &mut impl Iterator<Item = String>) -> Result<i32, String> {
    let mut runs = stats::MIN_RUNS;
    let mut allow_short = false;
    let mut base = env::var("GATE_BASE_SHA").unwrap_or_else(|_| "HEAD".to_string());
    let mut head = env::var("GATE_HEAD_SHA").unwrap_or_else(|_| "HEAD".to_string());
    let mut inject_detect_ms = 0.0;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--runs" => {
                runs = take(args, "--runs")?
                    .parse()
                    .map_err(|_| "--runs needs an integer")?
            }
            "--allow-short" => allow_short = true,
            "--base" => base = take(args, "--base")?,
            "--head" => head = take(args, "--head")?,
            "--inject-detect-ms" => {
                inject_detect_ms = take(args, "--inject-detect-ms")?
                    .parse()
                    .map_err(|_| "--inject-detect-ms needs a number")?;
            }
            other => return Err(format!("unknown flag '{other}'")),
        }
    }
    stats::require_runs(runs, allow_short)?;
    if inject_detect_ms < 0.0 {
        return Err("--inject-detect-ms must be >= 0".to_string());
    }
    let repo = env::current_dir().map_err(|err| err.to_string())?;
    let report = gate::run(&repo, &base, &head, runs, inject_detect_ms)?;
    gate::print_report(&report);
    gate::write_summary(&report);
    Ok(if report.failed { 1 } else { 0 })
}

fn parse_floor(args: &mut impl Iterator<Item = String>) -> Result<FloorArgs, String> {
    let mut binary = None;
    let mut groups = Vec::new();
    let mut runs = 1usize;
    let mut cwd = env::current_dir().map_err(|err| err.to_string())?;
    let mut storage_dir = None;
    let mut check_floor = false;
    let mut json_out = None;
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--binary" => binary = Some(PathBuf::from(take(args, "--binary")?)),
            "--group" => groups.push(take(args, "--group")?),
            "--runs" => {
                runs = take(args, "--runs")?
                    .parse()
                    .map_err(|_| "--runs needs an integer")?
            }
            "--cwd" => cwd = PathBuf::from(take(args, "--cwd")?),
            "--storage-dir" => storage_dir = Some(PathBuf::from(take(args, "--storage-dir")?)),
            "--check-floor" => check_floor = true,
            "--json-out" => json_out = Some(PathBuf::from(take(args, "--json-out")?)),
            other => return Err(format!("unknown flag '{other}'")),
        }
    }
    let binary = binary.ok_or_else(|| "--binary is required".to_string())?;
    Ok(FloorArgs {
        binary,
        groups,
        runs,
        cwd,
        storage_dir,
        check_floor,
        json_out,
    })
}

fn parse_versus(args: &mut impl Iterator<Item = String>) -> Result<VersusArgs, String> {
    let mut binary = None;
    let mut runs = 3i64;
    while let Some(arg) = args.next() {
        let (flag, inline) = match arg.split_once('=') {
            Some((flag, value)) if flag.starts_with("--") => {
                (flag.to_string(), Some(value.to_string()))
            }
            _ => (arg, None),
        };
        let mut value = |name: &str| match &inline {
            Some(value) => Ok(value.clone()),
            None => take(args, name),
        };
        match flag.as_str() {
            "--binary" => binary = Some(PathBuf::from(value("--binary")?)),
            "--runs" => {
                runs = value("--runs")?
                    .trim()
                    .parse()
                    .map_err(|_| "--runs needs an integer")?
            }
            other => return Err(format!("unknown flag '{other}'")),
        }
    }
    let binary = binary.ok_or_else(|| "--binary is required".to_string())?;
    Ok(VersusArgs { binary, runs })
}

fn take(args: &mut impl Iterator<Item = String>, flag: &str) -> Result<String, String> {
    args.next().ok_or_else(|| format!("{flag} needs a value"))
}

fn usage() -> String {
    "\
stele-bench floor --binary PATH [--group b1] [--runs N] [--check-floor]
stele-bench gate [--runs N] [--base SHA] [--head SHA] [--allow-short]
stele-bench versus --binary PATH [--runs N]

floor compares one binary with bench/baseline/summary.json.
gate builds two commits and fails when the head is slower beyond noise.
versus times the binary and PyPI stele-context 1.5.2 on parity/fixtures
(median of N runs, default 3) and fails unless Rust is faster on every row.
The Python is STELE_PARITY_PYTHON, else python3 or python.
"
    .to_string()
}
