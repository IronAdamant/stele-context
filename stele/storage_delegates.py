"""
Delegation mixin for StorageBackend.

Pure forwarding methods to SessionStorage, MetadataStorage,
SymbolStorage, and DocumentLockStorage.  Extracted from storage.py
to keep that file under 500 LOC.
"""

from typing import Any, Dict, List, Optional


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

    # -- Metadata methods -- delegated to MetadataStorage ---------------------

    def store_annotation(
        self,
        target: str,
        target_type: str,
        content: str,
        tags: Optional[List[str]] = None,
    ) -> int:
        """Store an annotation on a document or chunk."""
        return self._metadata_storage.store_annotation(
            target, target_type, content, tags
        )

    def get_annotations(
        self,
        target: Optional[str] = None,
        target_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve annotations with optional filters."""
        return self._metadata_storage.get_annotations(target, target_type, tags)

    def delete_annotation(self, annotation_id: int) -> bool:
        """Delete an annotation by ID."""
        return self._metadata_storage.delete_annotation(annotation_id)

    def record_change(
        self,
        summary: Dict[str, Any],
        session_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> int:
        """Record a change history entry."""
        return self._metadata_storage.record_change(summary, session_id, reason)

    def get_change_history(
        self,
        limit: int = 20,
        document_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve change history entries."""
        return self._metadata_storage.get_change_history(limit, document_path)

    def update_annotation(
        self,
        annotation_id: int,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> bool:
        """Update an annotation's content and/or tags."""
        return self._metadata_storage.update_annotation(annotation_id, content, tags)

    def search_annotations(
        self, query: str, target_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Search annotations by content text."""
        return self._metadata_storage.search_annotations(query, target_type)

    def prune_history(
        self,
        max_age_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
    ) -> int:
        """Prune change history entries."""
        return self._metadata_storage.prune_history(max_age_seconds, max_entries)

    # -- Symbol methods -- delegated to SymbolStorage -------------------------

    def store_symbols(self, symbols: Any) -> None:
        """Store a batch of Symbol objects."""
        self._symbol_storage.store_symbols(symbols)

    def store_edges(self, edges: Any) -> None:
        """Store a batch of symbol edges."""
        self._symbol_storage.store_edges(edges)

    def clear_document_symbols(self, document_path: str) -> None:
        """Remove all symbols for a document."""
        self._symbol_storage.clear_document_symbols(document_path)

    def clear_chunk_edges(self, chunk_ids: List[str]) -> None:
        """Remove all edges involving the given chunk IDs."""
        self._symbol_storage.clear_chunk_edges(chunk_ids)

    def clear_chunk_symbols(self, chunk_ids: List[str]) -> None:
        """Remove all symbols for the given chunk IDs."""
        self._symbol_storage.clear_chunk_symbols(chunk_ids)

    def clear_all_symbols(self) -> None:
        """Remove all symbols."""
        self._symbol_storage.clear_all_symbols()

    def clear_all_edges(self) -> None:
        """Remove all edges."""
        self._symbol_storage.clear_all_edges()

    def get_all_symbols(self) -> List[Dict[str, Any]]:
        """Get all symbols."""
        return self._symbol_storage.get_all_symbols()

    def find_definitions(self, name: str) -> List[Dict[str, Any]]:
        """Find all definitions for a symbol name."""
        return self._symbol_storage.find_definitions(name)

    def find_references_by_name(self, name: str) -> List[Dict[str, Any]]:
        """Find all references to a symbol name."""
        return self._symbol_storage.find_references_by_name(name)

    def get_edges_for_chunk(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get all edges involving a chunk."""
        return self._symbol_storage.get_edges_for_chunk(chunk_id)

    def get_incoming_edges(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get edges where other chunks reference this chunk."""
        return self._symbol_storage.get_incoming_edges(chunk_id)

    def get_outgoing_edges(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get edges where this chunk references other chunks."""
        return self._symbol_storage.get_outgoing_edges(chunk_id)

    def search_symbol_names(self, tokens: List[str]) -> List[Dict[str, Any]]:
        """Find definition symbols whose names match query tokens."""
        return self._symbol_storage.search_symbol_names(tokens)

    def get_symbol_stats(self) -> Dict[str, Any]:
        """Get symbol and edge statistics."""
        return self._symbol_storage.get_symbol_stats()

    # -- Session methods -- delegated to SessionStorage -----------------------

    def create_session(self, session_id: str, agent_id: Optional[str] = None) -> None:
        """Create a new KV-cache session."""
        self._session_storage.create_session(session_id, agent_id=agent_id)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session information."""
        return self._session_storage.get_session(session_id)

    def list_sessions(self, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered by agent_id."""
        return self._session_storage.list_sessions(agent_id=agent_id)

    def update_session(
        self,
        session_id: str,
        turn_count: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        """Update session metadata."""
        self._session_storage.update_session(session_id, turn_count, total_tokens)

    def store_kv_state(
        self,
        session_id: str,
        chunk_id: str,
        turn_number: int,
        kv_data: Any,
        relevance_score: float = 1.0,
    ) -> str:
        """Store KV-cache state for a chunk in a session."""
        return self._session_storage.store_kv_state(
            session_id, chunk_id, turn_number, kv_data, relevance_score
        )

    def load_kv_state(
        self,
        session_id: str,
        chunk_id: str,
        turn_number: int,
    ) -> Optional[Any]:
        """Load KV-cache state for a chunk in a session."""
        return self._session_storage.load_kv_state(session_id, chunk_id, turn_number)

    def get_session_chunks(
        self,
        session_id: str,
        turn_number: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get all chunks associated with a session."""
        return self._session_storage.get_session_chunks(session_id, turn_number)

    def rollback_session(self, session_id: str, target_turn: int) -> int:
        """Rollback session to a previous turn."""
        return self._session_storage.rollback_session(session_id, target_turn)

    def prune_chunks(self, session_id: str, max_tokens: int) -> int:
        """Prune low-relevance chunks to stay under token limit."""
        return self._session_storage.prune_chunks(session_id, max_tokens)

    # -- Document lock methods -- delegated to DocumentLockStorage ------------

    def acquire_document_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: float = 300.0,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Acquire exclusive ownership of a document."""
        return self._document_lock_storage.acquire_lock(
            document_path, agent_id, ttl, force
        )

    def refresh_document_lock(
        self,
        document_path: str,
        agent_id: str,
        ttl: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Refresh lock TTL without releasing."""
        return self._document_lock_storage.refresh_lock(document_path, agent_id, ttl)

    def release_document_lock(
        self, document_path: str, agent_id: str
    ) -> Dict[str, Any]:
        """Release ownership of a document."""
        return self._document_lock_storage.release_lock(document_path, agent_id)

    def get_document_lock_status(self, document_path: str) -> Dict[str, Any]:
        """Check lock status of a document."""
        return self._document_lock_storage.get_lock_status(document_path)

    def release_agent_locks(self, agent_id: str) -> Dict[str, Any]:
        """Release all locks held by an agent."""
        return self._document_lock_storage.release_agent_locks(agent_id)

    def check_and_increment_doc_version(
        self, document_path: str, expected_version: int
    ) -> Dict[str, Any]:
        """Atomic compare-and-swap on doc_version."""
        return self._document_lock_storage.check_and_increment_version(
            document_path, expected_version
        )

    def increment_doc_version(self, document_path: str) -> int:
        """Increment document version after write."""
        return self._document_lock_storage.increment_version(document_path)

    def get_document_version(self, document_path: str) -> Optional[int]:
        """Get current document version."""
        return self._document_lock_storage.get_version(document_path)

    def record_conflict(
        self,
        document_path: str,
        agent_a: str,
        agent_b: str,
        conflict_type: str,
        **kwargs: Any,
    ) -> Optional[int]:
        """Log a conflict event."""
        return self._document_lock_storage.record_conflict(
            document_path, agent_a, agent_b, conflict_type, **kwargs
        )

    def get_conflicts(
        self,
        document_path: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Retrieve conflict history."""
        return self._document_lock_storage.get_conflicts(document_path, agent_id, limit)

    def prune_conflicts(
        self,
        max_age_seconds: Optional[float] = None,
        max_entries: Optional[int] = None,
    ) -> int:
        """Prune old conflict entries."""
        return self._document_lock_storage.prune_conflicts(max_age_seconds, max_entries)

    def reap_expired_locks(self) -> Dict[str, Any]:
        """Clear all expired document locks."""
        return self._document_lock_storage.reap_expired_locks()

    def get_lock_stats(self) -> Dict[str, Any]:
        """Get aggregate lock and conflict statistics."""
        return self._document_lock_storage.get_lock_stats()
