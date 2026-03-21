"""
Typing protocols for Stele engine delegation boundaries.

These protocols document the structural contracts that delegation modules
(indexing.py, search_engine.py, change_detection.py) expect from the
objects they receive. The delegation modules use direct ``TYPE_CHECKING``
imports of the concrete classes instead of these protocols, since no
import cycles exist between the delegation and implementation modules.

These protocols remain as API reference documentation and for potential
use in tests or third-party integrations that need to satisfy the same
interface without importing the concrete classes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class StorageProto(Protocol):
    """Protocol for StorageBackend -- methods used by delegation modules."""

    db_path: Path
    index_dir: Path

    def get_document(self, document_path: str) -> dict[str, Any] | None: ...
    def get_document_chunks(self, document_path: str) -> list[dict[str, Any]]: ...
    def get_all_documents(self) -> list[dict[str, Any]]: ...
    def store_document(
        self,
        document_path: str,
        content_hash: str,
        chunk_count: int,
        last_modified: float,
    ) -> None: ...
    def store_chunk(
        self,
        chunk_id: str,
        document_path: str,
        content_hash: str,
        semantic_signature: Any,
        start_pos: int,
        end_pos: int,
        token_count: int,
        content: str | None = None,
    ) -> None: ...
    def get_chunk(self, chunk_id: str) -> dict[str, Any] | None: ...
    def get_chunk_content(self, chunk_id: str) -> str | None: ...
    def search_chunks(
        self, document_path: str | None = None
    ) -> list[dict[str, Any]]: ...
    def delete_chunks(self, chunk_ids: list[str]) -> int: ...
    def remove_document(self, document_path: str) -> dict[str, Any]: ...
    def increment_doc_version(self, document_path: str) -> None: ...
    def check_and_increment_doc_version(
        self, document_path: str, expected_version: int
    ) -> dict[str, Any]: ...
    def store_annotation(
        self,
        target: str,
        target_type: str,
        content: str,
        tags: list[str] | None,
    ) -> int: ...
    def get_annotations(
        self,
        target: str | None = None,
        target_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...
    def get_storage_stats(self) -> dict[str, Any]: ...
    def search_symbol_names(self, names: list[str]) -> list[dict[str, Any]]: ...
    def store_semantic_summary(
        self, chunk_id: str, summary: str, agent_signature: Any
    ) -> bool: ...
    def store_agent_signature(self, chunk_id: str, agent_signature: Any) -> bool: ...
    def create_session(self, session_id: str, agent_id: str | None = None) -> None: ...
    def get_session(self, session_id: str) -> dict[str, Any] | None: ...
    def load_kv_state(
        self, session_id: str, chunk_id: str, turn: int
    ) -> Any | None: ...
    def record_change(
        self,
        summary: Any,
        session_id: str | None = None,
        reason: str | None = None,
    ) -> None: ...
    def get_change_history(
        self, limit: int = 20, document_path: str | None = None
    ) -> list[dict[str, Any]]: ...


class VectorIndexProto(Protocol):
    """Protocol for VectorIndex -- methods used by delegation modules."""

    def search(self, query: list[float], k: int = 10) -> list[tuple[str, float]]: ...
    def add_chunk(self, chunk_id: str, signature: list[float]) -> None: ...
    def remove_chunk(self, chunk_id: str) -> None: ...
    def get_stats(self) -> dict[str, Any]: ...


class SymbolManagerProto(Protocol):
    """Protocol for SymbolGraphManager -- methods used by delegation modules."""

    def extract_document_symbols(
        self, document_path: str, chunks: list[Any]
    ) -> None: ...
    def rebuild_edges(self, affected_chunk_ids: set[str] | None = None) -> None: ...
    def propagate_staleness(self, changed_chunk_ids: set[str]) -> None: ...
    def attach_edges(self, entry: dict[str, Any], chunk_id: str) -> None: ...


class CoordinationProto(Protocol):
    """Protocol for CoordinationBackend -- methods used by delegation modules."""

    def notify_change(
        self, document_path: str, change_type: str, agent_id: str
    ) -> None: ...
    def notify_changes_batch(
        self, changes: list[tuple[str, str]], agent_id: str
    ) -> int: ...
