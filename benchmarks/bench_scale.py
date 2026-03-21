"""Benchmark HNSW and BM25 at scale for Stele.

Measures insert time, search latency, and memory usage at increasing
chunk counts.  HNSW scales: 2K/5K/10K (FULL) or 500/1K/2K (QUICK).
BM25 scales: 10K/50K (FULL) or 1K/5K/10K (QUICK).  Uses _QUICK mode
(env STELE_BENCH_QUICK=1) for reduced sizes suitable for CI.
"""

import hashlib
import math
import os
import random
import statistics
import time
import tracemalloc

from stele.bm25 import BM25Index
from stele.index import VectorIndex

_QUICK = os.environ.get("STELE_BENCH_QUICK") == "1"

# Pure-Python HNSW runs at ~15 inserts/s, so scales are chosen to keep
# total runtime reasonable (~2 min QUICK, ~30 min FULL).
HNSW_SCALES = [500, 1_000, 2_000] if _QUICK else [2_000, 5_000, 10_000]
BM25_SCALES = [1_000, 5_000, 10_000] if _QUICK else [10_000, 50_000]
SEARCH_ITERATIONS = 3 if not _QUICK else 2
INSERT_ITERATIONS = 1
K = 10


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


def _bench(func, iterations):
    """Run func multiple times and return median elapsed time in seconds."""
    times = []
    result = None
    for _ in range(iterations):
        start = time.perf_counter()
        result = func()
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    return statistics.median(times), result


def _format_row(op, size, ms, throughput, memory=""):
    return f"  {op:<30s} {size:<10s} {ms:>10.2f} {throughput:>15s} {memory:>12s}"


def _format_mem(nbytes):
    """Format byte count as human-readable string."""
    if nbytes >= 1 << 20:
        return f"{nbytes / (1 << 20):.1f} MB"
    if nbytes >= 1 << 10:
        return f"{nbytes / (1 << 10):.1f} KB"
    return f"{nbytes} B"


def _measure_peak_memory(func):
    """Run func under tracemalloc and return (result, peak_bytes)."""
    tracemalloc.start()
    result = func()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, peak


# ---------------------------------------------------------------------------
# Pre-generate data (seeded for reproducibility)
# ---------------------------------------------------------------------------


def _generate_vectors(n, seed=42):
    """Generate n unit-normalized random vectors with a fixed seed."""
    random.seed(seed)
    return [_random_vector() for _ in range(n)]


def _generate_ids(n):
    """Generate n deterministic chunk IDs."""
    return [hashlib.sha256(f"chunk-{i}".encode()).hexdigest()[:32] for i in range(n)]


def _generate_texts(n, seed=42):
    """Generate n synthetic texts with a fixed seed."""
    random.seed(seed)
    return [_make_text(i) for i in range(n)]


def _build_hnsw(ids, vectors):
    """Build a VectorIndex from pre-generated data."""
    vi = VectorIndex()
    for nid, vec in zip(ids, vectors):
        vi.add_chunk(nid, vec)
    return vi


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run():
    """Run all scale benchmarks and print results."""
    random.seed(42)

    header = (
        f"  {'Operation':<30s} {'Size':<10s} {'Time (ms)':>10s} "
        f"{'Throughput':>15s} {'Peak Mem':>12s}"
    )
    sep = "  " + "-" * 81

    # === HNSW insert ========================================================
    print("\n=== HNSW Insert at Scale ===\n")
    print(header)
    print(sep)

    max_n = max(HNSW_SCALES)
    all_vectors = _generate_vectors(max_n)
    all_ids = _generate_ids(max_n)

    # Cache built indices so search phase doesn't rebuild them.
    hnsw_indices = {}

    for n in HNSW_SCALES:
        vectors = all_vectors[:n]
        ids = all_ids[:n]

        def _insert(vecs=vectors, nids=ids):
            return _build_hnsw(nids, vecs)

        # Measure memory on the timed run (single pass).
        tracemalloc.start()
        t, vi = _bench(_insert, INSERT_ITERATIONS)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        hnsw_indices[n] = vi

        print(
            _format_row(
                "HNSW insert",
                f"{n:,}",
                t * 1000,
                f"{n / t:,.0f} ops/s",
                _format_mem(peak),
            )
        )

    print(sep)

    # === HNSW search latency ================================================
    print("\n=== HNSW Search Latency (k=10) ===\n")
    print(header)
    print(sep)

    for n in HNSW_SCALES:
        vi = hnsw_indices[n]

        random.seed(99)
        query = _random_vector()

        t, _ = _bench(lambda v=vi, q=query: v.search(q, k=K), SEARCH_ITERATIONS)
        print(
            _format_row(
                "HNSW search k=10",
                f"{n:,}",
                t * 1000,
                f"{1 / t:,.0f} qps",
            )
        )

    print(sep)

    # Free HNSW indices to reclaim memory before BM25 phase.
    del hnsw_indices

    # === BM25 index build ===================================================
    print("\n=== BM25 Index Build at Scale ===\n")
    print(header)
    print(sep)

    max_bm25 = max(BM25_SCALES)
    all_texts = _generate_texts(max_bm25)
    all_doc_ids = [f"doc-{i}" for i in range(max_bm25)]

    bm25_indices = {}

    for n in BM25_SCALES:
        texts = all_texts[:n]
        doc_ids = all_doc_ids[:n]

        def _build(txts=texts, dids=doc_ids):
            bm = BM25Index()
            for did, txt in zip(dids, txts):
                bm.add_document(did, txt)
            return bm

        _, peak = _measure_peak_memory(lambda: _build())
        t, bm25 = _bench(_build, INSERT_ITERATIONS)
        bm25_indices[n] = bm25

        print(
            _format_row(
                "BM25 add_document",
                f"{n:,}",
                t * 1000,
                f"{n / t:,.0f} ops/s",
                _format_mem(peak),
            )
        )

    print(sep)

    # === BM25 score_batch latency ===========================================
    print("\n=== BM25 score_batch Latency ===\n")
    print(header)
    print(sep)

    for n in BM25_SCALES:
        bm25 = bm25_indices[n]
        doc_ids = all_doc_ids[:n]
        sample_ids = doc_ids[: min(200, n)]

        t, _ = _bench(
            lambda b=bm25, s=sample_ids: b.score_batch(
                "function loop query handler", s
            ),
            SEARCH_ITERATIONS,
        )
        print(
            _format_row(
                "BM25 score_batch",
                f"{n:,}",
                t * 1000,
                f"{len(sample_ids) / t:,.0f} docs/s",
            )
        )

    print(sep)

    # === Summary ============================================================
    mode = "QUICK" if _QUICK else "FULL"
    print(f"\n  Mode: {mode}")
    hnsw_range = f"{HNSW_SCALES[0]:,}..{HNSW_SCALES[-1]:,}"
    bm25_range = f"{BM25_SCALES[0]:,}..{BM25_SCALES[-1]:,}"
    print(f"  HNSW scales: {hnsw_range}  |  BM25 scales: {bm25_range}")
    print(
        f"  Insert iterations: {INSERT_ITERATIONS}  |  Search iterations: {SEARCH_ITERATIONS}"
    )
    print()


if __name__ == "__main__":
    run()
