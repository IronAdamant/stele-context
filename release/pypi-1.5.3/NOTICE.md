# PyPI 1.5.3 notice

This notice was applied before `main` became Rust-only. Further 1.x tags are cut from `python-legacy`, which still has `pyproject.toml` and `publish.yml`.

The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x).

# Original note (prepared, not applied)

Apply this on `main` only after `v2.0.0` exists and its GitHub Release has the Rust binaries. Do not apply it on the rewrite branch: parity checks require the Python package to stay `1.5.2` until that commit.

## Diff to make

- `pyproject.toml` and `stele_context/__init__.py`: version `1.5.3`.
- `README.md` install section: the supported package is the Rust binary from the `v2.0.0` GitHub Release (`scripts/install.sh`, `scripts/install.ps1`, or the release assets). `pip install stele-context` stays on 1.5.x and does not install 2.0.
- `CHANGELOG.md`: one entry, "1.5.3 points at the Rust 2.0.0 binaries. No Python feature changes."
- Leave `[project.scripts]` so `stele-context` from the 1.5.3 sdist prints, on startup, that 2.0.0 is the Rust release: https://github.com/IronAdamant/stele-context/releases/tag/v2.0.0

Tag that commit `v1.5.3` and push the tag. `publish.yml` uploads it. Do not upload a 2.x wheel.
