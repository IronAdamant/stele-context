# Stele Context - Complete Project Documentation

**Last updated:** 2026-11-04. **Release:** Rust crate 2.0.0.

The product is the std-only Rust binary. The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x).

## Documentation

| Path | Purpose |
|------|---------|
| `README.md` | Install, MCP, and the benchmark table |
| `MIGRATION.md` | 1.5.x Python to 2.0 Rust: flags, store, MCP, media |
| `AGENTS.md` | How agents should use the tools |
| `CLAUDE.md` | Crate map and invariants |
| `CONTRIBUTING.md` | Rust setup and tests |
| `llms.txt` | Short product card |
| `CHANGELOG.md` | Release history, including 1.x |
| `STABILITY.md` | CLI and MCP stability. The class list is the 1.x API |
| `docs/philosophy.md` | Why there are no crates, Tier 1 vs Tier 2 |
| `docs/architecture.md` | Behaviour the port keeps. 1.x module names are historical |
| `docs/agent-workflow.md` | Index, enrich, retrieve |
| `docs/release-2.0.md` | v2 tags and v1 tags from `python-legacy` |
| `docs/release-automation.md` | 1.x release notes, with a pointer to the Rust workflow |

## Tree

| Path | Purpose |
|------|---------|
| `Cargo.toml`, `Cargo.lock` | Crate `stele-context` 2.0.0. No dependencies |
| `src/` | Library, `stele-context`, `stele-context-mcp` |
| `src/bin/stele-bench/` | B1 floor and D13 gate. Not shipped in the GitHub Release |
| `src/engine/` | Index, detect, search, query, locks |
| `src/chunk/`, `src/search/`, `src/store/` | Chunks, BM25, on-disk state |
| `src/symbols/`, `src/lang/`, `src/syntax/` | Extractors and lossless scanners |
| `src/media/`, `src/mcp/`, `src/http_serve.rs` | Media, stdio MCP, local HTTP |
| `parity/fixtures/` | Small files in many languages, including media. Rust tests read them |
| `parity/goldens/` | `fixtures.json`, `media.json`, `corpora.json`, `extensions.json` |
| `parity/.cache/` | Gitignored rich and flask trees from `scripts/fetch_corpora.sh` |
| `bench/baseline/` | Python 1.5.1 floor (`summary.json`, `raw.jsonl`) |
| `bench/rust-b1.json`, `bench/rust-rich.json` | Recorded Rust timings |
| `scripts/install.sh`, `scripts/install.ps1` | Install a release binary |
| `scripts/fetch_corpora.sh` | Download pinned corpora for tests |
| `npm/stele-context/` | Optional Node launcher. Not published by the tag workflows |
| `server.json`, `.mcp.json` | MCP registry and a local client example |
| `.github/workflows/rust.yml` | Format, zero-deps, tests, B1 floor. Linux, macOS, Windows |
| `.github/workflows/benchmark-gate.yml` | D13 via `stele-bench gate` |
| `.github/workflows/release-rust.yml` | `v2.*` GitHub Release binaries |
| `release/pypi-1.5.3/NOTICE.md` | Record of the 1.5.3 pointer release |

`publish.yml` is not on this branch. It is on `python-legacy`.
