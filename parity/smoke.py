"""Run the Python CLI against a copy of the fixtures.

Exit status and a few stable markers are the phase 0 check. Full JSON
equality waits for the Rust binary, because 1.5.2 replies embed absolute
paths and timestamps.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from stele_context.cli import main; raise SystemExit(main())",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _expect(proc: subprocess.CompletedProcess[str], label: str) -> str:
    if proc.returncode != 0:
        raise RuntimeError(
            f"{label} exited {proc.returncode}\n"
            f"stdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
        )
    return proc.stdout


def run_smoke(fixtures: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="stele-parity-") as tmp:
        root = Path(tmp) / "project"
        shutil.copytree(fixtures, root)
        store = Path(tmp) / "store"
        base = ["--storage-dir", str(store)]

        version = _expect(_run(["--version"], root), "--version")
        if "stele-context 1.5.3" not in version:
            raise RuntimeError(f"--version returned {version!r}")
        help_out = _expect(_run(["--help"], root), "--help")
        if "index" not in help_out or "doctor" not in help_out:
            raise RuntimeError("--help is missing index or doctor")

        _expect(_run([*base, "index", "."], root), "index")
        _expect(_run([*base, "detect"], root), "detect")
        _expect(_run([*base, "stats"], root), "stats")
        _expect(_run([*base, "doctor"], root), "doctor")

        parsed: dict[str, object] = {}
        json_cmds = {
            "map": [*base, "map", "--json"],
            "project-brief": [*base, "project-brief", "--json"],
            "search-keyword": [
                *base,
                "search",
                "greet",
                "--search-mode",
                "keyword",
                "--json",
            ],
            "search-hybrid": [
                *base,
                "search",
                "greet",
                "--search-mode",
                "hybrid",
                "--json",
            ],
            "search-text": [*base, "search-text", "def", "--json"],
            "agent-grep": [*base, "agent-grep", "classify", "--json"],
            "query": [*base, "query", "classify", "--json"],
            "find-definition": [*base, "find-definition", "classify", "--json"],
            "find-references": [*base, "find-references", "classify", "--json"],
            "impact-radius": [
                *base,
                "impact-radius",
                "--symbol",
                "classify",
                "--summary-mode",
                "--json",
            ],
            "enrichment-plan": [*base, "enrichment-plan", "--json"],
            "history": [*base, "history", "--json"],
        }
        for label, args in json_cmds.items():
            raw = _expect(_run(args, root), label)
            parsed[label] = json.loads(raw)

        definition = json.dumps(parsed["find-definition"])
        if "classify" not in definition:
            raise RuntimeError("find-definition did not mention classify")

        sample = next(root.rglob("sample.py"))
        _expect(
            _run([*base, "coupling", str(sample), "--json"], root),
            "coupling",
        )
