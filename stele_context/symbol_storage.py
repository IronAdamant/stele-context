"""
Symbol storage for Stele.

Handles persistent storage of extracted symbols and cross-file reference
edges in the SQLite database. Follows the same delegate pattern as
MetadataStorage and SessionStorage.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from stele_context.storage_schema import connect
from typing import Any


class SymbolStorage:
    """Persistent storage for symbols and symbol edges."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_tables()

    def _init_tables(self) -> None:
        """Create symbol and edge tables if they don't exist."""
        with connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    role TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    document_path TEXT NOT NULL,
                    line_number INTEGER,
                    container TEXT,
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
            # Migrate existing databases: add container column if missing
            cols = {
                row[1] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()
            }
            if "container" not in cols:
                conn.execute("ALTER TABLE symbols ADD COLUMN container TEXT")

            # File-level dependency fallback graph
            conn.execute("""
                CREATE TABLE IF NOT EXISTS file_dependencies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_document_path TEXT NOT NULL,
                    target_document_path TEXT NOT NULL,
                    dependency_type TEXT NOT NULL DEFAULT 'import',
                    symbol_name TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_deps_source ON file_dependencies(source_document_path)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_file_deps_target ON file_dependencies(target_document_path)"
            )

    # -- Bulk operations -----------------------------------------------------

    def store_symbols(self, symbols: list[Any]) -> None:
        """Store a batch of Symbol objects."""
        if not symbols:
            return
        with connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO symbols (name, kind, role, chunk_id, document_path, line_number, container) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        s.name,
                        s.kind,
                        s.role,
                        s.chunk_id,
                        s.document_path,
                        s.line_number,
                        getattr(s, "container", None),
                    )
                    for s in symbols
                ],
            )

    def store_edges(self, edges: list[tuple[str, str, str, str]]) -> None:
        """Store a batch of edges.

        Each edge: (source_chunk_id, target_chunk_id, edge_type, symbol_name).
        """
        if not edges:
            return
        with connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO symbol_edges "
                "(source_chunk_id, target_chunk_id, edge_type, symbol_name) "
                "VALUES (?, ?, ?, ?)",
                edges,
            )

    # -- Cleanup operations --------------------------------------------------

    def clear_document_symbols(self, document_path: str) -> None:
        """Remove all symbols for a document."""
        with connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM symbols WHERE document_path = ?", (document_path,)
            )

    def clear_chunk_edges(self, chunk_ids: list[str]) -> None:
        """Remove all edges involving the given chunk IDs."""
        if not chunk_ids:
            return
        placeholders = ",".join("?" * len(chunk_ids))
        with connect(self.db_path) as conn:
            conn.execute(
                f"DELETE FROM symbol_edges WHERE source_chunk_id IN ({placeholders}) "
                f"OR target_chunk_id IN ({placeholders})",
                chunk_ids + chunk_ids,
            )

    def get_edge_symbol_names_for_chunks(self, chunk_ids: list[str]) -> set[str]:
        """Return distinct symbol names from edges involving the given chunks."""
        if not chunk_ids:
            return set()
        placeholders = ",".join("?" * len(chunk_ids))
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT DISTINCT symbol_name FROM symbol_edges "
                f"WHERE source_chunk_id IN ({placeholders}) "
                f"OR target_chunk_id IN ({placeholders})",
                chunk_ids + chunk_ids,
            ).fetchall()
        return {r[0] for r in rows}

    def get_symbol_names_for_chunks(self, chunk_ids: list[str]) -> set[str]:
        """Return distinct symbol names defined or referenced in the given chunks."""
        if not chunk_ids:
            return set()
        placeholders = ",".join("?" * len(chunk_ids))
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT DISTINCT name FROM symbols WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            ).fetchall()
        return {r[0] for r in rows}

    def clear_chunk_symbols(self, chunk_ids: list[str]) -> None:
        """Remove all symbols for the given chunk IDs."""
        if not chunk_ids:
            return
        placeholders = ",".join("?" * len(chunk_ids))
        with connect(self.db_path) as conn:
            conn.execute(
                f"DELETE FROM symbols WHERE chunk_id IN ({placeholders})",
                chunk_ids,
            )

    def clear_all_symbols(self) -> None:
        """Remove all symbols."""
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM symbols")

    def clear_all_edges(self) -> None:
        """Remove all edges."""
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM symbol_edges")

    def store_file_dependencies(
        self, deps: list[tuple[str, str, str, str | None]]
    ) -> None:
        """Store file-level dependencies.

        Each dep: (source_document_path, target_document_path, dependency_type, symbol_name).
        """
        if not deps:
            return
        with connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO file_dependencies "
                "(source_document_path, target_document_path, dependency_type, symbol_name) "
                "VALUES (?, ?, ?, ?)",
                deps,
            )

    def clear_document_file_dependencies(self, document_path: str) -> None:
        """Remove file dependencies for a document (as source or target)."""
        with connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM file_dependencies WHERE source_document_path = ? OR target_document_path = ?",
                (document_path, document_path),
            )

    def clear_all_file_dependencies(self) -> None:
        """Remove all file-level dependencies."""
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM file_dependencies")

    def get_file_dependencies(self, document_path: str) -> list[dict[str, Any]]:
        """Get file dependencies where this document is the source."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM file_dependencies WHERE source_document_path = ?",
                (document_path,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_file_dependents(self, document_path: str) -> list[dict[str, Any]]:
        """Get file dependencies where this document is the target."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM file_dependencies WHERE target_document_path = ?",
                (document_path,),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- Query operations ---------------------------------------------------

    def get_all_symbols(self) -> list[dict[str, Any]]:
        """Get all symbols (for resolution)."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM symbols").fetchall()]

    def find_definitions(self, name: str) -> list[dict[str, Any]]:
        """Find all definitions for a symbol name."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbols WHERE name = ? AND role = 'definition'",
                    (name,),
                ).fetchall()
            ]

    def get_definition_file_counts(self, names: list[str]) -> dict[str, int]:
        """Return {name: distinct_document_count} for symbols defined as definitions.

        Used by ``coupling`` to weight ``semantic_score`` by uniqueness — a name
        like ``current`` defined in 50 files is much weaker evidence of coupling
        than a name like ``RegionStore`` defined in one. Names not present
        return 0.
        """
        if not names:
            return {}
        counts: dict[str, int] = {n: 0 for n in names}
        unique_names = list(set(names))
        with connect(self.db_path) as conn:
            for chunk_start in range(0, len(unique_names), 500):
                batch = unique_names[chunk_start : chunk_start + 500]
                placeholders = ",".join("?" * len(batch))
                rows = conn.execute(
                    f"SELECT name, COUNT(DISTINCT document_path) AS c "
                    f"FROM symbols WHERE role = 'definition' "
                    f"AND name IN ({placeholders}) GROUP BY name",
                    batch,
                ).fetchall()
                for row in rows:
                    counts[row[0]] = int(row[1])
        return counts

    def find_references_by_name(self, name: str) -> list[dict[str, Any]]:
        """Find all references to a symbol name."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbols WHERE name = ? AND role = 'reference'",
                    (name,),
                ).fetchall()
            ]

    def get_edges_for_chunk(self, chunk_id: str) -> list[dict[str, Any]]:
        """Get all edges involving a chunk (as source or target)."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbol_edges "
                    "WHERE source_chunk_id = ? OR target_chunk_id = ?",
                    (chunk_id, chunk_id),
                ).fetchall()
            ]

    def get_incoming_edges(self, chunk_id: str) -> list[dict[str, Any]]:
        """Get edges where other chunks reference this chunk (dependents)."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbol_edges WHERE target_chunk_id = ?",
                    (chunk_id,),
                ).fetchall()
            ]

    def get_outgoing_edges(self, chunk_id: str) -> list[dict[str, Any]]:
        """Get edges where this chunk references other chunks (dependencies)."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM symbol_edges WHERE source_chunk_id = ?",
                    (chunk_id,),
                ).fetchall()
            ]

    def search_symbol_names(self, tokens: list[str]) -> list[dict[str, Any]]:
        """Find definition symbols whose names match any of the given tokens.

        Uses case-insensitive exact match on symbol names.
        Returns unique (chunk_id, name, kind, document_path) tuples.
        """
        if not tokens:
            return []
        # Use OR conditions for each token
        conditions = " OR ".join(["LOWER(name) = ?"] * len(tokens))
        params = [t.lower() for t in tokens]
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT DISTINCT chunk_id, name, kind, document_path "
                f"FROM symbols WHERE role = 'definition' AND ({conditions})",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_symbols_for_chunks(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        """Get all symbols for a batch of chunk IDs."""
        if not chunk_ids:
            return []
        placeholders = ",".join("?" * len(chunk_ids))
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM symbols WHERE chunk_id IN ({placeholders})",
                    chunk_ids,
                ).fetchall()
            ]

    def get_symbol_stats(self) -> dict[str, Any]:
        """Get symbol and edge counts."""
        with connect(self.db_path) as conn:
            sym_count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            edge_count = conn.execute("SELECT COUNT(*) FROM symbol_edges").fetchone()[0]
            def_count = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE role = 'definition'"
            ).fetchone()[0]
            ref_count = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE role = 'reference'"
            ).fetchone()[0]
            runtime_count = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE chunk_id LIKE 'runtime:%'"
            ).fetchone()[0]
            return {
                "symbol_count": sym_count,
                "definition_count": def_count,
                "reference_count": ref_count,
                "edge_count": edge_count,
                "runtime_symbol_count": runtime_count,
            }

    def store_dynamic_symbols(
        self, symbols: list[dict[str, Any]], agent_id: str
    ) -> dict[str, Any]:
        """Store runtime/manifest symbols that don't correspond to real chunks.

        Runtime symbols use pseudo chunk_ids prefixed with ``runtime:{agent_id}:``.
        They appear in find_references, coupling, and impact_radius just like
        static symbols, enabling the symbol graph to model dynamic registrations
        (plugin hooks, runtime callbacks, etc.).

        Args:
            symbols: List of dicts with keys: name, kind, role, document_path,
                line_number (optional), description (optional).
            agent_id: Agent registering these symbols (for chunk_id namespacing).

        Returns dict with stored count and any validation errors.
        """
        if not symbols:
            return {"stored": 0, "errors": []}

        stored = 0
        errors: list[str] = []
        for sym in symbols:
            name = sym.get("name")
            kind = sym.get("kind", "function")
            role = sym.get("role", "definition")
            doc_path = sym.get("document_path", "")
            line_number = sym.get("line_number")

            if not name:
                errors.append(f"missing name in symbol: {sym}")
                continue
            if role not in ("definition", "reference"):
                errors.append(f"invalid role '{role}' for symbol '{name}'")
                continue

            chunk_id = f"runtime:{agent_id}:{name}"
            container = sym.get("container")
            with connect(self.db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO symbols "
                    "(name, kind, role, chunk_id, document_path, line_number, container) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (name, kind, role, chunk_id, doc_path, line_number, container),
                )
            stored += 1

        return {"stored": stored, "errors": errors}

    def remove_dynamic_symbols(self, agent_id: str) -> dict[str, Any]:
        """Remove all runtime symbols registered by a given agent.

        Returns count of removed symbols.
        """
        prefix = f"runtime:{agent_id}:"
        with connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM symbols WHERE chunk_id LIKE ?",
                (prefix + "%",),
            )
            count = cur.fetchone()[0]
            conn.execute(
                "DELETE FROM symbols WHERE chunk_id LIKE ?",
                (prefix + "%",),
            )
        return {"removed": count}

    def get_dynamic_symbols(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """Get all runtime symbols, optionally filtered by agent."""
        with connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM symbols WHERE chunk_id LIKE ? ORDER BY name",
                    (f"runtime:{agent_id}:%",),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM symbols WHERE chunk_id LIKE 'runtime:%' ORDER BY name"
                ).fetchall()
            return [dict(r) for r in rows]
