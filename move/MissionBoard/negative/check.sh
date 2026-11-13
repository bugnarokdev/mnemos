#!/bin/sh
# Negative compile tests for the mission board (BETA, testnet preview).
# Each *.move.txt file is added to a scratch copy of the package; the build must fail
# with the missing `store` ability error. Exits non-zero if any file compiles.
set -eu
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PKG=$(dirname "$HERE")
SUI=${SUI:-sui}
fail=0
for src in "$HERE"/*.move.txt; do
  tmp=$(mktemp -d)
  cp "$PKG/Move.toml" "$tmp/"
  [ -f "$PKG/Move.lock" ] && cp "$PKG/Move.lock" "$tmp/"
  mkdir "$tmp/sources"
  cp "$PKG"/sources/*.move "$tmp/sources/"
  cp "$src" "$tmp/sources/negative.move"
  if out=$("$SUI" move build --path "$tmp" 2>&1); then
    echo "FAIL $(basename "$src"): compiled, the object is transferable"
    fail=1
  elif printf '%s' "$out" | grep -q "does not have the ability 'store'"; then
    echo "PASS $(basename "$src"): refused, missing 'store' ability"
  else
    echo "FAIL $(basename "$src"): build failed for another reason"
    printf '%s\n' "$out" | tail -20
    fail=1
  fi
  rm -rf "$tmp"
done
exit $fail
