"""Benchmark storage operations for Stele Context.

Measures SQLite-backed store_chunk, get_chunk, search_chunks,
store_document, get_document, and get_storage_stats performance.
Uses a temporary directory so no persistent state is modified.
"""

import hashlib
import os
import random
import statistics
import tempfile
import time

from stele_context.storage import StorageBackend

_QUICK = os.environ.get("STELE_CONTEXT_BENCH_QUICK") == "1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signature():
    """Generate a realistic 128-dim signature (unit-normalized floats)."""
    raw = [random.gauss(0, 1) for _ in range(128)]
    norm = max(1e-9, sum(x * x for x in raw) ** 0.5)
    return [x / norm for x in raw]


def _make_chunk_id(i):
    return hashlib.sha256(f"chunk-{i}".encode()).hexdigest()[:32]


def _make_content_hash(i):
    return hashlib.sha256(f"content-{i}".encode()).hexdigest()


ITERATIONS = 2 if _QUICK else 3
_BATCH_LARGE = 200 if _QUICK else 1000


def _bench(func, iterations=ITERATIONS):
    """Run func multiple times and return median elapsed time in seconds."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        result = func()
        elapsed = time.perf_counter() - start
        times.append(elapsed)
    return statistics.median(times), result


def _format_row(op, count, ms, throughput):
    return f"  {op:<30s} {count:<10s} {ms:>10.2f} {throughput:>15s}"


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


def run(iterations=ITERATIONS):
    """Run all storage benchmarks and print results."""
    header = (
        f"  {'Operation':<30s} {'Count':<10s} {'Time (ms)':>10s} {'Throughput':>15s}"
    )
    sep = "  " + "-" * 69

    print("\n=== Storage Benchmarks ===\n")
    print(header)
    print(sep)

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = StorageBackend(tmpdir)

        # -- store_chunk: single -------------------------------------------
        sig = _make_signature()
        cid = _make_chunk_id(0)
        chash = _make_content_hash(0)
        t, _ = _bench(
            lambda: storage.store_chunk(
                cid,
                "doc.txt",
                chash,
                sig,
                0,
                100,
                25,
                content="hello world",
            ),
            iterations,
        )
        print(_format_row("store_chunk (single)", "1", t * 1000, f"{1 / t:.0f} ops/s"))

        # -- store_chunk: batch 100 ----------------------------------------
        sigs_100 = [_make_signature() for _ in range(100)]

        def _store_batch(n, offset=0):
            for i in range(n):
                idx = offset + i
                storage.store_chunk(
                    _make_chunk_id(idx + 1000),
                    f"doc_{idx}.txt",
                    _make_content_hash(idx + 1000),
                    sigs_100[i % len(sigs_100)],
                    0,
                    500,
                    50,
                    content=f"chunk content {idx}",
                )

        t, _ = _bench(lambda: _store_batch(100, 0), iterations)
        print(
            _format_row("store_chunk (batch)", "100", t * 1000, f"{100 / t:.0f} ops/s")
        )

        # -- store_chunk: batch large --------------------------------------
        sigs_extra = [_make_signature() for _ in range(100)]
        bl = _BATCH_LARGE

        def _store_batch_large():
            for i in range(bl):
                storage.store_chunk(
                    _make_chunk_id(i + 10000),
                    f"doc_{i}.txt",
                    _make_content_hash(i + 10000),
                    sigs_extra[i % len(sigs_extra)],
                    0,
                    500,
                    50,
                    content=f"chunk content large {i}",
                )

        t, _ = _bench(_store_batch_large, iterations)
        print(
            _format_row("store_chunk (batch)", str(bl), t * 1000, f"{bl / t:.0f} ops/s")
        )

        print(sep)

        # -- get_chunk: single lookup --------------------------------------
        t, _ = _bench(lambda: storage.get_chunk(_make_chunk_id(0)), iterations)
        print(_format_row("get_chunk (single)", "1", t * 1000, f"{1 / t:.0f} ops/s"))

        # -- search_chunks: by document_path -------------------------------
        t, _ = _bench(lambda: storage.search_chunks("doc_0.txt"), iterations)
        print(
            _format_row("search_chunks (by path)", "1", t * 1000, f"{1 / t:.0f} ops/s")
        )

        print(sep)

        # -- store_document + get_document ---------------------------------
        t, _ = _bench(
            lambda: storage.store_document("bench.txt", "abc123", 10, time.time()),
            iterations,
        )
        print(_format_row("store_document", "1", t * 1000, f"{1 / t:.0f} ops/s"))

        t, _ = _bench(lambda: storage.get_document("bench.txt"), iterations)
        print(_format_row("get_document", "1", t * 1000, f"{1 / t:.0f} ops/s"))

        print(sep)

        # -- get_storage_stats ---------------------------------------------
        t, stats = _bench(lambda: storage.get_storage_stats(), iterations)
        print(_format_row("get_storage_stats", "1", t * 1000, f"{1 / t:.0f} ops/s"))

        print(sep)
        print(
            f"\n  DB contains: {stats.get('chunk_count', '?')} chunks, "
            f"{stats.get('document_count', '?')} documents"
        )

    print()


if __name__ == "__main__":
    run()
