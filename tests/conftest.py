"""Shared test fixtures for ChunkForge tests."""

import pytest
import tempfile
from pathlib import Path


@pytest.fixture
def tmp_storage_dir(tmp_path):
    """Create a temporary storage directory."""
    storage_dir = tmp_path / "chunkforge_storage"
    storage_dir.mkdir()
    return str(storage_dir)


@pytest.fixture
def sample_text_file(tmp_path):
    """Create a sample text file for testing."""
    test_file = tmp_path / "sample.txt"
    test_file.write_text("""
# Sample Document

This is a sample document for testing ChunkForge.

## Features

- Dynamic semantic chunking
- Hybrid indexing
- KV-cache persistence

## Code Example

def hello():
    print('Hello, World!')

## Conclusion

ChunkForge is awesome!
""".strip())
    return str(test_file)


@pytest.fixture
def sample_python_file(tmp_path):
    """Create a sample Python file for testing."""
    test_file = tmp_path / "sample.py"
    test_file.write_text("""
#!/usr/bin/env python3
\"\"\"Sample Python module for testing.\"\"\"

import os
import sys
from typing import List, Optional


def greet(name: str) -> str:
    \"\"\"Return a greeting message.\"\"\"
    return f"Hello, {name}!"


class Calculator:
    \"\"\"A simple calculator class.\"\"\"
    
    def __init__(self):
        self.history: List[float] = []
    
    def add(self, a: float, b: float) -> float:
        \"\"\"Add two numbers.\"\"\"
        result = a + b
        self.history.append(result)
        return result
    
    def subtract(self, a: float, b: float) -> float:
        \"\"\"Subtract two numbers.\"\"\"
        result = a - b
        self.history.append(result)
        return result


if __name__ == "__main__":
    calc = Calculator()
    print(calc.add(5, 3))
    print(calc.subtract(10, 4))
""".strip())
    return str(test_file)


@pytest.fixture
def sample_markdown_file(tmp_path):
    """Create a sample Markdown file for testing."""
    test_file = tmp_path / "sample.md"
    test_file.write_text("""
# ChunkForge Documentation

## Introduction

ChunkForge is a local KV-cache management system.

## Installation

```bash
pip install chunkforge
```

## Usage

```python
from chunkforge import ChunkForge

cf = ChunkForge()
cf.index_documents(["document.py"])
```

## Features

1. **Dynamic Chunking**: Intelligent text splitting
2. **Change Detection**: Hash + semantic comparison
3. **KV Persistence**: SQLite + filesystem storage

## API Reference

### index_documents(paths: List[str])

Index one or more documents.

### detect_changes_and_update(session_id: str)

Detect changes and update KV-cache.

## License

MIT License
""".strip())
    return str(test_file)
