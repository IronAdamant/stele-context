"""
Cross-worktree coordination for multi-agent Stele.

Uses a shared SQLite database in the git common directory to coordinate
document locks and agent registration across worktrees.

Transparent fallback: when no git common directory is available (not in a
git repo, or no worktrees), coordination is disabled and the engine uses
per-worktree local locks only.
"""

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


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
    """Cross-worktree coordination via shared SQLite database.

    Lives in ``<git-common-dir>/stele/`` to be visible across all
    worktrees of a repository.  Manages:

    - Agent registry (who's active, which worktree, heartbeats)
    - Shared document locks (cross-worktree visibility)
    - Shared conflict log
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
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    worktree_root TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    last_heartbeat REAL NOT NULL,
                    pid INTEGER,
                    status TEXT DEFAULT 'active'
                )
            """)
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sc_doc "
                "ON shared_conflicts(document_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sc_time "
                "ON shared_conflicts(created_at)"
            )
            conn.commit()

    # -- Agent registry -------------------------------------------------------

    def register_agent(
        self,
        agent_id: str,
        worktree_root: str,
        pid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Register an agent with heartbeat."""
        now = time.time()
        if pid is None:
            pid = os.getpid()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agents
                (agent_id, worktree_root, started_at, last_heartbeat, pid, status)
                VALUES (?, ?, ?, ?, ?, 'active')
                ON CONFLICT(agent_id) DO UPDATE SET
                    worktree_root = excluded.worktree_root,
                    last_heartbeat = excluded.last_heartbeat,
                    pid = excluded.pid,
                    status = 'active'
                """,
                (agent_id, worktree_root, now, now, pid),
            )
            conn.commit()
        return {"registered": True, "agent_id": agent_id}

    def heartbeat(self, agent_id: str) -> Dict[str, Any]:
        """Update heartbeat timestamp for a registered agent."""
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE agents SET last_heartbeat = ? "
                "WHERE agent_id = ? AND status = 'active'",
                (now, agent_id),
            )
            conn.commit()
            return {"updated": cursor.rowcount > 0}

    def deregister_agent(self, agent_id: str) -> Dict[str, Any]:
        """Mark agent as stopped and release all its shared locks."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE agents SET status = 'stopped' WHERE agent_id = ?",
                (agent_id,),
            )
            cursor = conn.execute(
                "DELETE FROM shared_locks WHERE locked_by = ?",
                (agent_id,),
            )
            conn.commit()
            return {"deregistered": True, "locks_released": cursor.rowcount}

    def list_agents(
        self,
        active_only: bool = True,
        stale_timeout: float = 600.0,
    ) -> List[Dict[str, Any]]:
        """List registered agents with staleness detection."""
        now = time.time()
        with self._connect() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM agents WHERE status = 'active' "
                    "ORDER BY last_heartbeat DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agents ORDER BY last_heartbeat DESC"
                ).fetchall()

            agents = []
            for row in rows:
                d = dict(row)
                d["stale"] = (now - d["last_heartbeat"]) > stale_timeout
                agents.append(d)
            return agents

    def reap_stale_agents(self, timeout: float = 600.0) -> Dict[str, Any]:
        """Mark agents with no heartbeat as stopped and release locks."""
        cutoff = time.time() - timeout
        with self._connect() as conn:
            stale = conn.execute(
                "SELECT agent_id FROM agents "
                "WHERE status = 'active' AND last_heartbeat < ?",
                (cutoff,),
            ).fetchall()
            stale_ids = [r["agent_id"] for r in stale]

            if stale_ids:
                ph = ",".join("?" * len(stale_ids))
                conn.execute(
                    f"DELETE FROM shared_locks WHERE locked_by IN ({ph})",
                    stale_ids,
                )
                conn.execute(
                    f"UPDATE agents SET status = 'stopped' "
                    f"WHERE agent_id IN ({ph})",
                    stale_ids,
                )
                conn.commit()

            return {"reaped_count": len(stale_ids), "agents": stale_ids}

    # -- Shared document locks ------------------------------------------------

    def _agent_worktree(
        self, conn: sqlite3.Connection, agent_id: str,
    ) -> Optional[str]:
        row = conn.execute(
            "SELECT worktree_root FROM agents WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        return row["worktree_root"] if row else None

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
                        conn, document_path, owner, agent_id,
                        "lock_stolen", resolution="force_overwritten",
                    )

                conn.execute(
                    "UPDATE shared_locks SET locked_by = ?, locked_at = ?, "
                    "lock_ttl = ?, worktree_root = ? WHERE document_path = ?",
                    (agent_id, now, ttl,
                     self._agent_worktree(conn, agent_id), document_path),
                )
            else:
                conn.execute(
                    "INSERT INTO shared_locks "
                    "(document_path, locked_by, locked_at, lock_ttl, worktree_root) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (document_path, agent_id, now, ttl,
                     self._agent_worktree(conn, agent_id)),
                )

            conn.commit()
            return {"acquired": True}

    def release_lock(
        self, document_path: str, agent_id: str,
    ) -> Dict[str, Any]:
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
        self,
        document_path: str,
        agent_id: str,
        ttl: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Refresh lock TTL without releasing."""
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT locked_by, lock_ttl FROM shared_locks "
                "WHERE document_path = ?",
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
                    {"document_path": r["document_path"],
                     "was_locked_by": r["locked_by"]}
                    for r in rows
                ],
            }

    # -- Conflict log ---------------------------------------------------------

    def _record_conflict(
        self,
        conn: sqlite3.Connection,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        resolution: str = "rejected",
        details: Optional[Dict[str, Any]] = None,
    ) -> int:
        now = time.time()
        details_json = json.dumps(details) if details else None
        cursor = conn.execute(
            """
            INSERT INTO shared_conflicts
            (document_path, agent_a, agent_b, conflict_type,
             resolution, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (document_path, agent_a, agent_b, conflict_type,
             resolution, details_json, now),
        )
        conn.commit()
        return cursor.lastrowid

    def record_conflict(
        self,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        resolution: str = "rejected",
        details: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Log a conflict event to the shared conflict table."""
        with self._connect() as conn:
            return self._record_conflict(
                conn, document_path, agent_a, agent_b,
                conflict_type, resolution, details,
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
