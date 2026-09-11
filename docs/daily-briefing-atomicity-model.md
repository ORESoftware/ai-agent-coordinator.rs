# Daily briefing failure-atomic delivery model

## Purpose

This model is a deterministic acceptance oracle for `DEN-2466`, `DEN-2334`, `DEN-2333`, and `DEN-824`. It makes the delivery invariants executable before the production Rust state machine and persistence adapter are changed.

The model does not send a briefing or call a destination. It represents only durable state transitions and content-free digests.

## State

Every briefing run is bound to:

- a stable run identifier;
- an immutable normalized-input digest;
- an immutable policy digest;
- a monotonically increasing fencing token;
- a hashed owner identity;
- a deterministic composition digest;
- a hashed approved destination;
- a stable delivery-attempt identifier;
- a terminal destination receipt digest; and
- optional evidence that an ambiguous attempt was proven absent.

The phases are:

```text
pending
  -> claimed
  -> composed
  -> delivery_started
  -> delivered
```

An in-flight operation can instead become `ambiguous`. A replacement worker that takes over an in-flight operation also enters `ambiguous`; it cannot blindly send again. It must either record the original attempt’s positive receipt or provide durable absence evidence before creating a fresh attempt identifier.

## Failure atomicity

`apply()` validates the complete command and all state invariants before constructing a new frozen state value. `attempt()` converts a rejected command into an immutable receipt containing:

- the original state SHA-256;
- the canonical command SHA-256;
- the resulting state SHA-256;
- `changed=false`; and
- the bounded error reason.

For every rejected command, before and after state hashes must be identical. There is no in-place mutation or partial transition to repair.

## Idempotency

Retries of the same accepted operation return the existing state without mutation:

- repeated claim by the same owner and fence;
- repeated composition with the same input and composition digest;
- repeated delivery start with the same destination and attempt;
- repeated ambiguous marker;
- repeated positive receipt; and
- repeated absence proof.

Conflicting values fail without mutation.

## Fencing and takeover

Every command after claim must present the exact current fence. A takeover requires a strictly newer fence and a new hashed owner identity. Old workers can no longer compose, start delivery, reconcile, or complete the run.

Takeover of `delivery_started` changes the phase to `ambiguous` while preserving the original attempt. This prevents a replacement worker from interpreting process loss as proof that the destination did not accept the briefing.

## Checked scenarios

`fixtures/daily-briefing-atomicity-v1.json` currently covers:

1. normal claim, compose, send, and receipt;
2. idempotent retries at each accepted stage;
3. stale-fence rejection with unchanged state;
4. ambiguous send followed by a positive receipt;
5. ambiguous send, durable absence proof, and a new attempt;
6. takeover during an in-flight send;
7. conflicting second delivery attempt rejection;
8. wrong-attempt receipt rejection followed by correct completion;
9. conflicting composition rejection; and
10. prohibition on reusing an attempt proven absent.

Each scenario produces canonical step receipts and a final state digest. The fixture runner emits a deterministic aggregate receipt digest.

## Validation

```sh
python3 -m py_compile \
  scripts/daily_briefing_atomicity_model.py \
  scripts/test_daily_briefing_atomicity_model.py

python3 -m unittest -v scripts/test_daily_briefing_atomicity_model.py

python3 scripts/daily_briefing_atomicity_model.py \
  fixtures/daily-briefing-atomicity-v1.json \
  --json
```

The dedicated GitHub Actions workflow validates itself with pinned `actionlint`, runs all tests, executes all ten traces, validates digest-shaped receipts, and requires a clean tracked worktree.

## Production integration gate

This PR is a model and regression oracle, not the final storage integration. The production follow-up must:

1. map each Rust transition to an equivalent model command;
2. perform validation and persistence in one transaction or conditional write;
3. bind the database row version, lease owner, fencing token, input digest, policy digest, attempt, and receipt;
4. use an approved destination-specific idempotency key;
5. reconcile ambiguous remote responses before retrying;
6. run the same fixture semantics against the real storage adapter under crash and concurrency injection; and
7. keep outbound delivery disabled until protected destination credentials and the durable delivery gate are reviewed.

No production delivery, credential change, deployment, or external mutation is authorized by this model.
