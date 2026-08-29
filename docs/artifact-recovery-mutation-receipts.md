# Optimistic-concurrency receipts for coordinator mutations

A Linear or GitHub write is safe only for the exact target state that was observed and approved. A ticket can change project, state, title, or relationships; a branch or pull request can move; authorization and routing policy can change; and a provider timeout can hide an already accepted mutation. `coordinator_mutation_receipts.v1` makes those conditions explicit and prevents a stale plan from being applied to a different live object state.

## Contract

The report contains four immutable inputs and three derived outputs:

* a content-addressed policy for clock skew, snapshot age, lease duration, and retry attempts;
* current target snapshots with digests for identity, version, routing, authorization, and an authoritative lease;
* mutation intents containing only digests and bounded enums, never raw patches, issue text, provider errors, credentials, or personal data;
* result receipts from provider adapters, content-addressed to the intent, precondition, provider receipt, outcome, and resulting version;
* preflight assessments derived from the current snapshots and prior results;
* a bounded failure ledger for conflicts, denials, broken chains, and compensation work;
* a canonical report digest covering the complete report.

The policy version is the canonical SHA-256 digest of the policy fields. Changing a limit without changing the version is rejected.

## Pre-write protocol

An adapter must follow this sequence immediately before each mutation:

1. Resolve the stable target and re-read its current version, project or repository route, authorization capability, and lease.
2. Normalize the desired patch outside this contract and store only its SHA-256 digest.
3. For duplicate, blocking, or related-ticket changes, bind the live relation analysis digest.
4. Construct and content-address the intent, including actor, run, idempotency key, lease, policy, compensation, and chain context.
5. Run the planner against the fresh target snapshot.
6. Execute exactly once only when the decision is `ready`.
7. Record the provider outcome as an immutable result receipt and re-run validation.

A target-version, route, authorization, policy, or lease mismatch never falls back to the old patch. It produces a structured conflict or denial and requires a new read and a newly sealed intent.

## Decisions

* `ready`: every precondition still matches and no accepted result exists;
* `replay`: the same intent already has one accepted result, so return that receipt without mutating again;
* `conflict`: the version, route, lease, retry budget, idempotency key, or concurrent-intent set is unsafe;
* `expired`: the authoritative lease elapsed;
* `denied`: authorization or governing policy changed;
* `unverifiable`: the target cannot be resolved;
* `blocked_by_chain`: a required prior result is absent, rejected, belongs to another chain position, or its subject has moved.

Two different intents for the same target version are not arbitrarily ordered. They are both blocked as an ambiguous race. A lease service must select one owner and the losing intent must be replanned from a new snapshot.

## Idempotency and result receipts

The idempotency key is a digest bound into both intent and result. A retry of an accepted intent resolves to `replay`, even when the target later changes; it must not apply the patch again. Reusing one key for different intents is a conflict. More than one accepted result for one intent is a contract violation.

Accepted and compensated outcomes require a provider-receipt digest and an after-version digest. Rejected and provider-error outcomes cannot claim an after-version. Raw provider messages are prohibited; adapters store only a bounded detail digest and a separately controlled diagnostic reference when policy permits.

Result timestamps must be within the permitted clock skew, cannot predate the intent observation, and must fall inside the authoritative lease window. A late or backdated provider receipt is rejected rather than admitted as proof of a safe write.

## Cross-system chains and compensation

Every intent binds a workflow digest, step, sequence, previous result, and trace digest. A downstream mutation can proceed only when the previous result was accepted, belongs to the immediately preceding sequence in the same workflow, and the previous subject still has the recorded after-version.

This supports a trace such as Linear issue → repository route → branch and commit → pull request → exact-head evidence → Linear review state without treating an obsolete pull-request head as current proof. When a later provider step fails after an earlier accepted step, the report emits `compensation_required`; the intent's bounded compensation action identifies the only permitted rollback or manual-review path.

## Commands

```bash
python3 tools/artifact_recovery_mutation_receipts.py plan \
  --input raw-mutations.json \
  --output mutation-receipts.json \
  --now 2026-08-10T20:00:00Z

python3 tools/artifact_recovery_mutation_receipts.py validate \
  mutation-receipts.json \
  --now 2026-08-10T20:00:00Z
```

The `example` command emits a deterministic synthetic CI fixture. It is not evidence that a live Linear or GitHub write occurred.

## Integration boundary

This implementation is a dry-run and validation control. It does not call Linear or GitHub and does not authorize merges, deployments, force-pushes, default-branch writes, bulk relationship changes, or secret persistence. Provider adapters must preserve their normal authorization and confirmation rules and must never interpret a non-`ready` decision as success.

Refs DEN-3452, DEN-3434, DEN-3435, and DEN-2797.
