"""
Symbol graph manager for Stele.

Extracted from engine.py to follow the delegate pattern (like SessionManager).
Handles symbol extraction, edge resolution, staleness propagation,
and symbol-based queries (find_references, find_definition, impact_radius).
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any

from stele_context.chunkers.base import Chunk
from stele_context.symbols import (
    SymbolExtractor,
    Symbol,
    resolve_symbols,
    _NOISE_REFS,
    _module_matches_path,
)


def _resolve_test_edges(symbols: list[Symbol]) -> list[tuple[str, str, str, str]]:
    """Create ``test_of`` edges linking test files to their source files.

    Uses filename convention matching (``test_foo.py`` → ``foo.py``,
    ``foo_test.py`` → ``foo.py``) and import analysis (test imports that
    resolve to source modules).
    """
    # document_path -> first chunk_id
    doc_chunk: dict[str, str] = {}
    for sym in symbols:
        if sym.document_path not in doc_chunk:
            doc_chunk[sym.document_path] = sym.chunk_id

    # Classify documents
    test_docs: list[str] = []
    source_docs: list[str] = []
    for dp in doc_chunk:
        p = Path(dp)
        is_test = (
            p.name.startswith("test_")
            or p.stem.endswith("_test")
            or "tests" in p.parts
            or "test" in p.parts
        )
        if is_test:
            test_docs.append(dp)
        else:
            source_docs.append(dp)

    # stem -> source docs
    source_by_stem: dict[str, list[str]] = {}
    for dp in source_docs:
        source_by_stem.setdefault(Path(dp).stem, []).append(dp)

    edges: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for test_dp in test_docs:
        test_cid = doc_chunk[test_dp]
        p = Path(test_dp)
        candidates: set[str] = set()

        # Convention: test_X.py -> X.py
        if p.name.startswith("test_"):
            base = p.name[5:].rsplit(".", 1)[0]
            candidates.update(source_by_stem.get(base, []))

        # Convention: X_test.py -> X.py
        if p.stem.endswith("_test"):
            base = p.stem[:-5]
            candidates.update(source_by_stem.get(base, []))

        # Same-stem fallback for tests in tests/ directories
        if "tests" in p.parts or "test" in p.parts:
            candidates.update(source_by_stem.get(p.stem, []))

        # Import analysis
        for sym in symbols:
            if (
                sym.document_path == test_dp
                and sym.role == "reference"
                and sym.kind in ("module", "import")
            ):
                for src_dp in source_docs:
                    if _module_matches_path(sym.name, src_dp):
                        candidates.add(src_dp)

        for src_dp in candidates:
            src_cid = doc_chunk[src_dp]
            key = (test_cid, src_cid)
            if key in seen:
                continue
            seen.add(key)
            edges.append((test_cid, src_cid, "test_of", "test"))

    return edges


class SymbolGraphManager:
    """Manages the symbol graph: extraction, edges, staleness, queries.

    Follows the same delegate pattern as ``SessionManager``: receives
    the ``StorageBackend`` and owns all symbol-related operations.
    The engine holds the ``RWLock`` and calls these methods from within
    locked contexts — this class does no locking itself.
    """

    def __init__(self, storage: Any):
        self.storage = storage
        self._extractor = SymbolExtractor()

    def _symbol_index_snapshot(self) -> dict[str, Any]:
        """Lightweight stats so callers can tell empty index from missing symbol."""
        st = self.storage.get_storage_stats()
        sym_n = int(st.get("symbol_count", 0) or 0)
        doc_n = int(st.get("document_count", 0) or 0)
        return {
            "status": "empty" if sym_n == 0 else "ready",
            "indexed_documents": doc_n,
            "symbol_row_count": sym_n,
            "edge_count": int(st.get("edge_count", 0) or 0),
        }

    @staticmethod
    def _guidance_when_no_symbol_hits(snapshot: dict[str, Any]) -> str:
        if snapshot["status"] == "ready":
            return (
                "The symbol graph is populated but this name has no matches. "
                "The symbol may be absent from indexed files, not extracted "
                "(e.g. highly dynamic code), or spelled differently. "
                "Try search_text or agent_grep to confirm text occurrences."
            )
        if snapshot["indexed_documents"] == 0:
            return (
                "No documents are indexed yet, so the symbol index is empty. "
                "Run index on project paths first, then rebuild_symbols if "
                "definitions and references stay empty."
            )
        return (
            "Chunks are indexed but the symbol table is empty. "
            "Run index on source files you need for symbols, then rebuild_symbols "
            "to populate the graph."
        )

    # -- Helpers --------------------------------------------------------------

    @staticmethod
    def _dicts_to_symbols(raw: list[dict]) -> list[Symbol]:
        """Convert raw symbol dicts from storage to Symbol dataclasses."""
        return [
            Symbol(
                name=s["name"],
                kind=s["kind"],
                role=s["role"],
                chunk_id=s["chunk_id"],
                document_path=s["document_path"],
                line_number=s["line_number"],
            )
            for s in raw
        ]

    def attach_edges(self, entry: dict, chunk_id: str) -> None:
        """Attach symbol edges to a search result entry (in-place)."""
        outgoing = self.storage.get_outgoing_edges(chunk_id)
        incoming = self.storage.get_incoming_edges(chunk_id)
        if outgoing or incoming:
            entry["edges"] = {
                "depends_on": [
                    {"chunk_id": e["target_chunk_id"], "symbol": e["symbol_name"]}
                    for e in outgoing
                ],
                "depended_on_by": [
                    {"chunk_id": e["source_chunk_id"], "symbol": e["symbol_name"]}
                    for e in incoming
                ],
            }

    # -- Extraction and edge building -----------------------------------------

    def extract_document_symbols(
        self,
        doc_path: str,
        chunks: list[Chunk],
    ) -> None:
        """Extract symbols from a document's chunks and store them."""
        self.storage.clear_document_symbols(doc_path)
        ext = Path(doc_path).suffix.lstrip(".").lower()
        doc_symbols = []
        for chunk in chunks:
            if isinstance(chunk.content, str):
                syms = self._extractor.extract(
                    chunk.content,
                    doc_path,
                    chunk.chunk_id,
                    ext,
                )
                doc_symbols.extend(syms)
        if doc_symbols:
            self.storage.store_symbols(doc_symbols)

    def rebuild_edges(
        self,
        affected_chunk_ids: set[str] | None = None,
    ) -> None:
        """Rebuild symbol edges from current symbols.

        If affected_chunk_ids is given, only edges involving those chunks
        (or symbol names that changed in those chunks) are cleared and
        re-created (incremental). Otherwise, all edges are rebuilt from
        scratch.
        """
        all_syms_raw = self.storage.get_all_symbols()
        if not all_syms_raw:
            if affected_chunk_ids is None:
                self.storage.clear_all_edges()
            return

        all_syms = self._dicts_to_symbols(all_syms_raw)
        all_edges = resolve_symbols(all_syms)
        all_edges.extend(_resolve_test_edges(all_syms))

        if affected_chunk_ids is None:
            self.storage.clear_all_edges()
            self.storage.store_edges(all_edges)
        else:
            affected_list = list(affected_chunk_ids)
            old_names = self.storage.get_edge_symbol_names_for_chunks(affected_list)
            current_names = self.storage.get_symbol_names_for_chunks(affected_list)
            affected_names = old_names | current_names
            self.storage.clear_chunk_edges(affected_list)
            scoped = [
                e
                for e in all_edges
                if e[0] in affected_chunk_ids
                or e[1] in affected_chunk_ids
                or e[3] in affected_names
            ]
            self.storage.store_edges(scoped)

    def propagate_staleness(
        self,
        changed_chunk_ids: set[str],
        decay: float = 0.8,
        max_depth: int = 3,
    ) -> int:
        """Propagate staleness scores through the symbol graph.

        When chunks change, their dependents become potentially stale.
        Score decays by ``decay`` per hop.  Changed chunks get score 0.
        Returns the number of chunks marked stale.
        """
        self.storage.set_staleness_batch([(0.0, cid) for cid in changed_chunk_ids])

        visited: dict[str, float] = {}
        queue = deque((cid, 0) for cid in changed_chunk_ids)

        while queue:
            current_id, depth = queue.popleft()
            if depth > max_depth:
                continue

            edges = self.storage.get_incoming_edges(current_id)
            for edge in edges:
                dep_id = edge["source_chunk_id"]
                if dep_id in changed_chunk_ids:
                    continue
                new_score = decay ** (depth + 1)
                if dep_id not in visited or new_score > visited[dep_id]:
                    visited[dep_id] = new_score
                    queue.append((dep_id, depth + 1))

        if visited:
            self.storage.set_staleness_batch(
                [(score, cid) for cid, score in visited.items()]
            )

        return len(visited)

    # -- Queries --------------------------------------------------------------

    def stale_chunks(self, threshold: float = 0.3) -> dict[str, Any]:
        """Get chunks with staleness_score >= threshold, grouped by file."""
        stale = self.storage.get_stale_chunks(threshold)

        by_doc: dict[str, list[dict[str, Any]]] = {}
        for chunk in stale:
            by_doc.setdefault(chunk["document_path"], []).append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "staleness_score": chunk["staleness_score"],
                    "token_count": chunk["token_count"],
                    "content_preview": (chunk.get("content") or "")[:200],
                }
            )

        guidance = (
            "On actively developed codebases, many files may show mild staleness. "
            "Consider using threshold=0.5 to focus on direct dependency changes, "
            "or threshold=0.64 for transitive changes only."
            if threshold <= 0.3 and len(stale) > 50
            else None
        )

        out: dict[str, Any] = {
            "threshold": threshold,
            "total_stale": len(stale),
            "files_affected": len(by_doc),
            "by_document": [
                {"path": doc_path, "chunks": chunks}
                for doc_path, chunks in sorted(
                    by_doc.items(),
                    key=lambda x: max(c["staleness_score"] for c in x[1]),
                    reverse=True,
                )
            ],
        }
        if guidance:
            out["guidance"] = guidance
        return out

    def find_references(self, symbol: str) -> dict[str, Any]:
        """Find all definitions and references for a symbol name."""
        snapshot = self._symbol_index_snapshot()
        definitions = self.storage.find_definitions(symbol)
        references = self.storage.find_references_by_name(symbol)

        def _enrich(syms: list[dict]) -> list[dict]:
            results = []
            for sym in syms:
                chunk = self.storage.get_chunk(sym["chunk_id"])
                results.append(
                    {
                        "symbol": sym["name"],
                        "kind": sym["kind"],
                        "chunk_id": sym["chunk_id"],
                        "document_path": sym["document_path"],
                        "line_number": sym.get("line_number"),
                        "content_preview": (
                            (chunk.get("content") or "")[:200] if chunk else ""
                        ),
                    }
                )
            return results

        enriched_defs = _enrich(definitions)
        enriched_refs = _enrich(references)

        # Machine-readable verdict for quick dead-code checks
        if not definitions and not references:
            verdict = "not_found"
        elif definitions and not references:
            verdict = "unreferenced"
        elif not definitions and references:
            verdict = "external"
        else:
            verdict = "referenced"

        total = len(definitions) + len(references)
        out: dict[str, Any] = {
            "symbol": symbol,
            "verdict": verdict,
            "definitions": enriched_defs,
            "references": enriched_refs,
            "total": total,
            "symbol_index": snapshot,
        }
        if total == 0:
            out["guidance"] = self._guidance_when_no_symbol_hits(snapshot)
        else:
            out["guidance"] = None
        return out

    def find_definition(self, symbol: str) -> dict[str, Any]:
        """Find definition location(s) of a symbol."""
        snapshot = self._symbol_index_snapshot()
        definitions = self.storage.find_definitions(symbol)

        # Group by document_path to detect shadowed definitions
        by_doc: dict[str, list[dict[str, Any]]] = {}
        for defn in definitions:
            by_doc.setdefault(defn["document_path"], []).append(defn)

        results = []
        for doc_path, doc_defs in by_doc.items():
            # Sort by line number for stable ordering
            doc_defs_sorted = sorted(doc_defs, key=lambda d: d.get("line_number") or 0)
            shadowed = len(doc_defs_sorted) > 1
            for idx, defn in enumerate(doc_defs_sorted, 1):
                chunk = self.storage.get_chunk(defn["chunk_id"])
                entry: dict[str, Any] = {
                    "symbol": defn["name"],
                    "kind": defn["kind"],
                    "chunk_id": defn["chunk_id"],
                    "document_path": defn["document_path"],
                    "line_number": defn.get("line_number"),
                    "content": chunk.get("content") if chunk else None,
                    "token_count": chunk["token_count"] if chunk else 0,
                }
                if shadowed:
                    entry["definition_index"] = idx
                    entry["shadowed"] = True
                    entry["shadow_count"] = len(doc_defs_sorted)
                results.append(entry)

        count = len(results)
        out: dict[str, Any] = {
            "symbol": symbol,
            "definitions": results,
            "count": count,
            "symbol_index": snapshot,
        }
        if count == 0:
            out["guidance"] = self._guidance_when_no_symbol_hits(snapshot)
        else:
            out["guidance"] = None
        return out

    @staticmethod
    def _is_significant_symbol(
        symbol_name: str,
        significance_threshold: float,
        exclude_symbols: set[str] | None,
    ) -> bool:
        """Return True if an edge driven by this symbol should be traversed."""
        if exclude_symbols and symbol_name in exclude_symbols:
            return False
        if significance_threshold > 0.0 and symbol_name in _NOISE_REFS:
            return False
        return True

    def impact_radius(
        self,
        chunk_id: str | None = None,
        depth: int = 2,
        document_path: str | None = None,
        symbol: str | None = None,
        *,
        compact: bool = True,
        include_content: bool = True,
        path_filter: str | None = None,
        summary_mode: bool = False,
        top_n_files: int = 25,
        significance_threshold: float = 0.0,
        exclude_symbols: list[str] | None = None,
        direction: str = "dependents",
    ) -> dict[str, Any]:
        """Find all chunks affected by a change to a chunk or file (BFS).

        Accepts either ``chunk_id`` (single chunk), ``document_path``
        (all chunks in a file), or ``symbol`` (all chunks where the symbol
        is defined). At least one must be provided.

        ``compact`` returns per-file summaries instead of per-chunk records.
        ``include_content=False`` omits chunk text (smaller payloads).
        ``path_filter`` keeps only results whose path contains the substring.
        ``summary_mode`` (implies compact) returns a bounded payload: depth
        distribution plus top-N impacted files by chunk count (fan-in style).
        ``direction`` controls traversal: ``dependents`` (incoming edges,
        default), ``dependencies`` (outgoing edges), or ``both``.
        """
        if summary_mode:
            compact = True
            include_content = False
        # Resolve seed chunk IDs
        if symbol and not chunk_id:
            defs = self.storage.find_definitions(symbol)
            if not defs:
                return {
                    "origin": symbol,
                    "max_depth": depth,
                    "affected_chunks": 0,
                    "affected_files": 0,
                    "chunks": [],
                }
            seed_ids = {d["chunk_id"] for d in defs}
            origin = symbol
        elif document_path and not chunk_id:
            doc_chunks = self.storage.get_document_chunks(document_path)
            if not doc_chunks:
                return {
                    "origin": document_path,
                    "max_depth": depth,
                    "affected_chunks": 0,
                    "affected_files": 0,
                    "chunks": [],
                }
            seed_ids = {c["chunk_id"] for c in doc_chunks}
            origin = document_path
        elif chunk_id:
            seed_ids = {chunk_id}
            origin = chunk_id
        else:
            return {"error": "Provide chunk_id, document_path, or symbol"}

        exclude_set = set(exclude_symbols) if exclude_symbols else None
        direction = (direction or "dependents").lower()

        visited: set[str] = set()
        queue = deque((cid, 0) for cid in seed_ids)

        # When compact=True, aggregate per-file counts during BFS instead of
        # materialising every chunk first and condensing afterward.
        by_file: dict[str, dict[str, Any]] = {}
        result_chunks: list[dict[str, Any]] = []
        affected_files: set[str] = set()
        depth_counts: dict[int, int] = {}

        # Hybrid seeding for document_path: the symbol edge graph can be sparse
        # for base classes (module imports may not resolve to edges). We also
        # seed the BFS with raw symbol references to symbols defined in the file.
        if document_path and direction in ("dependents", "both"):
            definition_symbols = self.storage.get_symbols_for_chunks(list(seed_ids))
            defined_names = {
                s["name"]
                for s in definition_symbols
                if s["role"] == "definition"
                and self._is_significant_symbol(
                    s["name"], significance_threshold, exclude_set
                )
            }
            for name in defined_names:
                for ref in self.storage.find_references_by_name(name):
                    ref_cid = ref["chunk_id"]
                    if ref_cid not in seed_ids and ref_cid not in visited:
                        queue.append((ref_cid, 1))

        while queue:
            current_id, current_depth = queue.popleft()
            if current_id in visited or current_depth > depth:
                continue
            visited.add(current_id)

            if current_depth < depth:
                edges: list[dict[str, Any]] = []
                if direction in ("dependents", "both"):
                    edges.extend(self.storage.get_incoming_edges(current_id))
                if direction in ("dependencies", "both"):
                    edges.extend(self.storage.get_outgoing_edges(current_id))
                for edge in edges:
                    sym = edge["symbol_name"]
                    if not self._is_significant_symbol(
                        sym, significance_threshold, exclude_set
                    ):
                        continue
                    next_id = (
                        edge["source_chunk_id"]
                        if edge["target_chunk_id"] == current_id
                        else edge["target_chunk_id"]
                    )
                    if next_id not in visited:
                        queue.append((next_id, current_depth + 1))

            # Skip the seed chunk itself (depth 0 from document_path seed).
            if current_id in seed_ids and current_depth == 0:
                continue

            meta = self.storage.get_chunk(current_id)
            if not meta:
                continue
            doc_p = meta["document_path"]
            if path_filter is not None and path_filter not in doc_p:
                continue
            affected_files.add(doc_p)

            if compact:
                depth_counts[current_depth] = depth_counts.get(current_depth, 0) + 1
                if doc_p not in by_file:
                    by_file[doc_p] = {
                        "path": doc_p,
                        "chunk_count": 0,
                        "depth_min": current_depth,
                        "depth_max": current_depth,
                    }
                e = by_file[doc_p]
                e["chunk_count"] += 1
                e["depth_min"] = min(e["depth_min"], current_depth)
                e["depth_max"] = max(e["depth_max"], current_depth)
            else:
                row: dict[str, Any] = {
                    "chunk_id": current_id,
                    "document_path": doc_p,
                    "depth": current_depth,
                    "token_count": meta["token_count"],
                }
                if include_content:
                    row["content"] = meta.get("content")
                result_chunks.append(row)
                depth_counts[current_depth] = depth_counts.get(current_depth, 0) + 1

        out: dict[str, Any] = {
            "origin": origin,
            "max_depth": depth,
            "affected_chunks": len(result_chunks)
            if not compact
            else sum(v["chunk_count"] for v in by_file.values()),
            "affected_files": len(affected_files),
        }
        if significance_threshold > 0.0:
            out["significance_threshold"] = significance_threshold
        if direction != "dependents":
            out["direction"] = direction
        if compact:
            file_rows = sorted(by_file.values(), key=lambda x: x["path"])
            if summary_mode:
                top_n = max(1, top_n_files)
                top_impacted = sorted(
                    by_file.values(),
                    key=lambda x: (-x["chunk_count"], x["path"]),
                )[:top_n]
                out["summary_mode"] = True
                out["depth_distribution"] = {
                    str(k): depth_counts[k] for k in sorted(depth_counts)
                }
                out["files"] = top_impacted
                out["files_total"] = len(file_rows)
            else:
                out["files"] = file_rows
            out["chunks"] = []
        else:
            out["chunks"] = result_chunks
            if depth_counts:
                out["depth_distribution"] = {
                    str(k): depth_counts[k] for k in sorted(depth_counts)
                }
        return out

    def rebuild_graph(self) -> dict[str, Any]:
        """Rebuild the entire symbol graph from stored chunk content."""
        all_chunks = self.storage.search_chunks()

        self.storage.clear_all_symbols()
        self.storage.clear_all_edges()

        by_doc: dict[str, list[dict[str, Any]]] = {}
        for chunk in all_chunks:
            by_doc.setdefault(chunk["document_path"], []).append(chunk)

        total_symbols = 0
        for doc_path, chunks in by_doc.items():
            ext = Path(doc_path).suffix.lstrip(".").lower()
            doc_symbols = []
            for chunk in chunks:
                content = chunk.get("content")
                if content:
                    syms = self._extractor.extract(
                        content,
                        doc_path,
                        chunk["chunk_id"],
                        ext,
                    )
                    doc_symbols.extend(syms)
            if doc_symbols:
                self.storage.store_symbols(doc_symbols)
                total_symbols += len(doc_symbols)

        all_syms_raw = self.storage.get_all_symbols()
        all_syms = self._dicts_to_symbols(all_syms_raw)
        edges = resolve_symbols(all_syms)
        self.storage.store_edges(edges)

        return {
            "documents": len(by_doc),
            "symbols": total_symbols,
            "edges": len(edges),
        }

    def coupling(
        self,
        document_path: str,
        significance_threshold: float = 0.0,
        exclude_symbols: list[str] | None = None,
        mode: str = "edges",
    ) -> dict[str, Any]:
        """Find files semantically coupled to a given file.

        ``mode="edges"`` (default): queries symbol edges involving chunks from
        the target document, groups by the OTHER document, and counts shared
        symbols per pair. Falls back to dynamic symbols registered for this
        document_path when no indexed chunks exist.

        ``mode="co_consumers"``: detects files that are co-imported or
        co-referenced by the same consumers as the target file. This catches
        tight coupling between e.g. ``Recipe.js`` and ``Tag.js`` when both are
        heavily consumed by ``src/api/routes/recipes.js``.
        """
        mode = (mode or "edges").lower()
        exclude_set = set(exclude_symbols) if exclude_symbols else None

        if mode == "co_consumers":
            return self._coupling_co_consumers(
                document_path,
                significance_threshold=significance_threshold,
                exclude_set=exclude_set,
            )

        doc_chunks = self.storage.get_document_chunks(document_path)
        doc_chunk_ids: set[str] = set()
        if doc_chunks:
            doc_chunk_ids = {c["chunk_id"] for c in doc_chunks}
        else:
            # Fallback: use dynamic symbols registered for this path
            dyn = self.storage.get_all_symbols()
            doc_chunk_ids = {
                s["chunk_id"]
                for s in dyn
                if s.get("document_path") == document_path
                and s.get("chunk_id", "").startswith("runtime:")
            }

        if not doc_chunk_ids:
            return {
                "document_path": document_path,
                "coupled_files": [],
                "total_coupled": 0,
            }

        # Collect edges in both directions
        coupled: dict[str, dict[str, Any]] = {}
        for cid in doc_chunk_ids:
            # Outgoing: this file depends on other files
            for edge in self.storage.get_outgoing_edges(cid):
                sym = edge["symbol_name"]
                if not self._is_significant_symbol(
                    sym, significance_threshold, exclude_set
                ):
                    continue
                target_chunk = self.storage.get_chunk(edge["target_chunk_id"])
                if not target_chunk:
                    continue
                other_path = target_chunk["document_path"]
                if other_path == document_path:
                    continue
                entry = coupled.setdefault(
                    other_path,
                    {
                        "path": other_path,
                        "symbols": set(),
                        "outgoing": 0,
                        "incoming": 0,
                    },
                )
                entry["symbols"].add(sym)
                entry["outgoing"] += 1

            # Incoming: other files depend on this file
            for edge in self.storage.get_incoming_edges(cid):
                sym = edge["symbol_name"]
                if not self._is_significant_symbol(
                    sym, significance_threshold, exclude_set
                ):
                    continue
                source_chunk = self.storage.get_chunk(edge["source_chunk_id"])
                if not source_chunk:
                    continue
                other_path = source_chunk["document_path"]
                if other_path == document_path:
                    continue
                entry = coupled.setdefault(
                    other_path,
                    {
                        "path": other_path,
                        "symbols": set(),
                        "outgoing": 0,
                        "incoming": 0,
                    },
                )
                entry["symbols"].add(sym)
                entry["incoming"] += 1

        # Build result sorted by total edge count (strongest coupling first)
        results = []
        for entry in coupled.values():
            total = entry["outgoing"] + entry["incoming"]
            if entry["outgoing"] > 0 and entry["incoming"] > 0:
                direction = "bidirectional"
            elif entry["outgoing"] > 0:
                direction = "depends_on"
            else:
                direction = "depended_on_by"
            # Simple semantic score: more unique symbols = higher score,
            # but penalise if all symbols are generic single words.
            unique_syms = entry["symbols"]
            semantic_score: float = float(len(unique_syms))
            if semantic_score > 0:
                generic_ratio = sum(
                    1 for s in unique_syms if s in _NOISE_REFS or len(s) <= 3
                ) / len(unique_syms)
                semantic_score = round(semantic_score * (1.0 - generic_ratio), 2)
            results.append(
                {
                    "path": entry["path"],
                    "shared_symbols": sorted(unique_syms),
                    "shared_symbol_count": len(unique_syms),
                    "direction": direction,
                    "edge_count": total,
                    "semantic_score": semantic_score,
                }
            )
        results.sort(key=lambda x: (-x["semantic_score"], -x["edge_count"]))

        out: dict[str, Any] = {
            "document_path": document_path,
            "coupled_files": results,
            "total_coupled": len(results),
        }
        if significance_threshold > 0.0:
            out["significance_threshold"] = significance_threshold
        return out

    def _coupling_co_consumers(
        self,
        document_path: str,
        significance_threshold: float = 0.0,
        exclude_set: set[str] | None = None,
    ) -> dict[str, Any]:
        """Find files co-imported or co-referenced by the same consumers."""
        doc_chunks = self.storage.get_document_chunks(document_path)
        doc_chunk_ids = {c["chunk_id"] for c in doc_chunks} if doc_chunks else set()

        # Fallback to dynamic symbols
        if not doc_chunk_ids:
            dyn = self.storage.get_all_symbols()
            doc_chunk_ids = {
                s["chunk_id"]
                for s in dyn
                if s.get("document_path") == document_path
                and s.get("chunk_id", "").startswith("runtime:")
            }

        # Find all consumers (files that reference chunks in the target file)
        consumer_chunks: set[str] = set()
        for cid in doc_chunk_ids:
            for edge in self.storage.get_incoming_edges(cid):
                if self._is_significant_symbol(
                    edge["symbol_name"], significance_threshold, exclude_set
                ):
                    consumer_chunks.add(edge["source_chunk_id"])

        # For each consumer, find what other files it also references
        other_file_refs: dict[str, dict[str, Any]] = {}
        for consumer_cid in consumer_chunks:
            consumer_chunk = self.storage.get_chunk(consumer_cid)
            if not consumer_chunk:
                continue
            consumer_path = consumer_chunk["document_path"]
            for edge in self.storage.get_outgoing_edges(consumer_cid):
                sym = edge["symbol_name"]
                if not self._is_significant_symbol(
                    sym, significance_threshold, exclude_set
                ):
                    continue
                target_chunk = self.storage.get_chunk(edge["target_chunk_id"])
                if not target_chunk:
                    continue
                other_path = target_chunk["document_path"]
                if other_path == document_path:
                    continue
                entry = other_file_refs.setdefault(
                    other_path,
                    {
                        "path": other_path,
                        "symbols": set(),
                        "shared_consumers": set(),
                    },
                )
                entry["symbols"].add(sym)
                entry["shared_consumers"].add(consumer_path)

        results = []
        for entry in other_file_refs.values():
            unique_syms = entry["symbols"]
            semantic_score: float = float(len(unique_syms))
            if semantic_score > 0:
                generic_ratio = sum(
                    1 for s in unique_syms if s in _NOISE_REFS or len(s) <= 3
                ) / len(unique_syms)
                semantic_score = round(semantic_score * (1.0 - generic_ratio), 2)
            # Boost score by number of shared consumers (Jaccard-style proxy)
            consumer_score = round(len(entry["shared_consumers"]) * 0.5, 2)
            results.append(
                {
                    "path": entry["path"],
                    "shared_symbols": sorted(unique_syms),
                    "shared_symbol_count": len(unique_syms),
                    "shared_consumers": sorted(entry["shared_consumers"]),
                    "shared_consumer_count": len(entry["shared_consumers"]),
                    "direction": "co_consumed",
                    "semantic_score": round(semantic_score + consumer_score, 2),
                }
            )
        results.sort(key=lambda x: (-x["semantic_score"], -x["shared_consumer_count"]))

        return {
            "document_path": document_path,
            "coupled_files": results,
            "total_coupled": len(results),
        }
