# Proposal: Grep-First Indexing — "Post-it Notes for LLMs"

**Date:** 2026-03-31
**Status:** Draft

---

## Problem Statement

LLMs — including Opus 4.6 — repeatedly re-read files they've already examined, wasting tokens and extending context. The root cause is a retrieval gap: the LLM has no lightweight way to know "I already searched this file, here's what was in it, here's its staleness."

Semantic search (`search`) was supposed to bridge this but has a hard quality ceiling due to statistical embeddings. It is unreliable enough that agents fall back to re-reading files rather than trusting cached content.

The grep + index integration proposed here flips the discovery model: **grep finds, index caches, context retrieves.**

---

## Core Idea

When `agent_grep` or `search_text` runs on a file and finds matches, **automatically index that file** in the same call. The act of searching becomes the act of caching.

A new `get_search_history(session_id)` tool acts as the "post-it note" — it tells the agent which files it has already grep'd/searched, what pattern was used, and the staleness of each cached version.

The agent workflow becomes:

```
1. agent_grep "pattern" → matches returned + files indexed automatically
2. get_search_history → "you grep'd file X for 'pattern', it's fresh (staleness 0.0)"
3. get_context file X → full cached content, no re-read needed
4. Optional: find_references symbol → see all usages without re-reading
```

---

## What Changes

### 1. `agent_grep` / `search_text` — auto-index matched files

After finding matches in a file, call `storage.index_documents([file_path])` internally before returning. The search result already names the files — just index them.

**Upside:** The LLM doesn't need a separate `index` call. Searching and caching happen atomically.

**Risk:** Latency on large greps. Mitigation: index only files with matches, skip files with zero matches.

### 2. New tool: `get_search_history(session_id)`

Returns:
```json
{
  "session_id": "...",
  "searches": [
    {
      "pattern": "createRouter",
      "tool": "agent_grep",
      "files_checked": ["src/api/routes/users.js", "src/api/routes/recipes.js"],
      "files_with_matches": ["src/api/routes/users.js"],
      "searched_at": "2026-03-31T...",
      "stale_files": []
    },
    {
      "pattern": "allergen",
      "tool": "search_text",
      "files_checked": ["src/plugins/allergenChecker.js"],
      "files_with_matches": ["src/plugins/allergenChecker.js"],
      "searched_at": "2026-03-31T...",
      "stale_files": []
    }
  ],
  "indexed_files": ["src/api/routes/users.js", "src/plugins/allergenChecker.js"]
}
```

The `stale_files` field tells the LLM which cached files may need refresh.

### 3. `get_context` — indicate if file was recently grep'd

Today `get_context` returns cached content with no signal about whether the LLM has already seen this file in the current session. Add a `session_id` parameter and return a `recently_searched: bool` + `search_pattern: str | None` field. The LLM can then say "I already grep'd this for the exact line I need."

### 4. Session-scoped "read once" flag

Add a `session_read_files` table that records when a file's content was returned via `get_context` in a session. A `get_session_read_files(session_id)` tool lets the agent ask "what have I already fully read in this session?" — distinct from "what have I grep'd?"

### 5. `search` (semantic) — deprioritize in docs

The doc hierarchy should make `agent_grep` / `search_text` / `find_references` the primary path. `search` is for open exploration only, not targeted audits. Keep it — it has value for discovery — but stop implying it should be the default.

---

## What Stays the Same

- Zero required dependencies — all changes are in-memory session tracking + existing storage/indexing
- `find_references` stays as-is — already excellent
- `get_context` mtime fast-path unchanged
- `detect_changes` unchanged
- No new external dependencies

---

## Implementation Sketch

### `storage.py` additions

```python
# Session search history
def record_search(self, session_id: str, pattern: str, tool: str,
                  files_checked: list[str], files_with_matches: list[str]) -> None: ...
def get_search_history(self, session_id: str) -> list[dict]: ...

# Session read history
def record_file_read(self, session_id: str, document_path: str, chunk_ids: list[str]) -> None: ...
def get_session_read_files(self, session_id: str) -> list[dict]: ...
```

### `agent_grep.py` — post-index hook

After search completes and before returning results:
```python
# Index files with matches (fast, mtime check skips unchanged)
for file_path in files_with_matches:
    index_documents([file_path])
```

### `engine.py` — new tools

- `get_search_history(session_id)` → delegates to storage
- `get_session_read_files(session_id)` → delegates to storage
- `get_context(document_paths, session_id)` → adds `recently_searched` + staleness to response

---

## Renaming / Documentation

The CLAUDE.md section "Agent Workflow with Stele Context" should be rewritten with the grep-first model:

```
Before edits:
1. agent_grep "symbol" → find usages + auto-index those files
2. get_search_history → see what you already grep'd this session
3. get_context file → full content from cache, no re-read

After edits:
- stele-context index (updated files)
- detect_changes (see what changed)
```

Remove emphasis on `search` (semantic) as a primary retrieval path. Promote `find_references` as the primary symbol tool.

---

## Open Questions

1. **Auto-index latency**: Should `agent_grep` index synchronously or queue it? Synchronous is simpler but adds latency on first grep of large files. Consider: if file is fresh in cache (mtime unchanged), skip indexing in grep.

2. **Should `search_text` also auto-index?** Yes — any search that checks a file's content should cache that content. The pattern is the same.

3. **`search` (semantic) auto-index?** No — semantic search doesn't pin to specific files. Indexing "top 5 semantic hits" would be noisy and expensive.

4. **Session cleanup**: When does search/read history get cleared? Session ends naturally; no explicit cleanup needed unless sessions grow very large.

---

## Summary

| Change | Complexity | Impact |
|--------|------------|--------|
| `agent_grep` auto-index | Low | High — searching = caching |
| `search_text` auto-index | Low | High — same pattern |
| `get_search_history` tool | Low | High — "post-it" visibility |
| `get_context` + `recently_searched` | Low | Medium — reduces blind re-reads |
| `get_session_read_files` tool | Low | Medium — full-read tracking |
| Docs rewrites (CLAUDE.md) | Low | Medium — changes default behavior |
