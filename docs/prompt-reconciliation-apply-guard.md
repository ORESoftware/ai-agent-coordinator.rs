# Prompt reconciliation apply guard

This is the first guarded-write slice of DEN-1610. It establishes an explicit authorization boundary and an atomic local receipt ledger before authenticated GitHub and Linear mutation adapters are connected.

## Safety contract

Live reconciliation is refused unless all of these values match the exact reviewed plan:

1. `PROMPT_RECONCILIATION_APPLY_ENABLED=true` is present in the environment.
2. `--account` matches the plan's account fingerprint.
3. `--digest` matches the lowercase SHA-256 digest of the exact plan bytes.
4. `--confirmation 'APPLY PROMPT RECONCILIATION'` matches exactly.
5. Operation, mutation, and canonical issue identifiers are bounded safe ASCII.
6. The receipt ledger can be locked exclusively by this local process.

Credentials are deliberately not accepted as CLI flags. GitHub and Linear tokens remain environment-only inputs to the adapter layer and must never be written to plans, reports, receipts, issue bodies, logs, URLs, or telemetry.

## Receipt semantics

The receipt ledger is strict JSON with unknown fields denied. It is protected by an exclusive `create_new` lock file and replaced atomically through a synced temporary file. A repeated operation with the same mutation key and canonical issue is a no-op. A repeated operation with a different result fails closed.

The ledger contains only:

- operation ID;
- mutation key;
- canonical issue ID;
- application timestamp;
- schema version and monotonic local generation.

Distributed leases, fencing tokens, cross-host compare-and-set receipts, and duplicate-race repair remain explicitly scoped to DEN-1611.

## Local validation

```bash
cargo fmt --all -- --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --bin prompt-reconciliation-apply-guard
cargo build --release --bin prompt-reconciliation-apply-guard
```

The adapter follow-up in DEN-1610 will invoke this authorization contract immediately before each guarded mutation and will record a receipt only after the remote canonical result is known.