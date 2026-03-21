"""Benchmark chunking operations for Stele.

Measures TextChunker and CodeChunker throughput on synthetic content
of various sizes (1KB to 1MB). Reports median time over 3 iterations.
"""

import os
import statistics
import time

from stele.chunkers.text import TextChunker
from stele.chunkers.code import CodeChunker

_QUICK = os.environ.get("STELE_BENCH_QUICK") == "1"


# ---------------------------------------------------------------------------
# Synthetic content generators
# ---------------------------------------------------------------------------


def _make_prose(size_bytes: int) -> str:
    """Generate synthetic prose text of approximately the given size."""
    paragraph = (
        "The quick brown fox jumps over the lazy dog. "
        "Pack my box with five dozen liquor jugs. "
        "How vexingly quick daft zebras jump. "
        "Sphinx of black quartz, judge my vow.\n\n"
    )
    repeats = max(1, size_bytes // len(paragraph)) + 1
    return (paragraph * repeats)[:size_bytes]


def _make_python(size_bytes: int) -> str:
    """Generate synthetic Python code of approximately the given size."""
    func_template = (
        "def function_{i}(x, y):\n"
        '    """Compute result for case {i}."""\n'
        "    result = x + y * {i}\n"
        "    if result > 100:\n"
        "        return result - 50\n"
        "    return result\n\n\n"
    )
    parts = []
    i = 0
    while len("\n".join(parts)) < size_bytes:
        parts.append(func_template.format(i=i))
        i += 1
    return "\n".join(parts)[:size_bytes]


def _make_javascript(size_bytes: int) -> str:
    """Generate synthetic JavaScript code of approximately the given size."""
    func_template = (
        "function handler{i}(req, res) {{\n"
        "  const data = req.body;\n"
        "  if (!data || !data.id) {{\n"
        "    return res.status(400).json({{ error: 'missing id' }});\n"
        "  }}\n"
        "  const result = process(data, {i});\n"
        "  return res.json(result);\n"
        "}}\n\n"
    )
    parts = []
    i = 0
    while len("\n".join(parts)) < size_bytes:
        parts.append(func_template.format(i=i))
        i += 1
    return "\n".join(parts)[:size_bytes]


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

SIZES_FULL = {
    "1KB": 1024,
    "10KB": 10 * 1024,
    "100KB": 100 * 1024,
    "1MB": 1024 * 1024,
}

SIZES_QUICK = {
    "1KB": 1024,
    "10KB": 10 * 1024,
}

SIZES = SIZES_QUICK if _QUICK else SIZES_FULL
ITERATIONS = 2 if _QUICK else 3


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


def run(iterations=ITERATIONS):
    """Run all chunking benchmarks and print results."""
    text_chunker = TextChunker(chunk_size=256, max_chunk_size=4096)
    code_chunker = CodeChunker(chunk_size=256, max_chunk_size=4096)

    header = (
        f"  {'Operation':<30s} {'Size':<10s} {'Time (ms)':>10s} {'Throughput':>15s}"
    )
    sep = "  " + "-" * 69

    print("\n=== Chunking Benchmarks ===\n")
    print(header)
    print(sep)

    for label, nbytes in SIZES.items():
        # TextChunker
        prose = _make_prose(nbytes)
        t, chunks = _bench(
            lambda p=prose: text_chunker.chunk(p, "bench.txt"),
            iterations,
        )
        ms = t * 1000
        tp = f"{len(prose) / t / 1024:.0f} KB/s"
        print(_format_row("TextChunker", label, ms, tp))

        # CodeChunker (Python / AST)
        py_code = _make_python(nbytes)
        t, chunks = _bench(
            lambda c=py_code: code_chunker.chunk(c, "bench.py"),
            iterations,
        )
        ms = t * 1000
        tp = f"{len(py_code) / t / 1024:.0f} KB/s"
        print(_format_row("CodeChunker (Python AST)", label, ms, tp))

        # CodeChunker (JS / regex fallback)
        js_code = _make_javascript(nbytes)
        t, chunks = _bench(
            lambda c=js_code: code_chunker.chunk(c, "bench.js"),
            iterations,
        )
        ms = t * 1000
        tp = f"{len(js_code) / t / 1024:.0f} KB/s"
        print(_format_row("CodeChunker (JS regex)", label, ms, tp))

        print(sep)

    print()


if __name__ == "__main__":
    run()
