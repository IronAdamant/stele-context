"""
Engine mixin — search, context retrieval, and session history methods.

Contains the retrieval-facing methods of the `Stele` facade: `search`,
`search_text`, `agent_grep`, `get_context`, `get_search_history`,
`get_session_read_files`, plus the working-tree helpers
(`_index_working_tree`, `_git_working_tree_is_dirty`,
`_recent_files_path_prefix`).

**Inclusion criterion:** a method belongs here if it reads chunks/content
back out for an agent (hybrid/keyword/text/symbol-aware search, cached
file retrieval, session-scoped history lookups). Mutating methods,
symbol-graph queries, and locking live in their own mixins.

Relies on `self._lock`, `self.storage`, `self.vector_index`,
`self.bm25_index`, `self.search_alpha`, `self.symbol_manager`,
`self.detect_modality`, `self._normalize_path`, `self._resolve_path`,
`self._ensure_bm25`, `self._project_root`, `self.index_documents`
being provided by `Stele.__init__` or other mixins.
"""

from __future__ import annotations

from typing import Any

from stele_context import search_engine as _se
from stele_context.engine_utils import read_and_hash as _read_and_hash


class _SearchMixin:
    """Search + context retrieval methods for `Stele`."""

    # Attributes/methods provided by Stele.__init__ or other mixins.
    # Declared for mypy; kept as Any to avoid import cycles.
    _lock: Any
    storage: Any
    vector_index: Any
    bm25_index: Any
    search_alpha: Any
    symbol_manager: Any
    _project_root: Any
    _normalize_path: Any
    _resolve_path: Any
    detect_modality: Any
    _ensure_bm25: Any
    index_documents: Any  # provided by _IndexMixin

    def _index_working_tree(self, agent_id: str | None = None) -> list[str]:
        """Index modified and untracked files from the git working tree.

        Returns the list of newly indexed file paths.
        """
        if self._project_root is None:
            return []
        try:
            import subprocess

            result = subprocess.run(
                ["git", "status", "--porcelain", "-u"],
                cwd=str(self._project_root),
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                return []
            changed: list[str] = []
            for line in result.stdout.splitlines():
                if len(line) < 4:
                    continue
                # Two-letter status followed by space and path
                path = line[3:]
                # Handle renamed files "R   old -> new"
                if " -> " in path:
                    path = path.split(" -> ")[-1]
                abs_path = self._project_root / path
                if abs_path.is_file():
                    changed.append(str(abs_path))
            if changed:
                indexed = self.index_documents(changed, agent_id=agent_id)
                return [d["path"] for d in indexed.get("indexed", [])]
        except Exception:
            pass
        return []

    def _git_working_tree_is_dirty(self) -> bool:
        """Return True if the git working tree has uncommitted changes."""
        if self._project_root is None:
            return False
        try:
            import subprocess

            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self._project_root),
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    def _recent_files_path_prefix(self) -> str | None:
        """Return a path prefix covering the most recently modified 25% of files."""
        docs = self.storage.get_recent_documents(limit=0)
        if not docs:
            return None
        cutoff = max(1, len(docs) // 4)
        recent = docs[:cutoff]
        # Find longest common path prefix among recent files
        paths = [d["document_path"] for d in recent]
        if not paths:
            return None
        prefix = paths[0]
        for p in paths[1:]:
            while not p.startswith(prefix):
                prefix = prefix[: prefix.rfind("/")]
                if not prefix:
                    break
        return prefix if prefix else None

    def search_text(
        self,
        pattern: str,
        regex: bool = False,
        document_path: str | None = None,
        limit: int = 50,
        session_id: str | None = None,
        working_tree: bool = False,
    ) -> dict[str, Any]:
        """Search chunk content by exact substring or regex pattern.

        Perfect recall for literal patterns. Complements semantic search
        for cases where exact text matching is needed (e.g., finding all
        usages of a specific identifier before renaming).
        When session_id is provided, records search history and auto-indexes
        files with matches. When working_tree is True, auto-indexes modified
        and untracked files before searching.
        """
        if working_tree:
            self._index_working_tree(agent_id=session_id)

        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            matches = self.storage.search_text(
                pattern, regex=regex, document_path=document_path, limit=limit
            )
            doc_paths = sorted({m["document_path"] for m in matches})
            result = {
                "pattern": pattern,
                "regex": regex,
                "match_count": sum(m["match_count"] for m in matches),
                "chunk_count": len(matches),
                "results": matches,
                "files_checked": len(doc_paths),
                "files_with_matches": doc_paths,
            }

        # Auto-index and record history outside the read lock
        if session_id:
            self.storage.record_search(
                session_id=session_id,
                pattern=pattern,
                tool="search_text",
                files_checked=[document_path] if document_path else doc_paths,
                files_with_matches=doc_paths,
            )
            if doc_paths:
                self.index_documents(doc_paths, agent_id=session_id)

        return result

    def agent_grep(
        self,
        pattern: str,
        regex: bool = False,
        document_path: str | None = None,
        classify: bool = True,
        include_scope: bool = True,
        group_by: str = "file",
        max_tokens: int = 4000,
        deduplicate: bool = True,
        context_lines: int = 0,
        session_id: str | None = None,
        working_tree: bool = False,
    ) -> dict[str, Any]:
        """LLM-optimized search: grep with scope, classification, token budget.

        See :func:`stele_context.agent_grep.agent_grep` for full docs.
        When session_id is provided, records search history and auto-indexes
        files with matches (no separate index call needed).
        When working_tree is True, auto-indexes modified and untracked files
        before searching.
        """
        from stele_context.agent_grep import agent_grep as _agent_grep

        if working_tree:
            self._index_working_tree(agent_id=session_id)

        with self._lock.read_lock():
            if document_path is not None:
                document_path = self._normalize_path(document_path)
            result = _agent_grep(
                self.storage,
                pattern,
                regex=regex,
                document_path=document_path,
                classify=classify,
                include_scope=include_scope,
                group_by=group_by,
                max_tokens=max_tokens,
                deduplicate=deduplicate,
                context_lines=context_lines,
                session_id=session_id,
                auto_index_func=None,  # deferred outside lock to avoid deadlock
            )

        # Auto-index files with matches after releasing the read lock
        if session_id and result.get("files_with_matches"):
            self.index_documents(result["files_with_matches"], agent_id=session_id)

        return result

    def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        search_mode: str = "keyword",
        max_result_tokens: int | None = None,
        compact: bool = False,
        return_response_meta: bool = False,
        path_prefix: str | None = None,
        working_tree: bool = False,
        session_id: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if working_tree:
            self._index_working_tree(agent_id=session_id)
        if path_prefix is not None:
            path_prefix = self._normalize_path(path_prefix)
        with self._lock.read_lock():
            return _se.search_unlocked(
                query,
                top_k,
                vector_index=self.vector_index,
                storage=self.storage,
                get_bm25=lambda: self.bm25_index,
                search_alpha=self.search_alpha,
                symbol_manager=self.symbol_manager,
                do_ensure_bm25=self._ensure_bm25,
                search_mode=search_mode,
                max_result_tokens=max_result_tokens,
                compact=compact,
                return_response_meta=return_response_meta,
                path_prefix=path_prefix,
            )

    def get_context(
        self,
        document_paths: list[str],
        *,
        session_id: str | None = None,
        include_trust: bool = True,
        max_chunk_content_tokens: int | None = None,
    ) -> dict[str, Any]:
        with self._lock.read_lock():
            result = _se.get_context_unlocked(
                document_paths,
                normalize_path=self._normalize_path,
                resolve_path=self._resolve_path,
                detect_modality=self.detect_modality,
                read_and_hash=_read_and_hash,
                storage=self.storage,
                include_trust=include_trust,
                max_chunk_content_tokens=max_chunk_content_tokens,
                session_id=session_id,
            )
        # Record file reads in session after releasing lock
        if session_id and result.get("unchanged"):
            for entry in result["unchanged"]:
                chunk_ids = [c["chunk_id"] for c in entry.get("chunks", [])]
                if chunk_ids:
                    self.storage.record_file_read(session_id, entry["path"], chunk_ids)
        return result

    def get_search_history(self, session_id: str) -> dict[str, Any]:
        """Return all searches recorded for this session."""
        searches = self.storage.get_search_history(session_id)
        return {"session_id": session_id, "searches": searches}

    def get_session_read_files(self, session_id: str) -> list[dict[str, Any]]:
        """Return all files fully read in this session."""
        return self.storage.get_session_read_files(session_id)
