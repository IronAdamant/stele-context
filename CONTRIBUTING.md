# Contributing to Stele Context

Thank you for your interest in contributing to Stele Context. This document is the contributor guide.

The product is the Rust binary. The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x).

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

- Rust stable (1.83 or newer; CI uses stable)
- Git

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/stele-context.git
cd stele-context
cargo build --release
./target/release/stele-context --version
```

### Verify Installation

```bash
cargo fmt --check
cargo test
./target/release/stele-context --version
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
cargo test
cargo test --lib round_trip
bash scripts/fetch_corpora.sh
cargo test
```

The fetch step is optional. Corpus round-trip tests skip when `parity/.cache` is absent, and they run when it is present.

`cargo test` runs the parity tests against an installed interpreter. That interpreter needs `python3`, `stele-context==1.5.2`, and PyMuPDF (`pymupdf`). The tests skip the live comparison when those imports are missing.

### Writing Tests

- Put unit tests in the module they cover (`#[cfg(test)]`).
- Fixture bytes stay in `parity/fixtures`. Goldens stay in `parity/goldens`.
- Do not add a crate to the `[dependencies]` tables.

## Code Style

```bash
cargo fmt
cargo fmt --check
```

### Principles

1. **No crates.** The standard library is the runtime.
2. **Offline first.** The cache path does not call the network. Corpus download is a test helper only.
3. **Compatible CLI.** Do not break `stele-context` commands or MCP tool names inside 2.x.
4. **Tested.** A behaviour change needs a test, usually against `parity/goldens` when the bytes matter.

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
- Stele Context version (`stele-context --version`)
- Operating system and CPU
- Steps to reproduce
- Expected behaviour
- Actual behaviour
- Error messages

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

Thank you for contributing to Stele Context.

## Releases

Rust 2.x tags run `.github/workflows/release-rust.yml` and attach binaries to a GitHub Release. They do not publish to PyPI.

A 1.x tag is cut from `python-legacy`. `publish.yml` on that branch uploads the sdist. See [docs/release-2.0.md](docs/release-2.0.md).

## Local build artifacts

`target/` is gitignored. Do not commit it.
