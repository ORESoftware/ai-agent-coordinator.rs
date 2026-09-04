# Daily briefing receipt and delivery contracts

## Purpose

The daily portfolio briefing must be assembled from durable normalized lane results rather than by re-reading source systems during composition. A missing lane, partial write, stale receipt, or ambiguous destination response must remain visible and must never be converted into an empty successful briefing.

This slice implements two dependency-free contracts:

1. `portfolio-briefing-lane-manifest/v1` validates the complete set of normalized lane receipts and derives one deterministic `portfolio_briefing_input.v1` envelope.
2. `portfolio-briefing-delivery-state/v1` defines pure fenced state transitions for a later protected delivery adapter.

Neither contract fetches source data or sends a briefing.

## Lane manifest

A manifest declares:

- one fixed scheduled window and timezone;
- the immutable policy digest used by every lane;
- the exact expected lane IDs;
- one terminal receipt for every lane; and
- false-by-construction source-payload, credential, and delivery-authorization flags.

Every receipt binds the lane to its source cursor digest, result digest, exact producer commit, schema version, observation time, retained-until time, operation identity, and descriptor/path identity. Successful and explicit no-result receipts carry bounded result metadata. Failed, blocked, skipped, or unavailable receipts carry only a reason code and block composition.

The validator rejects:

- missing, duplicate, or unexpected lanes;
- missing or duplicate operation/path identities;
- receipts outside the scheduled window;
- invalid commit or SHA-256 identities;
- stale retention timestamps;
- result claims attached to non-ready lanes;
- a successful lane with zero items or bytes;
- a no-results lane with nonzero items;
- unknown fields or raw-payload fields; and
- any manifest that authorizes delivery or claims embedded credentials/source payloads.

Receipts are sorted by lane ID before the input envelope is hashed, so receipt arrival order cannot change the composition identity.

## Delivery state machine

The pure state machine supports:

```text
prepared -> claimed -> delivering -> delivered
                         |       \
                         |        -> reconciling -> delivered
                         |                       \
                         |                        -> retryable -> claimed
                         -> retryable -> claimed
                         -> terminal_failed
```

A claim requires a strictly newer fencing token. All post-claim operations must match both the current operation ID and fencing token. An ambiguous remote response moves to `reconciling`; the caller must prove either an immutable receipt or a reconciled not-delivered result before another attempt can be claimed.

`transition_delivery()` deep-copies the current state, validates the complete successor, and returns the successor only after every invariant passes. Illegal and malformed commands leave the supplied state byte-for-byte unchanged.

## Commands

Validate the checked-in eight-lane fixture:

```sh
python3 scripts/validate_daily_briefing_receipts.py lanes \
  fixtures/daily-briefing-lanes-v1.json \
  --require-ready --json
```

Run the adversarial suite:

```sh
python3 -m py_compile \
  scripts/daily_briefing_receipts.py \
  scripts/validate_daily_briefing_receipts.py \
  scripts/test_daily_briefing_receipts.py
python3 -m unittest -v scripts/test_daily_briefing_receipts.py
```

The fixture is synthetic and contains content-free test hashes only. It is not a production briefing receipt.

## Operational boundary

The current implementation is storage-independent and side-effect-free. Production collection still requires descriptor-relative reads from the approved result root, and production delivery still requires a protected destination adapter, exact allowlist, durable idempotency storage, and reviewed activation configuration. Those operational boundaries are intentionally not activated here.

Tracking: `DEN-2333`, `DEN-2334`, `DEN-2466`, and GitHub issues created for durable collection and atomic delivery.
