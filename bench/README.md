# Benchmarks

`baseline/summary.json` and `baseline/raw.jsonl` are the Python 1.5.1 measurements from 4 Nov 2026 (Linux 6.12.94 x86_64, 8 vCPU, rich 13.9.4). They are the hard floor: a Rust median must be strictly below the recorded Python median for every benchmark it covers.

`rust-b1.json` and `rust-rich.json` are recorded Rust runs. They are results, not a runner.

```bash
cargo build --release
./target/release/stele-bench floor \
  --binary ./target/release/stele-context \
  --group b1 --runs 10 --check-floor
```

D13 compares two commits on one runner. CI runs it as the `benchmark-gate` job:

```bash
./target/release/stele-bench gate --runs 16
```

At least 15 runs. The head fails when it is slower beyond run-to-run noise: a non-overlapping 95% confidence interval of the median, and a one-sided Mann-Whitney test at p < 0.05. A tie at exactly 0.05 is not a failure. Either check alone is not a failure.

`tools/list` reply size is printed and is not a failure by itself.

## First Rust B1 (measured, release build)

Host: Linux 6.12.94+ x86_64, 4 vCPU, rustc 1.99.1, 10 runs of `target/release/stele-context`. The Python floor was measured on an 8 vCPU box. Startup is single-threaded, and CI re-checks the floor on each OS.

| Command | Rust median | Rust min-max | Peak RSS | Output | Python 1.5.1 median |
|---|---:|---:|---:|---:|---:|
| `--version` | 1.65 ms | 1.51-1.92 | 10.3 MB | 20 B | 79.4 ms |
| `--help` | 1.69 ms | 1.51-1.83 | 10.3 MB | 327 B | 75.7 ms |
