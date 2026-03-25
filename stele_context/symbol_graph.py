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
from stele_context.symbols import SymbolExtractor, Symbol, resolve_symbols


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
        are cleared and re-created (incremental).  Otherwise, all edges
        are rebuilt from scratch.
        """
        all_syms_raw = self.storage.get_all_symbols()
        if not all_syms_raw:
            if affected_chunk_ids is None:
                self.storage.clear_all_edges()
            return

        all_syms = self._dicts_to_symbols(all_syms_raw)
        all_edges = resolve_symbols(all_syms)

        if affected_chunk_ids is None:
            self.storage.clear_all_edges()
            self.storage.store_edges(all_edges)
        else:
            self.storage.clear_chunk_edges(list(affected_chunk_ids))
            scoped = [
                e
                for e in all_edges
                if e[0] in affected_chunk_ids or e[1] in affected_chunk_ids
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

        return {
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

    def find_references(self, symbol: str) -> dict[str, Any]:
        """Find all definitions and references for a symbol name."""
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

        return {
            "symbol": symbol,
            "verdict": verdict,
            "definitions": enriched_defs,
            "references": enriched_refs,
            "total": len(definitions) + len(references),
        }

    def find_definition(self, symbol: str) -> dict[str, Any]:
        """Find definition location(s) of a symbol."""
        definitions = self.storage.find_definitions(symbol)

        results = []
        for defn in definitions:
            chunk = self.storage.get_chunk(defn["chunk_id"])
            results.append(
                {
                    "symbol": defn["name"],
                    "kind": defn["kind"],
                    "chunk_id": defn["chunk_id"],
                    "document_path": defn["document_path"],
                    "line_number": defn.get("line_number"),
                    "content": chunk.get("content") if chunk else None,
                    "token_count": chunk["token_count"] if chunk else 0,
                }
            )

        return {
            "symbol": symbol,
            "definitions": results,
            "count": len(results),
        }

    def impact_radius(
        self,
        chunk_id: str | None = None,
        depth: int = 2,
        document_path: str | None = None,
        *,
        compact: bool = False,
        include_content: bool = True,
        path_filter: str | None = None,
    ) -> dict[str, Any]:
        """Find all chunks affected by a change to a chunk or file (BFS).

        Accepts either ``chunk_id`` (single chunk) or ``document_path``
        (all chunks in a file).  At least one must be provided.

        ``compact`` returns per-file summaries instead of per-chunk records.
        ``include_content=False`` omits chunk text (smaller payloads).
        ``path_filter`` keeps only results whose path contains the substring.
        """
        # Resolve seed chunk IDs
        if document_path and not chunk_id:
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
            return {"error": "Provide chunk_id or document_path"}

        visited: set[str] = set()
        queue = deque((cid, 0) for cid in seed_ids)
        layers: dict[int, list[str]] = {}

        while queue:
            current_id, current_depth = queue.popleft()
            if current_id in visited or current_depth > depth:
                continue
            visited.add(current_id)
            layers.setdefault(current_depth, []).append(current_id)

            if current_depth < depth:
                edges = self.storage.get_incoming_edges(current_id)
                for edge in edges:
                    if edge["source_chunk_id"] not in visited:
                        queue.append((edge["source_chunk_id"], current_depth + 1))

        result_chunks: list[dict[str, Any]] = []
        affected_files: set[str] = set()
        for d, chunk_ids_at_depth in sorted(layers.items()):
            for cid in chunk_ids_at_depth:
                if cid in seed_ids and d == 0:
                    continue
                meta = self.storage.get_chunk(cid)
                if meta:
                    doc_p = meta["document_path"]
                    if path_filter is not None and path_filter not in doc_p:
                        continue
                    affected_files.add(doc_p)
                    row: dict[str, Any] = {
                        "chunk_id": cid,
                        "document_path": doc_p,
                        "depth": d,
                        "token_count": meta["token_count"],
                    }
                    if include_content:
                        row["content"] = meta.get("content")
                    result_chunks.append(row)

        out: dict[str, Any] = {
            "origin": origin,
            "max_depth": depth,
            "affected_chunks": len(result_chunks),
            "affected_files": len(affected_files),
        }
        if compact:
            by_file: dict[str, dict[str, Any]] = {}
            for row in result_chunks:
                p = row["document_path"]
                dval = row["depth"]
                if p not in by_file:
                    by_file[p] = {
                        "path": p,
                        "chunk_count": 0,
                        "depth_min": dval,
                        "depth_max": dval,
                    }
                e = by_file[p]
                e["chunk_count"] += 1
                e["depth_min"] = min(e["depth_min"], dval)
                e["depth_max"] = max(e["depth_max"], dval)
            out["files"] = sorted(by_file.values(), key=lambda x: x["path"])
            out["chunks"] = []
        else:
            out["chunks"] = result_chunks
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

    def coupling(self, document_path: str) -> dict[str, Any]:
        """Find files semantically coupled to a given file via symbol edges.

        Queries all symbol edges involving chunks from the target document,
        groups by the OTHER document, and counts shared symbols per pair.
        """
        doc_chunks = self.storage.get_document_chunks(document_path)
        if not doc_chunks:
            return {
                "document_path": document_path,
                "coupled_files": [],
                "total_coupled": 0,
            }

        doc_chunk_ids = {c["chunk_id"] for c in doc_chunks}

        # Collect edges in both directions
        coupled: dict[str, dict[str, Any]] = {}
        for cid in doc_chunk_ids:
            # Outgoing: this file depends on other files
            for edge in self.storage.get_outgoing_edges(cid):
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
                entry["symbols"].add(edge["symbol_name"])
                entry["outgoing"] += 1

            # Incoming: other files depend on this file
            for edge in self.storage.get_incoming_edges(cid):
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
                entry["symbols"].add(edge["symbol_name"])
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
            results.append(
                {
                    "path": entry["path"],
                    "shared_symbols": sorted(entry["symbols"]),
                    "shared_symbol_count": len(entry["symbols"]),
                    "direction": direction,
                    "edge_count": total,
                }
            )
        results.sort(key=lambda x: x["edge_count"], reverse=True)

        return {
            "document_path": document_path,
            "coupled_files": results,
            "total_coupled": len(results),
        }
