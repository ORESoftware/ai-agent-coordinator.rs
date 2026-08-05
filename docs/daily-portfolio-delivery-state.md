# Daily portfolio briefing delivery state

`src/daily_portfolio_delivery.rs` defines the storage-independent state machine for the consequential half of the daily portfolio briefing. The deterministic collector and composer may prepare a plan, but a destination send is permitted only while a worker owns a live fenced lease and the run is in the correct compare-and-set generation.

This module is the first DEN-2334 implementation slice. It deliberately contains no network client and no in-memory claim of durability. A PostgreSQL repository must persist the same immutable plan, lease, generation, attempt, receipt, and scheduled-baseline fields before a production destination adapter is enabled.

## Immutable run identity

A `PlanSpec` fixes:

- logical `run_key`;
- canonical `scheduled_run_key`;
- scheduled, recovery, or manual mode;
- source, plan, and outbound delivery SHA-256 digests;
- approved destination identity;
- destination idempotency key.

The idempotency key must equal the logical run key. Replanning the same key with identical immutable fields is a no-op. Any field drift is a conflict, not a silent update.

Run identities are mode-specific:

- scheduled: `run_key` exactly equals `daily-portfolio:scheduled:YYYY-MM-DD`;
- recovery: `run_key` begins `daily-portfolio:recovery:` and points at one canonical scheduled key;
- manual: `run_key` begins `daily-portfolio:manual:` and never replaces the scheduled comparison baseline.

Identifiers are bounded ASCII-safe values and reject credential-shaped material. Digests are lowercase SHA-256 values.

## Lease and fencing contract

Every mutating transition requires a `LeaseToken` containing the run key, owner, monotonically increasing fencing token, and expiry.

- An unexpired lease blocks another owner.
- Reacquisition after expiry advances the global fence, except an expired `delivering` run must first pass through explicit ambiguity recovery.
- A worker cannot reacquire an expired in-flight delivery and treat `begin` as an idempotent resend; `RecoveryRequired` forces `recover_expired_delivery` before a reconciliation lease can be issued.
- An old owner cannot renew, release, begin, fail, mark ambiguous, or commit a receipt after a newer fence exists.
- TTL is nonzero and at most one hour.
- A sending lease cannot be voluntarily released; the worker must record a terminal attempt outcome or let expiry recovery mark it ambiguous.

The future PostgreSQL adapter must allocate fences and claim leases atomically in one transaction. Comparing owner names without the fence is insufficient.

## Compare-and-set generations

Every run begins at generation 0. State mutations require the exact expected generation and advance it exactly once:

```text
planned --begin--> delivering --receipt--> delivered
                          |--failure--> failed --begin retry--> delivering
                          |--ambiguous--> ambiguous --verified receipt--> delivered
```

A repeated `begin` at the already-current delivering generation is an idempotent no-op only for the current live fence. An expired in-flight lease cannot be reacquired until recovery advances the run to `ambiguous`. A stale generation is rejected. Ambiguous runs cannot resend until the destination is reconciled; a verified receipt may close ambiguity under a new fenced lease.

Attempts increment only when entering `delivering`. Merely claiming a lease does not count an attempt.

## Crash windows

The regression suite covers the required restart boundaries:

1. **Before send:** a crashed owner leaves a planned run and an expiring lease. A later owner reacquires and records the first actual attempt.
2. **After send, before receipt:** direct reacquisition returns `RecoveryRequired`; expiry recovery changes `delivering` to `ambiguous`, and resend remains blocked until reconciliation.
3. **Destination confirmed, state not committed:** a new owner may record the externally verified receipt from `ambiguous` using the same logical and destination idempotency keys.
4. **After committed receipt:** the run is delivered and no further lease can be acquired; an exact operation replay is acknowledged without mutating state.

The storage adapter must perform each transition and its receipt/baseline update transactionally. An HTTP success observed only in process memory is not a committed delivery.

## Receipt contract

A `DestinationReceipt` contains a bounded receipt ID, exact destination identity, outbound body digest, and delivery timestamp.

The state machine rejects receipts whose destination or body digest differs from the immutable plan. A successful receipt changes the run to delivered, clears the lease and error, and records the receipt exactly once. Replaying the exact same receipt with the original compare-and-set generation returns `AlreadyApplied` even though the lease has been cleared; receipt or generation drift fails closed.

The future destination adapter must additionally enforce:

- an approved HTTPS endpoint or provider-native destination;
- no redirects;
- bounded request and response bodies;
- bounded timeouts;
- redacted authorization in logs and evidence;
- the logical run key as the provider idempotency key;
- explicit classification of timeout or uncertain response as ambiguous;
- reconciliation before resend.

## Scheduled baseline

Only confirmed scheduled or recovery receipts may advance the scheduled comparison baseline.

- Manual runs never update it.
- An older scheduled date cannot regress it.
- A newer date replaces it.
- A second delivery for the same scheduled date must match the existing plan digest, delivery digest, and receipt ID; otherwise the commit fails before mutating the run.

This prevents an old recovery or manual preview from changing the next scheduled briefing's unchanged-item suppression baseline.

## PostgreSQL persistence follow-on

The next DEN-2334 slice must add schema-authority and coordinator-repository support for:

- immutable run identity and digests;
- status, generation, attempt count, and bounded error;
- lease owner, fence, and expiry;
- destination receipt fields;
- one scheduled-baseline row;
- uniqueness on logical run key and destination idempotency key;
- checks for valid statuses, modes, digests, and bounded text;
- transactional claim, renew, transition, receipt, and baseline operations.

PostgreSQL integration tests must run two concurrent claimers and prove that only one live fence can mutate a run. Restart tests must reload state from a new connection rather than cloning the Rust value.

## Local validation

```bash
cargo fmt --all -- --check
cargo test --locked --test daily_portfolio_delivery_state -- --nocapture
cargo clippy --locked --all-targets --all-features -- -D warnings
```

The dedicated workflow also builds the release target and retains a machine-readable focused-test report. Production delivery remains disabled until the PostgreSQL repository, approved adapter, and canary evidence are merged and reviewed.
