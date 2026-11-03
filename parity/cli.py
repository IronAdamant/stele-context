"""``python -m parity`` commands: ``capture`` and ``verify``."""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path

from parity.corpora import CORPORA, capture_corpora
from parity.extract import capture_fixtures
from parity.inventory import build_inventory
from parity.smoke import run_smoke

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"
GOLDENS = ROOT / "goldens"
FIXTURE_GOLDEN = GOLDENS / "fixtures.json"
INVENTORY_GOLDEN = GOLDENS / "extensions.json"
CORPORA_GOLDEN = GOLDENS / "corpora.json"


def dumps(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _diff(expected: object, actual: object, label: str) -> str:
    left = dumps(expected).splitlines(keepends=True)
    right = dumps(actual).splitlines(keepends=True)
    lines = list(
        difflib.unified_diff(left, right, f"{label} golden", f"{label} live", n=2)
    )
    text = "".join(lines)
    if len(text) > 12000:
        return text[:12000] + "\n... diff truncated\n"
    return text


def _load(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(value), encoding="utf-8", newline="\n")


def _pins_from_golden() -> dict[str, str] | None:
    if not CORPORA_GOLDEN.is_file():
        return None
    data = _load(CORPORA_GOLDEN)
    if not isinstance(data, dict):
        return None
    pins = {}
    for row in data.get("corpora", []):
        pins[row["id"]] = row["tarball_sha256"]
    return pins


def capture(skip_corpora: bool) -> int:
    fixtures = capture_fixtures(FIXTURES)
    inventory = fixtures.pop("inventory")
    _write(FIXTURE_GOLDEN, fixtures)
    _write(INVENTORY_GOLDEN, inventory)
    print(
        f"fixtures: {fixtures['file_count']} files, "
        f"{fixtures['chunk_count']} chunks, {fixtures['symbol_count']} symbols"
    )
    if skip_corpora:
        return 0
    corpora = capture_corpora(pins=None)
    _write(CORPORA_GOLDEN, corpora)
    for row in corpora["corpora"]:
        print(
            f"{row['id']}: {row['file_count']} files, {row['chunk_count']} chunks, "
            f"{row['symbol_count']} symbols, capture {row['capture_sha256'][:12]}"
        )
    return 0


def verify(skip_corpora: bool) -> int:
    for path in (FIXTURE_GOLDEN, INVENTORY_GOLDEN):
        if not path.is_file():
            sys.stderr.write(f"missing {path}. Run python -m parity capture\n")
            return 2
    fixtures = capture_fixtures(FIXTURES)
    inventory = fixtures.pop("inventory")
    expected_fixtures = _load(FIXTURE_GOLDEN)
    if fixtures != expected_fixtures:
        sys.stderr.write(_diff(expected_fixtures, fixtures, "fixtures"))
        return 1
    expected_inventory = _load(INVENTORY_GOLDEN)
    live_inventory = build_inventory()
    if inventory != live_inventory or inventory != expected_inventory:
        sys.stderr.write(_diff(expected_inventory, inventory, "extensions"))
        return 1
    covered = {
        Path(row["path"]).suffix.removeprefix(".").lower() for row in fixtures["files"]
    }
    required = {row["ext"] for row in inventory["extensions"]}
    missing = sorted(required - covered)
    if missing:
        sys.stderr.write(
            "fixtures do not cover extensions: " + ", ".join(missing) + "\n"
        )
        return 1
    print(
        f"fixtures match ({fixtures['file_count']} files, "
        f"{fixtures['chunk_count']} chunks, {fixtures['symbol_count']} symbols)"
    )
    print(f"extensions match ({len(inventory['extensions'])} rows)")
    run_smoke(FIXTURES)
    print("cli smoke passed")
    if skip_corpora:
        return 0
    if not CORPORA_GOLDEN.is_file():
        sys.stderr.write("missing corpora golden. Run python -m parity capture\n")
        return 2
    expected = _load(CORPORA_GOLDEN)
    live = capture_corpora(pins=_pins_from_golden())
    if live != expected:
        sys.stderr.write(_diff(expected, live, "corpora"))
        return 1
    names = ", ".join(row["id"] for row in live["corpora"])
    print(f"corpora match ({names})")
    missing = [spec["id"] for spec in CORPORA if spec["id"] not in names]
    if missing:
        sys.stderr.write(f"corpora golden is missing {missing}\n")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parity")
    parser.add_argument("command", choices=("capture", "verify"))
    parser.add_argument(
        "--skip-corpora",
        action="store_true",
        help="Skip the rich and flask downloads. Fixture goldens still run.",
    )
    args = parser.parse_args(argv)
    if args.command == "capture":
        return capture(args.skip_corpora)
    return verify(args.skip_corpora)
