"""
Tests for worktree safety features added after session 17-18 stress test.

Covers:
- Project root detection (git repos, worktrees, no-git fallback)
- Path normalization (project-relative, outside-project, absolute-passthrough)
- Per-worktree storage isolation (project-local .stele/ directory)
- Automatic lock acquisition when agent_id is set
- MCP server agent_id injection
"""

import os

import pytest

from stele.engine import Stele


# ---------------------------------------------------------------------------
# Project root detection
# ---------------------------------------------------------------------------


class TestProjectRootDetection:
    def test_explicit_project_root(self, tmp_path):
        """Explicit project_root is used as-is."""
        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
        )
        assert e._project_root == tmp_path.resolve()

    def test_auto_detect_git_dir(self, tmp_path):
        """Detects .git directory as project root."""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        orig = os.getcwd()
        try:
            os.chdir(repo)
            e = Stele(storage_dir=str(tmp_path / "storage"))
            assert e._project_root == repo.resolve()
        finally:
            os.chdir(orig)

    def test_auto_detect_git_file_worktree(self, tmp_path):
        """Detects .git file (worktree) as project root."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /tmp/some/repo/.git/worktrees/x")
        orig = os.getcwd()
        try:
            os.chdir(worktree)
            e = Stele(storage_dir=str(tmp_path / "storage"))
            assert e._project_root == worktree.resolve()
        finally:
            os.chdir(orig)

    def test_no_git_returns_none(self, tmp_path):
        """No .git found → project_root is None."""
        isolated = tmp_path / "no_git"
        isolated.mkdir()
        orig = os.getcwd()
        try:
            os.chdir(isolated)
            root = Stele._detect_project_root()
            assert root is None
        finally:
            os.chdir(orig)


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


class TestPathNormalization:
    @pytest.fixture
    def engine(self, tmp_path):
        return Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
        )

    def test_absolute_within_project(self, engine, tmp_path):
        """Absolute path within project root → relative."""
        assert engine._normalize_path(
            str(tmp_path / "src" / "main.py")
        ) == os.path.join("src", "main.py")

    def test_absolute_outside_project(self, engine):
        """Absolute path outside project root → stays absolute."""
        result = engine._normalize_path("/etc/config.txt")
        assert os.path.isabs(result)

    def test_resolve_relative_to_project(self, engine, tmp_path):
        """Relative path resolves against project root."""
        resolved = engine._resolve_path(os.path.join("src", "main.py"))
        assert resolved == tmp_path / "src" / "main.py"

    def test_resolve_absolute_passthrough(self, engine):
        """Absolute path passes through _resolve_path unchanged."""
        resolved = engine._resolve_path("/etc/config.txt")
        assert str(resolved) == "/etc/config.txt"

    def test_normalize_then_resolve_roundtrip(self, engine, tmp_path):
        """normalize → resolve is a roundtrip for in-project paths."""
        original = str(tmp_path / "src" / "parser.py")
        normalized = engine._normalize_path(original)
        resolved = engine._resolve_path(normalized)
        assert str(resolved) == original

    def test_no_project_root_keeps_absolute(self, tmp_path):
        """With project_root=None, all paths stay absolute."""
        # Force no project root
        e = Stele.__new__(Stele)
        e._project_root = None
        result = e._normalize_path(str(tmp_path / "file.py"))
        assert os.path.isabs(result)


# ---------------------------------------------------------------------------
# Per-worktree storage isolation
# ---------------------------------------------------------------------------


class TestStorageIsolation:
    def test_default_storage_in_project_root(self, tmp_path):
        """When no storage_dir, uses <project_root>/.stele/."""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        (repo / ".git").mkdir()
        orig = os.getcwd()
        try:
            os.chdir(repo)
            e = Stele(project_root=str(repo))
            assert ".stele" in str(e.storage.base_dir)
            assert str(repo) in str(e.storage.base_dir)
        finally:
            os.chdir(orig)

    def test_env_var_overrides_default(self, tmp_path, monkeypatch):
        """STELE_STORAGE_DIR env var takes priority over project root."""
        custom = str(tmp_path / "custom_storage")
        monkeypatch.setenv("STELE_STORAGE_DIR", custom)
        e = Stele(project_root=str(tmp_path))
        assert str(e.storage.base_dir) == custom

    def test_explicit_storage_dir_wins(self, tmp_path, monkeypatch):
        """Explicit storage_dir overrides both env var and project root."""
        monkeypatch.setenv("STELE_STORAGE_DIR", "/should/not/use")
        explicit = str(tmp_path / "explicit")
        e = Stele(
            storage_dir=explicit,
            project_root=str(tmp_path),
        )
        assert str(e.storage.base_dir) == explicit

    def test_worktrees_get_isolated_storage(self, tmp_path):
        """Two worktrees with different project roots get separate DBs."""
        root_a = tmp_path / "main"
        root_b = tmp_path / "worktree"
        root_a.mkdir()
        root_b.mkdir()

        e_a = Stele(project_root=str(root_a))
        e_b = Stele(project_root=str(root_b))

        assert e_a.storage.db_path != e_b.storage.db_path


# ---------------------------------------------------------------------------
# Index with path normalization
# ---------------------------------------------------------------------------


class TestIndexWithNormalization:
    @pytest.fixture
    def setup(self, tmp_path):
        e = Stele(
            storage_dir=str(tmp_path / "stele_data"),
            project_root=str(tmp_path),
        )
        f = tmp_path / "parser.py"
        f.write_text("def parse():\n    pass\n")
        return e, f, tmp_path

    def test_index_stores_relative_path(self, setup):
        """Indexed documents use project-relative paths."""
        e, f, _ = setup
        result = e.index_documents([str(f)])
        assert result["indexed"][0]["path"] == "parser.py"

        # DB stores relative path
        doc = e.storage.get_document("parser.py")
        assert doc is not None

    def test_index_with_absolute_finds_same_doc(self, setup):
        """Re-indexing with absolute path matches existing relative key."""
        e, f, _ = setup
        e.index_documents([str(f)])

        # Modify and re-index with absolute path
        f.write_text("def parse_v2():\n    pass\n")
        result = e.index_documents([str(f)], force_reindex=True)
        assert result["indexed"][0]["path"] == "parser.py"

        # Only one document in the DB
        docs = e.storage.get_all_documents()
        parser_docs = [d for d in docs if d["document_path"] == "parser.py"]
        assert len(parser_docs) == 1

    def test_detect_changes_uses_normalized_paths(self, setup):
        """detect_changes_and_update works with both absolute and relative."""
        e, f, _ = setup
        e.index_documents([str(f)])

        f.write_text("def changed():\n    pass\n")
        # Pass absolute path — should find the relative key
        result = e.detect_changes_and_update(
            session_id="s1",
            document_paths=[str(f)],
        )
        assert len(result["modified"]) == 1
        assert result["modified"][0]["path"] == "parser.py"

    def test_get_context_with_normalized_paths(self, setup):
        """get_context resolves normalized paths for file I/O."""
        e, f, _ = setup
        e.index_documents([str(f)])

        result = e.get_context(document_paths=[str(f)])
        assert len(result["unchanged"]) == 1


# ---------------------------------------------------------------------------
# Automatic lock acquisition
# ---------------------------------------------------------------------------


class TestAutoLocking:
    @pytest.fixture
    def engine(self, tmp_path):
        e = Stele(
            storage_dir=str(tmp_path / "stele_data"),
            project_root=str(tmp_path),
        )
        f = tmp_path / "doc.py"
        f.write_text("def hello():\n    return 'world'\n")
        return e, f, tmp_path

    def test_auto_lock_on_index_with_agent_id(self, engine):
        """When agent_id is set, indexing auto-acquires lock."""
        e, f, _ = engine
        e.index_documents([str(f)], agent_id="agent-a")

        status = e.get_document_lock_status(str(f))
        assert status["locked"] is True
        assert status["locked_by"] == "agent-a"

    def test_no_auto_lock_without_agent_id(self, engine):
        """Without agent_id, no lock is acquired (backward compat)."""
        e, f, _ = engine
        e.index_documents([str(f)])

        status = e.get_document_lock_status(str(f))
        assert status["locked"] is False

    def test_auto_lock_blocks_second_agent(self, engine):
        """Auto-acquired lock prevents a second agent from indexing."""
        e, f, tmp_path = engine
        e.index_documents([str(f)], agent_id="agent-a")

        f.write_text("def updated():\n    pass\n")
        result = e.index_documents([str(f)], agent_id="agent-b", force_reindex=True)
        assert len(result["conflicts"]) == 1
        assert len(result["indexed"]) == 0

    def test_auto_lock_allows_same_agent(self, engine):
        """Same agent can re-index (owns the lock)."""
        e, f, tmp_path = engine
        e.index_documents([str(f)], agent_id="agent-a")

        f.write_text("def updated():\n    pass\n")
        result = e.index_documents([str(f)], agent_id="agent-a", force_reindex=True)
        assert len(result["indexed"]) == 1
        assert len(result["conflicts"]) == 0

    def test_auto_lock_on_new_document(self, engine):
        """First-time indexing with agent_id acquires lock on new doc."""
        e, _, tmp_path = engine
        new_file = tmp_path / "new.py"
        new_file.write_text("x = 1\n")

        e.index_documents([str(new_file)], agent_id="agent-a")

        status = e.get_document_lock_status(str(new_file))
        assert status["locked"] is True
        assert status["locked_by"] == "agent-a"

    def test_auto_lock_on_existing_unlocked_reindex(self, engine):
        """Re-indexing an unlocked doc with agent_id acquires lock."""
        e, f, tmp_path = engine
        # First index without agent_id
        e.index_documents([str(f)])
        status = e.get_document_lock_status(str(f))
        assert status["locked"] is False

        # Re-index with agent_id
        f.write_text("def v2():\n    pass\n")
        e.index_documents([str(f)], agent_id="agent-a", force_reindex=True)

        status = e.get_document_lock_status(str(f))
        assert status["locked"] is True
        assert status["locked_by"] == "agent-a"


# ---------------------------------------------------------------------------
# MCP server agent_id injection
# ---------------------------------------------------------------------------


class TestMCPAgentId:
    def test_http_server_generates_agent_id(self):
        """MCPServer generates a unique agent_id."""
        from stele.mcp_server import MCPServer
        from unittest.mock import MagicMock

        stele = MagicMock()
        server = MCPServer(stele=stele, port=0)
        assert server.agent_id.startswith("stele-http-")
        assert str(os.getpid()) in server.agent_id

    def test_http_server_custom_agent_id(self):
        """MCPServer accepts a custom agent_id."""
        from stele.mcp_server import MCPServer
        from unittest.mock import MagicMock

        stele = MagicMock()
        server = MCPServer(stele=stele, port=0, agent_id="my-agent")
        assert server.agent_id == "my-agent"

    def test_http_write_tool_injects_agent_id(self, tmp_path):
        """HTTP server injects agent_id for write operations."""
        from stele.mcp_server import MCPRequestHandler

        e = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "test.txt"
        f.write_text("hello")
        e.index_documents([str(f)])

        handler = MCPRequestHandler.__new__(MCPRequestHandler)
        handler.stele = e
        handler._server_agent_id = "test-agent"

        # Call a write tool without agent_id
        result = handler._execute_tool(
            "index_documents",
            {"paths": [str(f)], "force_reindex": True},
        )
        assert result.get("success") is True

    def test_http_read_tool_no_injection(self, tmp_path):
        """HTTP server does not inject agent_id for read operations."""
        from stele.mcp_server import MCPRequestHandler

        e = Stele(storage_dir=str(tmp_path / "storage"))
        handler = MCPRequestHandler.__new__(MCPRequestHandler)
        handler.stele = e
        handler._server_agent_id = "test-agent"

        result = handler._execute_tool("search", {"query": "hello"})
        assert result.get("success") is True


# ---------------------------------------------------------------------------
# Integration: worktree conflict scenario
# ---------------------------------------------------------------------------


class TestWorktreeConflictScenario:
    def test_same_relative_path_shares_lock(self, tmp_path):
        """Two absolute paths normalizing to the same relative key share a lock.

        This is the core fix: in the old system, /main/parser.py and
        /worktree/parser.py were different documents. Now both normalize
        to 'parser.py' and share the same document record + lock.
        """
        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
        )

        f = tmp_path / "parser.py"
        f.write_text("def parse(): pass\n")

        # Agent A indexes via absolute path
        e.index_documents([str(f)], agent_id="agent-a")

        # Lock is held under normalized key
        status = e.get_document_lock_status(str(f))
        assert status["locked"] is True

        # Also accessible via relative key
        status2 = e.get_document_lock_status("parser.py")
        assert status2["locked"] is True
        assert status2["locked_by"] == "agent-a"

    def test_directory_indexing_normalizes(self, tmp_path):
        """Indexing a directory produces normalized paths."""
        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
        )

        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("x = 1\n")
        (src / "b.py").write_text("y = 2\n")

        result = e.index_documents([str(src)])
        paths = {item["path"] for item in result["indexed"]}
        assert paths == {os.path.join("src", "a.py"), os.path.join("src", "b.py")}


# ---------------------------------------------------------------------------
# Cross-worktree coordination
# ---------------------------------------------------------------------------


class TestGitCommonDirDetection:
    def test_normal_repo(self, tmp_path):
        """Normal .git directory is its own common dir."""
        from stele.coordination import detect_git_common_dir

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        result = detect_git_common_dir(repo)
        assert result == repo / ".git"

    def test_worktree_with_commondir(self, tmp_path):
        """Worktree .git file with commondir resolves to shared .git."""
        from stele.coordination import detect_git_common_dir

        # Simulate main repo
        main = tmp_path / "main"
        main.mkdir()
        git_dir = main / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        (git_dir / "worktrees").mkdir()
        wt_gitdir = git_dir / "worktrees" / "feature"
        wt_gitdir.mkdir()
        (wt_gitdir / "commondir").write_text("../..\n")
        (wt_gitdir / "HEAD").write_text("ref: refs/heads/feature\n")

        # Simulate worktree
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n")

        result = detect_git_common_dir(worktree)
        assert result == git_dir.resolve()

    def test_no_git(self, tmp_path):
        """No .git → returns None."""
        from stele.coordination import detect_git_common_dir

        result = detect_git_common_dir(tmp_path)
        assert result is None

    def test_none_project_root(self):
        """None project_root → returns None."""
        from stele.coordination import detect_git_common_dir

        assert detect_git_common_dir(None) is None


class TestCoordinationBackend:
    @pytest.fixture
    def coord(self, tmp_path):
        from stele.coordination import CoordinationBackend

        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        return CoordinationBackend(git_dir)

    def test_register_and_list(self, coord):
        coord.register_agent("a1", "/path/main")
        agents = coord.list_agents()
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "a1"
        assert agents[0]["stale"] is False

    def test_heartbeat(self, coord):
        coord.register_agent("a1", "/path/main")
        result = coord.heartbeat("a1")
        assert result["updated"] is True

    def test_deregister_releases_locks(self, coord):
        coord.register_agent("a1", "/path/main")
        coord.acquire_lock("file.py", "a1")
        result = coord.deregister_agent("a1")
        assert result["locks_released"] == 1

        status = coord.get_lock_status("file.py")
        assert status["locked"] is False

    def test_shared_lock_blocks_other_agent(self, coord):
        coord.register_agent("a1", "/path/main")
        coord.register_agent("a2", "/path/worktree")
        coord.acquire_lock("file.py", "a1")

        result = coord.acquire_lock("file.py", "a2")
        assert result["acquired"] is False
        assert result["locked_by"] == "a1"

    def test_shared_lock_allows_same_agent(self, coord):
        coord.register_agent("a1", "/path/main")
        coord.acquire_lock("file.py", "a1")
        result = coord.acquire_lock("file.py", "a1")
        assert result["acquired"] is True

    def test_reap_stale_agents(self, coord):
        coord.register_agent("a1", "/path/main")
        coord.acquire_lock("file.py", "a1")

        # Force stale heartbeat
        import sqlite3

        with sqlite3.connect(coord.db_path) as conn:
            conn.execute("UPDATE agents SET last_heartbeat = 0 WHERE agent_id = 'a1'")
            conn.commit()

        result = coord.reap_stale_agents(timeout=1)
        assert result["reaped_count"] == 1
        assert coord.get_lock_status("file.py")["locked"] is False

    def test_shared_conflict_log(self, coord):
        coord.register_agent("a1", "/path/main")
        coord.register_agent("a2", "/path/wt")
        coord.acquire_lock("file.py", "a1")
        coord.acquire_lock("file.py", "a2", force=True)

        conflicts = coord.get_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "lock_stolen"


class TestCoordinationIntegration:
    def test_engine_with_coordination(self, tmp_path):
        """Engine uses coordination DB when git common dir exists."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(repo),
        )
        assert e._coordination is not None

    def test_engine_without_coordination(self, tmp_path):
        """Engine skips coordination when no git common dir."""
        no_git = tmp_path / "plain"
        no_git.mkdir()

        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(no_git),
        )
        assert e._coordination is None

    def test_enable_coordination_false(self, tmp_path):
        """enable_coordination=False disables even with git present."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(repo),
            enable_coordination=False,
        )
        assert e._coordination is None

    def test_cross_worktree_lock_visibility(self, tmp_path):
        """Two engines sharing coordination DB see each other's locks."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        f = repo / "shared.py"
        f.write_text("x = 1\n")

        e1 = Stele(
            storage_dir=str(tmp_path / "s1"),
            project_root=str(repo),
        )
        e2 = Stele(
            storage_dir=str(tmp_path / "s2"),
            project_root=str(repo),
        )

        e1.index_documents([str(f)], agent_id="agent-1")

        # Agent-1's lock should be visible to engine-2
        status = e2.get_document_lock_status(str(f))
        assert status["locked"] is True
        assert status["locked_by"] == "agent-1"

    def test_agent_registry_lifecycle(self, tmp_path):
        """Register → list → deregister lifecycle."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(repo),
        )
        e.register_agent("test-agent")
        agents = e.list_agents()
        assert any(a["agent_id"] == "test-agent" for a in agents)

        e.deregister_agent("test-agent")
        agents = e.list_agents()
        assert not any(a["agent_id"] == "test-agent" for a in agents)


# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------


class TestEnvironmentChecks:
    def test_scan_stale_pycache(self, tmp_path):
        """Detects .pyc files with missing .py source."""
        from stele.env_checks import scan_stale_pycache

        cache = tmp_path / "pkg" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "orphan.cpython-312.pyc").write_bytes(b"\x00")
        # No orphan.py exists

        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 1

    def test_scan_non_stale_pycache(self, tmp_path):
        """Non-stale .pyc (source exists) is not flagged."""
        from stele.env_checks import scan_stale_pycache

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "module.py").write_text("x = 1\n")
        cache = pkg / "__pycache__"
        cache.mkdir()
        (cache / "module.cpython-312.pyc").write_bytes(b"\x00")

        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 0

    def test_clean_stale_pycache(self, tmp_path):
        """Removes orphaned .pyc files."""
        from stele.env_checks import clean_stale_pycache

        cache = tmp_path / "pkg" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "gone.cpython-312.pyc").write_bytes(b"\x00")

        result = clean_stale_pycache(tmp_path)
        assert result["cleaned"] == 1
        assert not (cache / "gone.cpython-312.pyc").exists()

    def test_engine_check_environment(self, tmp_path):
        """Engine's check_environment returns structured results."""
        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        result = e.check_environment()
        assert "issues" in result
        assert "total_issues" in result

    def test_engine_clean_bytecache(self, tmp_path):
        """Engine's clean_bytecache delegates to env_checks."""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "stale.cpython-312.pyc").write_bytes(b"\x00")

        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(tmp_path),
            enable_coordination=False,
        )
        result = e.clean_bytecache()
        assert result["cleaned"] == 1

    def test_check_editable_installs(self, tmp_path):
        """check_editable_installs runs without error."""
        from stele.env_checks import check_editable_installs

        result = check_editable_installs(tmp_path)
        assert "editable_issues" in result
        assert isinstance(result["count"], int)


# ---------------------------------------------------------------------------
# Change notifications
# ---------------------------------------------------------------------------


class TestChangeNotifications:
    @pytest.fixture
    def coordinated_engine(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(repo),
        )
        return e, repo

    def test_index_generates_notification(self, coordinated_engine):
        """Indexing a file sends a change notification."""
        e, repo = coordinated_engine
        f = repo / "file.py"
        f.write_text("x = 1\n")
        e.index_documents([str(f)], agent_id="agent-a")

        notifs = e.get_notifications()
        assert notifs["count"] >= 1
        assert any(
            n["change_type"] == "indexed" and n["document_path"] == "file.py"
            for n in notifs["notifications"]
        )

    def test_notifications_exclude_self(self, coordinated_engine):
        """exclude_self filters out the requesting agent's notifications."""
        e, repo = coordinated_engine
        f = repo / "file.py"
        f.write_text("x = 1\n")
        e.index_documents([str(f)], agent_id="agent-a")

        notifs = e.get_notifications(exclude_self="agent-a")
        assert notifs["count"] == 0

    def test_notifications_since_timestamp(self, coordinated_engine):
        """since parameter filters by time."""
        import time

        e, repo = coordinated_engine
        f = repo / "file.py"
        f.write_text("x = 1\n")
        e.index_documents([str(f)], agent_id="agent-a")

        future = time.time() + 1000
        notifs = e.get_notifications(since=future)
        assert notifs["count"] == 0

    def test_detect_changes_generates_notification(self, coordinated_engine):
        """detect_changes_and_update notifies about modified files."""
        e, repo = coordinated_engine
        f = repo / "file.py"
        f.write_text("x = 1\n")
        e.index_documents([str(f)], agent_id="agent-a")

        f.write_text("x = 2  # modified\n")
        e.detect_changes_and_update(
            session_id="s1",
            agent_id="agent-a",
        )

        notifs = e.get_notifications()
        assert any(n["change_type"] == "modified" for n in notifs["notifications"])

    def test_no_notifications_without_coordination(self, tmp_path):
        """Without coordination, get_notifications returns empty."""
        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            enable_coordination=False,
        )
        notifs = e.get_notifications()
        assert notifs["count"] == 0


# ---------------------------------------------------------------------------
# Symbol graph extraction
# ---------------------------------------------------------------------------


class TestSymbolGraphExtraction:
    def test_symbol_manager_initialized(self, tmp_path):
        """Engine creates SymbolGraphManager on init."""
        from stele.symbol_graph import SymbolGraphManager

        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            enable_coordination=False,
        )
        assert isinstance(e.symbol_manager, SymbolGraphManager)

    def test_find_references_delegates(self, tmp_path):
        """find_references works through the extracted manager."""
        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            enable_coordination=False,
        )
        f = tmp_path / "test.py"
        f.write_text("def my_func():\n    pass\n\nresult = my_func()\n")
        e.index_documents([str(f)])

        result = e.find_references("my_func")
        assert result["total"] >= 1

    def test_rebuild_symbol_graph_delegates(self, tmp_path):
        """rebuild_symbol_graph works through the extracted manager."""
        e = Stele(
            storage_dir=str(tmp_path / "storage"),
            enable_coordination=False,
        )
        f = tmp_path / "test.py"
        f.write_text("class Foo:\n    pass\n")
        e.index_documents([str(f)])

        result = e.rebuild_symbol_graph()
        assert "symbols" in result
        assert "edges" in result
