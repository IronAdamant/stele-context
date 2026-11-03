"""Project root detection via .git and .stele-context.toml."""

from __future__ import annotations

from stele_context.engine import Stele
from stele_context.engine_utils import detect_project_root


def test_marker_file_is_project_root(tmp_path, monkeypatch):
    """An empty .stele-context.toml gives that directory its own index."""
    project = tmp_path / "notes"
    project.mkdir()
    (project / ".stele-context.toml").write_text("")
    monkeypatch.chdir(project)
    monkeypatch.delenv("STELE_CONTEXT_STORAGE_DIR", raising=False)

    root = detect_project_root()
    assert root == project.resolve()

    engine = Stele(enable_coordination=False)
    try:
        assert engine._project_root == project.resolve()
        assert (
            engine.storage.base_dir.resolve() == (project / ".stele-context").resolve()
        )
    finally:
        engine.storage.close()


def test_git_directory_is_project_root(tmp_path, monkeypatch):
    """A .git directory is still the root when no marker is nearer."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    nested = repo / "src"
    nested.mkdir()
    monkeypatch.chdir(nested)

    assert detect_project_root() == repo.resolve()


def test_neither_marker_nor_git_returns_none(tmp_path, monkeypatch):
    """No .git and no marker keeps the historical None result."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)

    assert detect_project_root() is None


def test_nested_marker_beats_higher_git(tmp_path, monkeypatch):
    """The nearest marker or .git wins, including a marker inside a repo."""
    repo = tmp_path / "repo"
    inner = repo / "vendor" / "lib"
    pkg = inner / "pkg"
    pkg.mkdir(parents=True)
    (repo / ".git").mkdir()
    (inner / ".stele-context.toml").write_text("")
    monkeypatch.chdir(pkg)

    assert detect_project_root() == inner.resolve()


def test_nested_git_beats_higher_marker(tmp_path, monkeypatch):
    """A nearer .git wins over a marker higher up the tree."""
    outer = tmp_path / "outer"
    repo = outer / "repo"
    repo.mkdir(parents=True)
    (outer / ".stele-context.toml").write_text("")
    (repo / ".git").write_text("gitdir: /tmp/example\n")
    monkeypatch.chdir(repo)

    assert detect_project_root() == repo.resolve()


def test_sibling_markers_do_not_share_a_root(tmp_path, monkeypatch):
    """Two unmarked-by-git projects with their own markers stay separate."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / ".stele-context.toml").write_text("")
    (second / ".stele-context.toml").write_text("")

    monkeypatch.chdir(first)
    first_root = detect_project_root()
    monkeypatch.chdir(second)
    second_root = detect_project_root()

    assert first_root == first.resolve()
    assert second_root == second.resolve()
    assert first_root != second_root


def test_explicit_root_ignores_markers(tmp_path, monkeypatch):
    """An explicit project root is used as given."""
    project = tmp_path / "marked"
    project.mkdir()
    (project / ".stele-context.toml").write_text("")
    monkeypatch.chdir(project)
    explicit = tmp_path / "elsewhere"
    explicit.mkdir()

    assert detect_project_root(str(explicit)) == explicit.resolve()
