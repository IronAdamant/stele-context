#!/bin/sh
# Install a stele-context 2.x binary from a GitHub Release.
# Usage: curl -fsSL .../install.sh | sh
# Override with STELE_VERSION (default 2.0.0) and STELE_PREFIX (default ~/.local).
set -eu
version="${STELE_VERSION:-2.0.0}"
prefix="${STELE_PREFIX:-$HOME/.local}"
repo="${STELE_REPO:-IronAdamant/stele-context}"
os=$(uname -s)
arch=$(uname -m)
case "$os-$arch" in
  Linux-x86_64) target=x86_64-unknown-linux-musl ;;
  Linux-aarch64) target=aarch64-unknown-linux-gnu ;;
  Darwin-arm64) target=aarch64-apple-darwin ;;
  Darwin-x86_64) target=x86_64-apple-darwin ;;
  *) echo "unsupported platform $os-$arch" >&2; exit 1 ;;
esac
url="https://github.com/${repo}/releases/download/v${version}/stele-context-${target}"
mkdir -p "$prefix/bin"
tmp=$(mktemp)
curl -fsSL "$url" -o "$tmp"
install -m 0755 "$tmp" "$prefix/bin/stele-context"
rm -f "$tmp"
echo "installed $prefix/bin/stele-context $version ($target)"
