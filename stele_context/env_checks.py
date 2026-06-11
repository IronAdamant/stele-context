"""
Environment checks for multi-agent Stele.

Standalone utility functions for detecting environment issues that can
cause subtle bugs in multi-agent/worktree workflows:

- Stale ``__pycache__`` directories with orphaned ``.pyc`` files
- Editable pip installs pointing to worktree paths
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _canonical_name(name: str) -> str:
    """PEP 503 canonical form so ``stele_context``/``stele-context`` compare equal."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_project_name_version(
    project_root: Path,
) -> tuple[str | None, str | None]:
    """Read ``[project] name``/``version`` from pyproject.toml, best-effort.

    Uses stdlib ``tomllib`` when available (3.11+) with a minimal line-scan
    fallback so the module stays dependency-free on 3.10. Returns
    ``(None, None)`` when the file is missing or unparseable — callers must
    treat that as "project identity unknown" and skip name-anchored checks.
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return (None, None)
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return (None, None)

    try:
        import tomllib

        proj = tomllib.loads(text).get("project", {})
        name = proj.get("name")
        version = proj.get("version")
        return (
            name if isinstance(name, str) else None,
            version if isinstance(version, str) else None,
        )
    except Exception:
        pass

    name = version = None
    in_project = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if not in_project:
            continue
        m = re.match(r"(name|version)\s*=\s*[\"']([^\"']+)[\"']", stripped)
        if m:
            if m.group(1) == "name" and name is None:
                name = m.group(2)
            elif m.group(1) == "version" and version is None:
                version = m.group(2)
    return (name, version)


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


def check_stale_egg_info(
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Check for stale ``*.egg-info`` directories in the project root.

    A leftover egg-info whose PKG-INFO version differs from the installed
    distribution shadows ``importlib.metadata`` for any Python process
    started from the project directory — tools then report the stale
    version and metadata instead of the installed package's.

    The comparison is anchored on ``pyproject.toml`` when present:

    - An egg-info whose ``Name`` differs from ``[project] name`` cannot be a
      product of the current project (typically left over from a package
      rename) and is flagged even when a matching stale distribution record
      makes the version comparison pass.
    - An egg-info for the project itself is also compared against the
      ``[project] version``, catching the case where the installed record
      and the egg-info are both stale but mutually consistent.

    Without a readable pyproject.toml, behavior is unchanged: only
    installed-version mismatches are flagged.
    """
    issues: list[dict[str, Any]] = []
    if project_root is None:
        return {"egg_info_issues": issues, "count": len(issues)}

    proj_name, proj_version = _read_project_name_version(project_root)

    try:
        import importlib.metadata

        for egg_dir in project_root.glob("*.egg-info"):
            pkg_info = egg_dir / "PKG-INFO"
            if not pkg_info.is_file():
                continue
            name = version = None
            try:
                for line in pkg_info.read_text(encoding="utf-8").splitlines():
                    if line.startswith("Name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("Version:"):
                        version = line.split(":", 1)[1].strip()
                    if name and version:
                        break
            except OSError:
                continue
            if not name or not version:
                continue

            if proj_name and _canonical_name(name) != _canonical_name(proj_name):
                issues.append(
                    {
                        "egg_info_dir": str(egg_dir),
                        "egg_info_version": version,
                        "installed_version": None,
                        "reason": "orphaned_name",
                        "warning": (
                            f"'{egg_dir.name}' is for distribution '{name}' "
                            f"but this project is '{proj_name}' — likely left "
                            f"over from a package rename. It provides a "
                            f"phantom distribution to any Python process run "
                            f"from this directory. Delete the directory."
                        ),
                    }
                )
                continue

            try:
                installed = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                if proj_version and version != proj_version:
                    issues.append(
                        {
                            "egg_info_dir": str(egg_dir),
                            "egg_info_version": version,
                            "installed_version": None,
                            "reason": "stale_vs_project",
                            "warning": (
                                f"'{egg_dir.name}' has version {version} but "
                                f"pyproject.toml declares {proj_version}, and "
                                f"'{name}' is not installed. Python run from "
                                f"this directory reads the stale metadata. "
                                f"Delete the directory or rebuild the package."
                            ),
                        }
                    )
                continue

            if installed != version:
                issues.append(
                    {
                        "egg_info_dir": str(egg_dir),
                        "egg_info_version": version,
                        "installed_version": installed,
                        "reason": "version_mismatch",
                        "warning": (
                            f"'{egg_dir.name}' has version {version} but "
                            f"'{name}' {installed} is installed. Python run "
                            f"from this directory reads the stale metadata. "
                            f"Delete the directory or rebuild the package."
                        ),
                    }
                )
            elif proj_version and version != proj_version:
                issues.append(
                    {
                        "egg_info_dir": str(egg_dir),
                        "egg_info_version": version,
                        "installed_version": installed,
                        "reason": "stale_vs_project",
                        "warning": (
                            f"'{egg_dir.name}' and the installed '{name}' "
                            f"both say {version} but pyproject.toml declares "
                            f"{proj_version} — install metadata was never "
                            f"refreshed. Re-run pip install -e . and delete "
                            f"the directory."
                        ),
                    }
                )
    except Exception:
        pass

    return {"egg_info_issues": issues, "count": len(issues)}


def check_stale_editable_metadata(
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Detect an editable install of this project with a stale recorded version.

    An editable install imports live code, but ``importlib.metadata`` and
    ``pip show`` report the version recorded at install time. When that
    record differs from ``[project] version`` in pyproject.toml, every
    metadata consumer sees an old version until ``pip install -e .`` is
    re-run. Only editable installs pointing at ``project_root`` are checked.
    """
    issues: list[dict[str, Any]] = []
    if project_root is None:
        return {"stale_editable_issues": issues, "count": len(issues)}

    proj_name, proj_version = _read_project_name_version(project_root)
    if not proj_name or not proj_version:
        return {"stale_editable_issues": issues, "count": len(issues)}

    try:
        import importlib.metadata

        for dist in importlib.metadata.distributions():
            dist_name = dist.metadata["Name"] if dist.metadata else None
            if not dist_name:
                continue
            if _canonical_name(dist_name) != _canonical_name(proj_name):
                continue
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
            if Path(url[7:]).resolve() != project_root.resolve():
                continue

            if dist.version != proj_version:
                issues.append(
                    {
                        "package": dist_name,
                        "installed_version": dist.version,
                        "project_version": proj_version,
                        "warning": (
                            f"Editable install of '{dist_name}' is recorded "
                            f"as {dist.version} but pyproject.toml declares "
                            f"{proj_version}. Code imports live, but pip and "
                            f"importlib.metadata report the stale version. "
                            f"Re-run pip install -e . to refresh the record."
                        ),
                    }
                )
    except Exception:
        pass

    return {"stale_editable_issues": issues, "count": len(issues)}
