# Releasing stele-context 2.0.0

Do this only after the rewrite pull request is green on Linux, macOS, and Windows, including the `benchmark-gate` job. The agent that prepared this branch does not merge, tag, or publish.

Rust indexes PNG, GIF, JPEG, audio, video, and PDF without optional libraries. Python indexes images only when Pillow imports. On rich 13.9.4 that is 496 documents and 1118 chunks with Pillow, and 475 documents and 1097 chunks without it. Rust always reports the Pillow counts. Image chunk bodies are header metadata, not Pillow's re-encoded bytes. Audio and video chunks are metadata lines, not librosa MFCCs or OpenCV frames. PDF text is literal and hex strings from uncompressed and Flate streams.

`parity/generate_media.py` writes the media fixture (Pillow, ffmpeg, PyMuPDF). On that fixture, Python 1.5.2 with Pillow, PyMuPDF, librosa, and OpenCV indexes 7 documents, 7 chunks, and 0 symbols, and Rust does the same. Python 1.5.1 `read_and_hash` decodes a PDF as text and `fitz.open` then raises `FileNotFoundError: no such file: '%PDF-1.7'` plus the decoded body. 1.5.2 reads PDF bytes and indexes the page. A valid MP3 is indexed by both 1.5.1 and 1.5.2; the earlier `LibsndfileError` was the stub file.

Symbol edges match Python's full rebuild on rich: 4939 edges (4412 cross_file, 461 intra_file, 66 test_of). `search` attaches them, and `impact_radius` and `coupling` walk them. `find_references` reads the symbol table, not the edge table. Python's resolver drops `container` before matching, and Rust does the same.

## Tags, in this order

1. `v2.0.0` on the merge commit of the rewrite. Rust crate version is already `2.0.0`.
2. `v1.5.3` on the follow-up commit that applies `release/pypi-1.5.3/`. That commit is the last Python package.

Pushing `v2.*` runs `.github/workflows/release-rust.yml` and creates the GitHub Release. It does not upload to PyPI.

Pushing `v1.*` runs `.github/workflows/publish.yml` and uploads the sdist to PyPI. The tag filter is `v1.*`. A published GitHub Release whose tag does not start with `v1.` skips the publish job, so a 2.x tag cannot upload to PyPI.

## Steps

1. Merge the rewrite pull request into `main`.
2. `git checkout main && git pull`
3. `git tag -a v2.0.0 -m "stele-context 2.0.0"`
4. `git push origin v2.0.0`
5. Wait until the Rust release workflow is green. The release assets are the six targets below plus `SHA256SUMS`.
6. On a new commit, apply the 1.5.3 notice described in `release/pypi-1.5.3/NOTICE.md`. Do not change the Rust crate version.
7. `git tag -a v1.5.3 -m "stele-context 1.5.3 notice"`
8. `git push origin v1.5.3`
9. Confirm PyPI shows 1.5.3 and that it has no 2.x release.

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
| PyPI trusted publisher | OIDC from `publish.yml` (`id-token: write`). No `PYPI_API_TOKEN`. | Already configured for this repo and `publish.yml` |
| `NPM_TOKEN` | Only if you later publish `npm/stele-context` with `npm publish`. No workflow reads it today. | No |

There is no signing key and no PyPI token in the repository.
