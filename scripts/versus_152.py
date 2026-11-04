#!/usr/bin/env python3
"""Time Rust and Python 1.5.2 on one corpus, in one process.

The corpus is parity/fixtures. Each row uses a fresh storage directory.
Warm rows index once, then time the command. Rust must be strictly faster
on every row. Python is the stele_context tree on origin/python-legacy.
"""

from __future__ import annotations

import argparse
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def legacy_src(repo: Path) -> Path:
    dest = repo / "target" / "python-legacy-src"
    if (dest / "stele_context" / "cli.py").is_file():
        return dest
    archived = subprocess.run(
        ["git", "archive", "origin/python-legacy", "stele_context"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    subprocess.run(["tar", "-x"], cwd=dest, input=archived.stdout, check=True)
    return dest


def median(samples: list[float]) -> float:
    return statistics.median(samples)


def time_command(argv: list[str], cwd: Path, env: dict[str, str], runs: int) -> float:
    samples: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=120,
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace")[-400:]
            raise SystemExit(f"{argv} exited {proc.returncode}\n{err}")
        samples.append(elapsed)
    return median(samples)


def prepare_corpus(repo: Path) -> Path:
    src = repo / "parity" / "fixtures"
    dest = Path(tempfile.mkdtemp(prefix="stele-versus-"))
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return dest


def py_argv(store: Path, args: list[str]) -> list[str]:
    if args[0].startswith("--"):
        return [sys.executable, "-m", "stele_context.cli", *args]
    return [
        sys.executable,
        "-m",
        "stele_context.cli",
        "--storage-dir",
        str(store),
        *args,
    ]


def rs_argv(binary: Path, store: Path, args: list[str]) -> list[str]:
    if args[0].startswith("--"):
        return [str(binary), *args]
    return [str(binary), "--storage-dir", str(store), *args]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    binary = Path(args.binary).resolve()
    src = legacy_src(repo)
    corpus = prepare_corpus(repo)
    py_file = next(corpus.rglob("*.py"))
    rel_py = py_file.relative_to(corpus).as_posix()
    symbol = "def"
    rows = [
        ("cli --version", ["--version"], "none"),
        ("cli --help", ["--help"], "none"),
        ("cli index . (cold)", ["index", "."], "cold"),
        ("cli index . (warm)", ["index", "."], "warm"),
        ("cli detect", ["detect"], "warm"),
        ("cli stats", ["stats"], "warm"),
        ("cli doctor", ["doctor"], "warm"),
        ("cli map", ["map"], "warm"),
        ("cli project-brief", ["project-brief"], "warm"),
        ("cli search keyword", ["search", symbol, "--search-mode", "keyword"], "warm"),
        ("cli search hybrid", ["search", symbol, "--search-mode", "hybrid"], "warm"),
        ("cli search-text", ["search-text", "def "], "warm"),
        ("cli agent-grep", ["agent-grep", "def "], "warm"),
        ("cli query", ["query", "def"], "warm"),
        ("cli find-definition", ["find-definition", "main"], "warm"),
        ("cli find-references", ["find-references", "main"], "warm"),
        ("cli impact-radius", ["impact-radius", "--symbol", "main"], "warm"),
        ("cli coupling", ["coupling", rel_py], "warm"),
        ("cli enrichment-plan", ["enrichment-plan"], "warm"),
        ("cli history", ["history"], "warm"),
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src)
    env.pop("STELE_CONTEXT_STORAGE_DIR", None)
    print(f"{'row':<28} {'python ms':>10} {'rust ms':>10} result")
    failed = False
    try:
        for name, argv, kind in rows:
            py_store = Path(tempfile.mkdtemp(prefix="stele-vs-py-"))
            rs_store = Path(tempfile.mkdtemp(prefix="stele-vs-rs-"))
            try:
                if kind == "warm":
                    subprocess.run(
                        py_argv(py_store, ["index", "."]),
                        cwd=corpus,
                        env=env,
                        check=True,
                        capture_output=True,
                        timeout=120,
                    )
                    subprocess.run(
                        rs_argv(binary, rs_store, ["index", "."]),
                        cwd=corpus,
                        env=env,
                        check=True,
                        capture_output=True,
                        timeout=120,
                    )
                py_ms = time_command(py_argv(py_store, argv), corpus, env, args.runs)
                rs_ms = time_command(rs_argv(binary, rs_store, argv), corpus, env, args.runs)
            finally:
                shutil.rmtree(py_store, ignore_errors=True)
                shutil.rmtree(rs_store, ignore_errors=True)
            ok = rs_ms < py_ms
            if not ok:
                failed = True
            print(f"{name:<28} {py_ms:10.1f} {rs_ms:10.1f} {'rust' if ok else 'FAIL'}")
    finally:
        shutil.rmtree(corpus, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
