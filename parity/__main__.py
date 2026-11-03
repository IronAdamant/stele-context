"""Entry point. Blocks numpy before stele imports so signatures match the
zero-dependency core (numpy is an optional accelerator and changes float32
rounding)."""

from __future__ import annotations

import sys


def _block_numpy() -> None:
    if "stele_context" in sys.modules or (
        "numpy" in sys.modules and sys.modules["numpy"] is not None
    ):
        sys.stderr.write(
            "python -m parity must run in a fresh process so numpy stays unloaded\n"
        )
        raise SystemExit(2)
    sys.modules["numpy"] = None


def _run() -> int:
    _block_numpy()
    from parity.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_run())
