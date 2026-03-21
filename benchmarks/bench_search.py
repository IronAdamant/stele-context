"""Benchmark search operations for Stele.

Measures VectorIndex insert and search, BM25Index add_document and
score_batch, and full hybrid search via engine.search(). All data is
synthetic; uses a temporary directory for any storage.
"""

import hashlib
import math
import os
import random
import statistics
import tempfile
import time

from stele.index import VectorIndex
from stele.bm25 import BM25Index
from stele.engine import Stele

_QUICK = os.environ.get("STELE_BENCH_QUICK") == "1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _random_vector(dim=128):
    """Generate a unit-normalized random 128-dim vector."""
    raw = [random.gauss(0, 1) for _ in range(dim)]
    norm = max(1e-9, math.sqrt(sum(x * x for x in raw)))
    return [x / norm for x in raw]


def _make_text(i, length=200):
    """Generate synthetic document text."""
    words = [
        "function",
        "variable",
        "loop",
        "condition",
        "return",
        "import",
        "class",
        "method",
        "parameter",
        "result",
        "data",
        "process",
        "handler",
        "request",
        "response",
        "error",
        "cache",
        "index",
        "search",
        "query",
    ]
    rng = random.Random(i)
    return " ".join(rng.choice(words) for _ in range(length))


ITERATIONS = 2 if _QUICK else 3
BATCH_SIZES = [100, 500] if _QUICK else [100, 1000, 5000]
K_VALUES = [5, 10, 20]


def _bench(func, iterations=ITERATIONS):
    """Run func multiple times and return median elapsed time in seconds."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        result = func()
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    return statistics.median(times), result


def _format_row(op, size, ms, throughput):
    return f"  {op:<30s} {size:<10s} {ms:>10.2f} {throughput:>15s}"


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run(iterations=ITERATIONS, batch_sizes=None):
    """Run all search benchmarks and print results."""
    if batch_sizes is None:
        batch_sizes = BATCH_SIZES

    header = (
        f"  {'Operation':<30s} {'Size':<10s} {'Time (ms)':>10s} {'Throughput':>15s}"
    )
    sep = "  " + "-" * 69

    # === VectorIndex benchmarks ============================================
    print("\n=== VectorIndex Benchmarks ===\n")
    print(header)
    print(sep)

    for n in batch_sizes:
        vectors = [_random_vector() for _ in range(n)]
        ids = [hashlib.sha256(f"node-{i}".encode()).hexdigest()[:32] for i in range(n)]

        def _insert_batch(vecs=vectors, nids=ids):
            vi = VectorIndex()
            for nid, vec in zip(nids, vecs):
                vi.add_chunk(nid, vec)
            return vi

        t, vi = _bench(_insert_batch, iterations)
        print(_format_row("VectorIndex.insert", str(n), t * 1000, f"{n / t:.0f} ops/s"))

    print(sep)

    # Search benchmarks on the largest index
    largest_n = batch_sizes[-1]
    vectors = [_random_vector() for _ in range(largest_n)]
    ids = [
        hashlib.sha256(f"node-{i}".encode()).hexdigest()[:32] for i in range(largest_n)
    ]
    vi = VectorIndex()
    for nid, vec in zip(ids, vectors):
        vi.add_chunk(nid, vec)

    query = _random_vector()
    for k in K_VALUES:
        t, results = _bench(lambda kk=k: vi.search(query, k=kk), iterations)
        print(
            _format_row(
                f"VectorIndex.search (k={k})",
                str(largest_n),
                t * 1000,
                f"{1 / t:.0f} qps",
            )
        )

    print(sep)

    # === BM25Index benchmarks ==============================================
    print("\n=== BM25Index Benchmarks ===\n")
    print(header)
    print(sep)

    bm25_sizes = [s for s in batch_sizes if s <= 5000]
    for n in bm25_sizes:
        texts = [_make_text(i) for i in range(n)]
        doc_ids = [f"doc-{i}" for i in range(n)]

        def _add_docs(txts=texts, dids=doc_ids):
            bm = BM25Index()
            for did, txt in zip(dids, txts):
                bm.add_document(did, txt)
            return bm

        t, bm25 = _bench(_add_docs, iterations)
        print(_format_row("BM25.add_document", str(n), t * 1000, f"{n / t:.0f} ops/s"))

    print(sep)

    # BM25 score_batch on largest
    largest_bm25 = bm25_sizes[-1]
    texts = [_make_text(i) for i in range(largest_bm25)]
    doc_ids = [f"doc-{i}" for i in range(largest_bm25)]
    bm25 = BM25Index()
    for did, txt in zip(doc_ids, texts):
        bm25.add_document(did, txt)

    sample_ids = doc_ids[:100]
    t, _ = _bench(
        lambda: bm25.score_batch("function loop query", sample_ids), iterations
    )
    print(
        _format_row(
            "BM25.score_batch",
            f"{len(sample_ids)} docs",
            t * 1000,
            f"{len(sample_ids) / t:.0f} docs/s",
        )
    )

    print(sep)

    # === Full hybrid search via engine =====================================
    print("\n=== Engine Hybrid Search ===\n")
    print(header)
    print(sep)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = Stele(storage_dir=tmpdir, enable_coordination=False)

        # Pre-populate: write temp files and index them
        n_docs = min(50, batch_sizes[-1] // 10)
        doc_dir = os.path.join(tmpdir, "docs")
        os.makedirs(doc_dir, exist_ok=True)
        paths = []
        for i in range(n_docs):
            p = os.path.join(doc_dir, f"doc_{i}.txt")
            with open(p, "w") as f:
                f.write(_make_text(i, length=500))
            paths.append(p)

        engine.index_documents(paths)

        for k in K_VALUES:
            t, results = _bench(
                lambda kk=k: engine.search("function loop variable result", top_k=kk),
                iterations,
            )
            print(
                _format_row(
                    f"engine.search (k={k})",
                    f"{n_docs} docs",
                    t * 1000,
                    f"{1 / t:.0f} qps",
                )
            )

    print(sep)

    # === Text pattern search (search_text) ==================================
    print("\n=== Engine Text Pattern Search ===\n")
    print(header)
    print(sep)

    with tempfile.TemporaryDirectory() as tmpdir:
        engine = Stele(storage_dir=tmpdir, enable_coordination=False)

        n_docs = min(50, batch_sizes[-1] // 10)
        doc_dir = os.path.join(tmpdir, "docs")
        os.makedirs(doc_dir, exist_ok=True)
        paths = []
        for i in range(n_docs):
            p = os.path.join(doc_dir, f"doc_{i}.py")
            with open(p, "w") as f:
                f.write(
                    f"from typing import Dict, List\n\n"
                    f"def function_{i}(data: Dict[str, List[int]]):\n"
                    f"    result = {{}}\n"
                    f"    for k in data:\n"
                    f"        result[k] = sum(data[k])\n"
                    f"    return result\n"
                )
            paths.append(p)

        engine.index_documents(paths)

        # Substring search
        t, result = _bench(
            lambda: engine.search_text("Dict["),
            iterations,
        )
        print(
            _format_row(
                "search_text (substring)",
                f"{n_docs} docs",
                t * 1000,
                f"{result['match_count']} matches",
            )
        )

        # Regex search
        t, result = _bench(
            lambda: engine.search_text(r"def function_\d+", regex=True),
            iterations,
        )
        print(
            _format_row(
                "search_text (regex)",
                f"{n_docs} docs",
                t * 1000,
                f"{result['match_count']} matches",
            )
        )

        # Scoped to single document
        t, result = _bench(
            lambda: engine.search_text("Dict[", document_path=paths[0]),
            iterations,
        )
        print(
            _format_row(
                "search_text (scoped)",
                "1 doc",
                t * 1000,
                f"{result['match_count']} matches",
            )
        )

    print(sep)
    print()


if __name__ == "__main__":
    run()
