"""
Delegation mixin for StorageBackend.

Pure forwarding methods to SessionStorage, MetadataStorage,
SymbolStorage, and DocumentLockStorage.  Extracted from storage.py
to keep that file under 500 LOC.
"""

from __future__ import annotations

from typing import Any


class StorageDelegatesMixin:
    """Thin forwarding layer for StorageBackend's sub-storage delegates.

    Expected instance attributes (set by StorageBackend.__init__):
        _session_storage: SessionStorage
        _metadata_storage: MetadataStorage
        _symbol_storage: SymbolStorage
        _document_lock_storage: DocumentLockStorage
    """

    _session_storage: Any
    _metadata_storage: Any
    _symbol_storage: Any
    _document_lock_storage: Any

    # -- Metadata (MetadataStorage) -------------------------------------------

    def store_annotation(
        self, target: str, target_type: str, content: str, tags: list[str] | None = None
    ) -> int:
        return self._metadata_storage.store_annotation(
            target, target_type, content, tags
        )

    def get_annotations(
        self,
        target: str | None = None,
        target_type: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._metadata_storage.get_annotations(target, target_type, tags)

    def delete_annotation(self, annotation_id: int) -> bool:
        return self._metadata_storage.delete_annotation(annotation_id)

    def update_annotation(
        self,
        annotation_id: int,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        return self._metadata_storage.update_annotation(annotation_id, content, tags)

    def search_annotations(
        self, query: str, target_type: str | None = None
    ) -> list[dict[str, Any]]:
        return self._metadata_storage.search_annotations(query, target_type)

    def record_change(
        self,
        summary: dict[str, Any],
        session_id: str | None = None,
        reason: str | None = None,
    ) -> int:
        return self._metadata_storage.record_change(summary, session_id, reason)

    def get_change_history(
        self, limit: int = 20, document_path: str | None = None
    ) -> list[dict[str, Any]]:
        return self._metadata_storage.get_change_history(limit, document_path)

    def prune_history(
        self, max_age_seconds: float | None = None, max_entries: int | None = None
    ) -> int:
        return self._metadata_storage.prune_history(max_age_seconds, max_entries)

    # -- Symbols (SymbolStorage) ----------------------------------------------

    def store_symbols(self, symbols: Any) -> None:
        self._symbol_storage.store_symbols(symbols)

    def store_edges(self, edges: Any) -> None:
        self._symbol_storage.store_edges(edges)

    def clear_document_symbols(self, document_path: str) -> None:
        self._symbol_storage.clear_document_symbols(document_path)

    def clear_chunk_edges(self, chunk_ids: list[str]) -> None:
        self._symbol_storage.clear_chunk_edges(chunk_ids)

    def clear_chunk_symbols(self, chunk_ids: list[str]) -> None:
        self._symbol_storage.clear_chunk_symbols(chunk_ids)

    def clear_all_symbols(self) -> None:
        self._symbol_storage.clear_all_symbols()

    def clear_all_edges(self) -> None:
        self._symbol_storage.clear_all_edges()

    def get_all_symbols(self) -> list[dict[str, Any]]:
        return self._symbol_storage.get_all_symbols()

    def find_definitions(self, name: str) -> list[dict[str, Any]]:
        return self._symbol_storage.find_definitions(name)

    def find_references_by_name(self, name: str) -> list[dict[str, Any]]:
        return self._symbol_storage.find_references_by_name(name)

    def get_edges_for_chunk(self, chunk_id: str) -> list[dict[str, Any]]:
        return self._symbol_storage.get_edges_for_chunk(chunk_id)

    def get_incoming_edges(self, chunk_id: str) -> list[dict[str, Any]]:
        return self._symbol_storage.get_incoming_edges(chunk_id)

    def get_outgoing_edges(self, chunk_id: str) -> list[dict[str, Any]]:
        return self._symbol_storage.get_outgoing_edges(chunk_id)

    def search_symbol_names(self, tokens: list[str]) -> list[dict[str, Any]]:
        return self._symbol_storage.search_symbol_names(tokens)

    def get_symbols_for_chunks(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        return self._symbol_storage.get_symbols_for_chunks(chunk_ids)

    def get_symbol_stats(self) -> dict[str, Any]:
        return self._symbol_storage.get_symbol_stats()

    def store_dynamic_symbols(
        self, symbols: list[dict[str, Any]], agent_id: str
    ) -> dict[str, Any]:
        return self._symbol_storage.store_dynamic_symbols(symbols, agent_id)

    def remove_dynamic_symbols(self, agent_id: str) -> dict[str, Any]:
        return self._symbol_storage.remove_dynamic_symbols(agent_id)

    def get_dynamic_symbols(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        return self._symbol_storage.get_dynamic_symbols(agent_id)

    # -- Sessions (SessionStorage) --------------------------------------------

    def create_session(self, session_id: str, agent_id: str | None = None) -> None:
        self._session_storage.create_session(session_id, agent_id=agent_id)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self._session_storage.get_session(session_id)

    def list_sessions(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        return self._session_storage.list_sessions(agent_id=agent_id)

    def update_session(
        self,
        session_id: str,
        turn_count: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        self._session_storage.update_session(session_id, turn_count, total_tokens)

    def store_kv_state(
        self,
        session_id: str,
        chunk_id: str,
        turn_number: int,
        kv_data: Any,
        relevance_score: float = 1.0,
    ) -> str:
        return self._session_storage.store_kv_state(
            session_id, chunk_id, turn_number, kv_data, relevance_score
        )

    def load_kv_state(
        self, session_id: str, chunk_id: str, turn_number: int
    ) -> Any | None:
        return self._session_storage.load_kv_state(session_id, chunk_id, turn_number)

    def get_session_chunks(
        self, session_id: str, turn_number: int | None = None
    ) -> list[dict[str, Any]]:
        return self._session_storage.get_session_chunks(session_id, turn_number)

    def rollback_session(self, session_id: str, target_turn: int) -> int:
        return self._session_storage.rollback_session(session_id, target_turn)

    def prune_chunks(self, session_id: str, max_tokens: int) -> int:
        return self._session_storage.prune_chunks(session_id, max_tokens)

    # -- Document locks (DocumentLockStorage) ---------------------------------

    def acquire_document_lock(
        self, document_path: str, agent_id: str, ttl: float = 300.0, force: bool = False
    ) -> dict[str, Any]:
        return self._document_lock_storage.acquire_lock(
            document_path, agent_id, ttl, force
        )

    def refresh_document_lock(
        self, document_path: str, agent_id: str, ttl: float | None = None
    ) -> dict[str, Any]:
        return self._document_lock_storage.refresh_lock(document_path, agent_id, ttl)

    def release_document_lock(
        self, document_path: str, agent_id: str
    ) -> dict[str, Any]:
        return self._document_lock_storage.release_lock(document_path, agent_id)

    def get_document_lock_status(self, document_path: str) -> dict[str, Any]:
        return self._document_lock_storage.get_lock_status(document_path)

    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        return self._document_lock_storage.release_agent_locks(agent_id)

    def check_and_increment_doc_version(
        self, document_path: str, expected_version: int
    ) -> dict[str, Any]:
        return self._document_lock_storage.check_and_increment_version(
            document_path, expected_version
        )

    def increment_doc_version(self, document_path: str) -> int:
        return self._document_lock_storage.increment_version(document_path)

    def get_document_version(self, document_path: str) -> int | None:
        return self._document_lock_storage.get_version(document_path)

    def record_conflict(
        self,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        **kwargs: Any,
    ) -> int | None:
        return self._document_lock_storage.record_conflict(
            document_path, agent_a, agent_b, conflict_type, **kwargs
        )

    def get_conflicts(
        self,
        document_path: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self._document_lock_storage.get_conflicts(document_path, agent_id, limit)

    def prune_conflicts(
        self, max_age_seconds: float | None = None, max_entries: int | None = None
    ) -> int:
        return self._document_lock_storage.prune_conflicts(max_age_seconds, max_entries)

    def reap_expired_locks(self) -> dict[str, Any]:
        return self._document_lock_storage.reap_expired_locks()

    def get_lock_stats(self) -> dict[str, Any]:
        return self._document_lock_storage.get_lock_stats()
