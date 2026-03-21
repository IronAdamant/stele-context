"""
Symbol storage for Stele.

Handles persistent storage of extracted symbols and cross-file reference
edges in the SQLite database. Follows the same delegate pattern as
MetadataStorage and SessionStorage.
"""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple


class SymbolStorage:
    """Persistent storage for symbols and symbol edges."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_tables()

    def _init_tables(self) -> None:
        """Create symbol and edge tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    role TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    document_path TEXT NOT NULL,
                    line_number INTEGER,
                    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbol_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_chunk_id TEXT NOT NULL,
                    target_chunk_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    symbol_name TEXT NOT NULL,
                    FOREIGN KEY (source_chunk_id) REFERENCES chunks(chunk_id),
                    FOREIGN KEY (target_chunk_id) REFERENCES chunks(chunk_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbols_chunk ON symbols(chunk_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbols_doc ON symbols(document_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbols_role ON symbols(name, role)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_edges_source "
                "ON symbol_edges(source_chunk_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_edges_target "
                "ON symbol_edges(target_chunk_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_edges_symbol "
                "ON symbol_edges(symbol_name)"
            )
            conn.commit()

    # -- Bulk operations -----------------------------------------------------

    def store_symbols(self, symbols: List[Any]) -> None:
        """Store a batch of Symbol objects."""
        if not symbols:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO symbols (name, kind, role, chunk_id, document_path, line_number) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (s.name, s.kind, s.role, s.chunk_id, s.document_path, s.line_number)
                    for s in symbols
                ],
            )
            conn.commit()

    def store_edges(self, edges: List[Tuple[str, str, str, str]]) -> None:
        """Store a batch of edges.

        Each edge: (source_chunk_id, target_chunk_id, edge_type, symbol_name).
        """
        if not edges:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO symbol_edges "
                "(source_chunk_id, target_chunk_id, edge_type, symbol_name) "
                "VALUES (?, ?, ?, ?)",
                edges,
            )
            conn.commit()

    # -- Cleanup operations --------------------------------------------------

    def clear_document_symbols(self, document_path: str) -> None:
        """Remove all symbols for a document."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM symbols WHERE document_path = ?", (document_path,)
            )
            conn.commit()

    def clear_chunk_edges(self, chunk_ids: List[str]) -> None:
        """Remove all edges involving the given chunk IDs."""
        if not chunk_ids:
            return
        placeholders = ",".join("?" * len(chunk_ids))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"DELETE FROM symbol_edges WHERE source_chunk_id IN ({placeholders}) "
                f"OR target_chunk_id IN ({placeholders})",
                chunk_ids + chunk_ids,
            )
            conn.commit()

    def clear_chunk_symbols(self, chunk_ids: List[str]) -> None:
        """Remove all symbols for the given chunk IDs."""
        if not chunk_ids:
            return
        placeholders = ",".join("?" * len(chunk_ids))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"DELETE FROM symbols WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )
            conn.commit()

    def clear_all_symbols(self) -> None:
        """Remove all symbols."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM symbols")
            conn.commit()

    def clear_all_edges(self) -> None:
        """Remove all edges."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM symbol_edges")
            conn.commit()

    # -- Query operations ----------------------------------------------------

    def get_all_symbols(self) -> List[Dict[str, Any]]:
        """Get all symbols (for resolution)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM symbols").fetchall()]

    def find_definitions(self, name: str) -> List[Dict[str, Any]]:
        """Find all definitions for a symbol name."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbols WHERE name = ? AND role = 'definition'",
                    (name,),
                ).fetchall()
            ]

    def find_references_by_name(self, name: str) -> List[Dict[str, Any]]:
        """Find all references to a symbol name."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbols WHERE name = ? AND role = 'reference'",
                    (name,),
                ).fetchall()
            ]

    def get_edges_for_chunk(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get all edges involving a chunk (as source or target)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbol_edges "
                    "WHERE source_chunk_id = ? OR target_chunk_id = ?",
                    (chunk_id, chunk_id),
                ).fetchall()
            ]

    def get_incoming_edges(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get edges where other chunks reference this chunk (dependents)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbol_edges WHERE target_chunk_id = ?",
                    (chunk_id,),
                ).fetchall()
            ]

    def get_outgoing_edges(self, chunk_id: str) -> List[Dict[str, Any]]:
        """Get edges where this chunk references other chunks (dependencies)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbol_edges WHERE source_chunk_id = ?",
                    (chunk_id,),
                ).fetchall()
            ]

    def search_symbol_names(self, tokens: List[str]) -> List[Dict[str, Any]]:
        """Find definition symbols whose names match any of the given tokens.

        Uses case-insensitive exact match on symbol names.
        Returns unique (chunk_id, name, kind, document_path) tuples.
        """
        if not tokens:
            return []
        # Use OR conditions for each token
        conditions = " OR ".join(["LOWER(name) = ?"] * len(tokens))
        params = [t.lower() for t in tokens]
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT DISTINCT chunk_id, name, kind, document_path "
                f"FROM symbols WHERE role = 'definition' AND ({conditions})",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_symbol_stats(self) -> Dict[str, Any]:
        """Get symbol and edge counts."""
        with sqlite3.connect(self.db_path) as conn:
            sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM symbol_edges").fetchone()[0]
            def_count = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE role = 'definition'"
            ).fetchone()[0]
            ref_count = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE role = 'reference'"
            ).fetchone()[0]
            return {
                "symbol_count": sym_count,
                "definition_count": def_count,
                "reference_count": ref_count,
                "edge_count": edge_count,
            }
