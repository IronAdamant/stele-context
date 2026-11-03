# Parity harness

Reference capture for the Rust 2.0.0 rewrite. The gate is Python **1.5.2**
with tree-sitter **off** and numpy **blocked**.

`python -m parity` blocks `numpy` before importing stele so semantic
signatures take the zero-dependency float path, then packs them to
little-endian float32. That is the bit pattern Rust must reproduce.
`len` on text is Unicode scalars, and `Counter.most_common` keeps
first-seen order on ties. Both are part of the signature.

## Reference choice

Tree-sitter is optional in 1.5.2 and removed in 2.0 (decision D3). The
golden tables are what the stdlib path emits:

- Python chunks and symbols come from `ast` (regex fallback on `SyntaxError`).
- Other languages use the regex chunker and `SymbolExtractor`.
- Shell, JSON, YAML, TOML, INI, and SQL are chunked and have an empty symbol list. That is 1.5.2, and it is the parity target.

An optional tree-sitter install produces different chunk boundaries. Those
are not the gate. Hybrid search, later, compares top-10 overlap at 9 of 10,
which is the threshold proposed in the plan.

Paths in goldens are project-relative and use `/`. File text is
`read_text(encoding="utf-8", errors="replace")`, so newlines are universal.

## Commands

```bash
python -m parity capture            # rewrite goldens, download corpora
python -m parity verify             # fixtures, CLI smoke, corpora
python -m parity verify --skip-corpora
```

Corpora are Textualize/rich v13.9.4 and pallets/flask 3.1.0. Tarball
digests are pinned in `goldens/corpora.json`. Code and text files only
(media chunkers are a later phase).

`extensions.json` lists every extension `CodeChunker` and `TextChunker`
accept, including each `code_patterns.py` grammar and regex key.
