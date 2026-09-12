# Fenced fabrication recovery protocol

## Purpose

This deterministic oracle supports `DEN-2527`, `DEN-2528`, `DEN-2529`, and the delivered parent slice `DEN-2525`. It defines the minimum state-machine and evidence semantics required before long-running fabrication and provider workers are integrated with PostgreSQL, Fiducia leases, and JetStream acknowledgement and dead-letter policy.

The oracle is dependency-free, content-free, and side-effect-free. It does not call a provider, broker, database, cluster, or object store.

## Immutable job identity

Every job is initialized with:

- a stable fabrication job identifier;
- a stable broker message identifier suitable for deterministic `Nats-Msg-Id` correlation; and
- a maximum delivery count.

The job identifier and message identifier never change across worker crashes, broker redelivery, checkpoint resume, terminal completion, dead-lettering, or broker acknowledgement.

## Lease and fencing

A claim records:

- a positive fencing token;
- a content-free worker identity digest; and
- the broker delivery count.

The first claim must advance from fence and delivery zero. A crash takeover must advance **both** the fence and delivery count. A duplicate claim by the same worker at the same fence and delivery is idempotent.

Every checkpoint, completion, failure, and dead-letter operation must present the exact current fence and worker digest. A stale or non-owning worker is rejected before state construction, and the before and after state hashes remain identical.

## Checkpoints

A checkpoint contains:

- a strictly monotonic sequence number;
- a checkpoint digest;
- an optional persisted provider-task digest; and
- an optional persisted artifact-reference digest.

The next checkpoint must advance by exactly one. Repeating the same sequence with byte-equivalent digest fields is idempotent. Repeating a sequence with different data, submitting a stale sequence, or skipping a sequence fails atomically.

Takeover and retry preserve the latest durable checkpoint and provider/artifact references. A worker can therefore resume polling or downstream processing instead of creating a duplicate provider task.

Completion requires at least one committed checkpoint and records a final artifact digest. This prevents a terminal record from bypassing all resumable state.

## Failures and retries

A failure records a bounded failure code and explicit retryable boolean. A retryable failure may be reclaimed only with a newer fence and newer broker delivery count. The new owner resumes from the persisted checkpoint, while transient failure metadata is cleared.

A non-retryable failure cannot be reclaimed for execution.

## Dead-letter policy

Dead-lettering requires a committed failure. It is allowed when either:

- the failure is non-retryable; or
- the broker delivery count has reached the configured maximum.

A retryable failure cannot enter the DLQ early. The dead-letter record includes a digest of the durable incident or DLQ envelope and preserves the original failure code and retryability classification.

## Commit-before-ACK

A broker ACK is represented separately from completion or dead-lettering. The worker first commits a terminal state and derives a canonical terminal digest from:

- job and message identity;
- terminal phase;
- fence and delivery count;
- latest checkpoint and provider/artifact references;
- final artifact or failure/DLQ data.

Only an ACK carrying that exact terminal digest is accepted. ACK before terminal commit and ACK with a mismatched digest are rejected atomically. ACK retry with the same digest is idempotent. The terminal digest excludes the ACK bit, so it remains stable before and after acknowledgement.

This models the required production ordering:

```text
PostgreSQL terminal transition commits
  -> immutable terminal receipt is available
  -> JetStream ACK is emitted
```

## Checked traces

`fixtures/fabrication-recovery-protocol-v1.json` includes thirteen deterministic traces:

1. normal checkpoints, completion, and ACK;
2. crash after checkpoint followed by fenced takeover and resume;
3. stale-writer rejection after takeover;
4. idempotent duplicate checkpoint;
5. conflicting same-sequence checkpoint rejection;
6. checkpoint sequence-gap rejection;
7. retryable failure, redelivery, resume, completion, and ACK;
8. non-retryable failure, DLQ commit, and ACK;
9. retryable failure at maximum delivery, DLQ commit, and ACK;
10. premature DLQ rejection;
11. ACK-before-terminal rejection;
12. wrong terminal digest rejection followed by correct idempotent ACK; and
13. terminal execution replay rejection.

Each step produces canonical state and command digests. Every rejected transition must preserve the exact state digest.

## Validation

```sh
python3 -m py_compile \
  scripts/fabrication_recovery_protocol.py \
  scripts/test_fabrication_recovery_protocol.py

python3 -m unittest -v scripts/test_fabrication_recovery_protocol.py

python3 scripts/fabrication_recovery_protocol.py \
  fixtures/fabrication-recovery-protocol-v1.json \
  --json
```

The dedicated GitHub Actions workflow validates itself with pinned `actionlint`, runs all focused tests, executes the thirteen traces, verifies state/terminal/aggregate digest shapes, and requires a clean worktree.

## Production integration gate

The owning fabrication service follow-up must map these semantics to one transactional PostgreSQL protocol and the existing Fiducia lease boundary:

1. claim or takeover uses a compare-and-swap on current fence, delivery count, and nonterminal state;
2. checkpoint writes bind job, message, worker, fence, sequence, provider task, and artifact reference;
3. provider task IDs are committed before polling or moving to another stage;
4. completion and failure are conditional on current ownership;
5. stale writes affect zero rows and emit bounded evidence;
6. JetStream ACK occurs only after the terminal transaction commits;
7. durable consumer names, `AckWait`, `MaxDeliver`, and backoff are versioned and reviewed;
8. poison messages commit a PostgreSQL incident and deterministic DLQ envelope before ACK;
9. crash, pod replacement, broker interruption, redelivery, expired lease, temporary database failure, duplicate delivery, and stale completion are exercised in isolated destructive tests; and
10. exact heads, schema/config versions, message identities, checkpoint receipts, terminal receipts, and cleanup evidence are retained.

No production worker activation, provider invocation, database migration, broker mutation, or deployment is authorized by this model.
