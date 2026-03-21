"""Agent registry for cross-worktree coordination.

Manages agent lifecycle: registration, heartbeats, staleness detection,
and reaping.  Standalone module with zero internal dependencies — uses
only the Python standard library.

All public functions accept a ``connect`` callable that returns an
``sqlite3.Connection`` (with ``row_factory = sqlite3.Row``).  This
keeps the module decoupled from ``CoordinationBackend`` while reusing
its WAL-mode connection pool.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Callable


ConnectFn = Callable[[], sqlite3.Connection]


def init_agents_table(connect: ConnectFn) -> None:
    """Create the ``agents`` table if it does not exist."""
    with connect() as conn:
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


def register_agent(
    connect: ConnectFn,
    agent_id: str,
    worktree_root: str,
    pid: int | None = None,
) -> dict[str, Any]:
    """Register an agent with heartbeat."""
    now = time.time()
    if pid is None:
        pid = os.getpid()
    with connect() as conn:
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
    return {"registered": True, "agent_id": agent_id}


def heartbeat(connect: ConnectFn, agent_id: str) -> dict[str, Any]:
    """Update heartbeat timestamp for a registered agent."""
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE agents SET last_heartbeat = ? "
            "WHERE agent_id = ? AND status = 'active'",
            (now, agent_id),
        )
        return {"updated": cursor.rowcount > 0}


def deregister_agent(connect: ConnectFn, agent_id: str) -> dict[str, Any]:
    """Mark agent as stopped and release all its shared locks."""
    with connect() as conn:
        conn.execute(
            "UPDATE agents SET status = 'stopped' WHERE agent_id = ?",
            (agent_id,),
        )
        cursor = conn.execute(
            "DELETE FROM shared_locks WHERE locked_by = ?",
            (agent_id,),
        )
        return {"deregistered": True, "locks_released": cursor.rowcount}


def list_agents(
    connect: ConnectFn,
    active_only: bool = True,
    stale_timeout: float = 600.0,
) -> list[dict[str, Any]]:
    """List registered agents with staleness detection."""
    now = time.time()
    with connect() as conn:
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


def reap_stale_agents(
    connect: ConnectFn,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Mark agents with no heartbeat as stopped and release locks."""
    cutoff = time.time() - timeout
    with connect() as conn:
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
                f"UPDATE agents SET status = 'stopped' WHERE agent_id IN ({ph})",
                stale_ids,
            )

        return {"reaped_count": len(stale_ids), "agents": stale_ids}


def agent_worktree(
    conn: sqlite3.Connection,
    agent_id: str,
) -> str | None:
    """Look up the worktree root for a registered agent.

    Unlike the other functions in this module, this accepts an already-open
    connection so callers can use it inside an existing transaction.
    """
    row = conn.execute(
        "SELECT worktree_root FROM agents WHERE agent_id = ?",
        (agent_id,),
    ).fetchone()
    return row["worktree_root"] if row else None
