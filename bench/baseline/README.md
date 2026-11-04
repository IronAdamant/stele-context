# stele-context Python baseline (measured 4 Nov 2026, AEDT)

Target: stele-context 1.5.1 from PyPI, extra [mcp] with mcp 1.30.0 (1.5.1 fails on mcp 2.x).
Sample: Textualize/rich v13.9.4 GitHub tarball (526 files, 475 docs indexed, 1097 chunks).
Box: Linux 6.12.94 x86_64, 8 vCPU Intel Xeon, 15.6 GB RAM, Python 3.13.5, glibc 2.41.
Method: bench.py --runs 7 (startup 10 runs, cold index 3 runs). Wall via perf_counter,
child peak RSS via os.wait4 ru_maxrss, output = stdout bytes (CLI) or text content bytes (MCP).
MCP: one persistent stdio JSON-RPC client per mode; server memory is VmHWM, not per call
(the per-call peak_rss_mb field reads 0.0 by design and should be ignored).
Files: raw.jsonl (every sample), summary.json (medians and spread), run.log (stdout).
