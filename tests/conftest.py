"""Shared pytest fixtures for Stele Context tests."""

from __future__ import annotations

import pytest

from stele_context.engine import Stele


@pytest.fixture
def stele_engine(tmp_path):
    """Create a basic Stele engine with a temporary storage directory."""
    return Stele(storage_dir=str(tmp_path / "storage"))


@pytest.fixture
def stele_engine_with_file(tmp_path):
    """Create a Stele engine with one indexed Python file.

    Returns (engine, file_path) tuple.
    """
    engine = Stele(storage_dir=str(tmp_path / "storage"))
    f = tmp_path / "test.py"
    f.write_text("def hello():\n    return 'world'\n")
    engine.index_documents([str(f)])
    return engine, str(f)


@pytest.fixture
def stele_engine_with_data(tmp_path):
    """Create a Stele engine with multiple indexed text files.

    Returns the engine with 3 indexed documents.
    """
    engine = Stele(storage_dir=str(tmp_path / "storage"))
    for i in range(3):
        f = tmp_path / f"test_{i}.txt"
        f.write_text(
            f"This is test document number {i} with some unique content about topic_{i}."
        )
    engine.index_documents([str(tmp_path / f"test_{i}.txt") for i in range(3)])
    return engine
