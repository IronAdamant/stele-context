"""D13 gate: interleaved main-versus-PR timings of the Python implementation.

At least 15 runs of each tracked command are taken on one runner. CI uses
16 so each side leads exactly half the pairs. The order alternates. A
benchmark fails only when both are true: the 95% confidence intervals of
the medians do not overlap and the head sits entirely above the base, and
a one-sided Mann-Whitney test gives p < 0.05.

``tools/list`` reply size is recorded and printed. It is not a failure by
itself. The hard floor against the recorded Python baseline applies when a
Rust binary is being measured, which this Python-versus-Python run is not.
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
import tempfile
import threading
import time
from pathlib import Path

from bench.gate import MIN_RUNS, regression

REPO = Path(__file__).resolve().parents[1]
BENCHES = (
    "cli-version",
    "cli-help",
    "mcp-initialize",
    "mcp-tools-list",
    "index-cold",
    "index-warm",
    "detect",
    "search-keyword",
)


def require_runs(runs: int, allow_short: bool) -> None:
    if runs < 1 or (runs < MIN_RUNS and not allow_short):
        raise SystemExit(
            f"D13 requires at least {MIN_RUNS} runs; got {runs}. "
            "--allow-short is for a local smoke test, not CI."
        )


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _resolve(repo: Path, sha: str) -> str:
    return _git(["rev-parse", sha], repo)


def _venv_python(venv: Path) -> Path:
    windows = venv / "Scripts" / "python.exe"
    if windows.is_file():
        return windows
    return venv / "bin" / "python"


def _script(venv: Path, name: str) -> Path:
    for folder in (venv / "bin", venv / "Scripts"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
        exe = folder / f"{name}.exe"
        if exe.is_file():
            return exe
    raise SystemExit(f"{name} was not installed in {venv}")


def _create_venv(venv: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return
    fallback = subprocess.run(
        [sys.executable, "-m", "virtualenv", str(venv)],
        capture_output=True,
        text=True,
    )
    if fallback.returncode != 0:
        raise SystemExit(
            "could not create a virtualenv.\n"
            f"venv: {proc.stderr[-800:]}\n"
            f"virtualenv: {fallback.stderr[-800:]}"
        )


def _install(venv: Path, checkout: Path) -> tuple[Path, Path]:
    _create_venv(venv)
    python = _venv_python(venv)
    proc = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-e",
            f"{checkout}[mcp]",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"pip install failed for {checkout}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return _script(venv, "stele-context"), _script(venv, "stele-context-mcp")


def _env(storage: Path | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("STELE_CONTEXT_STORAGE_DIR", None)
    # Same hash seed on both sides so dict order is not a source of noise.
    env["PYTHONHASHSEED"] = "0"
    env["STELE_MCP_MODE"] = "lite"
    if storage is not None:
        env["STELE_CONTEXT_STORAGE_DIR"] = str(storage)
    return env


def _empty_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="stele-gate-empty-"))


def _isolated_project(checkout: Path, dest: Path) -> Path:
    """A project that contains only this checkout's package.

    detect walks the project root. Pointing it at the whole git checkout
    would time the extra files a pull request added, which is not the
    implementation under test.
    """
    dest.mkdir(parents=True)
    shutil.copytree(checkout / "stele_context", dest / "stele_context")
    (dest / ".stele-context.toml").write_text(
        "[stele-context]\nrespect_gitignore = false\n",
        encoding="utf-8",
    )
    return dest


def _timed(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    delay_s: float = 0.0,
) -> float:
    started = time.perf_counter()
    if delay_s > 0:
        time.sleep(delay_s)
    proc = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    elapsed = (time.perf_counter() - started) * 1000.0
    if proc.returncode != 0:
        raise SystemExit(
            f"{argv} exited {proc.returncode}\n"
            f"{proc.stdout[-1500:].decode(errors='replace')}\n"
            f"{proc.stderr[-1500:].decode(errors='replace')}"
        )
    return elapsed


class _Mcp:
    def __init__(self, command: Path, cwd: Path, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [str(command)],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.pending = b""
        self.stderr: list[bytes] = []
        self._drain = threading.Thread(target=self._read_stderr, daemon=True)
        self._drain.start()

    def _read_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        while True:
            chunk = self.proc.stderr.read(4096)
            if not chunk:
                return
            self.stderr.append(chunk)

    def send(self, message: dict) -> None:
        if self.proc.stdin is None:
            raise SystemExit("mcp stdin closed")
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        self.proc.stdin.flush()

    def read_id(self, request_id: int, timeout: float) -> dict:
        if self.proc.stdout is None:
            raise SystemExit("mcp stdout closed")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None and not self.pending:
                err = b"".join(self.stderr).decode("utf-8", "replace")
                raise SystemExit(
                    f"mcp exited {self.proc.returncode} before id={request_id}\n{err}"
                )
            remaining = deadline - time.monotonic()
            ready, _, _ = select.select([self.proc.stdout], [], [], max(0.0, remaining))
            if not ready:
                break
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                break
            self.pending += chunk
            while b"\n" in self.pending:
                raw, self.pending = self.pending.split(b"\n", 1)
                line = raw.strip()
                if not line:
                    continue
                message = json.loads(line)
                if message.get("id") == request_id:
                    if "error" in message:
                        raise SystemExit(f"mcp error: {message['error']}")
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise SystemExit(f"mcp result missing: {message}")
                    return result
        err = b"".join(self.stderr).decode("utf-8", "replace")
        raise SystemExit(f"timed out waiting for mcp id={request_id}\n{err}")

    def close(self) -> None:
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._drain.join(timeout=2)


def _mcp(command: Path) -> tuple[float, float, int]:
    storage = Path(tempfile.mkdtemp(prefix="stele-gate-mcp-"))
    empty = _empty_dir()
    session = _Mcp(command, empty, _env(storage))
    try:
        started = time.perf_counter()
        session.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "stele-benchmark-gate", "version": "0"},
                },
            }
        )
        session.read_id(1, 60.0)
        initialize_ms = (time.perf_counter() - started) * 1000.0
        session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed_at = time.perf_counter()
        session.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        listed = session.read_id(2, 30.0)
        list_ms = (time.perf_counter() - listed_at) * 1000.0
        tools = listed.get("tools")
        if not isinstance(tools, list) or not tools:
            raise SystemExit(f"tools/list returned no tools: {list(listed)[:8]}")
        return initialize_ms, list_ms, len(json.dumps(listed).encode())
    finally:
        session.close()
        shutil.rmtree(storage, ignore_errors=True)
        shutil.rmtree(empty, ignore_errors=True)


def _index_suite(
    binary: Path, project: Path, detect_delay_s: float = 0.0
) -> dict[str, float]:
    storage = Path(tempfile.mkdtemp(prefix="stele-gate-index-"))
    env = _env()
    base = [str(binary), "--storage-dir", str(storage)]
    try:
        cold = _timed([*base, "index", "stele_context"], project, env, 180.0)
        warm = _timed([*base, "index", "stele_context"], project, env, 180.0)
        detect = _timed([*base, "detect"], project, env, 180.0, delay_s=detect_delay_s)
        search = _timed(
            [
                *base,
                "search",
                "index_documents",
                "--search-mode",
                "keyword",
                "--json",
            ],
            project,
            env,
            180.0,
        )
        return {
            "index-cold": cold,
            "index-warm": warm,
            "detect": detect,
            "search-keyword": search,
        }
    finally:
        shutil.rmtree(storage, ignore_errors=True)


def measure(
    binary: Path,
    mcp_binary: Path,
    project: Path,
    detect_delay_s: float = 0.0,
) -> tuple[dict[str, float], int]:
    env = _env()
    empty = _empty_dir()
    try:
        samples = {
            "cli-version": _timed([str(binary), "--version"], empty, env, 60.0),
            "cli-help": _timed([str(binary), "--help"], empty, env, 60.0),
        }
    finally:
        shutil.rmtree(empty, ignore_errors=True)
    initialize_ms, list_ms, list_bytes = _mcp(mcp_binary)
    samples["mcp-initialize"] = initialize_ms
    samples["mcp-tools-list"] = list_ms
    samples.update(_index_suite(binary, project, detect_delay_s))
    return samples, list_bytes


def _prepare(repo: Path, sha: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    _git(["worktree", "add", "--detach", str(dest), sha], repo)


def _cleanup_worktree(repo: Path, dest: Path) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(dest)],
        cwd=repo,
        capture_output=True,
        check=False,
    )
    shutil.rmtree(dest, ignore_errors=True)


def run_gate(
    repo: Path,
    base_sha: str,
    head_sha: str,
    runs: int,
    inject_detect_ms: float = 0.0,
) -> dict:
    base_sha = _resolve(repo, base_sha)
    head_sha = _resolve(repo, head_sha)
    root = Path(tempfile.mkdtemp(prefix="stele-gate-"))
    base_dir = root / "base"
    head_dir = root / "head"
    samples: dict[str, dict[str, list[float]]] = {
        name: {"base": [], "head": []} for name in BENCHES
    }
    list_bytes: dict[str, list[int]] = {"base": [], "head": []}
    try:
        _prepare(repo, base_sha, base_dir)
        _prepare(repo, head_sha, head_dir)
        base_bin, base_mcp = _install(root / "venv-base", base_dir)
        head_bin, head_mcp = _install(root / "venv-head", head_dir)
        projects = {
            "base": _isolated_project(base_dir, root / "proj-base"),
            "head": _isolated_project(head_dir, root / "proj-head"),
        }
        sides = {
            "base": (base_bin, base_mcp),
            "head": (head_bin, head_mcp),
        }
        for index in range(runs):
            # Each side leads half the time when runs is even. 16 keeps the
            # minimum of 15 and cancels a "whoever goes second is slower" effect.
            order = ("base", "head") if index % 2 == 0 else ("head", "base")
            print(f"run {index + 1}/{runs} order {' '.join(order)}", flush=True)
            for side in order:
                binary, mcp_binary = sides[side]
                delay = inject_detect_ms / 1000.0 if side == "head" else 0.0
                measured, size = measure(
                    binary, mcp_binary, projects[side], detect_delay_s=delay
                )
                for name in BENCHES:
                    samples[name][side].append(measured[name])
                list_bytes[side].append(size)
    finally:
        _cleanup_worktree(repo, base_dir)
        _cleanup_worktree(repo, head_dir)
        shutil.rmtree(root, ignore_errors=True)
    return _verdict(base_sha, head_sha, samples, list_bytes)


def _verdict(
    base_sha: str,
    head_sha: str,
    samples: dict[str, dict[str, list[float]]],
    list_bytes: dict[str, list[int]],
) -> dict:
    rows = []
    failed = False
    for name in BENCHES:
        base = samples[name]["base"]
        head = samples[name]["head"]
        if len(base) < MIN_RUNS or len(head) < MIN_RUNS:
            rows.append(
                {
                    "name": name,
                    "baseline_median": statistics.median(base),
                    "candidate_median": statistics.median(head),
                    "baseline_ci": [min(base), max(base)],
                    "candidate_ci": [min(head), max(head)],
                    "mannwhitney_p": 1.0,
                    "ci_slower": False,
                    "mw_slower": False,
                    "reject": False,
                    "short": True,
                }
            )
            continue
        outcome = regression(base, head)
        outcome["name"] = name
        outcome["short"] = False
        rows.append(outcome)
        if outcome["reject"]:
            failed = True
    return {
        "base": base_sha,
        "head": head_sha,
        "failed": failed,
        "benchmarks": rows,
        "tools_list_bytes": {
            "base_median": statistics.median(list_bytes["base"])
            if list_bytes["base"]
            else None,
            "head_median": statistics.median(list_bytes["head"])
            if list_bytes["head"]
            else None,
        },
    }


def _print_report(report: dict) -> None:
    print(f"base {report['base']}")
    print(f"head {report['head']}")
    print(f"{'benchmark':<18} {'base ms':>10} {'head ms':>10} {'p':>8} {'verdict':>8}")
    for row in report["benchmarks"]:
        verdict = "FAIL" if row["reject"] else "pass"
        print(
            f"{row['name']:<18} {row['baseline_median']:10.1f} "
            f"{row['candidate_median']:10.1f} {row['mannwhitney_p']:8.3f} {verdict:>8}"
        )
        if row["reject"]:
            print(
                f"  base CI {row['baseline_ci']} head CI {row['candidate_ci']} "
                f"ci_slower={row['ci_slower']} mw_slower={row['mw_slower']}"
            )
    sizes = report["tools_list_bytes"]
    print(
        "tools/list bytes "
        f"base {sizes['base_median']} head {sizes['head_median']} (reported, not gated)"
    )


def _write_summary(report: dict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## Benchmark gate (D13)",
        "",
        f"Base `{report['base']}` versus head `{report['head']}`.",
        "",
        "| Benchmark | Base median ms | Head median ms | Mann-Whitney p | Result |",
        "|---|---:|---:|---:|---|",
    ]
    for row in report["benchmarks"]:
        result = "fail" if row["reject"] else "pass"
        lines.append(
            f"| {row['name']} | {row['baseline_median']:.1f} | "
            f"{row['candidate_median']:.1f} | {row['mannwhitney_p']:.3f} | {result} |"
        )
    sizes = report["tools_list_bytes"]
    lines.append("")
    lines.append(
        f"tools/list median bytes: base {sizes['base_median']}, "
        f"head {sizes['head_median']}."
    )
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.ci_gate")
    parser.add_argument("--runs", type=int, default=MIN_RUNS)
    parser.add_argument(
        "--allow-short",
        action="store_true",
        help="Permit fewer than 15 runs for a local smoke test.",
    )
    parser.add_argument("--base", default=os.environ.get("GATE_BASE_SHA", "HEAD"))
    parser.add_argument("--head", default=os.environ.get("GATE_HEAD_SHA", "HEAD"))
    parser.add_argument(
        "--inject-detect-ms",
        type=float,
        default=0.0,
        help=(
            "Add this many milliseconds inside the head detect timing. "
            "For proving the gate rejects a real slowdown. CI leaves it at 0."
        ),
    )
    args = parser.parse_args(argv)
    require_runs(args.runs, args.allow_short)
    if args.inject_detect_ms < 0:
        raise SystemExit("--inject-detect-ms must be >= 0")
    report = run_gate(
        REPO, args.base, args.head, args.runs, inject_detect_ms=args.inject_detect_ms
    )
    _print_report(report)
    _write_summary(report)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
