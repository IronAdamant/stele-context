"""
Change notification storage for cross-worktree coordination.

Module-level functions that receive a connect callable, following the
same pattern as agent_registry.py. Extracted from coordination.py
to keep it under the 500 LOC limit.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Callable

ConnectFn = Callable[[], sqlite3.Connection]
GetWorktreeFn = Callable[[sqlite3.Connection, str], str | None]


def init_notifications_table(connect: ConnectFn) -> None:
    """Create the change_notifications table and index if missing."""
    with connect() as conn:
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
            "CREATE INDEX IF NOT EXISTS idx_cn_time ON change_notifications(created_at)"
        )


def notify_change(
    connect: ConnectFn,
    get_worktree: GetWorktreeFn,
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
    with connect() as conn:
        worktree = get_worktree(conn, agent_id)
        conn.execute(_sql, (document_path, change_type, agent_id, worktree, now))


def notify_changes_batch(
    connect: ConnectFn,
    get_worktree: GetWorktreeFn,
    changes: list[tuple],
    agent_id: str,
) -> int:
    """Batch-write change notifications (each: ``(path, type)``)."""
    if not changes:
        return 0
    now = time.time()
    _sql = (
        "INSERT INTO change_notifications"
        " (document_path, change_type, agent_id, worktree_root, created_at)"
        " VALUES (?, ?, ?, ?, ?)"
    )
    with connect() as conn:
        worktree = get_worktree(conn, agent_id)
        conn.executemany(
            _sql,
            [(p, ct, agent_id, worktree, now) for p, ct in changes],
        )
    return len(changes)


def get_notifications(
    connect: ConnectFn,
    since: float | None = None,
    exclude_agent: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Get change notifications, optionally since a timestamp.

    Auto-prunes entries older than 24 h.  Use ``exclude_agent``
    to skip the caller's own notifications.
    """
    with connect() as conn:
        # Lazy prune: remove notifications older than 24 hours
        cutoff = time.time() - 86400
        conn.execute(
            "DELETE FROM change_notifications WHERE created_at < ?",
            (cutoff,),
        )
        conditions: list[str] = []
        params: list[Any] = []

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
    connect: ConnectFn,
    max_age_seconds: float | None = None,
    max_entries: int | None = None,
) -> int:
    """Prune old change notifications. Returns deleted count."""
    deleted = 0
    with connect() as conn:
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

    return deleted
