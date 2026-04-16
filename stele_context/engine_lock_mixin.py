"""
Engine mixin — document locking and cross-agent coordination methods.

Contains the document-ownership and agent-coordination methods of the `Stele`
facade: `document_lock` (unified dispatcher), individual lock ops
(`acquire_document_lock`, `refresh_document_lock`, `release_document_lock`,
`get_document_lock_status`, `release_agent_locks`, `reap_expired_locks`,
`get_conflicts`), and agent registry ops (`register_agent`, `deregister_agent`,
`heartbeat`, `list_agents`, `get_notifications`).

**Inclusion criterion:** a method belongs here if it manipulates document
ownership/locking state or coordinates agents across worktrees. Other domains
live in their own mixins.

Relies on `self._lock`, `self._coordination`, `self._project_root`,
`self.storage`, `self._do_acquire_lock`, `self._do_release_lock`,
`self._do_get_lock_status`, and `self._normalize_path` being provided by
`Stele.__init__`.
"""

from __future__ import annotations

from typing import Any


class _LockMixin:
    """Document locking + agent coordination methods for `Stele`."""

    # Attributes/methods provided by Stele.__init__ or other mixins.
    # Declared for mypy; kept as Any to avoid import cycles.
    _lock: Any
    storage: Any
    _coordination: Any
    _project_root: Any
    _normalize_path: Any
    _do_acquire_lock: Any
    _do_release_lock: Any
    _do_get_lock_status: Any

    # -- Unified document locking ---------------------------------------------

    def document_lock(
        self,
        action: str,
        document_path: str | None = None,
        agent_id: str | None = None,
        ttl: float | None = None,
        force: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Unified document lock lifecycle tool.

        Actions:
          - acquire: acquire exclusive lock on document_path
          - release: release lock on document_path
          - refresh: refresh TTL on document_path
          - status: get lock status for document_path
          - release_all: release all locks held by agent_id
          - reap: clear expired locks globally
          - conflicts: get conflict audit log
        """
        action = action.lower()
        if action == "acquire":
            if not document_path or not agent_id:
                return {"error": "acquire requires document_path and agent_id"}
            return self.acquire_document_lock(
                document_path, agent_id, ttl or 300.0, force
            )
        if action == "release":
            if not document_path or not agent_id:
                return {"error": "release requires document_path and agent_id"}
            return self.release_document_lock(document_path, agent_id)
        if action == "refresh":
            if not document_path or not agent_id:
                return {"error": "refresh requires document_path and agent_id"}
            return self.refresh_document_lock(document_path, agent_id, ttl)
        if action == "status":
            if not document_path:
                return {"error": "status requires document_path"}
            return self.get_document_lock_status(document_path)
        if action == "release_all":
            if not agent_id:
                return {"error": "release_all requires agent_id"}
            return self.release_agent_locks(agent_id)
        if action == "reap":
            return self.reap_expired_locks()
        if action == "conflicts":
            return {
                "conflicts": self.get_conflicts(
                    document_path=document_path, agent_id=agent_id, limit=limit
                )
            }
        return {"error": f"Unknown action: {action}"}

    # -- Document ownership & conflict prevention -----------------------------

    def acquire_document_lock(
        self, document_path: str, agent_id: str, ttl: float = 300.0, force: bool = False
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return self._do_acquire_lock(
                self._normalize_path(document_path), agent_id, ttl, force
            )

    def refresh_document_lock(
        self, document_path: str, agent_id: str, ttl: float | None = None
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            dp = self._normalize_path(document_path)
            if self._coordination:
                return self._coordination.refresh_lock(dp, agent_id, ttl)
            return self.storage.refresh_document_lock(dp, agent_id, ttl)

    def release_document_lock(
        self, document_path: str, agent_id: str
    ) -> dict[str, Any]:
        with self._lock.write_lock():
            return self._do_release_lock(self._normalize_path(document_path), agent_id)

    def get_document_lock_status(self, document_path: str) -> dict[str, Any]:
        with self._lock.read_lock():
            return self._do_get_lock_status(self._normalize_path(document_path))

    def release_agent_locks(self, agent_id: str) -> dict[str, Any]:
        with self._lock.write_lock():
            if self._coordination:
                return self._coordination.release_agent_locks(agent_id)
            return self.storage.release_agent_locks(agent_id)

    def reap_expired_locks(self) -> dict[str, Any]:
        with self._lock.write_lock():
            if self._coordination:
                return self._coordination.reap_expired_locks()
            return self.storage.reap_expired_locks()

    def get_conflicts(
        self,
        document_path: str | None = None,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            if self._coordination:
                return self._coordination.get_conflicts(document_path, agent_id, limit)
            return self.storage.get_conflicts(document_path, agent_id, limit)

    # -- Agent coordination ---------------------------------------------------

    def register_agent(self, agent_id: str) -> dict[str, Any]:
        if not self._coordination:
            return {"registered": False, "reason": "no_coordination"}
        root = str(self._project_root) if self._project_root else ""
        return self._coordination.register_agent(agent_id, root)

    def deregister_agent(self, agent_id: str) -> dict[str, Any]:
        if not self._coordination:
            return {"deregistered": False, "reason": "no_coordination"}
        return self._coordination.deregister_agent(agent_id)

    def heartbeat(self, agent_id: str) -> dict[str, Any]:
        if not self._coordination:
            return {"updated": False}
        return self._coordination.heartbeat(agent_id)

    def list_agents(self, active_only: bool = True) -> list[dict[str, Any]]:
        if not self._coordination:
            return []
        return self._coordination.list_agents(active_only=active_only)

    def get_notifications(
        self,
        since: float | None = None,
        exclude_self: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if not self._coordination:
            return {"notifications": [], "count": 0, "latest_timestamp": 0.0}
        return self._coordination.get_notifications(
            since=since, exclude_agent=exclude_self, limit=limit
        )
