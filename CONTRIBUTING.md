# Contributing to Stele Context

Thank you for your interest in contributing to Stele Context! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Code Style](#code-style)
- [Submitting Changes](#submitting-changes)
- [Reporting Issues](#reporting-issues)

## Code of Conduct

This project adheres to a Code of Conduct. By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally
3. Create a branch for your changes
4. Make your changes
5. Test your changes
6. Submit a pull request

## Development Setup

### Prerequisites

- Python 3.9 or higher
- Git

### Installation

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/stele-context.git
cd stele-context

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode with dev dependencies
pip install -e ".[dev]"
```

### Verify Installation

```bash
# Run tests
pytest

# Check code style
mypy stele_context/

# Run CLI
stele-context --version
```

## Making Changes

### Branch Naming

Use descriptive branch names:
- `feature/add-vector-index`
- `fix/empty-file-handling`
- `docs/update-readme`

### Commit Messages

Write clear, concise commit messages:
```
Add vector index for fast similarity search

- Implement HNSW-based vector index
- Add search method with k-nearest neighbors
- Include unit tests for index operations
```

### What to Contribute

We welcome contributions in these areas:

1. **Bug Fixes** - Fix issues reported in GitHub Issues
2. **Features** - Implement features from the roadmap
3. **Tests** - Add test coverage
4. **Documentation** - Improve docs, add examples
5. **Performance** - Optimize slow operations
6. **Code Quality** - Refactor, improve readability

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=stele_context --cov-report=html

# Run specific test file
pytest tests/test_core.py

# Run specific test
pytest tests/test_core.py::TestStele::test_index_documents
```

### Writing Tests

- Place tests in the `tests/` directory
- Mirror the source structure: `tests/test_core.py` for `stele_context/core.py`
- Use pytest fixtures for common setup
- Aim for >90% coverage on new code

Example test:

```python
import pytest
from stele_context import Stele

def test_index_documents(tmp_path):
    """Test document indexing."""
    # Create test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello, world!")
    
    # Index document
    cf = Stele(storage_dir=str(tmp_path / "storage"))
    result = cf.index_documents([str(test_file)])
    
    # Verify
    assert result["total_chunks"] == 1
    assert result["total_tokens"] > 0
```

## Code Style

### Type Hints

All functions must have type hints:

```python
def process_chunk(
    self,
    chunk_id: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Process a chunk and return success status and message."""
    ...
```

### Docstrings

Use Google-style docstrings:

```python
def index_documents(
    self,
    paths: List[str],
    force_reindex: bool = False,
) -> Dict[str, Any]:
    """
    Index one or more documents.

    Performs dynamic semantic chunking and stores chunk metadata.
    Skips unchanged documents unless force_reindex is True.

    Args:
        paths: List of document paths to index
        force_reindex: Force re-indexing even if document hasn't changed

    Returns:
        Dictionary with indexing results containing:
        - indexed: List of successfully indexed documents
        - skipped: List of skipped unchanged documents
        - errors: List of errors encountered
        - total_chunks: Total number of chunks created
        - total_tokens: Total number of tokens

    Raises:
        FileNotFoundError: If a document path doesn't exist
        PermissionError: If a document can't be read
    """
    ...
```

### Code Formatting

We use:
- **Black** for code formatting
- **isort** for import sorting
- **mypy** for type checking
- **ruff** for linting

```bash
# Format code
black stele_context/ tests/
isort stele_context/ tests/

# Check types
mypy stele_context/

# Lint
ruff check stele_context/ tests/
```

### Principles

1. **Minimal Dependencies** - Prefer stdlib over external packages
2. **Offline First** - Never require network access
3. **Backward Compatible** - Don't break existing APIs
4. **Well Documented** - Every public function needs a docstring
5. **Tested** - Every feature needs tests

## Submitting Changes

### Pull Request Process

1. Update documentation if needed
2. Add tests for new functionality
3. Ensure all tests pass
4. Update CHANGELOG.md
5. Submit pull request with clear description

### Pull Request Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Tests pass locally
- [ ] Added tests for new functionality
- [ ] Coverage maintained/improved

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
```

### Review Process

1. Automated checks must pass (CI/CD)
2. At least one maintainer review required
3. Address review feedback
4. Maintainer merges when approved

## Reporting Issues

### Bug Reports

Include:
- Stele Context version
- Python version
- Operating system
- Steps to reproduce
- Expected behavior
- Actual behavior
- Error messages/stack traces

### Feature Requests

Include:
- Use case description
- Proposed solution
- Alternatives considered
- Additional context

## Development Guidelines

### Adding New Features

1. Create an issue first to discuss
2. Get approval from maintainers
3. Implement with tests
4. Update documentation
5. Submit PR

### Modifying Core Logic

- Discuss in issue first
- Maintain backward compatibility
- Add migration guide if breaking
- Update all affected tests

### Performance Changes

- Include benchmarks
- Document performance impact
- Test on various file sizes
- Consider memory usage

## Questions?

- Open a GitHub Issue for questions
- Join discussions in existing issues
- Reach out to maintainers

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to Stele Context! 🚀
