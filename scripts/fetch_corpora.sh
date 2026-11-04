#!/bin/bash
# Download the pinned rich 13.9.4 and flask 3.1.0 trees into parity/.cache.
# Rust tests round-trip those files when the cache is present.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
meta="$root/parity/goldens/corpora.json"
cache="$root/parity/.cache"
mkdir -p "$cache"

hash_file() {
  file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  else
    openssl dgst -sha256 "$file" | awk '{print $NF}'
  fi
}

awk '
  function clean(value) {
    gsub(/[",]/, "", value)
    return value
  }
  /"id":/ { id = clean($2) }
  /"tarball_sha256":/ { hash = clean($2) }
  /"url":/ { url = clean($2); print id, hash, url }
' "$meta" | while read -r id hash url; do
  dest="$cache/${id}.tar.gz"
  if [ ! -f "$dest" ]; then
    curl -fsSL -A "stele-context-parity/2.0.0" -o "$dest" "$url"
  fi
  got="$(hash_file "$dest")"
  if [ "$got" != "$hash" ]; then
    echo "$id sha256 $got" >&2
    exit 1
  fi
  extracted="$cache/$id"
  if [ ! -d "$extracted" ] || [ -z "$(ls -A "$extracted" 2>/dev/null || true)" ]; then
    mkdir -p "$extracted"
    tar -xzf "$dest" -C "$extracted"
  fi
done

echo "corpora ready"
