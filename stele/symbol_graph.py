"""
Symbol graph manager for Stele.

Extracted from engine.py to follow the delegate pattern (like SessionManager).
Handles symbol extraction, edge resolution, staleness propagation,
and symbol-based queries (find_references, find_definition, impact_radius).
"""

from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from stele.chunkers.base import Chunk
from stele.symbols import SymbolExtractor, Symbol, resolve_symbols


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
    def _dicts_to_symbols(raw: List[Dict]) -> List[Symbol]:
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

    def attach_edges(self, entry: Dict, chunk_id: str) -> None:
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
        chunks: List[Chunk],
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
        affected_chunk_ids: Optional[Set[str]] = None,
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
        changed_chunk_ids: Set[str],
        decay: float = 0.8,
        max_depth: int = 3,
    ) -> int:
        """Propagate staleness scores through the symbol graph.

        When chunks change, their dependents become potentially stale.
        Score decays by ``decay`` per hop.  Changed chunks get score 0.
        Returns the number of chunks marked stale.
        """
        self.storage.set_staleness_batch([(0.0, cid) for cid in changed_chunk_ids])

        visited: Dict[str, float] = {}
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

    def stale_chunks(self, threshold: float = 0.3) -> Dict[str, Any]:
        """Get chunks with staleness_score >= threshold, grouped by file."""
        stale = self.storage.get_stale_chunks(threshold)

        by_doc: Dict[str, list] = {}
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

    def find_references(self, symbol: str) -> Dict[str, Any]:
        """Find all definitions and references for a symbol name."""
        definitions = self.storage.find_definitions(symbol)
        references = self.storage.find_references_by_name(symbol)

        def _enrich(syms: List[Dict]) -> List[Dict]:
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

        return {
            "symbol": symbol,
            "definitions": _enrich(definitions),
            "references": _enrich(references),
            "total": len(definitions) + len(references),
        }

    def find_definition(self, symbol: str) -> Dict[str, Any]:
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
        chunk_id: str,
        depth: int = 2,
    ) -> Dict[str, Any]:
        """Find all chunks affected by a change to this chunk (BFS)."""
        visited: set = set()
        queue = deque([(chunk_id, 0)])
        layers: Dict[int, List[str]] = {}

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

        result_chunks = []
        for d, chunk_ids in sorted(layers.items()):
            for cid in chunk_ids:
                if cid == chunk_id and d == 0:
                    continue
                meta = self.storage.get_chunk(cid)
                if meta:
                    result_chunks.append(
                        {
                            "chunk_id": cid,
                            "document_path": meta["document_path"],
                            "depth": d,
                            "content": meta.get("content"),
                            "token_count": meta["token_count"],
                        }
                    )

        return {
            "origin_chunk_id": chunk_id,
            "max_depth": depth,
            "affected_chunks": len(result_chunks),
            "chunks": result_chunks,
        }

    def rebuild_graph(self) -> Dict[str, Any]:
        """Rebuild the entire symbol graph from stored chunk content."""
        all_chunks = self.storage.search_chunks()

        self.storage.clear_all_symbols()
        self.storage.clear_all_edges()

        by_doc: Dict[str, list] = {}
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
