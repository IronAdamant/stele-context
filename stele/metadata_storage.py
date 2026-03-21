"""
Metadata storage for Stele.

Handles persistent storage of annotations and change history.
Follows the delegate pattern used by SessionStorage.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from stele.storage_schema import connect
from typing import Any


class MetadataStorage:
    """
    Persistent storage for annotations and change history.

    Manages annotation CRUD and change history recording.
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def store_annotation(
        self,
        target: str,
        target_type: str,
        content: str,
        tags: list[str] | None = None,
    ) -> int:
        """Store an annotation. Returns the annotation ID."""
        now = time.time()
        tags_json = json.dumps(tags) if tags else None

        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO annotations
                (target, target_type, content, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (target, target_type, content, tags_json, now, now),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    def get_annotations(
        self,
        target: str | None = None,
        target_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve annotations with optional filters."""
        query = "SELECT * FROM annotations WHERE 1=1"
        params: list[Any] = []

        if target is not None:
            query += " AND target = ?"
            params.append(target)

        if target_type is not None:
            query += " AND target_type = ?"
            params.append(target_type)

        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query + " ORDER BY created_at DESC", params)
            rows = [dict(row) for row in cursor.fetchall()]

        # Parse tags JSON and filter by tags if requested
        for row in rows:
            row["tags"] = json.loads(row["tags"]) if row["tags"] else []

        if tags:
            tag_set = set(tags)
            rows = [r for r in rows if tag_set & set(r["tags"])]

        return rows

    def delete_annotation(self, annotation_id: int) -> bool:
        """Delete an annotation by ID. Returns True if deleted."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM annotations WHERE id = ?", (annotation_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_annotation(
        self,
        annotation_id: int,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """Update an annotation's content and/or tags. Returns True if found."""
        now = time.time()
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [now]

        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if tags is not None:
            sets.append("tags = ?")
            params.append(json.dumps(tags))

        params.append(annotation_id)
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                f"UPDATE annotations SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def search_annotations(
        self, query: str, target_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Search annotations by content text (LIKE match)."""
        sql = "SELECT * FROM annotations WHERE content LIKE ?"
        params: list[Any] = [f"%{query}%"]
        if target_type:
            sql += " AND target_type = ?"
            params.append(target_type)
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = [
                dict(r)
                for r in conn.execute(
                    sql + " ORDER BY created_at DESC", params
                ).fetchall()
            ]
        for row in rows:
            row["tags"] = json.loads(row["tags"]) if row["tags"] else []
        return rows

    def record_change(
        self,
        summary: dict[str, Any],
        session_id: str | None = None,
        reason: str | None = None,
    ) -> int:
        """Record a change history entry. Returns the entry ID."""
        now = time.time()
        summary_json = json.dumps(summary, default=str)

        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO change_history
                (timestamp, session_id, summary_json, reason)
                VALUES (?, ?, ?, ?)
            """,
                (now, session_id, summary_json, reason),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    def get_change_history(
        self,
        limit: int = 20,
        document_path: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve change history entries.

        When document_path is given, the limit is applied after filtering
        so the caller reliably gets up to `limit` matching results.
        """
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if document_path:
                # Fetch all, filter in Python, then limit
                cursor = conn.execute(
                    "SELECT * FROM change_history ORDER BY timestamp DESC"
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM change_history ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                )
            rows = [dict(row) for row in cursor.fetchall()]

        for row in rows:
            row["summary"] = json.loads(row["summary_json"])
            del row["summary_json"]

        if document_path:
            rows = [
                r
                for r in rows
                if self._summary_mentions_document(r["summary"], document_path)
            ][:limit]

        return rows

    def prune_history(
        self,
        max_age_seconds: float | None = None,
        max_entries: int | None = None,
    ) -> int:
        """Prune change history by age and/or max entry count. Returns deleted count."""
        deleted = 0
        with connect(self.db_path) as conn:
            if max_age_seconds is not None:
                cutoff = time.time() - max_age_seconds
                cursor = conn.execute(
                    "DELETE FROM change_history WHERE timestamp < ?", (cutoff,)
                )
                deleted += cursor.rowcount
            if max_entries is not None:
                cursor = conn.execute(
                    "DELETE FROM change_history WHERE id NOT IN "
                    "(SELECT id FROM change_history ORDER BY timestamp DESC LIMIT ?)",
                    (max_entries,),
                )
                deleted += cursor.rowcount
            conn.commit()
        return deleted

    @staticmethod
    def _summary_mentions_document(summary: dict[str, Any], document_path: str) -> bool:
        """Check if a change summary mentions a specific document."""
        for key in ("unchanged", "removed"):
            if document_path in summary.get(key, []):
                return True
        for key in ("modified", "new"):
            for entry in summary.get(key, []):
                if isinstance(entry, dict) and entry.get("path") == document_path:
                    return True
                if isinstance(entry, str) and entry == document_path:
                    return True
        return False
