# Design philosophy

Stele Context is built for **software agents** that work inside a codebase over **many chat sessions**. The goal is simple to state and hard to do well: **find the right slices of the project when you need them**, without re-reading whole trees every time, and **keep that knowledge retrievable** after the current session ends.

## Why zero required dependencies

The core library ships with **no non-stdlib dependencies** on purpose:

- **Supply chain and deployment**: Agents run in sandboxes, CI, and air-gapped environments. Fewer packages means fewer surprises.
- **No bundled embedding model**: Downloading and versioning model weights is a different product. Stele stays a **local index and retrieval layer**, not a model host.

Optional extras (`performance`, `tree-sitter`, multimodal chunkers, etc.) stay **offline** and **explicit**.

## Two tiers of “meaning”

**Tier 1 — always on (statistical signatures)**  
Every chunk gets a compact **128-dimensional signature** derived from structure and text statistics. It powers change detection and a baseline vector for hybrid search (HNSW + BM25) with **no external model**. The hybrid ranker **falls back to BM25** when vector/keyword signals disagree, scores are flat, or the top raw cosine is weak (v1.0.5+); optional **`path_prefix`** scopes **`map`** / **`search`** when one index covers multiple trees.

**Tier 2 — agent-supplied (semantic summaries or vectors)**  
You — or another LLM — act as the **embedding model**:

- Write a short **summary** of what a chunk (or whole file) is *about*, or
- Supply a **raw vector** (same dimension space as the engine expects) after you have reasoned over the content.

Stele stores Tier 2 data in SQLite and folds it into search (with a boost over pure Tier 1). **Search quality for “what did we mean?” queries improves when Tier 2 is filled in**; Tier 1 alone is intentionally lightweight.

So: **first passes can be slower** (read, chunk, optionally summarize). **Later passes are faster**: retrieve from the index, session, and symbol graph instead of re-ingesting everything.

## Sessions and the long-lived index

- **Index + chunks** live under the project’s `.stele-context/` (or configured storage). They **persist** across runs and sessions.
- **Sessions** (when you use them) hold working state: attached chunks, rollbacks, optional KV-style notes tied to the engine.

Together, this supports: **Session A** indexes and annotates; **Session B** searches and continues without re-discovering the repo from scratch.

## What Stele is not

- It is **not** a hosted vector DB or a framework that pulls in half the PyPI ecosystem.
- **Generic “semantic search” over code** without Tier 2 can miss nuance; **symbol tools** (`find_references`, `find_definition`, `agent_grep`, `search_text`) remain the precision layer for renames and verification.

For a **concrete agent workflow** (index → enrich → retrieve), see [Agent workflow](agent-workflow.md).

## Comparison by design (not by vendor)

This table is meant to stay **stable**: it compares *design choices*, not feature checklists that go stale.

| Dimension | Stele Context |
|-----------|----------------|
| Core runtime dependencies | Zero (stdlib only) |
| Network required | No |
| Who produces “semantic” vectors for search | Optional: **you** (summaries / vectors) or Tier 1 only |
| Primary store | SQLite + on-disk indices under project |
| Typical use | Local agent memory for **one codebase** + optional coordination across worktrees |

Other tools optimize for different goals (hosted models, cloud vector DBs, large dependency graphs). Use Stele when **offline, minimal deps, and agent-controlled semantics** matter.
