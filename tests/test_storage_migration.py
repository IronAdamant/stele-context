"""Tests for storage migration (content column, JSON serialization)."""

import json
import sqlite3
import zlib

from chunkforge.storage import StorageBackend


class TestStorageMigration:
    """Tests for database migration."""

    def test_content_column_exists(self, tmp_path):
        """Test that content column is added to chunks table."""
        storage = StorageBackend(base_dir=str(tmp_path / "storage"))

        with sqlite3.connect(storage.db_path) as conn:
            cursor = conn.execute("PRAGMA table_info(chunks)")
            columns = {row[1] for row in cursor.fetchall()}

        assert "content" in columns

    def test_store_chunk_with_content(self, tmp_path):
        """Test storing chunk with text content."""
        storage = StorageBackend(base_dir=str(tmp_path / "storage"))

        sig = [0.0] * 128
        storage.store_chunk(
            chunk_id="test-1",
            document_path="test.txt",
            content_hash="abc123",
            semantic_signature=sig,
            start_pos=0,
            end_pos=100,
            token_count=25,
            content="Hello world content",
        )

        content = storage.get_chunk_content("test-1")
        assert content == "Hello world content"

    def test_store_chunk_without_content(self, tmp_path):
        """Test storing chunk without content (backward compat)."""
        storage = StorageBackend(base_dir=str(tmp_path / "storage"))

        sig = [0.0] * 128
        storage.store_chunk(
            chunk_id="test-2",
            document_path="test.txt",
            content_hash="def456",
            semantic_signature=sig,
            start_pos=0,
            end_pos=50,
            token_count=12,
        )

        content = storage.get_chunk_content("test-2")
        assert content is None

    def test_search_chunks_with_content(self, tmp_path):
        """Test search_chunks returns content."""
        storage = StorageBackend(base_dir=str(tmp_path / "storage"))

        sig = [0.0] * 128
        storage.store_chunk(
            chunk_id="test-3",
            document_path="test.txt",
            content_hash="ghi789",
            semantic_signature=sig,
            start_pos=0,
            end_pos=20,
            token_count=5,
            content="Search me",
        )

        results = storage.search_chunks(document_path="test.txt")
        assert len(results) == 1
        assert results[0]["content"] == "Search me"

    def test_migration_preserves_existing_data(self, tmp_path):
        """Test that migration doesn't destroy existing chunk data."""
        db_path = tmp_path / "storage" / "chunkforge.db"
        db_path.parent.mkdir(parents=True)

        # Create old schema without content column
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    semantic_signature BLOB NOT NULL,
                    start_pos INTEGER NOT NULL,
                    end_pos INTEGER NOT NULL,
                    token_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    version INTEGER DEFAULT 1
                )
            """)
            # Create other required tables
            conn.execute("""
                CREATE TABLE chunk_history (
                    chunk_id TEXT, version INTEGER,
                    content_hash TEXT, semantic_signature BLOB,
                    created_at REAL, PRIMARY KEY (chunk_id, version))
            """)
            conn.execute("""
                CREATE TABLE documents (
                    document_path TEXT PRIMARY KEY,
                    content_hash TEXT, chunk_count INTEGER,
                    indexed_at REAL, last_modified REAL)
            """)
            conn.execute("""
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at REAL, last_updated REAL,
                    turn_count INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0)
            """)
            conn.execute("""
                CREATE TABLE session_chunks (
                    session_id TEXT, chunk_id TEXT, turn_number INTEGER,
                    kv_path TEXT, relevance_score REAL DEFAULT 1.0,
                    PRIMARY KEY (session_id, chunk_id, turn_number))
            """)

            import struct

            sig_bytes = struct.pack("128f", *([0.0] * 128))
            conn.execute(
                """
                INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
            """,
                ("old-chunk", "test.txt", "hash123", sig_bytes, 0, 50, 12, 0.0, 0.0),
            )
            conn.commit()

        # Now create StorageBackend — should migrate
        storage = StorageBackend(base_dir=str(tmp_path / "storage"))

        # Old chunk should still be accessible
        chunk = storage.get_chunk("old-chunk")
        assert chunk is not None
        assert chunk["chunk_id"] == "old-chunk"
        assert chunk["content"] is None  # No content in old schema


class TestSessionStorageJSON:
    """Tests for JSON-based session storage (replacing pickle)."""

    def test_store_and_load_kv_json(self, tmp_path):
        """Test storing and loading KV state with JSON."""
        storage = StorageBackend(base_dir=str(tmp_path / "storage"))

        # Store KV data
        kv_data = {"key": "value", "numbers": [1, 2, 3]}
        storage.create_session("json-test")

        sig = [0.0] * 128
        storage.store_chunk(
            chunk_id="chunk-1",
            document_path="test.txt",
            content_hash="abc",
            semantic_signature=sig,
            start_pos=0,
            end_pos=10,
            token_count=2,
        )

        storage.store_kv_state("json-test", "chunk-1", 0, kv_data)

        # Load it back
        loaded = storage.load_kv_state("json-test", "chunk-1", 0)
        assert loaded == kv_data

    def test_kv_state_compressed(self, tmp_path):
        """Test that KV state is compressed with zlib."""
        storage = StorageBackend(base_dir=str(tmp_path / "storage"))
        storage.create_session("compress-test")

        sig = [0.0] * 128
        storage.store_chunk(
            chunk_id="chunk-2",
            document_path="test.txt",
            content_hash="def",
            semantic_signature=sig,
            start_pos=0,
            end_pos=10,
            token_count=2,
        )

        kv_data = {"large": "data" * 100}
        kv_path = storage.store_kv_state("compress-test", "chunk-2", 0, kv_data)

        from pathlib import Path

        raw = Path(kv_path).read_bytes()

        # Should be zlib compressed
        decompressed = zlib.decompress(raw)
        assert json.loads(decompressed) == kv_data

    def test_load_nonexistent_kv(self, tmp_path):
        """Test loading non-existent KV state returns None."""
        storage = StorageBackend(base_dir=str(tmp_path / "storage"))
        result = storage.load_kv_state("no-session", "no-chunk", 0)
        assert result is None
