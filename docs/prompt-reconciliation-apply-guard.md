# Prompt reconciliation apply guard

This DEN-1610 slice establishes the local authorization and idempotency boundary that authenticated GitHub and Linear adapters must call before and after a remote mutation.

## Authorization

The guard refuses apply unless all conditions hold:

1. `PROMPT_RECONCILIATION_APPLY_ENABLED=true` is present in the environment.
2. `--account` exactly matches the reviewed plan's account fingerprint.
3. `--digest` exactly matches the lowercase SHA-256 digest of the reviewed plan bytes.
4. `--confirmation 'APPLY PROMPT RECONCILIATION'` is exact.
5. Operation, mutation, and canonical issue identifiers are bounded safe ASCII.
6. The local receipt ledger can be locked exclusively.

Credentials are not accepted as command-line arguments. GitHub and Linear tokens remain environment-only adapter inputs and must never appear in plans, reports, receipts, issue bodies, URLs, logs, or telemetry.

## Receipts

The strict JSON ledger denies unknown fields and contains only bounded operation metadata. It is written through a synced temporary file followed by atomic replacement. A rerun with the same operation, mutation key, and canonical issue is a no-op. A conflicting canonical result fails closed.

This local lock prevents two processes on one host from mutating the same ledger simultaneously. Cross-host leases, monotonic fencing tokens, compare-and-set storage, and duplicate-race repair remain scoped to DEN-1611.

## Validation

```bash
cargo fmt --all -- --check
cargo test --bin prompt-reconciliation-apply-guard
cargo clippy --all-targets --all-features -- -D warnings
cargo build --release --bin prompt-reconciliation-apply-guard
```

The authenticated, allowlisted GitHub evidence reader and guarded Linear update-before-create worker are documented in `docs/prompt-reconciliation-adapters.md`. Distributed fencing and duplicate-race repair remain scoped to DEN-1611.