"""The parity harness matches a fresh 1.5.2 capture and covers every extension."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from parity.inventory import EXTRACTOR_BY_EXT
from stele_context.symbols import SymbolExtractor

ROOT = Path(__file__).resolve().parents[1]

_PROBES = {
    "python-ast": "def foo():\n    return 1\n",
    "javascript": "function foo() {\n    return 1;\n}\n",
    "java": "public class A {\n    public int run() { return 1; }\n}\n",
    "go": "func foo() {}\n",
    "rust": "fn foo() {}\n",
    "csharp": "public class A {\n    public int Run() { return 1; }\n}\n",
    "swift": "func foo() -> Int { return 1 }\n",
    "c": "int foo(void) { return 1; }\n",
    "ruby": "def foo\n  1\nend\n",
    "php": "<?php\nfunction foo() { return 1; }\n",
    "html": '<div id="main" class="panel"></div>\n',
    "css": "#main { color: black; }\n.panel { display: block; }\n",
    "none": "function foo() { return 1; }\n",
}


def test_extractor_map_matches_observed_symbols() -> None:
    extractor = SymbolExtractor()
    for ext, kind in EXTRACTOR_BY_EXT.items():
        content = _PROBES[kind]
        found = extractor.extract(content, f"probe.{ext}", "chunk", ext)
        if kind == "none":
            assert found == [], ext
        else:
            assert found, ext


def test_fixture_goldens_and_cli_smoke() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "parity", "verify", "--skip-corpora"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(
    not (ROOT / "parity" / "goldens" / "corpora.json").is_file(),
    reason="corpus golden is captured by python -m parity capture",
)
def test_corpus_golden_is_present() -> None:
    text = (ROOT / "parity" / "goldens" / "corpora.json").read_text(encoding="utf-8")
    assert "rich-13.9.4" in text
    assert "flask-3.1.0" in text
