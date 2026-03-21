"""Run all Stele benchmarks and print a summary.

Executes each benchmark file as a subprocess. Supports a --quick flag
that reduces iteration counts and data sizes for CI environments.

Usage:
    python benchmarks/run_all.py          # full run
    python benchmarks/run_all.py --quick  # reduced sizes for CI
"""

import os
import subprocess
import sys
import time


BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BENCH_DIR)

BENCHMARKS = [
    ("Chunking", "bench_chunking.py"),
    ("Storage", "bench_storage.py"),
    ("Search", "bench_search.py"),
    ("Scale", "bench_scale.py"),
]


def run_benchmark(name, script, quick=False):
    """Run a single benchmark script and return (success, elapsed, output)."""
    script_path = os.path.join(BENCH_DIR, script)
    env = os.environ.copy()
    env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if quick:
        env["STELE_BENCH_QUICK"] = "1"

    start = time.perf_counter()
    result = subprocess.run(
        [sys.executable, script_path],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        env=env,
        timeout=300,
    )
    elapsed = time.perf_counter() - start

    return result.returncode == 0, elapsed, result.stdout, result.stderr


def main():
    quick = "--quick" in sys.argv
    mode = "QUICK" if quick else "FULL"

    print(f"\n{'=' * 72}")
    print(f"  Stele Performance Benchmarks ({mode})")
    print(f"{'=' * 72}")

    results = []
    total_start = time.perf_counter()

    for name, script in BENCHMARKS:
        print(f"\n--- Running: {name} ---\n")
        success, elapsed, stdout, stderr = run_benchmark(name, script, quick)

        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, file=sys.stderr, end="")

        status = "PASS" if success else "FAIL"
        results.append((name, status, elapsed))

    total_elapsed = time.perf_counter() - total_start

    # Summary
    print(f"\n{'=' * 72}")
    print("  Summary")
    print(f"{'=' * 72}")
    print(f"\n  {'Benchmark':<20s} {'Status':<10s} {'Time (s)':>10s}")
    print(f"  {'-' * 42}")
    for name, status, elapsed in results:
        print(f"  {name:<20s} {status:<10s} {elapsed:>10.2f}")
    print(f"  {'-' * 42}")
    print(f"  {'Total':<20s} {'':10s} {total_elapsed:>10.2f}")
    print()

    failed = sum(1 for _, s, _ in results if s == "FAIL")
    if failed:
        print(f"  {failed} benchmark(s) FAILED")
        sys.exit(1)
    else:
        print("  All benchmarks passed.")


if __name__ == "__main__":
    main()
