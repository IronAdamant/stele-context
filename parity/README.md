# Parity fixtures

`fixtures/` and `goldens/` are the corpus the Rust tests read. `cargo test` checks chunks and symbols against `goldens/fixtures.json`, and media indexing against `goldens/media.json`.

`goldens/corpora.json` pins the rich 13.9.4 and flask 3.1.0 tarballs. `bash scripts/fetch_corpora.sh` downloads them into `parity/.cache` (gitignored). Round-trip tests run when that cache is present and skip when it is not.

`goldens/extensions.json` is the 1.5.2 extension inventory. It is kept as a record. Nothing in the Rust crate imports it.

The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x). The Python harness that wrote these goldens is on that branch.
