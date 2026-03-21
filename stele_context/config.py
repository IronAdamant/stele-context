"""
Configuration loader for Stele.

Reads `.stele-context.toml` from the project root (if present) and provides
defaults that can be overridden by constructor parameters.

Uses stdlib tomllib (Python 3.11+) with a minimal fallback parser
for Python 3.9-3.10.  Zero external dependencies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Prefer stdlib tomllib (3.11+), then tomli, then builtin fallback
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[import-not-found,no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


def _parse_toml_minimal(text: str) -> dict[str, Any]:
    """Minimal TOML parser for flat config files.

    Handles:
      - [section] headers
      - key = "string"
      - key = 123 / 123.4 / true / false
      - key = ["a", "b"]
      - # comments
      - blank lines

    Does NOT handle nested tables, inline tables, multi-line strings,
    or other advanced TOML features.  Sufficient for .stele-context.toml.
    """
    result: dict[str, Any] = {}
    current_section: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # Section header
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            if current_section not in result:
                result[current_section] = {}
            continue

        # Key = value
        if "=" not in line:
            continue

        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()

        # Remove inline comments (but not inside strings)
        if val and val[0] not in ('"', "'", "["):
            comment_pos = val.find("#")
            if comment_pos > 0:
                val = val[:comment_pos].strip()

        parsed_val = _parse_value(val)

        if current_section is not None:
            result[current_section][key] = parsed_val
        else:
            result[key] = parsed_val

    return result


def _parse_value(val: str) -> Any:
    """Parse a single TOML value."""
    if not val:
        return ""

    # String
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        return val[1:-1]

    # Boolean
    if val == "true":
        return True
    if val == "false":
        return False

    # Array
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        items = []
        for item in inner.split(","):
            item = item.strip()
            if item:
                items.append(_parse_value(item))
        return items

    # Integer
    try:
        return int(val)
    except ValueError:
        pass

    # Float
    try:
        return float(val)
    except ValueError:
        pass

    return val


def load_config(project_root: Path | None = None) -> dict[str, Any]:
    """Load configuration from .stele-context.toml in the project root.

    Returns a flat dict of config values from the [stele-context] section.
    Returns empty dict if no config file exists or parsing fails.
    """
    if project_root is None:
        return {}

    config_path = project_root / ".stele-context.toml"
    if not config_path.is_file():
        return {}

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    try:
        if tomllib is not None:
            data = tomllib.loads(raw)
        else:
            data = _parse_toml_minimal(raw)
    except Exception:
        return {}

    # Extract [stele-context] section (or top-level if no section)
    return data.get("stele-context", data)


def apply_config(
    config: dict[str, Any],
    *,
    storage_dir: str | None = None,
    chunk_size: int | None = None,
    max_chunk_size: int | None = None,
    merge_threshold: float | None = None,
    change_threshold: float | None = None,
    search_alpha: float | None = None,
    skip_dirs: set | None = None,
) -> dict[str, Any]:
    """Merge config file values with explicit constructor params.

    Explicit params always win.  Returns a dict with resolved values.
    Only keys present in either source are included.
    """
    resolved: dict[str, Any] = {}

    _FIELDS = {
        "storage_dir": (storage_dir, str),
        "chunk_size": (chunk_size, int),
        "max_chunk_size": (max_chunk_size, int),
        "merge_threshold": (merge_threshold, float),
        "change_threshold": (change_threshold, float),
        "search_alpha": (search_alpha, float),
    }

    for key, (explicit_val, cast_fn) in _FIELDS.items():
        if explicit_val is not None:
            resolved[key] = explicit_val
        elif key in config:
            try:
                resolved[key] = cast_fn(config[key])
            except (ValueError, TypeError):
                pass

    # skip_dirs: merge config list into default set
    if skip_dirs is not None:
        resolved["skip_dirs"] = skip_dirs
    elif "skip_dirs" in config:
        raw = config["skip_dirs"]
        if isinstance(raw, list):
            resolved["skip_dirs"] = set(raw)

    return resolved
