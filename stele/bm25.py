"""
BM25 keyword index for Stele.

Provides term-frequency based scoring to complement HNSW vector search
for hybrid retrieval. Pure Python implementation with zero dependencies.
"""

import math
import re
from collections import Counter
from typing import Dict, List

_WORD_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")


class BM25Index:
    """
    Okapi BM25 keyword index for hybrid search.

    Maintains term frequencies and inverse document frequencies
    for fast keyword scoring. Designed to run alongside HNSW
    vector search — HNSW finds semantic neighbours, BM25 boosts
    exact keyword matches.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs: Counter = Counter()
        self.doc_lengths: Dict[str, int] = {}
        self.term_freqs: Dict[str, Counter] = {}
        self.avg_dl: float = 0.0
        self.n_docs: int = 0

    def add_document(self, doc_id: str, text: str) -> None:
        """Add or replace a document in the index."""
        if doc_id in self.term_freqs:
            self.remove_document(doc_id)

        terms = self._tokenize(text)
        self.term_freqs[doc_id] = Counter(terms)
        self.doc_lengths[doc_id] = len(terms)
        for term in set(terms):
            self.doc_freqs[term] += 1
        self.n_docs += 1
        self._update_avg_dl()

    def remove_document(self, doc_id: str) -> None:
        """Remove a document from the index."""
        if doc_id not in self.term_freqs:
            return
        for term in self.term_freqs[doc_id]:
            self.doc_freqs[term] -= 1
            if self.doc_freqs[term] <= 0:
                del self.doc_freqs[term]
        del self.term_freqs[doc_id]
        del self.doc_lengths[doc_id]
        self.n_docs -= 1
        self._update_avg_dl()

    def score(self, query: str, doc_id: str) -> float:
        """Compute BM25 score for a query against a single document."""
        return self._score_terms(self._tokenize(query), doc_id)

    def _score_terms(self, query_terms: List[str], doc_id: str) -> float:
        """Compute BM25 score from pre-tokenized query terms."""
        if doc_id not in self.term_freqs or self.avg_dl == 0:
            return 0.0

        total = 0.0
        dl = self.doc_lengths[doc_id]
        tf_map = self.term_freqs[doc_id]

        for term in query_terms:
            n = self.doc_freqs.get(term, 0)
            if n == 0:
                continue
            idf = math.log((self.n_docs - n + 0.5) / (n + 0.5) + 1.0)
            tf = tf_map.get(term, 0)
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (1.0 - self.b + self.b * dl / self.avg_dl)
            total += idf * numerator / denominator

        return total

    def score_batch(self, query: str, doc_ids: List[str]) -> Dict[str, float]:
        """Score multiple documents against a query (tokenizes once)."""
        terms = self._tokenize(query)
        return {doc_id: self._score_terms(terms, doc_id) for doc_id in doc_ids}

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize text into lowercase word terms (len > 1)."""
        return [w.lower() for w in _WORD_RE.findall(text) if len(w) > 1]

    def _update_avg_dl(self) -> None:
        """Recompute average document length."""
        if self.n_docs > 0:
            self.avg_dl = sum(self.doc_lengths.values()) / self.n_docs
        else:
            self.avg_dl = 0.0

    def to_dict(self) -> Dict:
        """Serialize to a plain dict for persistence."""
        return {
            "k1": self.k1,
            "b": self.b,
            "doc_freqs": dict(self.doc_freqs),
            "doc_lengths": self.doc_lengths,
            "term_freqs": {doc_id: dict(tf) for doc_id, tf in self.term_freqs.items()},
            "avg_dl": self.avg_dl,
            "n_docs": self.n_docs,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "BM25Index":
        """Reconstruct from serialized dict."""
        idx = cls(k1=data["k1"], b=data["b"])
        idx.doc_freqs = Counter(data["doc_freqs"])
        idx.doc_lengths = data["doc_lengths"]
        idx.term_freqs = {
            doc_id: Counter(tf) for doc_id, tf in data["term_freqs"].items()
        }
        idx.avg_dl = data["avg_dl"]
        idx.n_docs = data["n_docs"]
        return idx
