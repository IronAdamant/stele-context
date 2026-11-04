"""Benchmark runner.

The uploaded 1.5.1 log records timings and output sizes, not argv. The
command list in this module is the canonical list from this repo onward.
``bench/baseline/summary.json`` is the hard floor for wall time on every
benchmark a Rust binary covers (D13).

RSS comes from ``wait4`` on Unix. Linux reports ``ru_maxrss`` in kilobytes
and macOS reports bytes. Windows records wall time and leaves RSS null.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

BASELINE = Path(__file__).resolve().parent / "baseline" / "summary.json"

# Names join to summary.json. ``floor`` is the Python row a Rust run must beat.
COMMANDS: list[dict] = [
    {
        "name": "cli --version (startup)",
        "args": ["--version"],
        "group": "b1",
        "floor": "cli --version (startup)",
    },
    {"name": "cli --help", "args": ["--help"], "group": "b1", "floor": "cli --help"},
    {
        "name": "cli index . (cold, empty store)",
        "args": ["index", "."],
        "group": "b4",
        "floor": "cli index . (cold, empty store)",
        "fresh_store": True,
    },
    {
        "name": "cli index . (warm, nothing changed)",
        "args": ["index", "."],
        "group": "b5",
        "floor": "cli index . (warm, nothing changed)",
    },
    {
        "name": "cli detect (nothing changed)",
        "args": ["detect"],
        "group": "b5",
        "floor": "cli detect (nothing changed)",
    },
    {"name": "cli stats", "args": ["stats"], "group": "b6", "floor": "cli stats"},
    {"name": "cli doctor", "args": ["doctor"], "group": "b6", "floor": "cli doctor"},
    {
        "name": "cli map --json",
        "args": ["map", "--json"],
        "group": "b6",
        "floor": "cli map --json",
    },
    {
        "name": "cli project-brief --json",
        "args": ["project-brief", "--json"],
        "group": "b6",
        "floor": "cli project-brief --json",
    },
    {
        "name": "cli search hybrid --json",
        "args": ["search", "console markup", "--search-mode", "hybrid", "--json"],
        "group": "b6",
        "floor": "cli search hybrid --json",
    },
    {
        "name": "cli search keyword --json",
        "args": ["search", "console markup", "--search-mode", "keyword", "--json"],
        "group": "b6",
        "floor": "cli search keyword --json",
    },
    {
        "name": "cli search-text --json",
        "args": ["search-text", "def ", "--json"],
        "group": "b6",
        "floor": "cli search-text --json",
    },
    {
        "name": "cli agent-grep --json",
        "args": ["agent-grep", "class Console", "--json"],
        "group": "b6",
        "floor": "cli agent-grep --json",
    },
    {
        "name": "cli query --json",
        "args": ["query", "console markup rendering", "--json"],
        "group": "b6",
        "floor": "cli query --json",
    },
    {
        "name": "cli find-definition --json",
        "args": ["find-definition", "Console", "--json"],
        "group": "b6",
        "floor": "cli find-definition --json",
    },
    {
        "name": "cli find-references --json",
        "args": ["find-references", "Console", "--json"],
        "group": "b6",
        "floor": "cli find-references --json",
    },
    {
        "name": "cli impact-radius --json",
        "args": ["impact-radius", "--symbol", "Console", "--summary-mode", "--json"],
        "group": "b6",
        "floor": "cli impact-radius --json",
    },
    {
        "name": "cli coupling --json",
        "args": ["coupling", "rich/console.py", "--json"],
        "group": "b6",
        "floor": "cli coupling --json",
    },
    {
        "name": "cli enrichment-plan --json",
        "args": ["enrichment-plan", "--json"],
        "group": "b6",
        "floor": "cli enrichment-plan --json",
    },
    {
        "name": "cli history --json",
        "args": ["history", "--json"],
        "group": "b6",
        "floor": "cli history --json",
    },
]


def load_floor() -> dict[str, float]:
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    return {
        row["name"]: float(row["median_ms"])
        for row in data["results"]
        if "median_ms" in row
    }


def python_binary() -> list[str]:
    return [
        sys.executable,
        "-c",
        "from stele_context.cli import main; raise SystemExit(main())",
    ]


def _rss_kb(usage: object) -> float | None:
    ru = getattr(usage, "ru_maxrss", None)
    if ru is None:
        return None
    if sys.platform == "darwin":
        return float(ru) / 1024.0
    return float(ru)


def _read_pipes(out_r: int, err_r: int) -> tuple[bytes, bytes]:
    out = bytearray()
    err = bytearray()
    open_fds = {out_r, err_r}
    while open_fds:
        readable, _, _ = select.select(list(open_fds), [], [])
        for fd in readable:
            chunk = os.read(fd, 1 << 16)
            target = out if fd == out_r else err
            if chunk:
                target += chunk
                continue
            os.close(fd)
            open_fds.remove(fd)
    return bytes(out), bytes(err)


def _run_fork(argv: list[str], cwd: Path, env: dict[str, str]) -> dict:
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    started = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        try:
            os.dup2(out_w, 1)
            os.dup2(err_w, 2)
            os.close(out_r)
            os.close(err_r)
            os.close(out_w)
            os.close(err_w)
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        except OSError as exc:
            os.write(2, str(exc).encode())
            os._exit(127)
    os.close(out_w)
    os.close(err_w)
    stdout, stderr = _read_pipes(out_r, err_r)
    _pid, status, usage = os.wait4(pid, 0)
    return {
        "wall_ms": (time.perf_counter() - started) * 1000.0,
        "rss_kb": _rss_kb(usage),
        "out_bytes": len(stdout),
        "rc": os.waitstatus_to_exitcode(status),
        "stderr_tail": stderr[-400:].decode("utf-8", "replace"),
    }


def _run_popen(argv: list[str], cwd: Path, env: dict[str, str]) -> dict:
    started = time.perf_counter()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = proc.communicate()
    return {
        "wall_ms": (time.perf_counter() - started) * 1000.0,
        "rss_kb": None,
        "out_bytes": len(stdout),
        "rc": proc.returncode,
        "stderr_tail": stderr[-400:].decode("utf-8", "replace"),
    }


def run_once(argv: list[str], cwd: Path, env: dict[str, str]) -> dict:
    if hasattr(os, "fork") and hasattr(os, "wait4"):
        return _run_fork(argv, cwd, env)
    return _run_popen(argv, cwd, env)


def summarize(name: str, samples: list[dict]) -> dict:
    walls = [sample["wall_ms"] for sample in samples]
    rss = [sample["rss_kb"] for sample in samples if sample["rss_kb"] is not None]
    outs = [
        sample["out_bytes"] for sample in samples if sample["out_bytes"] is not None
    ]
    return {
        "name": name,
        "runs": len(samples),
        "median_ms": statistics.median(walls),
        "min_ms": min(walls),
        "max_ms": max(walls),
        "stdev_ms": statistics.pstdev(walls) if len(walls) > 1 else 0.0,
        "peak_rss_mb": (max(rss) / 1024.0) if rss else None,
        "out_bytes": statistics.median(outs) if outs else None,
        "rc": sorted({sample["rc"] for sample in samples}),
    }


def _reset_store(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def execute(
    argv: list[str], commands: list[dict], cwd: Path, store: Path | None, runs: int
) -> list[dict]:
    env = os.environ.copy()
    if store is not None:
        env["STELE_CONTEXT_STORAGE_DIR"] = str(store)
    summaries = []
    for spec in commands:
        if store is not None and spec.get("fresh_store"):
            samples = []
            for _ in range(runs):
                _reset_store(store)
                samples.append(run_once(argv + spec["args"], cwd, env))
        else:
            samples = [run_once(argv + spec["args"], cwd, env) for _ in range(runs)]
        bad = [sample for sample in samples if sample["rc"] != 0]
        summary = summarize(spec["name"], samples)
        if bad:
            summary["error"] = bad[0]["stderr_tail"]
        summaries.append(summary)
        print(
            f"{spec['name']}: median {summary['median_ms']:.1f} ms rc {summary['rc']}",
            flush=True,
        )
    return summaries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench")
    parser.add_argument(
        "--binary", default="python", help="python, or a path to a binary"
    )
    parser.add_argument(
        "--group", action="append", default=[], help="b1, b4, b5, b6; repeatable"
    )
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--storage-dir", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--check-floor",
        action="store_true",
        help="Fail if any measured median is not strictly below the Python floor.",
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        print("runs must be >= 1", file=sys.stderr)
        return 2
    selected = COMMANDS
    if args.group:
        wanted = set(args.group)
        selected = [spec for spec in COMMANDS if spec["group"] in wanted]
        if not selected:
            print(f"no commands in groups {sorted(wanted)}", file=sys.stderr)
            return 2
    binary = python_binary() if args.binary == "python" else [args.binary]
    floor = load_floor()
    missing = [spec["floor"] for spec in selected if spec["floor"] not in floor]
    if missing:
        print(f"floor table is missing {missing}", file=sys.stderr)
        return 2
    summaries = execute(binary, selected, args.cwd, args.storage_dir, args.runs)
    if args.json_out:
        args.json_out.write_text(
            json.dumps(summaries, indent=2) + "\n", encoding="utf-8"
        )
    if any("error" in row for row in summaries):
        return 1
    if args.check_floor:
        from bench.gate import beats_floor

        failed = []
        for spec, row in zip(selected, summaries, strict=True):
            if not beats_floor(row["median_ms"], floor[spec["floor"]]):
                failed.append(
                    f"{spec['name']}: {row['median_ms']:.1f} ms "
                    f">= python {floor[spec['floor']]:.1f} ms"
                )
        if failed:
            print("\n".join(failed), file=sys.stderr)
            return 1
    return 0
