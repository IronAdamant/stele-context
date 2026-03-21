"""
Session storage for Stele.

Handles persistent storage of session state, KV-cache blobs, and
session lifecycle operations (create, update, rollback, prune).

Uses SQLite for metadata and filesystem for KV-cache blobs.
"""

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from pathlib import Path
from stele.storage_schema import connect
from typing import Any

try:
    import msgspec

    HAS_MSGSPEC = True
except ImportError:
    HAS_MSGSPEC = False
    msgspec = None  # type: ignore


class SessionStorage:
    """
    Persistent storage for sessions and KV-cache state.

    Manages session lifecycle, KV-cache blob serialization,
    and session chunk associations.
    """

    def __init__(self, db_path: Path, kv_dir: Path):
        """
        Initialize session storage.

        Args:
            db_path: Path to SQLite database
            kv_dir: Directory for KV-cache blobs
        """
        self.db_path = db_path
        self.kv_dir = kv_dir
        self.kv_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, session_id: str, agent_id: str | None = None) -> None:
        """Create a new session (no-op if exists).

        If agent_id is provided and the session already exists, the
        agent_id is updated.
        """
        now = time.time()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions
                (session_id, created_at, last_updated, turn_count, total_tokens, agent_id)
                VALUES (?, ?, ?, 0, 0, ?)
            """,
                (session_id, now, now, agent_id),
            )
            if agent_id is not None:
                conn.execute(
                    "UPDATE sessions SET agent_id = ? WHERE session_id = ?",
                    (agent_id, session_id),
                )
            conn.commit()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get session metadata."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """List sessions, optionally filtered by agent_id."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if agent_id is not None:
                cursor = conn.execute(
                    "SELECT * FROM sessions WHERE agent_id = ? "
                    "ORDER BY last_updated DESC",
                    (agent_id,),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM sessions ORDER BY last_updated DESC"
                )
            return [dict(row) for row in cursor.fetchall()]

    def update_session(
        self,
        session_id: str,
        turn_count: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        """Update session metadata."""
        now = time.time()
        updates = ["last_updated = ?"]
        params: list[Any] = [now]

        if turn_count is not None:
            updates.append("turn_count = ?")
            params.append(turn_count)

        if total_tokens is not None:
            updates.append("total_tokens = ?")
            params.append(total_tokens)

        params.append(session_id)

        with connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE session_id = ?", params
            )
            conn.commit()

    def store_kv_state(
        self,
        session_id: str,
        chunk_id: str,
        turn_number: int,
        kv_data: Any,
        relevance_score: float = 1.0,
    ) -> str:
        """
        Store KV-cache state for a chunk in a session.

        Serializes with JSON (compressed with zlib). Falls back to
        msgspec if available for complex types.

        Returns:
            Path to stored KV file
        """
        session_kv_dir = self.kv_dir / session_id
        session_kv_dir.mkdir(exist_ok=True)

        kv_filename = f"{chunk_id}_turn{turn_number}.kv"
        kv_path = session_kv_dir / kv_filename

        # Serialize with JSON + zlib compression
        try:
            encoded = json.dumps(kv_data).encode("utf-8")
        except (TypeError, ValueError):
            if HAS_MSGSPEC:
                try:
                    encoded = msgspec.json.encode(kv_data)
                except (TypeError, msgspec.EncodeError):
                    encoded = json.dumps(str(kv_data)).encode("utf-8")
            else:
                encoded = json.dumps(str(kv_data)).encode("utf-8")
        kv_path.write_bytes(zlib.compress(encoded))

        with connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO session_chunks
                (session_id, chunk_id, turn_number, kv_path, relevance_score)
                VALUES (?, ?, ?, ?, ?)
            """,
                (session_id, chunk_id, turn_number, str(kv_path), relevance_score),
            )
            conn.commit()

        return str(kv_path)

    def load_kv_state(
        self,
        session_id: str,
        chunk_id: str,
        turn_number: int,
    ) -> Any | None:
        """Load KV-cache state for a chunk in a session."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT kv_path FROM session_chunks
                WHERE session_id = ? AND chunk_id = ? AND turn_number = ?
            """,
                (session_id, chunk_id, turn_number),
            )
            row = cursor.fetchone()

        if row is None or row[0] is None:
            return None

        kv_path = Path(row[0])
        if not kv_path.exists():
            return None

        data = kv_path.read_bytes()

        # Try JSON + zlib first
        try:
            decompressed = zlib.decompress(data)
            return json.loads(decompressed)
        except (zlib.error, json.JSONDecodeError):
            pass

        # Try msgspec JSON (uncompressed, for legacy files)
        if HAS_MSGSPEC:
            try:
                return msgspec.json.decode(data)
            except (msgspec.DecodeError, UnicodeDecodeError):
                pass

        return None

    def get_session_chunks(
        self,
        session_id: str,
        turn_number: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get all chunks associated with a session."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            if turn_number is not None:
                cursor = conn.execute(
                    """
                    SELECT sc.*, c.content_hash, c.semantic_signature, c.token_count
                    FROM session_chunks sc
                    JOIN chunks c ON sc.chunk_id = c.chunk_id
                    WHERE sc.session_id = ? AND sc.turn_number = ?
                    ORDER BY sc.relevance_score DESC
                """,
                    (session_id, turn_number),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT sc.*, c.content_hash, c.semantic_signature, c.token_count
                    FROM session_chunks sc
                    JOIN chunks c ON sc.chunk_id = c.chunk_id
                    WHERE sc.session_id = ?
                    ORDER BY sc.turn_number DESC, sc.relevance_score DESC
                """,
                    (session_id,),
                )

            return [dict(row) for row in cursor.fetchall()]

    def rollback_session(self, session_id: str, target_turn: int) -> int:
        """Rollback session to a previous turn. Returns chunks removed."""
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT turn_count FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = cursor.fetchone()
            if row is None:
                return 0

            current_turn = row[0]
            if target_turn >= current_turn:
                return 0

            cursor = conn.execute(
                """
                DELETE FROM session_chunks
                WHERE session_id = ? AND turn_number > ?
            """,
                (session_id, target_turn),
            )
            removed_count = cursor.rowcount

            conn.execute(
                """
                UPDATE sessions SET turn_count = ?, last_updated = ?
                WHERE session_id = ?
            """,
                (target_turn, time.time(), session_id),
            )

            conn.commit()

        self._cleanup_orphaned_kv_files(session_id)
        return removed_count

    def prune_chunks(self, session_id: str, max_tokens: int) -> int:
        """Prune low-relevance chunks to stay under token limit."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.execute(
                """
                SELECT sc.chunk_id, sc.turn_number, c.token_count, sc.relevance_score
                FROM session_chunks sc
                JOIN chunks c ON sc.chunk_id = c.chunk_id
                WHERE sc.session_id = ?
                ORDER BY sc.relevance_score ASC
            """,
                (session_id,),
            )

            chunks = [dict(row) for row in cursor.fetchall()]

            total_tokens = sum(c["token_count"] for c in chunks)
            if total_tokens <= max_tokens:
                return 0

            pruned_count = 0
            for chunk in chunks:
                if total_tokens <= max_tokens:
                    break

                conn.execute(
                    """
                    DELETE FROM session_chunks
                    WHERE session_id = ? AND chunk_id = ? AND turn_number = ?
                """,
                    (session_id, chunk["chunk_id"], chunk["turn_number"]),
                )

                total_tokens -= chunk["token_count"]
                pruned_count += 1

            # Update total_tokens in sessions table
            if pruned_count > 0:
                conn.execute(
                    """
                    UPDATE sessions SET total_tokens = ?, last_updated = ?
                    WHERE session_id = ?
                """,
                    (total_tokens, time.time(), session_id),
                )

            conn.commit()

        self._cleanup_orphaned_kv_files(session_id)
        return pruned_count

    def _cleanup_orphaned_kv_files(self, session_id: str) -> None:
        """Remove KV files that are no longer referenced in database."""
        session_kv_dir = self.kv_dir / session_id
        if not session_kv_dir.exists():
            return

        with connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT kv_path FROM session_chunks WHERE session_id = ?", (session_id,)
            )
            referenced_paths = {row[0] for row in cursor.fetchall() if row[0]}

        for kv_file in session_kv_dir.glob("*.kv"):
            if str(kv_file) not in referenced_paths:
                kv_file.unlink()
