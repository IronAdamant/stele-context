"""Retrieval eval as product: drive the real eval suite and gate on recall."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Import the shipped eval module (not a re-implementation).
from benchmarks import eval_search_quality as eval_mod

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.search_regression
class TestEvalSearchQualityProduct:
    def test_keyword_eval_passes_gate(self):
        summary = eval_mod.run_eval_suite(
            search_mode="keyword", with_tier2=False, min_avg_recall_10=0.40
        )
        assert summary["n_queries"] > 0
        assert summary["avg_recall_10"] >= 0.40
        assert summary["passed_gate"] is True

    def test_hybrid_eval_runs(self):
        summary = eval_mod.run_eval_suite(
            search_mode="hybrid", with_tier2=False, min_avg_recall_10=0.30
        )
        assert summary["search_mode"] == "hybrid"
        assert "avg_recall_10" in summary
        # Hybrid without Tier-2 can be weaker; gate at 30% for this mode.
        assert summary["passed_gate"] is True

    def test_tier2_delta_report_structure(self):
        report = eval_mod.run_tier2_delta(search_mode="hybrid", min_avg_recall_10=0.30)
        assert "baseline" in report
        assert "tier2" in report
        assert "delta_recall_10" in report
        assert report["baseline"]["with_tier2"] is False
        assert report["tier2"]["with_tier2"] is True
        assert report["tier2"]["tier2_stored"] >= 1

    def test_cli_entry_keyword_json(self):
        """Exercise the real CLI entry point of the eval script."""
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "benchmarks" / "eval_search_quality.py"),
                "--search-mode",
                "keyword",
                "--json",
                "--min-recall",
                "0.40",
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        # JSON may be the only stdout, or mixed; find the object.
        out = proc.stdout.strip()
        data = json.loads(out)
        assert data["avg_recall_10"] >= 0.40
        assert data["passed_gate"] is True
