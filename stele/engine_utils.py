"""
Path utilities and lock routing helpers for the Stele engine.

Standalone functions -- no circular imports back to engine.py.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Set


def normalize_path(path: str, project_root: Optional[Path]) -> str:
    """Convert a path to project-relative if within the project root."""
    p = Path(path)
    if project_root is not None:
        if p.is_absolute():
            try:
                return str(p.resolve().relative_to(project_root))
            except ValueError:
                pass
        else:
            resolved = (project_root / p).resolve()
            try:
                return str(resolved.relative_to(project_root))
            except ValueError:
                pass
    return str(p.resolve())


def resolve_path(normalized: str, project_root: Optional[Path]) -> Path:
    """Convert a normalized path back to absolute for file I/O."""
    p = Path(normalized)
    if not p.is_absolute() and project_root is not None:
        return project_root / p
    return p


def detect_project_root(explicit: Optional[str] = None) -> Optional[Path]:
    """Detect project root by walking up from CWD looking for .git."""
    if explicit is not None:
        return Path(explicit).resolve()
    cwd = Path.cwd().resolve()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".git").exists():
            return parent
    return None


def do_acquire_lock(
    doc_path: str,
    agent_id: str,
    coordination: Any,
    storage: Any,
    ttl: float = 300.0,
    force: bool = False,
) -> Dict[str, Any]:
    if coordination:
        return coordination.acquire_lock(doc_path, agent_id, ttl, force)
    return storage.acquire_document_lock(doc_path, agent_id, ttl, force)


def do_get_lock_status(
    doc_path: str,
    coordination: Any,
    storage: Any,
) -> Dict[str, Any]:
    if coordination:
        return coordination.get_lock_status(doc_path)
    return storage.get_document_lock_status(doc_path)


def do_release_lock(
    doc_path: str,
    agent_id: str,
    coordination: Any,
    storage: Any,
) -> Dict[str, Any]:
    if coordination:
        return coordination.release_lock(doc_path, agent_id)
    return storage.release_document_lock(doc_path, agent_id)


def do_record_conflict(
    document_path: str,
    agent_a: str,
    agent_b: str,
    conflict_type: str,
    coordination: Any,
    storage: Any,
    **kwargs: Any,
) -> Optional[int]:
    if coordination:
        return coordination.record_conflict(
            document_path, agent_a, agent_b, conflict_type, **kwargs
        )
    return storage.record_conflict(
        document_path, agent_a, agent_b, conflict_type, **kwargs
    )


def check_environment_impl(
    project_root: Optional[Path],
    skip_dirs: Set[str],
) -> Dict[str, Any]:
    """Run environment checks: stale bytecache + editable installs."""
    from stele.env_checks import scan_stale_pycache, check_editable_installs

    result: Dict[str, Any] = {"issues": []}
    if project_root:
        skip = skip_dirs - {"__pycache__"}
        bc = scan_stale_pycache(project_root, skip)
        if bc["total_stale_files"] > 0:
            result["issues"].append({"type": "stale_bytecache", **bc})
        ei = check_editable_installs(project_root)
        if ei["count"] > 0:
            result["issues"].append({"type": "editable_install_mismatch", **ei})
    result["total_issues"] = len(result["issues"])
    return result


def init_coordination(
    project_root: Optional[Path],
) -> Any:
    """Initialize cross-worktree coordination if git common dir exists."""
    from stele.coordination import CoordinationBackend, detect_git_common_dir

    git_common = detect_git_common_dir(project_root)
    if git_common is None:
        return None
    try:
        return CoordinationBackend(git_common)
    except (OSError, Exception):
        return None
