"""
Release command for the Stele CLI.

This provides `stele-context release ...` as a first-class command,
delegating to the powerful scripts/release.py implementation.

This is part of the Grok Build release automation effort.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any


def cmd_release(args: Any, stele: Any) -> int:
    """Handler for `stele-context release`."""
    script_path = Path(__file__).parent.parent / "scripts" / "release.py"

    if not script_path.exists():
        print("Error: scripts/release.py not found.", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(script_path)]

    if getattr(args, "version", None):
        cmd += ["--version", args.version]
    if getattr(args, "bump", None):
        cmd += ["--bump", args.bump]
    if getattr(args, "message", None):
        cmd += ["--message", args.message]
    if getattr(args, "dry_run", False):
        cmd += ["--dry-run"]
    if getattr(args, "skip_tests", False):
        cmd += ["--skip-tests"]
    if getattr(args, "push", False):
        cmd += ["--push"]
    if getattr(args, "no_push_main", False):
        cmd += ["--no-push-main"]
    if getattr(args, "yes", False):
        cmd += ["--yes"]

    print("Delegating to scripts/release.py ...")
    result = subprocess.run(cmd)
    return result.returncode


def add_release_parser(subparsers: Any) -> None:
    """Add the 'release' subparser to the main CLI."""
    release_parser = subparsers.add_parser(
        "release",
        help="Release automation helper (Grok Build friendly)",
        description="Automate version bumps, changelog updates, testing, tagging, and PyPI publishing.",
    )
    release_parser.add_argument(
        "--version",
        help="Explicit new version (e.g. 1.3.3)",
    )
    release_parser.add_argument(
        "--bump",
        choices=["patch", "minor", "major"],
        default="patch",
        help="Semantic version bump (default: patch)",
    )
    release_parser.add_argument(
        "-m",
        "--message",
        help="Release message for CHANGELOG and git tag",
    )
    release_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without modifying anything",
    )
    release_parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip running the test suite",
    )
    release_parser.add_argument(
        "--push",
        action="store_true",
        help="Push main and the new tag (triggers automated PyPI publish)",
    )
    release_parser.add_argument(
        "--no-push-main",
        action="store_true",
        help="Only push the tag when using --push",
    )
    release_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt (for automation / Grok Build)",
    )

    release_parser.set_defaults(func=cmd_release)
