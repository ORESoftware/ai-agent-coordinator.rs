# Prompt reconciliation fencing and receipt invariants

DEN-1611 protects reconciliation from overlapping workers, expired leases, stale retries, and duplicate issue races.

## Lease and fence invariants

- every successful acquisition receives a strictly increasing fencing token;
- an unexpired lease blocks every competing acquisition;
- renewal preserves the fence and returns a new expiry-bearing token;
- expired tokens cannot renew, release, record receipts, or repair duplicates;
- after reacquisition, all older fences fail even when an old worker resumes;
- an expired pre-renewal capability remains unusable even while the renewed lease is active;
- lease TTLs and identifiers are bounded.

## Receipt invariants

- writes require the current owner, current fence, unexpired token, and exact expected generation;
- each successful new receipt increments the generation exactly once;
- an exact rerun is a no-op and does not advance generation;
- a conflicting result for the same operation fails closed;
- counter overflow fails closed.

## Duplicate-race repair

A repair operation sorts and deduplicates candidate issue IDs, chooses one deterministic canonical issue, records that choice under the fenced receipt CAS, and maps aliases to the canonical issue. An idempotent repair rerun must observe exactly the same aliases; it cannot silently mutate aliases without a new generation.

## Failure-injection coverage

`tests/prompt_reconciliation_fencing_failures.rs` exercises the cross-worker sequences that matter before a durable adapter is wired:

- two logical workers read the same receipt generation and only the first compare-and-set succeeds;
- a worker disappears without releasing its lease, a replacement resumes after expiry, and the durable receipt suppresses a duplicate mutation;
- a duplicate-repair worker crashes, the replacement performs an exact no-op replay, and later candidate-set drift fails closed;
- renewal does not revive the expired pre-renewal capability.

These tests model process loss and stale retries while keeping one in-memory state object as the stand-in for the future linearizable store. Storage restart and network-partition tests remain mandatory at the adapter layer.

## Production storage

The current state machine is storage-neutral. Production must use a linearizable compare-and-set store or a Fiducia lease/fence primitive. A local file lock alone is insufficient across pods or hosts. The durable adapter must preserve `next_fence`, receipt generation, receipts, aliases, and lease expiry atomically, then rerun the failure sequences across actual process restarts and injected partial failures.
