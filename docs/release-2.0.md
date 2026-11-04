# Releasing stele-context 2.0

`v2.0.0` is the Rust release. `main` contains only that crate. The old Python 1.x version lives on the `python-legacy` branch and on PyPI (1.5.x).

Rust indexes PNG, GIF, JPEG, audio, video, and PDF without optional libraries. On rich 13.9.4 that is 496 documents and 1118 chunks. Image chunk bodies are header metadata. Audio and video chunks are metadata lines. PDF text is literal and hex strings from uncompressed and Flate streams. The media fixture is `parity/fixtures/media` with `parity/goldens/media.json` (7 documents, 7 chunks, 0 symbols).

Symbol edges match the 1.5.2 full rebuild on rich: 4939 edges (4412 cross_file, 461 intra_file, 66 test_of). `search` attaches them, and `impact_radius` and `coupling` walk them. `find_references` reads the symbol table, not the edge table.

## Tags

Pushing `v2.*` from `main` runs `.github/workflows/release-rust.yml` and creates the GitHub Release. It does not upload to PyPI.

Pushing `v1.*` from `python-legacy` runs `publish.yml` on that branch and uploads the sdist to PyPI. The tag filter is `v1.*`. Cut 1.x tags from `python-legacy`, because that is the commit that still contains `pyproject.toml` and the workflow. A 2.x tag does not match the filter.

## Another 2.x tag

1. `cargo test` is green on Linux, macOS, and Windows, including `benchmark-gate`.
2. Bump `version` in `Cargo.toml` only. Do not add a crate.
3. `git tag -a v2.x.y -m "stele-context v2.x.y"`
4. `git push origin v2.x.y`
5. Wait until the Rust release workflow is green. The release assets are the six targets below plus `SHA256SUMS`.

## Another 1.x tag

1. Check out `python-legacy`.
2. Bump `pyproject.toml` on that branch only.
3. `git tag -a v1.x.y -m "stele-context v1.x.y"`
4. `git push origin v1.x.y`
5. Confirm PyPI shows that 1.x version and still has no 2.x release.

## Binaries

| Asset | Triple |
| --- | --- |
| `stele-context-x86_64-unknown-linux-musl` | Linux x86_64, musl |
| `stele-context-aarch64-unknown-linux-gnu` | Linux aarch64, glibc |
| `stele-context-aarch64-apple-darwin` | macOS Apple silicon |
| `stele-context-x86_64-apple-darwin` | macOS Intel |
| `stele-context-x86_64-pc-windows-msvc.exe` | Windows x86_64 |
| `stele-context-aarch64-pc-windows-msvc.exe` | Windows Arm |

Each triple also has `stele-context-mcp-<triple>`. `scripts/install.sh` and `scripts/install.ps1` download the matching asset. The npm package in `npm/stele-context` is a launcher; it is not published by the tag workflows.

## Secrets

| Secret | Where it is used | Required |
| --- | --- | --- |
| `GITHUB_TOKEN` | GitHub Release upload. The workflow permission `contents: write` supplies it. | Yes, automatic |
| PyPI trusted publisher | OIDC from `publish.yml` on `python-legacy` (`id-token: write`). No `PYPI_API_TOKEN`. | Already configured for this repo and `publish.yml` |
| `NPM_TOKEN` | Only if you later publish `npm/stele-context` with `npm publish`. No workflow reads it today. | No |

There is no signing key and no PyPI token in the repository.
