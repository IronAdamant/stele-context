#!/usr/bin/env python3
"""
Stele Context Release Automation Script

This script helps perform consistent releases, especially for Grok Build agents.

Usage examples:
    python -m scripts.release --bump patch --message "MCP storm fixes"
    python -m scripts.release --version 1.3.3 --push
    python -m scripts.release --dry-run

It will:
  1. Bump version in pyproject.toml and stele_context/__init__.py
  2. Update CHANGELOG.md (move Unreleased content into the new version)
  3. Run quality gates (ruff, mypy, pytest)
  4. Create an annotated git tag
  5. Optionally push (tag + main)

This is part of the Grok Build release automation setup.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
INIT_FILE = ROOT / "stele_context" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"
COMPLETE_DOCS = ROOT / "COMPLETE_PROJECT_DOCUMENTATION.md"


def get_current_version() -> str:
    """Read the current version from pyproject.toml."""
    content = PYPROJECT.read_text()
    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise RuntimeError("Could not find version in pyproject.toml")
    return match.group(1)


def bump_version(current: str, bump_type: str | None, new_version: str | None) -> str:
    """Compute the next version."""
    if new_version:
        return new_version

    if not bump_type:
        bump_type = "patch"

    major, minor, patch = map(int, current.split(".")[:3])

    if bump_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif bump_type == "minor":
        minor += 1
        patch = 0
    else:  # patch
        patch += 1

    return f"{major}.{minor}.{patch}"


def update_pyproject(new_version: str) -> None:
    content = PYPROJECT.read_text()
    new_content = re.sub(
        r'(version\s*=\s*["\'])([^"\']+)(["\'])',
        rf"\g<1>{new_version}\g<3>",
        content,
    )
    PYPROJECT.write_text(new_content)
    print(f"✓ Updated pyproject.toml → {new_version}")


def update_init_file(new_version: str) -> None:
    content = INIT_FILE.read_text()
    new_content = re.sub(
        r'(__version__\s*=\s*["\'])([^"\']+)(["\'])',
        rf"\g<1>{new_version}\g<3>",
        content,
    )
    INIT_FILE.write_text(new_content)
    print(f"✓ Updated stele_context/__init__.py → {new_version}")


def update_changelog(new_version: str, message: str | None) -> None:
    """Update CHANGELOG.md with the new version section."""
    today = datetime.now().strftime("%Y-%m-%d")
    content = CHANGELOG.read_text()

    # Replace [Unreleased] with the new version header
    unreleased_header = "## [Unreleased]"
    new_header = f"## [Unreleased]\n\n## [{new_version}] - {today}"

    if unreleased_header not in content:
        print("⚠ Warning: Could not find '## [Unreleased]' in CHANGELOG.md")
        return

    # Add a basic entry if the user provided a message
    extra = ""
    if message:
        extra = f"\n\n### Changed\n- {message}\n"

    new_content = content.replace(unreleased_header, new_header + extra, 1)

    CHANGELOG.write_text(new_content)
    print(f"✓ Updated CHANGELOG.md with [{new_version}] section")


def update_complete_docs(new_version: str) -> None:
    """Update the header in COMPLETE_PROJECT_DOCUMENTATION.md."""
    if not COMPLETE_DOCS.exists():
        return

    today = datetime.now().strftime("%Y-%m-%d")
    content = COMPLETE_DOCS.read_text()

    # Update the first line that has "Last updated" and "Release"
    new_line = f"**Last updated:** {today} · **Release:** v{new_version} (Grok Build)"

    new_content = re.sub(
        r"\*\*Last updated:\*\*.*?\*\*Release:\*\*.*",
        new_line,
        content,
        count=1,
    )

    if new_content != content:
        COMPLETE_DOCS.write_text(new_content)
        print(f"✓ Updated COMPLETE_PROJECT_DOCUMENTATION.md header → v{new_version}")


def run_quality_gates(skip_tests: bool = False) -> bool:
    """Run ruff, mypy, and pytest."""
    print("\n--- Running quality gates ---")

    commands = [
        ("ruff check", ["ruff", "check", "stele_context/", "scripts/"]),
        (
            "ruff format --check",
            ["ruff", "format", "--check", "stele_context/", "scripts/"],
        ),
        ("mypy", ["mypy", "stele_context/"]),
    ]

    if not skip_tests:
        commands.append(("pytest", ["pytest", "-q", "--tb=no"]))

    for name, cmd in commands:
        print(f"\n▶ {name}")
        result = subprocess.run(cmd, cwd=ROOT)
        if result.returncode != 0:
            print(f"✗ {name} failed")
            return False
        print(f"✓ {name} passed")

    return True


def create_git_tag(version: str, message: str) -> None:
    """Create an annotated git tag."""
    tag_name = f"v{version}"
    tag_message = f"Stele Context {tag_name}\n\n{message}"

    # Check if tag already exists
    existing = subprocess.run(
        ["git", "tag", "-l", tag_name], capture_output=True, text=True, cwd=ROOT
    )
    if tag_name in existing.stdout:
        print(f"⚠ Tag {tag_name} already exists. Skipping tag creation.")
        return

    subprocess.run(
        ["git", "tag", "-a", tag_name, "-m", tag_message],
        cwd=ROOT,
        check=True,
    )
    print(f"✓ Created annotated tag: {tag_name}")


def push_release(version: str, push_main: bool = True) -> None:
    """Push main and the new tag."""
    tag_name = f"v{version}"
    print(f"\n--- Pushing release {tag_name} ---")

    if push_main:
        subprocess.run(["git", "push", "origin", "main"], cwd=ROOT, check=True)
        print("✓ Pushed main")

    subprocess.run(["git", "push", "origin", tag_name], cwd=ROOT, check=True)
    print(f"✓ Pushed tag {tag_name}")
    print("\nThe publish workflow should now trigger automatically (tag push).")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stele Context release helper (Grok Build friendly)"
    )
    parser.add_argument("--version", help="Explicit new version (e.g. 1.3.3)")
    parser.add_argument(
        "--bump",
        choices=["patch", "minor", "major"],
        default="patch",
        help="Semantic version bump type (default: patch)",
    )
    parser.add_argument(
        "--message", "-m", help="Short release message for CHANGELOG and tag"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making changes",
    )
    parser.add_argument(
        "--skip-tests", action="store_true", help="Skip running the test suite"
    )
    parser.add_argument(
        "--push", action="store_true", help="Push main + tag after successful release"
    )
    parser.add_argument(
        "--no-push-main", action="store_true", help="When --push, only push the tag"
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt (useful for automation / Grok Build agents)",
    )

    args = parser.parse_args()

    current = get_current_version()
    new_version = bump_version(current, args.bump, args.version)

    print(f"Current version: {current}")
    print(f"New version:     {new_version}")
    if args.dry_run:
        print("\n[DRY RUN] No files will be modified.")
        return 0

    if not args.yes:
        if input(f"\nProceed with release v{new_version}? [y/N] ").lower() != "y":
            print("Aborted.")
            return 1
    else:
        print("Running in non-interactive mode (--yes). Proceeding with release.")

    try:
        update_pyproject(new_version)
        update_init_file(new_version)
        update_changelog(new_version, args.message)
        update_complete_docs(new_version)

        if not run_quality_gates(skip_tests=args.skip_tests):
            print("\n✗ Quality gates failed. Fix issues and try again.")
            return 1

        msg = args.message or f"Release v{new_version}"
        create_git_tag(new_version, msg)

        if args.push:
            push_release(new_version, push_main=not args.no_push_main)

        print(f"\n✅ Release v{new_version} prepared successfully!")
        print("Next steps (if you didn't use --push):")
        print(f"   git push origin main && git push origin v{new_version}")
        print("   (The tag push will trigger the PyPI publish workflow)")

    except Exception as e:
        print(f"\n✗ Error during release: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
