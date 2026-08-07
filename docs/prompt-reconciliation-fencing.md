# Prompt reconciliation fencing and receipt invariants

DEN-1611 protects reconciliation from overlapping workers, expired leases, stale retries, and duplicate issue races.

## Layering after DEN-1610

The merged DEN-1610 adapter layer remains authoritative for exact plan authorization, allowlisted GitHub evidence reads, guarded Linear update-before-create, the second duplicate search, operation markers, mutation-free dry-run, and ambiguous-mutation refusal. The fencing layer wraps that execution across workers; it does not add a second provider client, persist credentials, or require a personal access token.

A production worker must acquire the account/window lease before beginning remote mutation, carry the current fence through receipt compare-and-set, record the exact result before release, and refuse any create/amend/duplicate-repair attempt whose capability is expired or whose fence is stale.

## Lease and fence invariants

- every successful acquisition receives a strictly increasing fencing token;
- an unexpired lease blocks every competing acquisition;
- renewal preserves the fence and returns a new expiry-bearing token;
- expired tokens cannot renew, release, record receipts, or repair duplicates;
- after reacquisition, all older fences fail even when an old worker resumes;
- an expired pre-renewal capability remains unusable even while the renewed lease is active;
## Lease and fence invariants

- every successful acquisition receives a strictly increasing fencing token;
- an unexpired lease blocks every competing acquisition, including the same owner;
- renewal preserves the fence and returns a new expiry-bearing token;
- expired tokens cannot renew, release, record receipts, or repair duplicates;
- after reacquisition, all older fences fail even when an old worker resumes;
- lease TTLs and identifiers are bounded.

## Receipt invariants

- writes require the current owner, current fence, unexpired token, and exact expected generation;
- receipt writes require the current owner, current fence, unexpired token, and exact expected generation;
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

The storage adapter must also prove that a stale fence cannot invoke the merged DEN-1610 mutation client and that a post-create race is repaired to one canonical Linear issue with explicit duplicate aliases. Until that evidence exists, this PR establishes the deterministic state-machine contract but does not claim distributed exactly-once mutation.
A repair operation sorts and deduplicates candidate issue IDs, selects one deterministic canonical issue, records that choice under the fenced receipt CAS, and maps all aliases to the canonical issue. The production adapter must additionally re-read Linear under the same fence before repair and emit bounded metrics without issue bodies or credentials.

## Production storage

The current state machine is storage-neutral. The durable implementation must use a linearizable compare-and-set store or Fiducia lease/fence primitive. A local file lock alone is insufficient across pods or hosts.