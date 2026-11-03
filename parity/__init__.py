"""Parity harness for the Rust rewrite.

Run ``python -m parity``. The package does not import stele at import time
so a caller can block numpy first and capture the zero-dependency signature
path.
"""

from __future__ import annotations

GOLDEN_SCHEMA = "stele-parity-golden/1"
REFERENCE = "tree-sitter-off"
PYTHON_PACKAGE = "1.5.2"
