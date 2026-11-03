# Benchmarks

`baseline/summary.json` and `baseline/raw.jsonl` are the Python 1.5.1
measurements from 4 Nov 2026 (Linux 6.12.94 x86_64, 8 vCPU, rich 13.9.4).
They are the hard floor: a Rust median must be strictly below the recorded
Python median for every benchmark it covers.

The uploaded log does not include argv. `bench.py` is the canonical command
list from this repo onward. Startup (`--version`, `--help`) is comparable
directly. Later commands use the arguments in `COMMANDS` on the rich corpus;
do not treat the 1.5.1 output sizes as exact for those argv.

```bash
python -m bench --binary python --group b1 --runs 10 --cwd /path/to/rich
python -m bench --binary ./target/release/stele-context --group b1 --runs 15 --check-floor
```

D13, once both binaries exist: 15 or more interleaved runs of main and the
PR on the same runner. The PR fails when the candidate is slower beyond
run-to-run noise. That is a non-overlapping 95% confidence interval of the
median, or a one-sided Mann-Whitney test at p < 0.05 (`bench/gate.py`).
A confirmed slowdown is a bug. It is not waived.

`STELE_CONTEXT_STORAGE_DIR` is set from `--storage-dir` when that flag is
passed. Cold index wipes the store before every run.

## First Rust B1 (measured, release build)

Host: Linux 6.12.94+ x86_64, 4 vCPU, rustc 1.99.1, 10 runs of `target/release/stele-context`. The Python floor was measured on an 8 vCPU box; startup is single-threaded, and CI re-checks the floor on each OS.

| Command | Rust median | Rust min–max | Peak RSS | Output | Python 1.5.1 median |
|---|---:|---:|---:|---:|---:|
| `--version` | 1.65 ms | 1.51–1.92 | 10.3 MB | 20 B | 79.4 ms |
| `--help` | 1.69 ms | 1.51–1.83 | 10.3 MB | 327 B | 75.7 ms |

Raw samples are `rust-b1.json`. CI runs the same floor check on Linux, macOS, and Windows.
