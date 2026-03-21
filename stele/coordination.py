"""Cross-worktree coordination for multi-agent Stele.

Shared SQLite database in the git common directory for document locks,
agent registration, and conflict logging across worktrees.  Falls back
transparently when no git common directory is available.

Shared lock primitives live in ``lock_ops.py``.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from stele import agent_registry
from stele import change_notifications as _cn
from stele import lock_ops


def detect_git_common_dir(project_root: Path | None) -> Path | None:
    """Find the git common directory shared across all worktrees.

    For normal repos: returns the ``.git/`` directory.
    For worktrees: follows the ``.git`` file to the main repo's ``.git/``.
    Uses the ``commondir`` file (git standard) with a parent-heuristic
    fallback.  Returns None if not in a git repo.
    """
    if project_root is None:
        return None

    git_entry = project_root / ".git"
    if not git_entry.exists():
        return None

    if git_entry.is_dir():
        return git_entry

    # .git is a file — parse "gitdir: <path>"
    try:
        content = git_entry.read_text().strip()
    except OSError:
        return None

    if not content.startswith("gitdir: "):
        return None

    gitdir = Path(content[8:])
    if not gitdir.is_absolute():
        gitdir = (project_root / gitdir).resolve()

    # Method 1: commondir file (git standard for worktrees)
    commondir_file = gitdir / "commondir"
    if commondir_file.exists():
        try:
            commondir_rel = commondir_file.read_text().strip()
            common = (gitdir / commondir_rel).resolve()
            if (common / "HEAD").exists():
                return common
        except OSError:
            pass

    # Method 2: worktrees/ parent heuristic
    if gitdir.parent.name == "worktrees":
        common = gitdir.parent.parent
        if (common / "HEAD").exists():
            return common

    # Fallback: gitdir itself if it looks like a valid git dir
    if (gitdir / "HEAD").exists():
        return gitdir

    return None


class CoordinationBackend:
    """Cross-worktree coordination via shared SQLite in ``<git-common-dir>/stele/``.

    Manages agent registry, shared document locks, and conflict log.
    """

    def __init__(self, git_common_dir: Path):
        self.base_dir = git_common_dir / "stele"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.base_dir / "coordination.db"
        self._init_database()

    def _connect(self) -> sqlite3.Connection:
        """Create a connection with WAL mode, busy timeout, and perf PRAGMAs."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        agent_registry.init_agents_table(self._connect)
        _cn.init_notifications_table(self._connect)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shared_locks (
                    document_path TEXT PRIMARY KEY,
                    locked_by TEXT NOT NULL,
                    locked_at REAL NOT NULL,
                    lock_ttl REAL DEFAULT 300.0,
                    worktree_root TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS shared_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_path TEXT NOT NULL,
                    agent_a TEXT NOT NULL,
                    agent_b TEXT NOT NULL,
                    conflict_type TEXT NOT NULL,
                    expected_version INTEGER,
                    actual_version INTEGER,
                    resolution TEXT DEFAULT 'rejected',
                    details_json TEXT,
                    created_at REAL NOT NULL
                )
            """)
            # Migration: add version columns to existing shared_conflicts
            cursor = conn.execute("PRAGMA table_info(shared_conflicts)")
            cols = {row[1] for row in cursor.fetchall()}
            if "expected_version" not in cols:
                conn.execute(
                    "ALTER TABLE shared_conflicts ADD COLUMN expected_version INTEGER"
                )
                conn.execute(
                    "ALTER TABLE shared_conflicts ADD COLUMN actual_version INTEGER"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sc_doc "
                "ON shared_conflicts(document_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sc_time ON shared_conflicts(created_at)"
            )
            conn.commit()

    # -- Agent registry (delegated to stele.agent_registry) --------------------

    def register_agent(
        self, agent_id: str, worktree_root: str, pid: int | None = None
    ) -> dict[str, Any]:
        return agent_registry.register_agent(
            self._connect, agent_id, worktree_root, pid
        )

    def heartbeat(self, agent_id: str) -> dict[str, Any]:
        return agent_registry.heartbeat(self._connect, agent_id)

    def deregister_agent(self, agent_id: str) -> dict[str, Any]:
        return agent_registry.deregister_agent(self._connect, agent_id)

    def list_agents(
        self, active_only: bool = True, stale_timeout: float = 600.0
    ) -> list[dict[str, Any]]:
        return agent_registry.list_agents(self._connect, active_only, stale_timeout)

    def reap_stale_agents(self, timeout: float = 600.0) -> dict[str, Any]:
        return agent_registry.reap_stale_agents(self._connect, timeout)

    # -- Shared document locks ------------------------------------------------

    @staticmethod
    def _agent_worktree(conn: sqlite3.Connection, agent_id: str) -> str | None:
        return agent_registry.agent_worktree(conn, agent_id)

    def acquire_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: float = 300.0,
        force: bool = False,
    ) -> dict[str, Any]:
        """Acquire a shared cross-worktree document lock."""
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM shared_locks WHERE document_path = ?",
                (document_path,),
            ).fetchone()

            if row is not None:
                owner = row["locked_by"]
                locked_at = row["locked_at"]
                current_ttl = row["lock_ttl"]
                expired = now > locked_at + current_ttl

                if owner == agent_id:
                    conn.execute(
                        "UPDATE shared_locks SET locked_at = ?, lock_ttl = ? "
                        "WHERE document_path = ?",
                        (now, ttl, document_path),
                    )
                    conn.commit()
                    return {"acquired": True}

                if not expired and not force:
                    return {
                        "acquired": False,
                        "locked_by": owner,
                        "locked_at": locked_at,
                        "expires_at": locked_at + current_ttl,
                        "worktree": row["worktree_root"],
                    }

                if force and not expired:
                    lock_ops.record_conflict(
                        conn,
                        "shared_conflicts",
                        document_path,
                        owner,
                        agent_id,
                        "lock_stolen",
                        resolution="force_overwritten",
                    )

                conn.execute(
                    "UPDATE shared_locks SET locked_by = ?, locked_at = ?, "
                    "lock_ttl = ?, worktree_root = ? WHERE document_path = ?",
                    (
                        agent_id,
                        now,
                        ttl,
                        self._agent_worktree(conn, agent_id),
                        document_path,
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO shared_locks "
                    "(document_path, locked_by, locked_at, lock_ttl, worktree_root) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        document_path,
                        agent_id,
                        now,
                        ttl,
                        self._agent_worktree(conn, agent_id),
                    ),
                )

            conn.commit()
            return {"acquired": True}

    def release_lock(self, document_path: str, agent_id: str) -> dict[str, Any]:
        """Release a shared document lock. Only the holder can release."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT locked_by FROM shared_locks WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            if row is None:
                return {"released": False, "reason": "not_locked"}
            if row["locked_by"] != agent_id:
                return {"released": False, "reason": "not_owner"}
            conn.execute(
                "DELETE FROM shared_locks WHERE document_path = ?",
                (document_path,),
            )
            conn.commit()
            return {"released": True}

    def get_lock_status(self, document_path: str) -> dict[str, Any]:
        """Check shared lock status. Expired locks are cleaned up."""
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM shared_locks WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            if row is None:
                return {"locked": False}

            locked_at = row["locked_at"]
            ttl = row["lock_ttl"]
            if now > locked_at + ttl:
                conn.execute(
                    "DELETE FROM shared_locks WHERE document_path = ?",
                    (document_path,),
                )
                conn.commit()
                return {"locked": False}

            return {
                "locked": True,
                "locked_by": row["locked_by"],
                "locked_at": locked_at,
                "expires_at": locked_at + ttl,
                "worktree": row["worktree_root"],
            }

    def refresh_lock(
        self, document_path: str, agent_id: str, ttl: float | None = None
    ) -> dict[str, Any]:
        """Refresh lock TTL without releasing."""
        with self._connect() as conn:
            return lock_ops.refresh_lock(
                conn,
                "shared_locks",
                document_path,
                agent_id,
                ttl,
                not_found_reason="not_locked",
            )

    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        """Release all shared locks held by an agent."""
        with self._connect() as conn:
            return lock_ops.release_agent_locks(
                conn,
                "shared_locks",
                agent_id,
                delete=True,
            )

    def reap_expired_locks(self) -> dict[str, Any]:
        """Clear all expired shared locks."""
        with self._connect() as conn:
            return lock_ops.reap_expired_locks(
                conn,
                "shared_locks",
                delete=True,
            )

    # -- Conflict log ---------------------------------------------------------

    def _record_conflict(
        self,
        conn_or_none: sqlite3.Connection | None,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        expected_version: int | None = None,
        actual_version: int | None = None,
        resolution: str = "rejected",
        details: dict[str, Any] | None = None,
    ) -> int | None:
        conn = conn_or_none if conn_or_none is not None else self._connect()
        try:
            result = lock_ops.record_conflict(
                conn,
                "shared_conflicts",
                document_path,
                agent_a,
                agent_b,
                conflict_type,
                expected_version,
                actual_version,
                resolution,
                details,
            )
            if conn_or_none is None:
                conn.commit()
            return result
        finally:
            if conn_or_none is None:
                conn.close()

    def record_conflict(
        self,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        expected_version: int | None = None,
        actual_version: int | None = None,
        resolution: str = "rejected",
        details: dict[str, Any] | None = None,
    ) -> int | None:
        """Log a conflict event to the shared conflict table."""
        return self._record_conflict(
            None,
            document_path,
            agent_a,
            agent_b,
            conflict_type,
            expected_version,
            actual_version,
            resolution,
            details,
        )

    def get_conflicts(
        self,
        document_path: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Retrieve shared conflict history."""
        with self._connect() as conn:
            return lock_ops.query_conflicts(
                conn,
                "shared_conflicts",
                document_path,
                agent_id,
                limit,
            )

    # -- Change notifications (delegated to stele.change_notifications) -------

    def notify_change(
        self, document_path: str, change_type: str, agent_id: str
    ) -> None:
        _cn.notify_change(
            self._connect, self._agent_worktree, document_path, change_type, agent_id
        )

    def notify_changes_batch(self, changes: list[tuple], agent_id: str) -> int:
        return _cn.notify_changes_batch(
            self._connect, self._agent_worktree, changes, agent_id
        )

    def get_notifications(
        self,
        since: float | None = None,
        exclude_agent: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return _cn.get_notifications(self._connect, since, exclude_agent, limit)

    def prune_notifications(
        self,
        max_age_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> int:
        return _cn.prune_notifications(self._connect, max_age_seconds, max_entries)
