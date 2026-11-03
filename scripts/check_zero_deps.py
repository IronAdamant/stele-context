"""Fail if the Rust crate depends on anything but itself."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    cargo = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))
    for table in ("dependencies", "dev-dependencies", "build-dependencies"):
        deps = cargo.get(table) or {}
        if deps:
            print(f"Cargo.toml [{table}] is not empty: {deps}", file=sys.stderr)
            return 1
    if cargo.get("build") is not None:
        print("Cargo.toml must not set a build script", file=sys.stderr)
        return 1
    lock = tomllib.loads((ROOT / "Cargo.lock").read_text(encoding="utf-8"))
    names = [pkg.get("name") for pkg in lock.get("package", [])]
    if names != ["stele-context"]:
        print(
            f"Cargo.lock packages must be only stele-context, found {names}",
            file=sys.stderr,
        )
        return 1
    print("zero dependencies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
