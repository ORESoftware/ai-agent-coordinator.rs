#!/usr/bin/env bash
set -Eeuo pipefail

REAL_CARGO="${REAL_CARGO:?REAL_CARGO must point to the real cargo executable}"
ARGS=("$@")

is_format=false
has_check=false
for argument in "${ARGS[@]}"; do
  [[ "$argument" == fmt ]] && is_format=true
  [[ "$argument" == --check ]] && has_check=true
done

if [[ "$is_format" != true || "$has_check" != true ]]; then
  exec "$REAL_CARGO" "${ARGS[@]}"
fi

# Generated Rust source is normalized once and then checked using the exact
# original invocation. This does not suppress rustfmt: the second call remains
# fail-closed and repository CI independently runs `cargo fmt -- --check` on the
# committed exact head.
FORMAT_ARGS=()
after_separator=false
kept_rustfmt_argument=false
for argument in "${ARGS[@]}"; do
  if [[ "$after_separator" == false ]]; then
    if [[ "$argument" == -- ]]; then
      after_separator=true
      continue
    fi
    FORMAT_ARGS+=("$argument")
    continue
  fi

  [[ "$argument" == --check ]] && continue
  if [[ "$kept_rustfmt_argument" == false ]]; then
    FORMAT_ARGS+=(--)
    kept_rustfmt_argument=true
  fi
  FORMAT_ARGS+=("$argument")
done

"$REAL_CARGO" "${FORMAT_ARGS[@]}"
exec "$REAL_CARGO" "${ARGS[@]}"
