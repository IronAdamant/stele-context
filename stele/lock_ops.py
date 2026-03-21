"""
Shared lock operation primitives for Stele.

Pure functions used by both ``DocumentLockStorage`` (per-worktree locks
on the ``documents`` table) and ``CoordinationBackend`` (cross-worktree
locks on the ``shared_locks`` table).  Each function accepts an open
``sqlite3.Connection`` and a table name, keeping callers simple.

**Transaction policy:** These functions never call ``conn.commit()``.
Callers are responsible for committing — typically via a context
manager (``with connect(...) as conn:``).

Follows the same zero-internal-deps pattern as ``agent_registry.py``
and ``change_notifications.py``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


def hydrate_conflicts(rows: list) -> list[dict[str, Any]]:
    """Convert conflict rows to dicts, parsing details_json inline."""
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


def refresh_lock(
    conn: sqlite3.Connection,
    table: str,
    document_path: str,
    agent_id: str,
    ttl: float | None,
    not_found_reason: str = "document_not_found",
) -> dict[str, Any]:
    """Check owner and refresh lock TTL.  Shared by local and shared locks."""
    now = time.time()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        f"SELECT locked_by, lock_ttl FROM {table} WHERE document_path = ?",
        (document_path,),
    ).fetchone()
    if row is None:
        return {"refreshed": False, "reason": not_found_reason}
    if row["locked_by"] != agent_id:
        return {"refreshed": False, "reason": "not_owner"}
    new_ttl = ttl if ttl is not None else row["lock_ttl"]
    conn.execute(
        f"UPDATE {table} SET locked_at = ?, lock_ttl = ? WHERE document_path = ?",
        (now, new_ttl, document_path),
    )
    return {"refreshed": True, "expires_at": now + new_ttl}


def record_conflict(
    conn: sqlite3.Connection,
    table: str,
    document_path: str,
    agent_a: str,
    agent_b: str,
    conflict_type: str,
    expected_version: int | None = None,
    actual_version: int | None = None,
    resolution: str = "rejected",
    details: dict[str, Any] | None = None,
) -> int | None:
    """Insert a conflict row.  Shared by local and shared conflict tables."""
    now = time.time()
    details_json = json.dumps(details) if details else None
    cursor = conn.execute(
        f"INSERT INTO {table}"
        " (document_path, agent_a, agent_b, conflict_type,"
        " expected_version, actual_version,"
        " resolution, details_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            document_path,
            agent_a,
            agent_b,
            conflict_type,
            expected_version,
            actual_version,
            resolution,
            details_json,
            now,
        ),
    )
    return cursor.lastrowid


def query_conflicts(
    conn: sqlite3.Connection,
    table: str,
    document_path: str | None = None,
    agent_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Build WHERE clause and query conflict rows.  Shared by both tables."""
    conn.row_factory = sqlite3.Row
    conditions: list[str] = []
    params: list[Any] = []
    if document_path is not None:
        conditions.append("document_path = ?")
        params.append(document_path)
    if agent_id is not None:
        conditions.append("(agent_a = ? OR agent_b = ?)")
        params.extend([agent_id, agent_id])
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    query = f"SELECT * FROM {table} {where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return hydrate_conflicts(rows)


def release_agent_locks(
    conn: sqlite3.Connection,
    table: str,
    agent_id: str,
    *,
    delete: bool = False,
) -> dict[str, Any]:
    """Release all locks held by an agent.

    When ``delete=True``, rows are removed (shared_locks).
    When ``delete=False``, columns are NULLed (documents table).
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT document_path FROM {table} WHERE locked_by = ?",
        (agent_id,),
    ).fetchall()
    docs = [r["document_path"] for r in rows]
    if docs:
        if delete:
            conn.execute(f"DELETE FROM {table} WHERE locked_by = ?", (agent_id,))
        else:
            conn.execute(
                f"UPDATE {table} SET locked_by = NULL, locked_at = NULL "
                "WHERE locked_by = ?",
                (agent_id,),
            )
    return {"released_count": len(docs), "documents": docs}


def reap_expired_locks(
    conn: sqlite3.Connection,
    table: str,
    *,
    delete: bool = False,
) -> dict[str, Any]:
    """Clear all expired locks, returning details of reaped entries.

    When ``delete=True``, expired rows are removed (shared_locks).
    When ``delete=False``, lock columns are NULLed (documents table).
    """
    now = time.time()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"SELECT document_path, locked_by FROM {table} "
        "WHERE locked_by IS NOT NULL AND (locked_at + lock_ttl) < ?",
        (now,),
    ).fetchall()
    if rows:
        if delete:
            conn.execute(
                f"DELETE FROM {table} WHERE (locked_at + lock_ttl) < ?",
                (now,),
            )
        else:
            expired_paths = [r["document_path"] for r in rows]
            placeholders = ",".join("?" * len(expired_paths))
            conn.execute(
                f"UPDATE {table} SET locked_by = NULL, locked_at = NULL "
                f"WHERE document_path IN ({placeholders})",
                expired_paths,
            )
    return {
        "reaped_count": len(rows),
        "documents": [
            {"document_path": r["document_path"], "was_locked_by": r["locked_by"]}
            for r in rows
        ],
    }
