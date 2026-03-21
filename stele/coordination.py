"""Cross-worktree coordination for multi-agent Stele.

Shared SQLite database in the git common directory for document locks,
agent registration, and conflict logging across worktrees.  Falls back
transparently when no git common directory is available.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from stele import agent_registry


def detect_git_common_dir(project_root: Optional[Path]) -> Optional[Path]:
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
        """Create a connection with WAL mode and busy timeout."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        agent_registry.init_agents_table(self._connect)
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
                    resolution TEXT DEFAULT 'rejected',
                    details_json TEXT,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS change_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_path TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    worktree_root TEXT,
                    created_at REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sc_doc "
                "ON shared_conflicts(document_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sc_time ON shared_conflicts(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cn_time "
                "ON change_notifications(created_at)"
            )
            conn.commit()

    # -- Agent registry (delegated to stele.agent_registry) --------------------

    def register_agent(
        self, agent_id: str, worktree_root: str, pid: Optional[int] = None
    ) -> Dict[str, Any]:
        """Register an agent with heartbeat."""
        return agent_registry.register_agent(
            self._connect, agent_id, worktree_root, pid
        )

    def heartbeat(self, agent_id: str) -> Dict[str, Any]:
        """Update heartbeat timestamp for a registered agent."""
        return agent_registry.heartbeat(self._connect, agent_id)

    def deregister_agent(self, agent_id: str) -> Dict[str, Any]:
        """Mark agent as stopped and release all its shared locks."""
        return agent_registry.deregister_agent(self._connect, agent_id)

    def list_agents(
        self, active_only: bool = True, stale_timeout: float = 600.0
    ) -> List[Dict[str, Any]]:
        """List registered agents with staleness detection."""
        return agent_registry.list_agents(self._connect, active_only, stale_timeout)

    def reap_stale_agents(self, timeout: float = 600.0) -> Dict[str, Any]:
        """Mark agents with no heartbeat as stopped and release locks."""
        return agent_registry.reap_stale_agents(self._connect, timeout)

    # -- Shared document locks ------------------------------------------------

    @staticmethod
    def _agent_worktree(conn: sqlite3.Connection, agent_id: str) -> Optional[str]:
        return agent_registry.agent_worktree(conn, agent_id)

    def acquire_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: float = 300.0,
        force: bool = False,
    ) -> Dict[str, Any]:
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
                    # Re-acquire (refresh)
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
                    self._record_conflict(
                        conn,
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

    def release_lock(self, document_path: str, agent_id: str) -> Dict[str, Any]:
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

    def get_lock_status(self, document_path: str) -> Dict[str, Any]:
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
        self, document_path: str, agent_id: str, ttl: Optional[float] = None
    ) -> Dict[str, Any]:
        """Refresh lock TTL without releasing."""
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT locked_by, lock_ttl FROM shared_locks WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            if row is None:
                return {"refreshed": False, "reason": "not_locked"}
            if row["locked_by"] != agent_id:
                return {"refreshed": False, "reason": "not_owner"}
            new_ttl = ttl if ttl is not None else row["lock_ttl"]
            conn.execute(
                "UPDATE shared_locks SET locked_at = ?, lock_ttl = ? "
                "WHERE document_path = ?",
                (now, new_ttl, document_path),
            )
            conn.commit()
            return {"refreshed": True, "expires_at": now + new_ttl}

    def release_agent_locks(self, agent_id: str) -> Dict[str, Any]:
        """Release all shared locks held by an agent."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document_path FROM shared_locks WHERE locked_by = ?",
                (agent_id,),
            ).fetchall()
            docs = [r["document_path"] for r in rows]
            if docs:
                conn.execute(
                    "DELETE FROM shared_locks WHERE locked_by = ?",
                    (agent_id,),
                )
                conn.commit()
            return {"released_count": len(docs), "documents": docs}

    def reap_expired_locks(self) -> Dict[str, Any]:
        """Clear all expired shared locks."""
        now = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document_path, locked_by FROM shared_locks "
                "WHERE (locked_at + lock_ttl) < ?",
                (now,),
            ).fetchall()
            if rows:
                conn.execute(
                    "DELETE FROM shared_locks WHERE (locked_at + lock_ttl) < ?",
                    (now,),
                )
                conn.commit()
            return {
                "reaped_count": len(rows),
                "documents": [
                    {
                        "document_path": r["document_path"],
                        "was_locked_by": r["locked_by"],
                    }
                    for r in rows
                ],
            }

    # -- Conflict log ---------------------------------------------------------

    def _record_conflict(
        self,
        conn_or_none: Optional[sqlite3.Connection],
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        resolution: str = "rejected",
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        now = time.time()
        details_json = json.dumps(details) if details else None
        conn = conn_or_none if conn_or_none is not None else self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO shared_conflicts"
                " (document_path, agent_a, agent_b, conflict_type,"
                " resolution, details_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    document_path,
                    agent_a,
                    agent_b,
                    conflict_type,
                    resolution,
                    details_json,
                    now,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            if conn_or_none is None:
                conn.close()

    def record_conflict(
        self,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        resolution: str = "rejected",
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Log a conflict event to the shared conflict table."""
        return self._record_conflict(
            None,
            document_path,
            agent_a,
            agent_b,
            conflict_type,
            resolution,
            details,
        )

    def get_conflicts(
        self,
        document_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Retrieve shared conflict history."""
        with self._connect() as conn:
            conditions: List[str] = []
            params: List[Any] = []
            if document_path is not None:
                conditions.append("document_path = ?")
                params.append(document_path)
            if agent_id is not None:
                conditions.append("(agent_a = ? OR agent_b = ?)")
                params.extend([agent_id, agent_id])

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            query = (
                f"SELECT * FROM shared_conflicts {where} "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(query, params).fetchall()

            results = []
            for row in rows:
                d = dict(row)
                if d.get("details_json"):
                    d["details"] = json.loads(d["details_json"])
                    del d["details_json"]
                else:
                    d.pop("details_json", None)
                results.append(d)
            return results

    # -- Change notifications -------------------------------------------------

    def notify_change(
        self,
        document_path: str,
        change_type: str,
        agent_id: str,
    ) -> None:
        """Record a change notification visible to all worktrees."""
        now = time.time()
        _sql = (
            "INSERT INTO change_notifications"
            " (document_path, change_type, agent_id, worktree_root, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
        )
        with self._connect() as conn:
            worktree = self._agent_worktree(conn, agent_id)
            conn.execute(_sql, (document_path, change_type, agent_id, worktree, now))
            conn.commit()

    def notify_changes_batch(self, changes: List[tuple], agent_id: str) -> int:
        """Batch-write change notifications (each: ``(path, type)``)."""
        if not changes:
            return 0
        now = time.time()
        _sql = (
            "INSERT INTO change_notifications"
            " (document_path, change_type, agent_id, worktree_root, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
        )
        with self._connect() as conn:
            worktree = self._agent_worktree(conn, agent_id)
            conn.executemany(
                _sql,
                [(p, ct, agent_id, worktree, now) for p, ct in changes],
            )
            conn.commit()
        return len(changes)

    def get_notifications(
        self,
        since: Optional[float] = None,
        exclude_agent: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get change notifications, optionally since a timestamp.

        Auto-prunes entries older than 24 h.  Use ``exclude_agent``
        to skip the caller's own notifications.
        """
        with self._connect() as conn:
            # Lazy prune: remove notifications older than 24 hours
            cutoff = time.time() - 86400
            conn.execute(
                "DELETE FROM change_notifications WHERE created_at < ?",
                (cutoff,),
            )
            conditions: List[str] = []
            params: List[Any] = []

            if since is not None:
                conditions.append("created_at > ?")
                params.append(since)
            if exclude_agent is not None:
                conditions.append("agent_id != ?")
                params.append(exclude_agent)

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            query = (
                f"SELECT * FROM change_notifications {where} "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(query, params).fetchall()

            notifications = [dict(r) for r in rows]
            latest = max(
                (n["created_at"] for n in notifications),
                default=since or 0.0,
            )

            return {
                "notifications": notifications,
                "count": len(notifications),
                "latest_timestamp": latest,
            }

    def prune_notifications(
        self,
        max_age_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
    ) -> int:
        """Prune old change notifications. Returns deleted count."""
        deleted = 0
        with self._connect() as conn:
            if max_age_seconds is not None:
                cutoff = time.time() - max_age_seconds
                cursor = conn.execute(
                    "DELETE FROM change_notifications WHERE created_at < ?",
                    (cutoff,),
                )
                deleted += cursor.rowcount

            if max_entries is not None:
                cursor = conn.execute(
                    "DELETE FROM change_notifications WHERE id NOT IN "
                    "(SELECT id FROM change_notifications "
                    "ORDER BY created_at DESC LIMIT ?)",
                    (max_entries,),
                )
                deleted += cursor.rowcount

            conn.commit()
        return deleted
