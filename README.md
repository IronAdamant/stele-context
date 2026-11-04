# Stele Context

**Persistent memory for AI coding agents. A zero-dependency MCP server that lets Claude Code, Claude Desktop, Cursor, and any MCP-compatible assistant remember your codebase between conversations.**

The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x).

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Rust 1.83+](https://img.shields.io/badge/rust-1.83+-orange.svg)](https://www.rust-lang.org/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)](https://github.com/IronAdamant/stele-context)
[![CI](https://github.com/IronAdamant/stele-context/actions/workflows/rust.yml/badge.svg)](https://github.com/IronAdamant/stele-context/actions/workflows/rust.yml)

## The problem

Every new conversation with Claude Code, Cursor, or another coding agent starts from zero. The agent re-reads the same files it read yesterday. On a medium-sized project that spends real tokens and real context on code that has not changed.

Stele Context is a local context cache: index once, then pay only for what changed.

## What it does

1. **Indexes your project once.** Code, docs, configs, images, audio, video, and PDF go into a local SQLite database. Media is decoded with the Rust standard library. There is no optional native library.
2. **Detects file changes** with an mtime and size fast path, then SHA-256. Unchanged files cost nothing to re-read.
3. **Returns a diff instead of the whole file** when something did change.
4. **Searches** by keyword (BM25, the default), hybrid rank when you supply Tier 2 summaries, or exact text.
5. **Maps symbols** for find-definition, find-references, impact, and coupling.

Everything runs offline. No API keys, no cloud, no telemetry. The crate has an empty `[dependencies]` table.

## Quick start

### Install

The product is the Rust 2.0.0 binary from the [v2.0.0 GitHub Release](https://github.com/IronAdamant/stele-context/releases/tag/v2.0.0).

```bash
curl -fsSL https://raw.githubusercontent.com/IronAdamant/stele-context/v2.0.0/scripts/install.sh | sh
```

Windows: `scripts/install.ps1`, or download `stele-context-<target>` from the release assets. Build from this repo with `cargo build --release`. The binaries are `stele-context` and `stele-context-mcp`.

### First run

```bash
stele-context init .
```

This indexes the project (respecting `.gitignore`), prints an MCP lite client config, and shows `doctor` next steps.

### Index and search

```bash
stele-context index src/ docs/ README.md
stele-context search "how does authentication work"
stele-context search-text "TODO"
stele-context agent-grep "createApp"
```

The index lives in `.stele-context/` at the project root.

### Connect an MCP client

```bash
stele-context serve-mcp
```

**Claude Code** (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "stele-context": {
      "command": "stele-context",
      "args": ["serve-mcp"],
      "env": { "STELE_MCP_MODE": "lite" }
    }
  }
}
```

**Claude Desktop** uses the same shape in `~/.config/Claude/claude_desktop_config.json` (Linux or Mac) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). Cursor and other MCP clients use the same command. `stele-context init` prints a snippet.

Default mode is **lite**. Set `STELE_MCP_MODE=standard` or `full` for the wider tool list. `stele-context-mcp` is the same server, selected by the binary name.

### Agent ritual

```
doctor -> query (session_id) -> agent_grep / find_* -> get_context (trust/diff)
```

After edits: `detect`. For better hybrid search: `enrichment-plan`, then store summaries with the MCP tools.

## Configuration

A `.stele-context.toml` in the project root is also the project-root marker. Stele walks upward and stops at the nearest `.git` or `.stele-context.toml`. An empty file is enough for a folder that is not a git checkout. With neither, storage falls back to `~/.stele-context/`.

```toml
[stele-context]
storage_dir = "/var/tmp/stele-index"
```

`storage_dir` is applied. `chunk_size`, `skip_dirs`, and `respect_gitignore` are read and not applied: chunks stay at 256, and the walk uses the fixed skip list plus gitignore. The full key list is in [MIGRATION.md](MIGRATION.md#configuration-keys).

You can also set `STELE_CONTEXT_STORAGE_DIR` or pass `--storage-dir`. Flag, then config, then the environment variable, then the default.

## Supported files

Code and text: `.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.kt`, `.scala`, `.c`, `.cpp`, `.cs`, `.go`, `.rs`, `.rb`, `.php`, `.swift`, `.sh`, `.sql`, `.html`, `.css`, `.json`, `.yaml`, `.toml`, `.ini`, `.md`, `.txt`, `.rst`, `.csv`, `.log`, and the other extensions in `src/walk.rs`.

Media, with no extra packages: PNG, JPEG, GIF, WAV, MP3, MP4, and PDF. Image and audio chunks are header metadata. PDF text is taken from the file bytes.

## Troubleshooting

**MCP server not connecting.** Use the full path to the binary.

**A lock is stuck.** Use the `document_lock` tool to reap expired locks.

**Cached text looks stale.** `doctor` and `get_context` report trust. Re-index with `stele-context index <file>`. `detect` lists changed files and indexes new ones.

## FAQ

**Does this work with Cursor and other MCP tools?** Yes. Any MCP client can launch `stele-context serve-mcp`.

**Does it replace grep?** No. Use grep for a one-off lookup. Stele remembers what was indexed, what changed, and how symbols connect.

**Does it need a network or an API key?** No. Summaries and embedding vectors are supplied by the caller. No model is bundled.

**Is the code local?** Yes. The index stays on the machine. There is no dependency crate and no pickle.

**Can several agents share an index?** Yes. Document locks and a conflict log cover one index, including across git worktrees. The shared file is `<git-common-dir>/stele-context/coordination.json`.

## Migrating from the Python version

stele-context 1.5.x on PyPI is the Python package. 2.0.0 is this Rust binary. The Python source stays on the `python-legacy` branch. `pip install stele-context` does not install 2.0.

Install 2.0 from the [v2.0.0 GitHub Release](https://github.com/IronAdamant/stele-context/releases/tag/v2.0.0): `scripts/install.sh`, `scripts/install.ps1`, or the release assets. Those assets cover Linux x86_64 (musl) and aarch64, macOS Apple silicon and Intel, and Windows x86_64 and Arm. `install.sh` and `install.ps1` install `stele-context`. `stele-context serve-mcp` is the MCP server. `stele-context-mcp-<target>` is the same server under the other file name.

A storage directory that only has a 1.5 `stele_context.db` is copied into the native store on open. The original database is not modified. `chunk_size`, `skip_dirs`, and `respect_gitignore` in `.stele-context.toml` are applied.

Chunk and symbol rows on `parity/goldens/fixtures.json` match 1.5.2 with tree-sitter off and numpy not loaded. Media chunk bodies are metadata, not the optional Pillow, librosa, OpenCV, or PyMuPDF bytes. The one Python defect 2.0 does not copy is the 1.5.1 PDF open: that release decoded the file as text and PyMuPDF raised `FileNotFoundError` for a path starting `%PDF-1.7`. 1.5.2 and 2.0 read the bytes. Page text is still not the PyMuPDF string.

Flags, JSON envelopes, MCP schemas, locks, HTTP, and removals are listed in [MIGRATION.md](MIGRATION.md). Each item is marked same, intentional change, bug fixed from Python, or not supported.

## Benchmarks

Recorded on Linux 6.12.94+ x86_64, 4 vCPU, rustc 1.99.1, release build, 5 runs, rich 13.9.4 (496 documents after image indexing). The Python column is the recorded 1.5.1 floor from an 8 vCPU host (`bench/baseline/summary.json`). Every Rust median is strictly below that floor. CI re-checks startup with `stele-bench floor --group b1 --check-floor`.

| Command | Rust median | Rust min-max | Peak RSS | Python 1.5.1 median |
|---|---:|---:|---:|---:|
| `cli --version (startup)` | 1.8 ms | 1.7-2.5 | 10.5 MB | 79.4 ms |
| `cli --help` | 1.7 ms | 1.7-1.8 | 10.6 MB | 75.7 ms |
| `cli index . (cold, empty store)` | 8849.8 ms | 8823.0-8860.3 | 82.8 MB | 10656.1 ms |
| `cli index . (warm, nothing changed)` | 72.6 ms | 71.7-75.8 | 69.6 MB | 197.8 ms |
| `cli detect (nothing changed)` | 66.7 ms | 66.4-66.9 | 69.7 MB | 167.9 ms |
| `cli stats` | 62.7 ms | 62.7-62.8 | 69.6 MB | 162.5 ms |
| `cli doctor` | 50.6 ms | 50.5-51.2 | 52.0 MB | 189.7 ms |
| `cli map --json` | 97.5 ms | 97.2-97.7 | 69.6 MB | 163.3 ms |
| `cli project-brief --json` | 61.4 ms | 61.2-62.2 | 69.7 MB | 165.5 ms |
| `cli search hybrid --json` | 72.2 ms | 72.1-72.3 | 69.6 MB | 169.0 ms |
| `cli search keyword --json` | 71.9 ms | 71.5-72.3 | 69.6 MB | 161.2 ms |
| `cli search-text --json` | 62.5 ms | 62.0-62.9 | 69.6 MB | 141.9 ms |
| `cli agent-grep --json` | 63.9 ms | 63.7-93.6 | 69.7 MB | 228.3 ms |
| `cli query --json` | 73.3 ms | 72.9-74.0 | 69.7 MB | 193.5 ms |
| `cli find-definition --json` | 61.8 ms | 61.5-62.2 | 69.6 MB | 141.4 ms |
| `cli find-references --json` | 63.7 ms | 63.3-64.0 | 69.7 MB | 166.2 ms |
| `cli impact-radius --json` | 66.9 ms | 66.7-67.4 | 69.7 MB | 179.6 ms |
| `cli coupling --json` | 63.4 ms | 63.0-63.5 | 69.7 MB | 185.3 ms |
| `cli enrichment-plan --json` | 62.4 ms | 62.0-68.9 | 69.7 MB | 137.7 ms |
| `cli history --json` | 61.7 ms | 61.2-61.9 | 69.7 MB | 135.1 ms |

## Development

```bash
cargo fmt --check
cargo test
bash scripts/fetch_corpora.sh   # optional rich and flask round-trip corpus
cargo build --release
./target/release/stele-bench floor \
  --binary ./target/release/stele-context --group b1 --runs 10 --check-floor
```

`[dependencies]` and `[dev-dependencies]` stay empty. `cargo test` includes `cargo_manifest_and_lock_are_std_only`, which fails if `Cargo.lock` lists anything other than this crate.

Rust 2.x tags run `.github/workflows/release-rust.yml` and attach binaries to a GitHub Release. They do not publish to PyPI. A future 1.x tag is cut from `python-legacy`, where `publish.yml` still lives.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
