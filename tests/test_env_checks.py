"""Tests for the env_checks module (stale pycache and editable installs)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from stele.env_checks import (
    check_editable_installs,
    clean_stale_pycache,
    scan_stale_pycache,
)


class TestScanStalePycache:
    """Tests for scan_stale_pycache()."""

    def test_empty_directory(self, tmp_path):
        result = scan_stale_pycache(tmp_path)
        assert result == {"stale_dirs": [], "total_stale_files": 0}

    def test_no_pycache_dirs(self, tmp_path):
        (tmp_path / "module.py").write_text("pass")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 0

    def test_finds_orphaned_pyc(self, tmp_path):
        """A .pyc with no matching .py in the parent dir is orphaned."""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "deleted_module.cpython-311.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 1
        assert len(result["stale_dirs"]) == 1
        assert result["stale_dirs"][0]["count"] == 1
        assert (
            "deleted_module.cpython-311.pyc" in result["stale_dirs"][0]["stale_files"]
        )

    def test_multiple_orphaned_files(self, tmp_path):
        """Multiple orphaned .pyc files in the same __pycache__."""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "a.cpython-311.pyc").write_bytes(b"\x00")
        (cache / "b.cpython-311.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 2
        assert result["stale_dirs"][0]["count"] == 2

    def test_no_false_positive_when_source_exists(self, tmp_path):
        """A .pyc with a matching .py in the parent dir is NOT orphaned."""
        (tmp_path / "mymod.py").write_text("pass")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "mymod.cpython-311.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 0
        assert result["stale_dirs"] == []

    def test_mixed_stale_and_valid(self, tmp_path):
        """Only the orphaned .pyc is flagged, not the one with a .py."""
        (tmp_path / "valid.py").write_text("pass")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "valid.cpython-311.pyc").write_bytes(b"\x00")
        (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 1
        assert result["stale_dirs"][0]["stale_files"] == ["orphan.cpython-311.pyc"]

    def test_nested_pycache(self, tmp_path):
        """Finds orphaned .pyc in nested subdirectory __pycache__."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        cache = pkg / "__pycache__"
        cache.mkdir()
        (cache / "gone.cpython-310.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 1
        assert result["stale_dirs"][0]["path"] == str(Path("pkg") / "__pycache__")

    def test_skip_dirs_default(self, tmp_path):
        """Default skip_dirs excludes .git, node_modules, .venv, venv."""
        for d in [".git", "node_modules", ".venv", "venv"]:
            cache = tmp_path / d / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 0

    def test_skip_dirs_custom(self, tmp_path):
        """Custom skip_dirs set is respected."""
        cache = tmp_path / "build" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        # Without skip
        result = scan_stale_pycache(tmp_path, skip_dirs=set())
        assert result["total_stale_files"] == 1
        # With skip
        result = scan_stale_pycache(tmp_path, skip_dirs={"build"})
        assert result["total_stale_files"] == 0

    def test_hidden_dirs_skipped(self, tmp_path):
        """Directories starting with . (other than __pycache__) are skipped."""
        cache = tmp_path / ".hidden" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path, skip_dirs=set())
        assert result["total_stale_files"] == 0

    def test_pyc_without_cpython_tag(self, tmp_path):
        """A .pyc file without the cpython tag still gets checked correctly."""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        # No dot in stem -> module_name == full stem
        (cache / "simple.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 1
        # Now create the matching source
        (tmp_path / "simple.py").write_text("pass")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 0

    def test_path_in_result_is_relative(self, tmp_path):
        """The path in stale_dirs is relative to root."""
        cache = tmp_path / "sub" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        reported_path = result["stale_dirs"][0]["path"]
        # Should be relative, not absolute
        assert not Path(reported_path).is_absolute()

    def test_multiple_pycache_dirs(self, tmp_path):
        """Multiple __pycache__ dirs across subdirectories are all scanned."""
        for name in ["a", "b", "c"]:
            pkg = tmp_path / name
            pkg.mkdir()
            cache = pkg / "__pycache__"
            cache.mkdir()
            (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        result = scan_stale_pycache(tmp_path)
        assert result["total_stale_files"] == 3
        assert len(result["stale_dirs"]) == 3


class TestCleanStalePycache:
    """Tests for clean_stale_pycache()."""

    def test_removes_orphaned_pyc(self, tmp_path):
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        pyc = cache / "orphan.cpython-311.pyc"
        pyc.write_bytes(b"\x00")
        result = clean_stale_pycache(tmp_path)
        assert result["cleaned"] == 1
        assert not pyc.exists()

    def test_removes_empty_pycache_dir(self, tmp_path):
        """Empty __pycache__ dir is removed after cleaning all .pyc files."""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        clean_stale_pycache(tmp_path)
        assert not cache.exists()

    def test_keeps_pycache_dir_with_remaining_files(self, tmp_path):
        """__pycache__ dir is kept if it still has valid .pyc files after cleaning."""
        (tmp_path / "valid.py").write_text("pass")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "valid.cpython-311.pyc").write_bytes(b"\x00")
        (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        result = clean_stale_pycache(tmp_path)
        assert result["cleaned"] == 1
        assert cache.exists()
        assert (cache / "valid.cpython-311.pyc").exists()

    def test_does_not_remove_valid_pyc(self, tmp_path):
        """Valid .pyc files (with matching .py) are not removed."""
        (tmp_path / "module.py").write_text("pass")
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        pyc = cache / "module.cpython-311.pyc"
        pyc.write_bytes(b"\x00")
        result = clean_stale_pycache(tmp_path)
        assert result["cleaned"] == 0
        assert pyc.exists()

    def test_clean_multiple_dirs(self, tmp_path):
        """Cleaning works across multiple __pycache__ directories."""
        for name in ["x", "y"]:
            pkg = tmp_path / name
            pkg.mkdir()
            cache = pkg / "__pycache__"
            cache.mkdir()
            (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        result = clean_stale_pycache(tmp_path)
        assert result["cleaned"] == 2
        assert result["total_stale_files"] == 2

    def test_clean_nothing_when_no_stale(self, tmp_path):
        result = clean_stale_pycache(tmp_path)
        assert result["cleaned"] == 0
        assert result["total_stale_files"] == 0

    def test_clean_respects_skip_dirs(self, tmp_path):
        """Cleaning also honors the skip_dirs parameter."""
        cache = tmp_path / "vendor" / "__pycache__"
        cache.mkdir(parents=True)
        pyc = cache / "orphan.cpython-311.pyc"
        pyc.write_bytes(b"\x00")
        result = clean_stale_pycache(tmp_path, skip_dirs={"vendor"})
        assert result["cleaned"] == 0
        assert pyc.exists()

    def test_idempotent(self, tmp_path):
        """Running clean twice is safe; second run finds nothing."""
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "orphan.cpython-311.pyc").write_bytes(b"\x00")
        first = clean_stale_pycache(tmp_path)
        assert first["cleaned"] == 1
        second = clean_stale_pycache(tmp_path)
        assert second["cleaned"] == 0
        assert second["total_stale_files"] == 0


class TestCheckEditableInstalls:
    """Tests for check_editable_installs()."""

    def test_no_editable_installs(self):
        """Returns empty when no distributions have direct_url.json."""
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = None
        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=Path("/project"))
        assert result == {"editable_issues": [], "count": 0}

    def test_detects_editable_outside_project(self, tmp_path):
        """Flags editable install pointing outside the project root."""
        other_path = tmp_path / "other_worktree"
        other_path.mkdir()
        project_root = tmp_path / "main"
        project_root.mkdir()

        direct_url = json.dumps(
            {"url": f"file://{other_path}", "dir_info": {"editable": True}}
        )
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = direct_url
        mock_dist.metadata = {"Name": "mypkg"}

        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=project_root)
        assert result["count"] == 1
        assert result["editable_issues"][0]["package"] == "mypkg"
        assert result["editable_issues"][0]["install_path"] == str(other_path.resolve())

    def test_no_issue_when_paths_match(self, tmp_path):
        """No issue when editable install path matches project root."""
        direct_url = json.dumps(
            {"url": f"file://{tmp_path}", "dir_info": {"editable": True}}
        )
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = direct_url
        mock_dist.metadata = {"Name": "mypkg"}

        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=tmp_path)
        assert result["count"] == 0

    def test_skips_non_editable(self):
        """Non-editable installs with direct_url.json are skipped."""
        direct_url = json.dumps(
            {"url": "file:///some/path", "dir_info": {"editable": False}}
        )
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = direct_url

        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=Path("/project"))
        assert result["count"] == 0

    def test_skips_non_file_url(self):
        """Editable installs with non-file URLs are skipped."""
        direct_url = json.dumps(
            {"url": "https://github.com/x/y", "dir_info": {"editable": True}}
        )
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = direct_url

        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=Path("/project"))
        assert result["count"] == 0

    def test_handles_malformed_json(self):
        """Malformed direct_url.json is silently skipped."""
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = "not json{{"

        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=Path("/project"))
        assert result["count"] == 0

    def test_handles_read_text_exception(self):
        """Exception from read_text is silently skipped."""
        mock_dist = MagicMock()
        mock_dist.read_text.side_effect = FileNotFoundError("missing")

        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=Path("/project"))
        assert result["count"] == 0

    def test_no_project_root(self):
        """When project_root is None, no issues are reported."""
        direct_url = json.dumps(
            {"url": "file:///some/path", "dir_info": {"editable": True}}
        )
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = direct_url

        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=None)
        assert result["count"] == 0

    def test_handles_importlib_metadata_failure(self):
        """Gracefully handles failure to import/use importlib.metadata."""
        with patch(
            "importlib.metadata.distributions", side_effect=RuntimeError("broken")
        ):
            result = check_editable_installs(project_root=Path("/project"))
        assert result == {"editable_issues": [], "count": 0}

    def test_missing_dir_info_key(self):
        """direct_url.json without dir_info key is skipped."""
        direct_url = json.dumps({"url": "file:///some/path"})
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = direct_url

        with patch("importlib.metadata.distributions", return_value=[mock_dist]):
            result = check_editable_installs(project_root=Path("/project"))
        assert result["count"] == 0

    def test_multiple_distributions(self, tmp_path):
        """Multiple editable installs are all checked."""
        project_root = tmp_path / "main"
        project_root.mkdir()
        other = tmp_path / "other"
        other.mkdir()

        ok_dist = MagicMock()
        ok_dist.read_text.return_value = json.dumps(
            {"url": f"file://{project_root}", "dir_info": {"editable": True}}
        )
        ok_dist.metadata = {"Name": "ok-pkg"}

        bad_dist = MagicMock()
        bad_dist.read_text.return_value = json.dumps(
            {"url": f"file://{other}", "dir_info": {"editable": True}}
        )
        bad_dist.metadata = {"Name": "bad-pkg"}

        non_editable = MagicMock()
        non_editable.read_text.return_value = None

        with patch(
            "importlib.metadata.distributions",
            return_value=[ok_dist, bad_dist, non_editable],
        ):
            result = check_editable_installs(project_root=project_root)
        assert result["count"] == 1
        assert result["editable_issues"][0]["package"] == "bad-pkg"
