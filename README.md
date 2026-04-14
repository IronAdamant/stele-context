# Stele Context

**Persistent memory for AI coding agents. An MCP server that helps Claude Code, Claude Desktop, Cursor, and other AI tools remember your codebase between conversations.**

[![PyPI](https://img.shields.io/pypi/v/stele-context.svg)](https://pypi.org/project/stele-context/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-green.svg)](https://github.com/IronAdamant/stele-context)
[![Tests](https://github.com/IronAdamant/stele-context/actions/workflows/test.yml/badge.svg)](https://github.com/IronAdamant/stele-context/actions)

## The Problem

Every time you start a new conversation with Claude Code, Cursor, or any AI coding assistant, it has to read your files from scratch. For a medium-sized project, that's thousands of tokens spent re-reading code that hasn't changed since last time. Your AI agent has no memory of what it already knows about your project.

## What Stele Context Does

Stele Context gives your AI coding agent persistent memory across conversations. It:

1. **Indexes your project files** once — code, docs, configs, even images and PDFs
2. **Detects what changed** since last time — only changed files get re-read
3. **Searches your code** by meaning or keywords, not just filenames
4. **Tracks how your code connects** — knows which files import from which, so it can tell your agent "if you change this file, these other files might break"

Everything runs **locally on your machine**. No internet, no API calls, no cloud. Just Python and SQLite.

![Semantic search demo](docs/semantic-search-demo.png)

## Quick Start

### Install

```bash
pip install stele-context
```

### Index your project

```bash
stele-context index src/ docs/ README.md
```

This reads your files, breaks them into meaningful chunks, and stores them locally in a `.stele-context/` folder in your project.

### Search your code

```bash
stele-context search "how does authentication work"
stele-context search "database connection" --top-k 10
```

### Connect it to Claude Code or Claude Desktop

Stele Context works as an [MCP server](https://modelcontextprotocol.io/) — a plugin that gives your AI agent extra tools.

```bash
pip install stele-context[mcp]
```

**Claude Code** — add to `~/.claude/settings.json`:
```json
{
  "mcpServers": {
    "stele-context": {
      "command": "stele-context",
      "args": ["serve-mcp"]
    }
  }
}
```

**Claude Desktop** — add to `~/.config/Claude/claude_desktop_config.json` (Linux/Mac) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):
```json
{
  "mcpServers": {
    "stele-context": {
      "command": "stele-context",
      "args": ["serve-mcp"]
    }
  }
}
```

> **Tip:** If you installed in a virtualenv, use the full path: run `which stele-context` to find it.

Once connected, your agent gets **42 tools** for searching, indexing, and navigating your code — it'll use them automatically when they're helpful. (Set `STELE_MCP_MODE=lite` for ~15 essential tools, or `STELE_MCP_MODE=full` for the complete surface.)

## Who Is This For?

- You use **Claude Code**, **Claude Desktop**, **Cursor**, or another AI coding tool
- You're tired of your agent re-reading the same files at the start of every conversation
- You want your AI to **remember your codebase** and know how your code connects
- You want a **code search tool** that understands your project, not just filenames
- You want something that runs **100% offline** with **no API keys** and **no cloud**

If you've ever wished your AI coding assistant had a persistent memory for your project, that's what this does.

## What Can It Do?

### For everyday use

| What you want | How Stele helps |
|---------------|-----------------|
| "Don't re-read files that haven't changed" | `get_context` returns cached content for unchanged files, only re-reads modified ones |
| "Ask a broad question about my code"" | `query` combines semantic search, symbol graph, and text grep into one deduplicated result list |
| "What files would break if I change this?" | `impact_radius` follows the dependency chain to find affected files; `significance_threshold` filters out noise from common symbols like `push`/`addEdge`. Also works with `symbol=` for dynamic/runtime hooks |
| "Which files are tightly coupled?" | `coupling` shows shared symbols with a `semantic_score` that discounts generic boilerplate |
| "Search my code by what it does, not just keywords" | `search` combines meaning-based and keyword matching |
| "Find every line matching a pattern" | `agent_grep` does text/regex search with token-budgeted results |
| "Run several operations in one round-trip" | `batch` executes multiple tool calls under a single write lock |

### For power users

- **Multi-agent safe** — Multiple AI agents can share the same index without stepping on each other (document locking, version tracking, conflict detection)
- **Works with git worktrees** — Each worktree gets its own index, with shared coordination across all of them
- **Session management** — Save and restore agent state between conversations (rollback, pruning)
- **Supports many file types** — Code (12 languages), text, Markdown, images, PDFs, audio, video (some need optional packages)

## How Much Does It Save?

| What changed | Tokens without Stele | Tokens with Stele | Savings |
|--------------|---------------------|-------------------|---------|
| Nothing (same code) | 10,000 | 0 | 100% |
| A typo fix | 10,000 | ~100 | 99% |
| Edited a few functions | 10,000 | ~1,000 | 90% |
| Rewrote the whole file | 10,000 | 10,000 | 0% |

The less your code changes between conversations, the more tokens you save.

## Python API

You can also use Stele Context directly in Python scripts:

```python
from stele_context import Stele

engine = Stele()

# Index your project
result = engine.index_documents(["src/", "README.md"])
print(f"Indexed {result['total_chunks']} chunks")

# Search by meaning or keywords
results = engine.search("authentication logic", top_k=5)
for r in results:
    print(f"{r['document_path']}: {r['content'][:100]}...")

# Check what changed since last time
changes = engine.detect_changes_and_update()
print(f"{len(changes['modified'])} files changed, {len(changes['new'])} new files")

# Find where a function/class is used
refs = engine.find_references("MyClassName")
print(f"Verdict: {refs['verdict']}")  # referenced, unreferenced, external, or not_found

# What breaks if I change this file?
impact = engine.impact_radius(document_path="src/main.py")
print(f"{impact['affected_files']} files could be affected")

# Analyze impact of a dynamic/runtime symbol
impact = engine.impact_radius(symbol="onRecipeCreate")
print(f"{impact['affected_files']} files could be affected")
```

## Configuration

Create a `.stele-context.toml` in your project root to customize behavior:

```toml
[stele-context]
chunk_size = 512                # How big each chunk is (in tokens)
skip_dirs = [".git", "node_modules", "dist", "vendor"]
```

All settings are optional — defaults work well for most projects.

<details>
<summary>All configuration options</summary>

```toml
[stele-context]
storage_dir = ".stele-context"   # Where to store the index
chunk_size = 256                 # Target tokens per chunk
max_chunk_size = 4096            # Maximum tokens per chunk
merge_threshold = 0.7            # When to merge similar adjacent chunks
change_threshold = 0.85          # When to consider a chunk "unchanged"
search_alpha = 0.42              # Balance between meaning-based and keyword search
skip_dirs = [".git", "node_modules", "__pycache__"]
```

You can also set `STELE_CONTEXT_STORAGE_DIR` as an environment variable, or pass options directly in Python:

```python
engine = Stele(chunk_size=512, skip_dirs=[".git", "node_modules", "dist"])
```

Priority: Python arguments > `.stele-context.toml` > environment variables > defaults.

</details>

## Optional Extras

The core package has **zero dependencies** — it runs on Python's standard library alone. Optional packages add support for more file types and better performance:

```bash
pip install stele-context[tree-sitter]   # Better code understanding (9 languages)
pip install stele-context[image,pdf]     # Image and PDF support
pip install stele-context[performance]   # Faster search with numpy
pip install stele-context[all]           # Everything
```

<details>
<summary>Full extras list</summary>

| Extra | What it adds |
|-------|-------------|
| `performance` | Faster math for search (numpy, msgspec) |
| `tree-sitter` | Better code parsing for JS/TS, Java, C/C++, Go, Rust, Ruby, PHP |
| `image` | Index and search images (Pillow) |
| `pdf` | Extract text from PDFs (pymupdf) |
| `audio` | Index audio files (librosa) |
| `video` | Index video keyframes (opencv) |
| `mcp` | MCP server for Claude Desktop/Code |
| `all` | All of the above |

</details>

## Supported File Types

**Built-in (no extra packages needed):**
`.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.cpp`, `.c`, `.h`, `.go`, `.rs`, `.rb`, `.php`, `.swift`, `.sh`, `.sql`, `.html`, `.css`, `.json`, `.yaml`, `.toml`, `.md`, `.txt`, `.rst`, `.csv`, `.log`

**With optional packages:**
Images (`.png`, `.jpg`, `.gif`, etc.), PDFs, audio (`.mp3`, `.wav`, etc.), video (`.mp4`, `.avi`, etc.)

## Troubleshooting

**`ImportError: No module named 'stele_context'`**
Make sure it's installed: `pip install stele-context`. If using a virtualenv, activate it first.

**MCP server not connecting**
Use the full path to the binary. Run `which stele-context` and put that path in your config.

**`PermissionError` when indexing**
Another agent might be holding a lock. Run `stele-context` with the `reap_expired_locks` tool to clean up.

## FAQ

**How do I make Claude Code remember my project between conversations?**
Install Stele Context and add it as an MCP server (see Quick Start above). Once connected, Claude Code can index your project and recall file contents, symbol locations, and code structure across conversations without re-reading everything.

**Does this work with Cursor / other AI coding tools?**
Yes. Stele Context runs as an MCP server, which is a standard protocol. Any AI tool that supports MCP can use it. It also has an HTTP REST API and a Python library for direct integration.

**Does it need an internet connection or API keys?**
No. Everything runs locally on your machine. No API calls, no cloud, no model downloads, no telemetry. Zero dependencies — just Python's standard library.

**Is my code safe?**
Yes. Your code never leaves your machine. No data is sent anywhere. Zero third-party dependencies means no supply chain risk. ~13,000 lines of Python you can read and audit yourself.

**Can multiple AI agents use it at the same time?**
Yes. Built-in document locking and version tracking prevent agents from stepping on each other.

**Where is the data stored?**
In a `.stele-context/` folder in your project root. It's just a SQLite database and some index files. Each git worktree gets its own.

**How is this different from just using CLAUDE.md or project memory?**
CLAUDE.md gives your agent instructions. Stele Context gives it a searchable index of your entire codebase — every function, every import, every file relationship. It knows what changed since last time and can answer "where is this function used?" or "what breaks if I change this file?" without reading everything again.

## Learn More

- [AGENTS.md](AGENTS.md) — How AI agents should use Stele Context
- [Design philosophy](docs/philosophy.md) — Why it's built this way
- [Agent workflow](docs/agent-workflow.md) — Step-by-step agent integration guide
- [CHANGELOG](CHANGELOG.md) — What changed in each version
- [Technical architecture](docs/architecture.md) — Deep dive into internals

## Development

```bash
pip install -e ".[dev]"
pytest                              # 880+ tests
pytest --cov=stele_context           # With coverage
mypy stele_context/                 # Type checking
ruff check stele_context/           # Linting
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License — see [LICENSE](LICENSE) for details.
