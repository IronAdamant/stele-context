"""Tests for the CLI module (cli.py and cli_metadata.py)."""

from __future__ import annotations

import argparse
import json

import pytest

from stele_context import __version__
from stele_context.cli import (
    cmd_clear,
    cmd_detect,
    cmd_index,
    cmd_search,
    cmd_stats,
    main,
)
from stele_context.cli_metadata import cmd_annotate, cmd_history, cmd_map
from stele_context.engine import Stele  # noqa: F401 (used by stele_engine fixture)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ns(**kwargs):
    """Build a minimal argparse.Namespace for a command."""
    defaults = {
        "output_json": False,
        "paths": [],
        "force": False,
        "session": "default",
        "confirm": True,
        "query": "",
        "top_k": 5,
        "target": None,
        "type": None,
        "content": None,
        "tags": None,
        "limit": 20,
        "document": None,
        "annotation_id": None,
        "storage_dir": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# 1. --version flag
# ---------------------------------------------------------------------------


class TestVersion:
    def test_version_flag_prints_version(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert __version__ in captured.out


# ---------------------------------------------------------------------------
# 2. --help exits 0
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_no_command_prints_help(self, capsys):
        rc = main([])
        assert rc == 0
        out = capsys.readouterr().out
        assert "stele-context" in out


# ---------------------------------------------------------------------------
# 3. cmd_index with a real temp file
# ---------------------------------------------------------------------------


class TestCmdIndex:
    def test_index_single_text_file(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "doc.txt"
        doc.write_text("Hello world. This is a test document.")
        stele = stele_engine
        args = _ns(paths=[str(doc)])
        rc = cmd_index(args, stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Indexed" in out
        assert str(doc) in out

    def test_index_python_file(self, tmp_path, capsys, stele_engine):
        src = tmp_path / "sample.py"
        src.write_text("def foo():\n    return 42\n")
        stele = stele_engine
        rc = cmd_index(_ns(paths=[str(src)]), stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "chunks" in out

    def test_index_nonexistent_file_returns_error(self, tmp_path, capsys, stele_engine):
        stele = stele_engine
        args = _ns(paths=[str(tmp_path / "missing.txt")])
        rc = cmd_index(args, stele)
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error" in err or "error" in err or "missing" in err.lower()

    def test_index_via_main(self, tmp_path, capsys):
        doc = tmp_path / "main_test.txt"
        doc.write_text("Content indexed via main()")
        rc = main(["--storage-dir", str(tmp_path / "storage"), "index", str(doc)])
        assert rc == 0


# ---------------------------------------------------------------------------
# 4. cmd_search with an indexed file
# ---------------------------------------------------------------------------


class TestCmdSearch:
    def test_search_after_indexing(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "code.py"
        doc.write_text(
            "def authenticate(user, password):\n    return user == 'admin'\n"
        )
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()  # discard index output

        args = _ns(query="authentication", top_k=5)
        rc = cmd_search(args, stele)
        assert rc == 0
        out = capsys.readouterr().out
        # Either results found or "No results found" — both valid
        assert len(out) > 0

    def test_search_empty_index(self, tmp_path, capsys, stele_engine):
        stele = stele_engine
        args = _ns(query="nothing here", top_k=3)
        rc = cmd_search(args, stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No results" in out

    def test_search_json_output(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "data.txt"
        doc.write_text("Some searchable text content for testing purposes.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        args = _ns(query="searchable text", top_k=5, output_json=True)
        rc = cmd_search(args, stele)
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# 5. cmd_stats output
# ---------------------------------------------------------------------------


class TestCmdStats:
    def test_stats_empty_store(self, tmp_path, capsys, stele_engine):
        stele = stele_engine
        rc = cmd_stats(_ns(), stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Stele Statistics" in out
        assert "Documents:" in out
        assert "Chunks:" in out

    def test_stats_after_indexing(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "file.txt"
        doc.write_text("Content for stats test.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        rc = cmd_stats(_ns(), stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Version:" in out
        assert __version__ in out

    def test_stats_via_main(self, tmp_path, capsys):
        rc = main(["--storage-dir", str(tmp_path / "storage"), "stats"])
        assert rc == 0
        assert "Stele Statistics" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 6. cmd_detect
# ---------------------------------------------------------------------------


class TestCmdDetect:
    def test_detect_no_changes(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "stable.txt"
        doc.write_text("Stable content that won't change.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        args = _ns(session="test-session", paths=[str(doc)])
        rc = cmd_detect(args, stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Detecting changes" in out

    def test_detect_modified_file(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "changing.txt"
        doc.write_text("Original content.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        doc.write_text("Modified content after initial indexing.")
        args = _ns(session="default", paths=[str(doc)])
        rc = cmd_detect(args, stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Cache:" in out


# ---------------------------------------------------------------------------
# 7. cmd_map output
# ---------------------------------------------------------------------------


class TestCmdMap:
    def test_map_empty(self, tmp_path, capsys, stele_engine):
        stele = stele_engine
        rc = cmd_map(_ns(), stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No indexed documents" in out

    def test_map_with_indexed_files(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "mapped.txt"
        doc.write_text("Content for the project map.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        rc = cmd_map(_ns(), stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Project Map" in out

    def test_map_json_output(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "json_map.txt"
        doc.write_text("JSON map test content.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        rc = cmd_map(_ns(output_json=True), stele)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert "documents" in parsed
        assert "total_documents" in parsed


# ---------------------------------------------------------------------------
# 8. cmd_clear clears data
# ---------------------------------------------------------------------------


class TestCmdClear:
    def test_clear_removes_indexed_data(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "to_clear.txt"
        doc.write_text("Data that will be cleared.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        # Verify data exists
        stats_before = stele.get_stats()
        assert stats_before["storage"]["chunk_count"] > 0

        rc = cmd_clear(_ns(confirm=True), stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Done" in out

        stats_after = stele.get_stats()
        assert stats_after["storage"]["chunk_count"] == 0


# ---------------------------------------------------------------------------
# 9. cmd_annotate creates annotation
# ---------------------------------------------------------------------------


class TestCmdAnnotate:
    def test_annotate_document(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "annotated.txt"
        doc.write_text("Document to annotate.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        args = _ns(
            target=str(doc),
            type="document",
            content="This is a test annotation",
            tags=["test", "demo"],
        )
        rc = cmd_annotate(args, stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Created annotation" in out

    def test_annotate_json_output(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "ann_json.txt"
        doc.write_text("Annotate with JSON output.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        capsys.readouterr()

        args = _ns(
            target=str(doc),
            type="document",
            content="JSON annotation",
            tags=None,
            output_json=True,
        )
        rc = cmd_annotate(args, stele)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert "id" in parsed
        assert "target" in parsed
        assert parsed["target_type"] == "document"


# ---------------------------------------------------------------------------
# 10. cmd_history displays entries
# ---------------------------------------------------------------------------


class TestCmdHistory:
    def test_history_empty(self, tmp_path, capsys, stele_engine):
        stele = stele_engine
        rc = cmd_history(_ns(limit=20, document=None), stele)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No change history" in out

    def test_history_after_detect(self, tmp_path, capsys, stele_engine):
        doc = tmp_path / "history_doc.txt"
        doc.write_text("Content for history test.")
        stele = stele_engine
        cmd_index(_ns(paths=[str(doc)]), stele)
        cmd_detect(_ns(session="default", paths=[str(doc)]), stele)
        capsys.readouterr()

        rc = cmd_history(_ns(limit=20, document=None), stele)
        assert rc == 0
        # History may or may not be populated depending on implementation
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_history_json_output(self, tmp_path, capsys, stele_engine):
        stele = stele_engine
        args = _ns(limit=20, document=None, output_json=True)
        rc = cmd_history(args, stele)
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert isinstance(parsed, list)


# ---------------------------------------------------------------------------
# 11. JSON output mode --json flag via main()
# ---------------------------------------------------------------------------


class TestJsonOutputMode:
    def test_search_json_via_main(self, tmp_path, capsys):
        doc = tmp_path / "search_json.txt"
        doc.write_text("JSON search output test content.")
        main(["--storage-dir", str(tmp_path / "storage"), "index", str(doc)])
        capsys.readouterr()

        rc = main(
            [
                "--storage-dir",
                str(tmp_path / "storage"),
                "search",
                "JSON search output",
                "--json",
            ]
        )
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert isinstance(parsed, list)

    def test_map_json_via_main(self, tmp_path, capsys):
        rc = main(["--storage-dir", str(tmp_path / "storage"), "map", "--json"])
        assert rc == 0
        parsed = json.loads(capsys.readouterr().out)
        assert "documents" in parsed


# ---------------------------------------------------------------------------
# 12. Error cases
# ---------------------------------------------------------------------------


class TestErrorCases:
    def test_index_nonexistent_path(self, tmp_path, capsys):
        rc = main(
            [
                "--storage-dir",
                str(tmp_path / "storage"),
                "index",
                str(tmp_path / "does_not_exist.txt"),
            ]
        )
        assert rc == 1

    def test_annotate_missing_required_args(self, tmp_path):
        # argparse should raise SystemExit when --type/--content are missing
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--storage-dir",
                    str(tmp_path / "storage"),
                    "annotate",
                    "some_target",
                ]
            )
        assert exc_info.value.code != 0

    def test_update_annotation_no_content_or_tags(self, tmp_path, capsys, stele_engine):
        stele = stele_engine
        from stele_context.cli_metadata import cmd_update_annotation

        args = _ns(annotation_id=1, content=None, tags=None)
        rc = cmd_update_annotation(args, stele)
        assert rc == 1
        err = capsys.readouterr().err
        assert "content" in err or "tags" in err

    def test_unknown_command_via_parser(self, tmp_path):
        # argparse rejects unknown subcommands with SystemExit
        with pytest.raises(SystemExit) as exc_info:
            main(["--storage-dir", str(tmp_path / "storage"), "nonexistent-cmd"])
        assert exc_info.value.code != 0
