#!/usr/bin/env bash
# Translate reviewed non-secret CLI flags into environment variables through
# the pinned flags-2-env contract, then exec the coordinator or another command.
#
# Usage:
#   bash scripts/with-flags2env.sh [flags...] -- command [args...]
set -euo pipefail

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
flags=()
while (($#)) && [[ "$1" != "--" ]]; do
  flags+=("$1")
  shift
done

if (($# == 0)); then
  echo "usage: bash scripts/with-flags2env.sh [flags...] -- command [args...]" >&2
  exit 2
fi
shift

if (($# == 0)); then
  echo "flags2env: command is required after --" >&2
  exit 2
fi

bin="${FLAGS2ENV_BIN:-}"
if [[ -z "$bin" ]]; then
  if [[ -x "$root/tools/flags-2-env/build/flags2env" ]]; then
    bin="$root/tools/flags-2-env/build/flags2env"
  elif [[ -x "$root/vendor/flags-2-env/build/flags2env" ]]; then
    bin="$root/vendor/flags-2-env/build/flags2env"
  elif command -v flags2env >/dev/null 2>&1; then
    bin="$(command -v flags2env)"
  else
    echo "flags2env: set FLAGS2ENV_BIN or build the pinned checkout under tools/flags-2-env" >&2
    exit 127
  fi
fi

if [[ ! -x "$bin" ]]; then
  echo "flags2env: executable not found at $bin" >&2
  exit 127
fi

exports="$("$bin" shell-env --config "$root/.cli-flags.toml" -- "${flags[@]}")"
eval "$exports"
exec "$@"
