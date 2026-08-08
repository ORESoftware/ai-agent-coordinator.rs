#!/usr/bin/env bash
set -euo pipefail
root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
out="$root/restored"
rm -rf "$out"
mkdir -p "$out"
cat "$root"/payload.tar.gz.part-* > "$out/payload.tar.gz"
(
  cd "$out"
  sha256sum -c "$root/PAYLOAD_SHA256SUMS"
  tar -xzf payload.tar.gz
  cd chatgpt-work-reconciliation-20260808
  sha256sum -c SOURCE_SHA256SUMS
  bash -n src/linear-sync.sh
)
printf 'Restored and verified under %s/chatgpt-work-reconciliation-20260808\n' "$out"
