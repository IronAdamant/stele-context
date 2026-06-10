"""Tests for diff-since-cache (context_diff.py + get_context integration)."""

from __future__ import annotations

import hashlib
import os

from stele_context.context_diff import (
    build_change_diff,
    reconstruct_cached_text,
)
from stele_context.engine import Stele


def _chunks_for(text: str, pieces: list[tuple[int, int]]) -> list[dict]:
    """Build chunk dicts slicing *text* at the given (start, end) offsets."""
    return [{"content": text[s:e], "start_pos": s, "end_pos": e} for s, e in pieces]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TestReconstructCachedText:
    def test_contiguous_chunks_rebuild_exactly(self):
        text = "line one\nline two\nline three\n"
        chunks = _chunks_for(text, [(0, 9), (9, 18), (18, len(text))])
        assert reconstruct_cached_text(chunks) == text

    def test_out_of_order_chunks_are_sorted(self):
        text = "abcdef"
        chunks = _chunks_for(text, [(3, 6), (0, 3)])
        assert reconstruct_cached_text(chunks) == text

    def test_stripped_trailing_whitespace_restored_from_end_pos(self):
        # Chunkers strip content but end_pos spans the raw region.
        text = "def f():\n    pass\n"
        chunks = [{"content": "def f():\n    pass", "start_pos": 0, "end_pos": 18}]
        assert reconstruct_cached_text(chunks) == text

    def test_blank_line_seam_between_regions_restored(self):
        text = "def a():\n    pass\n\ndef b():\n    pass\n"
        chunks = [
            {"content": "def a():\n    pass", "start_pos": 0, "end_pos": 19},
            {"content": "def b():\n    pass", "start_pos": 19, "end_pos": len(text)},
        ]
        assert reconstruct_cached_text(chunks) == text

    def test_empty_chunks_returns_none(self):
        assert reconstruct_cached_text([]) is None

    def test_binary_content_returns_none(self):
        assert reconstruct_cached_text([{"content": b"\x00", "start_pos": 0}]) is None


class TestBuildChangeDiff:
    def test_exact_diff_for_small_edit(self):
        old = "def hello():\n    return 'world'\n"
        new = "def hello():\n    return 'planet'\n"
        chunks = _chunks_for(old, [(0, len(old))])
        diff = build_change_diff(chunks, _hash(old), new)
        assert diff is not None
        assert diff["diff_exact"] is True
        assert diff["added_lines"] == 1
        assert diff["removed_lines"] == 1
        assert "-    return 'world'" in diff["diff"]
        assert "+    return 'planet'" in diff["diff"]
        assert diff["diff_truncated"] is False

    def test_newline_gap_between_chunks_is_recovered(self):
        old = "alpha\n\nbeta\n"
        # Blank line lost from content but recoverable from the offset gap.
        chunks = [
            {"content": "alpha\n", "start_pos": 0},
            {"content": "beta\n", "start_pos": 7},
        ]
        diff = build_change_diff(chunks, _hash(old), "alpha\n\ngamma\n")
        assert diff is not None
        assert diff["diff_exact"] is True
        assert diff["added_lines"] == 1
        assert diff["removed_lines"] == 1

    def test_legacy_missing_trailing_newline_is_recovered(self):
        # Legacy rows: last chunk's end_pos used the stripped length, so
        # the file's final newline isn't in the offsets — the +"\n"
        # candidate catches it.
        old = "def f():\n    pass\n"
        chunks = [{"content": "def f():\n    pass", "start_pos": 0, "end_pos": 17}]
        diff = build_change_diff(chunks, _hash(old), "def f():\n    return 1\n")
        assert diff is not None
        assert diff["diff_exact"] is True

    def test_inexact_reconstruction_is_flagged(self):
        # Separator was a space, not newlines — genuinely unrecoverable.
        old = "alpha beta\n"
        chunks = [
            {"content": "alpha", "start_pos": 0, "end_pos": 5},
            {"content": "beta\n", "start_pos": 6, "end_pos": 11},
        ]
        diff = build_change_diff(chunks, _hash(old), "alpha gamma\n")
        assert diff is not None
        assert diff["diff_exact"] is False

    def test_binary_disk_content_returns_none(self):
        chunks = _chunks_for("text", [(0, 4)])
        assert build_change_diff(chunks, _hash("text"), b"\x00\x01") is None

    def test_no_chunks_returns_none(self):
        assert build_change_diff([], _hash("x"), "x") is None

    def test_truncation_and_reread_recommendation(self):
        old = "\n".join(f"old line {i}" for i in range(300))
        new = "\n".join(f"new line {i}" for i in range(300))
        chunks = _chunks_for(old, [(0, len(old))])
        diff = build_change_diff(chunks, _hash(old), new, max_diff_tokens=50)
        assert diff is not None
        assert diff["diff_truncated"] is True
        assert diff["recommendation"] == "reread_file"
        assert diff["diff_tokens"] <= 50

    def test_small_diff_recommends_read_diff(self):
        old = "\n".join(f"stable line number {i}" for i in range(100))
        new = old.replace("stable line number 50", "edited line number 50")
        chunks = _chunks_for(old, [(0, len(old))])
        diff = build_change_diff(chunks, _hash(old), new)
        assert diff is not None
        assert diff["recommendation"] == "read_diff"


class TestGetContextDiffIntegration:
    def _touch_mtime(self, path: str) -> None:
        """Force a different mtime so the fast-path doesn't mask the change."""
        st = os.stat(path)
        os.utime(path, (st.st_atime, st.st_mtime + 10))

    def test_changed_file_carries_diff_since_cache(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "mod.py"
        f.write_text("def hello():\n    return 'world'\n")
        engine.index_documents([str(f)])

        f.write_text("def hello():\n    return 'planet'\n")
        self._touch_mtime(str(f))

        result = engine.get_context([str(f)])
        assert len(result["changed"]) == 1
        entry = result["changed"][0]
        assert "diff_since_cache" in entry
        diff = entry["diff_since_cache"]
        assert "+    return 'planet'" in diff["diff"]
        assert diff["diff_exact"] is True
        assert diff["recommendation"] in ("read_diff", "reread_file")

    def test_multi_function_file_small_edit_has_no_phantom_changes(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "big.py"
        funcs = [
            f"def func_{i}():\n    return 'stable value number {i}'\n\n"
            for i in range(40)
        ]
        f.write_text("".join(funcs))
        engine.index_documents([str(f)])

        f.write_text(
            f.read_text().replace("stable value number 20", "EDITED value number 20")
        )
        self._touch_mtime(str(f))

        diff = engine.get_context([str(f)])["changed"][0]["diff_since_cache"]
        assert diff["diff_exact"] is True
        assert diff["added_lines"] == 1
        assert diff["removed_lines"] == 1
        assert diff["recommendation"] == "read_diff"

    def test_include_diff_false_omits_diff(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "mod.py"
        f.write_text("def hello():\n    return 'world'\n")
        engine.index_documents([str(f)])

        f.write_text("def hello():\n    return 'planet'\n")
        self._touch_mtime(str(f))

        result = engine.get_context([str(f)], include_diff=False)
        assert len(result["changed"]) == 1
        assert "diff_since_cache" not in result["changed"][0]

    def test_unchanged_file_has_no_diff(self, stele_engine_with_file):
        engine, path = stele_engine_with_file
        result = engine.get_context([path])
        assert result["changed"] == []
        assert len(result["unchanged"]) == 1

    def test_max_diff_tokens_is_respected(self, tmp_path):
        engine = Stele(storage_dir=str(tmp_path / "storage"))
        f = tmp_path / "big.txt"
        old = "\n".join(f"original content line {i}" for i in range(200))
        f.write_text(old)
        engine.index_documents([str(f)])

        f.write_text("\n".join(f"rewritten content line {i}" for i in range(200)))
        self._touch_mtime(str(f))

        result = engine.get_context([str(f)], max_diff_tokens=40)
        diff = result["changed"][0]["diff_since_cache"]
        assert diff["diff_truncated"] is True
        assert diff["diff_tokens"] <= 40
