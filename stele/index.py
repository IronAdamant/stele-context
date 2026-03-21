"""
Vector index for Stele.

Provides fast approximate nearest neighbor search using HNSW
(Hierarchical Navigable Small World) algorithm. Pure Python
implementation with zero dependencies.

This enables O(log n) similarity search instead of O(n) scan,
dramatically improving performance for large chunk collections.
"""

from __future__ import annotations

import array
import heapq
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


def _to_float_array(vec: Any) -> array.array:
    """Convert a vector to array.array('f') for better memory and cache locality."""
    if isinstance(vec, array.array) and vec.typecode == "f":
        return vec
    return array.array("f", vec)


@dataclass
class IndexNode:
    """A node in the HNSW graph."""

    node_id: str
    vector: Any  # array.array('f') for performance
    level: int = 0
    connections: dict[int, set[str]] = field(default_factory=lambda: defaultdict(set))
    _norm: float | None = field(default=None, repr=False)

    @property
    def norm(self) -> float:
        """Cached L2 norm of the vector."""
        if self._norm is None:
            self._norm = math.sqrt(sum(x * x for x in self.vector))
        return self._norm


class HNSWIndex:
    """
    Hierarchical Navigable Small World index for fast similarity search.

    Provides approximate nearest neighbor search in O(log n) time.
    Pure Python implementation with zero dependencies.

    Parameters:
        M: Maximum connections per node (default: 16)
        ef_construction: Search width during construction (default: 200)
        ef_search: Search width during query (default: 50)
        ml: Level generation factor (default: 1/ln(M))
    """

    def __init__(
        self,
        M: int = 16,
        ef_construction: int = 200,
        ef_search: int = 50,
        ml: float | None = None,
    ):
        """
        Initialize HNSW index.

        Args:
            M: Maximum connections per node
            ef_construction: Search width during construction
            ef_search: Search width during query
            ml: Level generation factor (auto-calculated if None)
        """
        self.M = M
        self.M_max0 = M * 2  # Max connections for level 0
        self.ef_construction = ef_construction
        self.ef_search = ef_search
        self.ml = ml if ml is not None else 1.0 / math.log(M)

        # Storage
        self.nodes: dict[str, IndexNode] = {}
        self.entry_point: str | None = None
        self.max_level: int = 0

        # Statistics
        self._insert_count: int = 0

    def _adaptive_ef(self, k: int) -> int:
        """Scale ef_search based on index size and requested k."""
        n = len(self.nodes)
        if n < 100:
            return max(k, 10)
        if n < 1000:
            return max(k, self.ef_search)
        if n < 10000:
            return max(k, self.ef_search * 2)
        return max(k, self.ef_search * 4)

    def _random_level(self) -> int:
        """Generate random level for new node."""
        level = 0
        while random.random() < self.ml and level < 32:
            level += 1
        return level

    def _distance(self, vec1: Any, vec2: Any) -> float:
        """Distance for normalized vectors (1 - dot product).

        For L2-normalized vectors this preserves the same ordering as
        Euclidean distance but avoids the sqrt and per-element subtract.
        """
        return 1.0 - sum(a * b for a, b in zip(vec1, vec2))

    def _cosine_similarity(
        self,
        vec1: Any,
        vec2: Any,
        norm1: float | None = None,
        norm2: float | None = None,
    ) -> float:
        """Compute cosine similarity between vectors, using cached norms if provided."""
        dot = sum(a * b for a, b in zip(vec1, vec2))
        n1 = norm1 if norm1 is not None else math.sqrt(sum(a * a for a in vec1))
        n2 = norm2 if norm2 is not None else math.sqrt(sum(b * b for b in vec2))

        if n1 == 0 or n2 == 0:
            return 0.0

        return dot / (n1 * n2)

    def _search_layer(
        self,
        query: list[float] | array.array,
        entry_points: set[str],
        ef: int,
        level: int,
    ) -> list[tuple[float, str]]:
        """
        Search for nearest neighbors in a single layer.

        Args:
            query: Query vector
            entry_points: Starting points for search
            ef: Number of candidates to explore
            level: Current level

        Returns:
            List of (distance, node_id) tuples sorted by distance
        """
        # Visited set
        visited: set[str] = set(entry_points)

        # Candidates (min-heap by distance)
        candidates: list[tuple[float, str]] = []
        for ep in entry_points:
            if ep in self.nodes:
                dist = self._distance(query, self.nodes[ep].vector)
                heapq.heappush(candidates, (dist, ep))

        # Results (max-heap by distance, keep worst at top)
        results: list[tuple[float, str]] = []
        for dist, node_id in candidates:
            heapq.heappush(results, (-dist, node_id))

        while candidates:
            # Get closest candidate
            cand_dist, cand_id = heapq.heappop(candidates)

            # If candidate is farther than worst result, stop
            if results and cand_dist > -results[0][0]:
                break

            # Explore neighbors
            if cand_id in self.nodes:
                node = self.nodes[cand_id]
                neighbors = node.connections.get(level, set())

                for neighbor_id in neighbors:
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)

                        if neighbor_id in self.nodes:
                            neighbor = self.nodes[neighbor_id]
                            dist = self._distance(query, neighbor.vector)

                            # If better than worst result, add to candidates
                            if len(results) < ef or dist < -results[0][0]:
                                heapq.heappush(candidates, (dist, neighbor_id))
                                heapq.heappush(results, (-dist, neighbor_id))

                                # Keep only top ef results
                                if len(results) > ef:
                                    heapq.heappop(results)

        # Convert to sorted list
        return sorted([(-dist, node_id) for dist, node_id in results])

    def _select_neighbors(
        self,
        node_id: str,
        candidates: list[tuple[float, str]],
        M: int,
        level: int,
    ) -> list[str]:
        """
        Select best neighbors for a node using simple heuristic.

        Args:
            node_id: Node to select neighbors for
            candidates: List of (distance, candidate_id) tuples
            M: Maximum number of neighbors
            level: Current level

        Returns:
            List of selected neighbor IDs
        """
        # Sort by distance
        candidates.sort(key=lambda x: x[0])

        # Select closest M neighbors
        selected: list[str] = []
        for dist, cand_id in candidates:
            if len(selected) >= M:
                break
            if cand_id != node_id:
                selected.append(cand_id)

        return selected

    def insert(self, node_id: str, vector: Any) -> None:
        """
        Insert a vector into the index.

        Args:
            node_id: Unique identifier for the vector
            vector: Vector to insert (converted to array.array('f') internally)
        """
        vec = _to_float_array(vector)
        if node_id in self.nodes:
            # Update existing node (invalidate cached norm)
            self.nodes[node_id].vector = vec
            self.nodes[node_id]._norm = None
            return

        # Create new node
        level = self._random_level()
        node = IndexNode(node_id=node_id, vector=vec, level=level)
        self.nodes[node_id] = node

        # If first node, set as entry point
        if self.entry_point is None:
            self.entry_point = node_id
            self.max_level = level
            return

        # Find entry point for insertion
        current_entry = self.entry_point
        current_level = self.max_level

        # Navigate down from top level
        for lc in range(current_level, level, -1):
            # Search in this layer
            results = self._search_layer(
                vec,
                {current_entry},
                ef=1,
                level=lc,
            )
            if results:
                current_entry = results[0][1]

        # Insert at each level
        for lc in range(min(level, current_level), -1, -1):
            # Search for neighbors
            results = self._search_layer(
                vec,
                {current_entry},
                ef=self.ef_construction,
                level=lc,
            )

            # Select neighbors
            M = self.M_max0 if lc == 0 else self.M
            neighbors = self._select_neighbors(node_id, results, M, lc)

            # Add connections
            for neighbor_id in neighbors:
                node.connections[lc].add(neighbor_id)
                self.nodes[neighbor_id].connections[lc].add(node_id)

                # Prune neighbor's connections if needed
                neighbor = self.nodes[neighbor_id]
                max_conn = self.M_max0 if lc == 0 else self.M
                if len(neighbor.connections[lc]) > max_conn:
                    # Get neighbor's neighbors
                    nn_results = []
                    for nn_id in neighbor.connections[lc]:
                        if nn_id in self.nodes:
                            nn_dist = self._distance(
                                neighbor.vector,
                                self.nodes[nn_id].vector,
                            )
                            nn_results.append((nn_dist, nn_id))

                    # Select best neighbors
                    new_neighbors = self._select_neighbors(
                        neighbor_id, nn_results, max_conn, lc
                    )

                    # Update connections
                    removed = neighbor.connections[lc] - set(new_neighbors)
                    neighbor.connections[lc] = set(new_neighbors)

                    for removed_id in removed:
                        if removed_id in self.nodes:
                            self.nodes[removed_id].connections[lc].discard(neighbor_id)

            # Update entry point for next level
            if results:
                current_entry = results[0][1]

        # Update entry point if new node has higher level
        if level > self.max_level:
            self.entry_point = node_id
            self.max_level = level

        self._insert_count += 1

    def search(
        self,
        query: list[float],
        k: int = 10,
        ef: int | None = None,
    ) -> list[tuple[str, float]]:
        """
        Search for k nearest neighbors.

        Args:
            query: Query vector
            k: Number of results to return
            ef: Search width (defaults to ef_search)

        Returns:
            List of (node_id, similarity_score) tuples sorted by similarity
        """
        if not self.nodes:
            return []

        if ef is None:
            ef = self._adaptive_ef(k)

        # Start from entry point
        if self.entry_point is None:
            return []
        current_entry: str = self.entry_point
        current_level = self.max_level

        # Navigate down from top level
        for lc in range(current_level, 0, -1):
            results = self._search_layer(
                query,
                {current_entry},
                ef=1,
                level=lc,
            )
            if results:
                current_entry = results[0][1]

        # Search at level 0
        results = self._search_layer(
            query,
            {current_entry},
            ef=max(ef, k),
            level=0,
        )

        # Convert distances to similarities and return top k
        # Pre-compute query norm once for all comparisons
        query_norm = math.sqrt(sum(a * a for a in query))
        similarities = []
        for dist, node_id in results[:k]:
            if node_id in self.nodes:
                node = self.nodes[node_id]
                similarity = self._cosine_similarity(
                    query,
                    node.vector,
                    norm1=query_norm,
                    norm2=node.norm,
                )
                similarities.append((node_id, similarity))

        # Sort by similarity (descending)
        similarities.sort(key=lambda x: x[1], reverse=True)

        return similarities[:k]

    def remove(self, node_id: str) -> bool:
        """
        Remove a vector from the index.

        Args:
            node_id: ID of vector to remove

        Returns:
            True if removed, False if not found
        """
        if node_id not in self.nodes:
            return False

        node = self.nodes[node_id]

        # Remove connections and repair graph connectivity
        for level, neighbors in node.connections.items():
            neighbor_list = [n for n in neighbors if n in self.nodes]
            max_conn = self.M_max0 if level == 0 else self.M

            # Remove the deleted node from all neighbours
            for neighbor_id in neighbor_list:
                self.nodes[neighbor_id].connections[level].discard(node_id)

            # Reconnect neighbours to each other to prevent graph fragmentation
            for neighbor_id in neighbor_list:
                neighbor = self.nodes[neighbor_id]
                if len(neighbor.connections[level]) < max_conn:
                    for other_id in neighbor_list:
                        if (
                            other_id != neighbor_id
                            and other_id not in neighbor.connections[level]
                        ):
                            if len(neighbor.connections[level]) >= max_conn:
                                break
                            neighbor.connections[level].add(other_id)
                            self.nodes[other_id].connections[level].add(neighbor_id)

        # Remove node
        del self.nodes[node_id]

        # Update entry point if needed
        if self.entry_point == node_id:
            if self.nodes:
                # Find new entry point (highest level)
                self.entry_point = max(
                    self.nodes.keys(),
                    key=lambda nid: self.nodes[nid].level,
                )
                self.max_level = self.nodes[self.entry_point].level
            else:
                self.entry_point = None
                self.max_level = 0

        return True

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        if not self.nodes:
            return {
                "node_count": 0,
                "max_level": 0,
                "avg_connections": 0.0,
            }

        total_connections = sum(
            sum(len(conns) for conns in node.connections.values())
            for node in self.nodes.values()
        )

        return {
            "node_count": len(self.nodes),
            "max_level": self.max_level,
            "avg_connections": total_connections / len(self.nodes),
            "insert_count": self._insert_count,
        }

    def clear(self) -> None:
        """Clear all vectors from the index."""
        self.nodes.clear()
        self.entry_point = None
        self.max_level = 0
        self._insert_count = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize index state to a plain dict."""
        nodes = {}
        for nid, node in self.nodes.items():
            nodes[nid] = {
                "v": list(node.vector),  # array.array -> list for JSON
                "l": node.level,
                "c": {str(k): sorted(v) for k, v in node.connections.items()},
            }
        return {
            "M": self.M,
            "M0": self.M_max0,
            "ef_c": self.ef_construction,
            "ef_s": self.ef_search,
            "ml": self.ml,
            "ep": self.entry_point,
            "max_l": self.max_level,
            "nodes": nodes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HNSWIndex":
        """Reconstruct index from serialized dict."""
        idx = cls(
            M=data["M"],
            ef_construction=data["ef_c"],
            ef_search=data["ef_s"],
            ml=data["ml"],
        )
        idx.M_max0 = data["M0"]
        idx.entry_point = data["ep"]
        idx.max_level = data["max_l"]
        for nid, ndata in data["nodes"].items():
            node = IndexNode(
                node_id=nid,
                vector=_to_float_array(ndata["v"]),
                level=ndata["l"],
                connections=defaultdict(
                    set, {int(k): set(v) for k, v in ndata["c"].items()}
                ),
            )
            idx.nodes[nid] = node
        idx._insert_count = len(idx.nodes)
        return idx


class VectorIndex:
    """
    High-level vector index for Stele.

    Wraps HNSW index with chunk-specific functionality.
    """

    def __init__(
        self,
        M: int = 16,
        ef_construction: int = 200,
        ef_search: int = 50,
    ):
        """
        Initialize vector index.

        Args:
            M: Maximum connections per node
            ef_construction: Search width during construction
            ef_search: Search width during query
        """
        self.index = HNSWIndex(
            M=M,
            ef_construction=ef_construction,
            ef_search=ef_search,
        )

    def add_chunk(self, chunk_id: str, vector: list[float]) -> None:
        """
        Add a chunk vector to the index.

        Args:
            chunk_id: Chunk identifier
            vector: Semantic signature vector
        """
        self.index.insert(chunk_id, vector)

    def search(
        self,
        query_vector: list[float],
        k: int = 10,
    ) -> list[tuple[str, float]]:
        """
        Search for similar chunks.

        Args:
            query_vector: Query vector
            k: Number of results

        Returns:
            List of (chunk_id, similarity_score) tuples
        """
        return self.index.search(query_vector, k=k)

    def remove_chunk(self, chunk_id: str) -> bool:
        """
        Remove a chunk from the index.

        Args:
            chunk_id: Chunk identifier

        Returns:
            True if removed
        """
        return self.index.remove(chunk_id)

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        stats = self.index.get_stats()
        stats["chunk_count"] = len(self.index.nodes)
        return stats

    def clear(self) -> None:
        """Clear all chunks from the index."""
        self.index.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (HNSW graph with embedded vectors)."""
        return {"hnsw": self.index.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VectorIndex":
        """Reconstruct from serialized dict (handles old and new format)."""
        vi = cls.__new__(cls)
        vi.index = HNSWIndex.from_dict(data["hnsw"])
        return vi
