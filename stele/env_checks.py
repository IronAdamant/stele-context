"""
Environment checks for multi-agent Stele.

Standalone utility functions for detecting environment issues that can
cause subtle bugs in multi-agent/worktree workflows:

- Stale ``__pycache__`` directories with orphaned ``.pyc`` files
- Editable pip installs pointing to worktree paths
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def scan_stale_pycache(
    root: Path,
    skip_dirs: set[str] | None = None,
) -> dict[str, Any]:
    """Find ``__pycache__`` directories containing orphaned ``.pyc`` files.

    A ``.pyc`` file is considered orphaned when its corresponding ``.py``
    source no longer exists in the parent directory.

    Returns:
        dict with ``stale_dirs`` (list of dicts) and ``total_stale_files``.
    """
    if skip_dirs is None:
        skip_dirs = {".git", "node_modules", ".venv", "venv"}

    stale_dirs: list[dict[str, Any]] = []
    total_stale = 0

    for cache_dir in root.rglob("__pycache__"):
        if any(part in skip_dirs for part in cache_dir.parts):
            continue
        if any(
            part.startswith(".")
            for part in cache_dir.relative_to(root).parts
            if part != "__pycache__"
        ):
            continue

        stale_files = []
        for pyc in cache_dir.glob("*.pyc"):
            # .pyc format: module.cpython-3X.pyc
            parts = pyc.stem.rsplit(".", 1)
            module_name = parts[0] if len(parts) == 2 else pyc.stem
            source = cache_dir.parent / f"{module_name}.py"
            if not source.exists():
                stale_files.append(pyc.name)

        if stale_files:
            try:
                rel = str(cache_dir.relative_to(root))
            except ValueError:
                rel = str(cache_dir)
            stale_dirs.append(
                {
                    "path": rel,
                    "stale_files": stale_files,
                    "count": len(stale_files),
                }
            )
            total_stale += len(stale_files)

    return {"stale_dirs": stale_dirs, "total_stale_files": total_stale}


def clean_stale_pycache(
    root: Path,
    skip_dirs: set[str] | None = None,
) -> dict[str, Any]:
    """Remove orphaned ``.pyc`` files and empty ``__pycache__`` directories.

    Returns scan results plus a ``cleaned`` count.
    """
    result = scan_stale_pycache(root, skip_dirs)
    cleaned = 0

    for dir_info in result["stale_dirs"]:
        cache_dir = root / dir_info["path"]
        for pyc_name in dir_info["stale_files"]:
            pyc_path = cache_dir / pyc_name
            if pyc_path.exists():
                pyc_path.unlink()
                cleaned += 1
        # Remove empty __pycache__ dirs
        if cache_dir.exists() and not any(cache_dir.iterdir()):
            cache_dir.rmdir()

    result["cleaned"] = cleaned
    return result


def check_editable_installs(
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Check for editable pip installs pointing outside the project root.

    An editable install (``pip install -e .``) from a worktree redirects
    the system import to the worktree path.  If the worktree is later
    removed, all imports break or load stale code.

    Uses ``importlib.metadata`` (stdlib) to inspect ``direct_url.json``
    (PEP 610) for editable installs.
    """
    issues: list[dict[str, Any]] = []

    try:
        import importlib.metadata

        for dist in importlib.metadata.distributions():
            try:
                direct_url_text = dist.read_text("direct_url.json")
            except Exception:
                continue
            if direct_url_text is None:
                continue

            try:
                info = json.loads(direct_url_text)
            except (json.JSONDecodeError, ValueError):
                continue

            if not info.get("dir_info", {}).get("editable"):
                continue

            url = info.get("url", "")
            if not url.startswith("file://"):
                continue

            install_path = Path(url[7:]).resolve()

            # Flag if install path differs from project root
            if project_root is not None and install_path != project_root.resolve():
                issues.append(
                    {
                        "package": dist.metadata["Name"],
                        "install_path": str(install_path),
                        "project_root": str(project_root),
                        "warning": (
                            f"Editable install of '{dist.metadata['Name']}' "
                            f"points to '{install_path}' but project root is "
                            f"'{project_root}'. This may cause stale imports "
                            f"if the install path is a worktree."
                        ),
                    }
                )
    except Exception:
        pass

    return {"editable_issues": issues, "count": len(issues)}
