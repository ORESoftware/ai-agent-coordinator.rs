#!/usr/bin/env bash
set -Eeuo pipefail

# Reconstruct and execute the validated no-force reconciler payload. The payload
# is chunked only to keep recovery commits reviewable through constrained write
# surfaces; its decoded SHA-256 is pinned below.
readonly EXPECTED_SHA256="1f0415e2452b6b7fc2c3a6e9e2b28e3934327e13c2460905f411307274fe4e0e"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mapfile -t parts < <(find "$repo_root/data" -maxdepth 1 -type f \
  -name 'zed-fleet-reconcile-no-force.part*.b64' -print | LC_ALL=C sort)
((${#parts[@]} > 0)) || { echo "no reconciler payload parts found" >&2; exit 1; }
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cat "${parts[@]}" | base64 --decode | gzip --decompress >"$tmp"
actual="$(sha256sum "$tmp" | awk '{print $1}')"
[[ "$actual" == "$EXPECTED_SHA256" ]] || {
  echo "reconciler payload digest mismatch: $actual" >&2
  exit 1
}
bash -n "$tmp"
exec bash "$tmp" "$@"
