"""Tests for .gitignore-aware indexing (gitignore.py + expand_paths)."""

from __future__ import annotations

from pathlib import Path

from stele_context.engine import Stele
from stele_context.gitignore import GitignoreMatcher


def _matcher(lines: list[str], base: Path | None = None) -> GitignoreMatcher:
    return GitignoreMatcher(lines, base or Path("/project"))


class TestGitignoreMatcher:
    def test_basename_pattern_matches_any_depth(self):
        m = _matcher(["*.log"])
        assert m.is_ignored("debug.log")
        assert m.is_ignored("deep/nested/debug.log")
        assert not m.is_ignored("debug.txt")

    def test_anchored_pattern_matches_root_only(self):
        m = _matcher(["/build.py"])
        assert m.is_ignored("build.py")
        assert not m.is_ignored("src/build.py")

    def test_embedded_slash_anchors_pattern(self):
        m = _matcher(["docs/internal.md"])
        assert m.is_ignored("docs/internal.md")
        assert not m.is_ignored("other/docs/internal.md")

    def test_dir_only_pattern_ignores_contents(self):
        m = _matcher(["generated/"])
        assert m.is_ignored("generated/out.py")
        assert m.is_ignored("sub/generated/out.py")
        # dir-only pattern must not match a *file* named "generated"
        assert not m.is_ignored("generated", is_dir=False)

    def test_negation_last_match_wins(self):
        m = _matcher(["*.log", "!keep.log"])
        assert m.is_ignored("other.log")
        assert not m.is_ignored("keep.log")

    def test_double_star_glob(self):
        m = _matcher(["src/**/temp.py"])
        assert m.is_ignored("src/a/b/temp.py")
        assert m.is_ignored("src/temp.py")
        assert not m.is_ignored("lib/a/temp.py")

    def test_question_mark_and_class(self):
        m = _matcher(["cache?.py", "v[0-9].txt"])
        assert m.is_ignored("cache1.py")
        assert not m.is_ignored("cache12.py")
        assert m.is_ignored("v3.txt")
        assert not m.is_ignored("vx.txt")

    def test_comments_and_blanks_skipped(self):
        m = _matcher(["# comment", "", "*.tmp"])
        assert m.is_ignored("a.tmp")
        assert not m.is_ignored("# comment")

    def test_ignored_ancestor_ignores_file(self):
        m = _matcher(["vendor"])
        assert m.is_ignored("vendor/lib/code.py")

    def test_path_outside_base_is_not_ignored(self, tmp_path):
        m = GitignoreMatcher(["*.py"], tmp_path)
        assert not m.is_ignored_path(Path("/elsewhere/x.py"))

    def test_load_returns_none_without_gitignore(self, tmp_path):
        assert GitignoreMatcher.load(tmp_path) is None
        assert GitignoreMatcher.load(None) is None

    def test_load_reads_root_gitignore(self, tmp_path):
        (tmp_path / ".gitignore").write_text("*.secret\n")
        m = GitignoreMatcher.load(tmp_path)
        assert m is not None
        assert m.is_ignored_path(tmp_path / "creds.secret")


class TestIndexingRespectsGitignore:
    def _project(self, tmp_path) -> Path:
        root = tmp_path / "proj"
        root.mkdir()
        (root / ".gitignore").write_text("ignored.py\nsecrets/\n")
        (root / "kept.py").write_text("def kept():\n    return 1\n")
        (root / "ignored.py").write_text("def ignored():\n    return 2\n")
        (root / "secrets").mkdir()
        (root / "secrets" / "keys.txt").write_text("hunter2\n")
        return root

    def test_directory_index_skips_ignored_files(self, tmp_path):
        root = self._project(tmp_path)
        engine = Stele(storage_dir=str(tmp_path / "storage"), project_root=str(root))
        engine.index_documents([str(root)])
        indexed = {d["document_path"] for d in engine.storage.get_all_documents()}
        assert "kept.py" in indexed
        assert "ignored.py" not in indexed
        assert not any("secrets" in p for p in indexed)

    def test_explicit_file_beats_gitignore(self, tmp_path):
        root = self._project(tmp_path)
        engine = Stele(storage_dir=str(tmp_path / "storage"), project_root=str(root))
        engine.index_documents([str(root / "ignored.py")])
        indexed = {d["document_path"] for d in engine.storage.get_all_documents()}
        assert "ignored.py" in indexed

    def test_respect_gitignore_false_indexes_everything(self, tmp_path):
        root = self._project(tmp_path)
        engine = Stele(
            storage_dir=str(tmp_path / "storage"),
            project_root=str(root),
            respect_gitignore=False,
        )
        engine.index_documents([str(root)])
        indexed = {d["document_path"] for d in engine.storage.get_all_documents()}
        assert "ignored.py" in indexed

    def test_config_file_can_disable(self, tmp_path):
        root = self._project(tmp_path)
        (root / ".stele-context.toml").write_text(
            "[stele-context]\nrespect_gitignore = false\n"
        )
        engine = Stele(storage_dir=str(tmp_path / "storage"), project_root=str(root))
        engine.index_documents([str(root)])
        indexed = {d["document_path"] for d in engine.storage.get_all_documents()}
        assert "ignored.py" in indexed

    def test_detect_changes_scan_new_respects_gitignore(self, tmp_path):
        root = self._project(tmp_path)
        engine = Stele(storage_dir=str(tmp_path / "storage"), project_root=str(root))
        engine.index_documents([str(root / "kept.py")])
        result = engine.detect_changes_and_update("scan-session", scan_new=True)
        new_paths = {e["path"] for e in result["new"]}
        assert "ignored.py" not in new_paths
