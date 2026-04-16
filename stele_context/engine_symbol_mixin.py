"""
Engine mixin — symbol-graph query and dynamic-symbol registration methods.

Contains the symbol-graph-facing methods of the `Stele` facade:
`find_references`, `find_definition`, `impact_radius`, `coupling`,
`rebuild_symbol_graph`, plus dynamic-symbol registration
(`register_dynamic_symbols`, `remove_dynamic_symbols`, `get_dynamic_symbols`).

**Inclusion criterion:** a method belongs here if it operates on the symbol
graph via `self.symbol_manager` or dynamic-symbol storage. Static chunk storage,
search, locks, and other domains live in their own mixins.

Relies on `self._lock`, `self.symbol_manager`, `self.storage`, and
`self._normalize_path` being provided by `Stele.__init__`.
"""

from __future__ import annotations

from typing import Any


class _SymbolMixin:
    """Symbol-graph query + dynamic-symbol registration methods for `Stele`."""

    # Attributes/methods provided by Stele.__init__ or other mixins.
    # Declared for mypy; kept as Any to avoid import cycles.
    _lock: Any
    storage: Any
    symbol_manager: Any
    _normalize_path: Any

    def find_references(self, symbol: str) -> dict[str, Any]:
        with self._lock.read_lock():
            return self.symbol_manager.find_references(symbol)

    def find_definition(self, symbol: str) -> dict[str, Any]:
        with self._lock.read_lock():
            return self.symbol_manager.find_definition(symbol)

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
        if document_path:
            document_path = self._normalize_path(document_path)
        with self._lock.read_lock():
            return self.symbol_manager.impact_radius(
                chunk_id,
                depth,
                document_path,
                symbol=symbol,
                compact=compact,
                include_content=include_content,
                path_filter=path_filter,
                summary_mode=summary_mode,
                top_n_files=top_n_files,
                significance_threshold=significance_threshold,
                exclude_symbols=exclude_symbols,
                direction=direction,
            )

    def coupling(
        self,
        document_path: str,
        significance_threshold: float = 0.0,
        exclude_symbols: list[str] | None = None,
        mode: str = "edges",
    ) -> dict[str, Any]:
        document_path = self._normalize_path(document_path)
        with self._lock.read_lock():
            return self.symbol_manager.coupling(
                document_path,
                significance_threshold=significance_threshold,
                exclude_symbols=exclude_symbols,
                mode=mode,
            )

    def rebuild_symbol_graph(self) -> dict[str, Any]:
        with self._lock.write_lock():
            return self.symbol_manager.rebuild_graph()

    def register_dynamic_symbols(
        self,
        symbols: list[dict[str, Any]],
        agent_id: str,
    ) -> dict[str, Any]:
        """Register runtime/dynamic symbols that don't correspond to indexed chunks.

        Use this for plugin hook registrations, runtime callbacks, and other
        symbols that only exist at runtime and are invisible to static analysis.

        Dynamic symbols appear in ``find_references``, ``coupling``, and
        ``impact_radius`` just like statically-extracted symbols, enabling the
        symbol graph to model dynamic registration patterns.

        Symbols are namespaced by agent_id in the storage layer
        (``runtime:{agent_id}:{name}``) and can be removed with
        ``remove_dynamic_symbols``.

        Args:
            symbols: List of dicts with keys: name (required), kind
                (default "function"), role (default "definition"),
                document_path (default ""), line_number (optional).
            agent_id: Agent registering these symbols (used for namespacing
                and later removal).

        Example::

            engine.register_dynamic_symbols(
                symbols=[
                    {"name": "on_recipe_validated", "kind": "function",
                     "document_path": "src/plugins/hooks.js"},
                    {"name": "dietary_check_hook", "kind": "function",
                     "role": "reference",
                     "document_path": "src/services/validator.js"},
                ],
                agent_id="my-agent-123",
            )
        """
        with self._lock.write_lock():
            result = self.storage.store_dynamic_symbols(symbols, agent_id)
            if result.get("stored"):
                # Rebuild edges so dynamic symbols are connected into the graph.
                self.symbol_manager.rebuild_edges()
            return result

    def remove_dynamic_symbols(self, agent_id: str) -> dict[str, Any]:
        """Remove all dynamic symbols previously registered by an agent.

        Returns count of removed symbols.
        """
        with self._lock.write_lock():
            result = self.storage.remove_dynamic_symbols(agent_id)
            if result.get("removed"):
                self.symbol_manager.rebuild_edges()
            return result

    def get_dynamic_symbols(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        """List all registered dynamic/runtime symbols, optionally filtered."""
        with self._lock.read_lock():
            return self.storage.get_dynamic_symbols(agent_id)
