"""
Document ownership and optimistic locking storage for Stele.

Manages per-document locks, version checking, and conflict logging
for multi-agent coordination. Follows the same delegate pattern as
SessionStorage, MetadataStorage, and SymbolStorage.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from stele.storage_schema import connect
from typing import Any


class DocumentLockStorage:
    """Per-document ownership, optimistic locking, and conflict log.

    Owns the ``locked_by``, ``locked_at``, ``lock_ttl``, ``doc_version``
    columns on the ``documents`` table, and the ``document_conflicts``
    table.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    # -- Per-document ownership -----------------------------------------------

    def acquire_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: float = 300.0,
        force: bool = False,
    ) -> dict[str, Any]:
        """Acquire exclusive ownership of a document.

        Expired locks are transparently reclaimed.  If ``force=True``,
        the lock is stolen and a conflict is logged.
        """
        now = time.time()
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT locked_by, locked_at, lock_ttl, doc_version "
                "FROM documents WHERE document_path = ?",
                (document_path,),
            ).fetchone()

            if row is None:
                return {"acquired": False, "reason": "document_not_found"}

            current_owner = row["locked_by"]
            locked_at = row["locked_at"] or 0.0
            current_ttl = row["lock_ttl"] or 300.0
            expired = current_owner and (now > locked_at + current_ttl)

            if current_owner and current_owner != agent_id and not expired:
                if not force:
                    return {
                        "acquired": False,
                        "locked_by": current_owner,
                        "locked_at": locked_at,
                        "expires_at": locked_at + current_ttl,
                    }
                # Force-steal: log conflict
                self._record_conflict(
                    conn,
                    document_path=document_path,
                    agent_a=current_owner,
                    agent_b=agent_id,
                    conflict_type="lock_stolen",
                    resolution="force_overwritten",
                )

            conn.execute(
                "UPDATE documents SET locked_by = ?, locked_at = ?, lock_ttl = ? "
                "WHERE document_path = ?",
                (agent_id, now, ttl, document_path),
            )
            conn.commit()
            return {"acquired": True, "doc_version": row["doc_version"]}

    def refresh_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        """Reset the TTL timer on an existing lock without releasing it.

        Only the lock holder can refresh.  Optionally sets a new TTL.
        """
        now = time.time()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT locked_by, lock_ttl FROM documents WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            if row is None:
                return {"refreshed": False, "reason": "document_not_found"}
            if row[0] != agent_id:
                return {"refreshed": False, "reason": "not_owner"}
            new_ttl = ttl if ttl is not None else row[1]
            conn.execute(
                "UPDATE documents SET locked_at = ?, lock_ttl = ? "
                "WHERE document_path = ?",
                (now, new_ttl, document_path),
            )
            conn.commit()
            return {
                "refreshed": True,
                "expires_at": now + new_ttl,
            }

    def release_lock(
        self,
        document_path: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """Release ownership.  Only the holder can release."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT locked_by FROM documents WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            if row is None:
                return {"released": False, "reason": "document_not_found"}
            if row[0] != agent_id:
                return {"released": False, "reason": "not_owner"}
            conn.execute(
                "UPDATE documents SET locked_by = NULL, locked_at = NULL "
                "WHERE document_path = ?",
                (document_path,),
            )
            conn.commit()
            return {"released": True}

    def get_lock_status(self, document_path: str) -> dict[str, Any]:
        """Check lock status.  Expired locks are reported as unlocked."""
        now = time.time()
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT locked_by, locked_at, lock_ttl "
                "FROM documents WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            if row is None:
                return {"locked": False, "reason": "document_not_found"}

            owner = row["locked_by"]
            locked_at = row["locked_at"] or 0.0
            ttl = row["lock_ttl"] or 300.0

            if not owner or now > locked_at + ttl:
                return {"locked": False}

            return {
                "locked": True,
                "locked_by": owner,
                "locked_at": locked_at,
                "expires_at": locked_at + ttl,
            }

    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        """Release all locks held by an agent (cleanup on disconnect)."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT document_path FROM documents WHERE locked_by = ?",
                (agent_id,),
            )
            docs = [r[0] for r in cursor.fetchall()]
            if docs:
                conn.execute(
                    "UPDATE documents SET locked_by = NULL, locked_at = NULL "
                    "WHERE locked_by = ?",
                    (agent_id,),
                )
                conn.commit()
            return {"released_count": len(docs), "documents": docs}

    def reap_expired_locks(self) -> dict[str, Any]:
        """Clear all expired locks.  Returns details of reaped locks."""
        now = time.time()
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT document_path, locked_by, locked_at, lock_ttl "
                "FROM documents "
                "WHERE locked_by IS NOT NULL "
                "AND (locked_at + lock_ttl) < ?",
                (now,),
            ).fetchall()
            if rows:
                expired_paths = [r["document_path"] for r in rows]
                placeholders = ",".join("?" * len(expired_paths))
                conn.execute(
                    f"UPDATE documents SET locked_by = NULL, locked_at = NULL "
                    f"WHERE document_path IN ({placeholders})",
                    expired_paths,
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

    def get_lock_stats(self) -> dict[str, Any]:
        """Get aggregate lock and conflict statistics."""
        now = time.time()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE locked_by IS NOT NULL"
            ).fetchone()
            total_locked = row[0]

            row = conn.execute(
                "SELECT COUNT(*) FROM documents "
                "WHERE locked_by IS NOT NULL "
                "AND (locked_at + lock_ttl) < ?",
                (now,),
            ).fetchone()
            expired_locks = row[0]

            row = conn.execute("SELECT COUNT(*) FROM document_conflicts").fetchone()
            total_conflicts = row[0]

            row = conn.execute(
                "SELECT MAX(created_at) FROM document_conflicts"
            ).fetchone()
            last_conflict_at = row[0]

            agents_row = conn.execute(
                "SELECT COUNT(DISTINCT locked_by) FROM documents "
                "WHERE locked_by IS NOT NULL"
            ).fetchone()
            active_agents = agents_row[0]

        return {
            "locked_documents": total_locked,
            "expired_locks": expired_locks,
            "active_lock_agents": active_agents,
            "total_conflicts": total_conflicts,
            "last_conflict_at": last_conflict_at,
        }

    # -- Optimistic locking ---------------------------------------------------

    def get_version(self, document_path: str) -> int | None:
        """Get current version of a document."""
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT doc_version FROM documents WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            return row[0] if row else None

    def increment_version(self, document_path: str) -> int:
        """Atomically increment version, return new value."""
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE documents SET doc_version = doc_version + 1 "
                "WHERE document_path = ?",
                (document_path,),
            )
            row = conn.execute(
                "SELECT doc_version FROM documents WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            conn.commit()
            return row[0] if row else 1

    def check_and_increment_version(
        self,
        document_path: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Atomic compare-and-swap on doc_version.

        Returns ``{"success": True, "new_version": N}`` on match,
        ``{"success": False, "expected": E, "actual": A}`` on mismatch.
        """
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT doc_version FROM documents WHERE document_path = ?",
                (document_path,),
            ).fetchone()
            if row is None:
                return {"success": False, "reason": "document_not_found"}

            actual = row[0] or 1
            if actual != expected_version:
                return {
                    "success": False,
                    "expected": expected_version,
                    "actual": actual,
                }

            conn.execute(
                "UPDATE documents SET doc_version = ? WHERE document_path = ?",
                (actual + 1, document_path),
            )
            conn.commit()
            return {"success": True, "new_version": actual + 1}

    # -- Conflict log ---------------------------------------------------------

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
        """Log a conflict event.  Returns conflict ID."""
        with connect(self.db_path) as conn:
            return self._record_conflict(
                conn,
                document_path=document_path,
                agent_a=agent_a,
                agent_b=agent_b,
                conflict_type=conflict_type,
                expected_version=expected_version,
                actual_version=actual_version,
                resolution=resolution,
                details=details,
            )

    def _record_conflict(
        self,
        conn: sqlite3.Connection,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        expected_version: int | None = None,
        actual_version: int | None = None,
        resolution: str = "rejected",
        details: dict[str, Any] | None = None,
    ) -> int | None:
        """Internal: log conflict within an existing connection."""
        now = time.time()
        details_json = json.dumps(details) if details else None
        cursor = conn.execute(
            """
            INSERT INTO document_conflicts
            (document_path, agent_a, agent_b, conflict_type,
             expected_version, actual_version, resolution, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
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
        conn.commit()
        return cursor.lastrowid

    def get_conflicts(
        self,
        document_path: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Retrieve conflict history with optional filters."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            conditions = []
            params: list[Any] = []

            if document_path is not None:
                conditions.append("document_path = ?")
                params.append(document_path)
            if agent_id is not None:
                conditions.append("(agent_a = ? OR agent_b = ?)")
                params.extend([agent_id, agent_id])

            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            query = (
                f"SELECT * FROM document_conflicts {where} "
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

    def prune_conflicts(
        self,
        max_age_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> int:
        """Prune old conflict entries.  Returns deleted count."""
        deleted = 0
        with connect(self.db_path) as conn:
            if max_age_seconds is not None:
                cutoff = time.time() - max_age_seconds
                cursor = conn.execute(
                    "DELETE FROM document_conflicts WHERE created_at < ?",
                    (cutoff,),
                )
                deleted += cursor.rowcount

            if max_entries is not None:
                cursor = conn.execute(
                    "DELETE FROM document_conflicts WHERE id NOT IN "
                    "(SELECT id FROM document_conflicts ORDER BY created_at DESC LIMIT ?)",
                    (max_entries,),
                )
                deleted += cursor.rowcount

            conn.commit()
        return deleted
