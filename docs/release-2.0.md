# Releasing stele-context 2.0.0

Do this only after the rewrite pull request is green on Linux, macOS, and Windows, including the `benchmark-gate` job. The agent that prepared this branch does not merge, tag, or publish.

## Tags, in this order

1. `v2.0.0` on the merge commit of the rewrite. Rust crate version is already `2.0.0`.
2. `v1.5.3` on the follow-up commit that applies `release/pypi-1.5.3/`. That commit is the last Python package.

Pushing `v2.*` runs `.github/workflows/release-rust.yml` and creates the GitHub Release. It does not upload to PyPI.

Pushing `v1.*` runs `.github/workflows/publish.yml` and uploads the sdist to PyPI. `v2.*` does not match that workflow.

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
