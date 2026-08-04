# Prompt reconciliation plans

`prompt_reconciliation` is the second implementation slice of DEN-834 and the implementation target for DEN-1609. It combines the existing deterministic prompt-intake report with bounded GitHub landing evidence, bounded Linear candidate summaries, and durable mutation receipts. The result is a deterministic, mutation-free plan for each prompt.

This module does not call GitHub or Linear and cannot mutate external state. Authenticated evidence collectors and the explicitly authorized apply worker remain DEN-1610. Fenced leases, compare-and-set receipt persistence, and post-create duplicate repair remain DEN-1611.

## Inputs

The planner accepts two values:

1. the `PromptIntakeReport` produced by `prompt_intake`; and
2. `ReconciliationEvidence`, supplied by a separate trusted collector.

Evidence is keyed by the prompt planner's stable `mutation_key`. Unknown fields fail deserialization. Evidence records are bounded and may contain only:

- repository identity;
- whether collection completed;
- one landing state: `no_match`, `non_default_only`, `default_branch`, or `conflicting`;
- resolvable `https://github.com/...` links without queries, fragments, user-info, or whitespace;
- bounded Linear candidate metadata: issue ID, `https://linear.app/...` URL, project, status, scope signature, mutation keys, and repository identities;
- a boolean indicating residual operational work; and
- durable mutation receipts containing stable identifiers, outcome, and canonical issue ID.

Do not place credentials, prompt bodies, hidden reasoning, mailbox or chat payloads, Git remote URLs containing user-info, or unbounded connector responses in this input.

## Planning order

For each actionable prompt the planner applies the following order:

1. A prior successful receipt suppresses all duplicate work.
2. Ambiguous or unresolved ownership is sent to manual review.
3. Missing, incomplete, conflicting, or unexpected repository evidence fails closed.
4. Linear candidates are scored by exact mutation key, then exact scope in the resolved project, then repository/project match.
5. Tied top candidates and exact matches that are canceled or already duplicates require review.
6. A single canonical candidate is amended before any create is proposed.
7. Default-branch landing with no residual work and no canonical candidate produces `already_landed`, not backlog noise.
8. Unlanded work or residual operational work with no candidate produces one deterministic create plan.

Every proposed amend or create contains a stable SHA-256 operation/idempotency key derived from the prompt mutation key, operation kind, and target. The output contains bounded prompt summaries and fingerprints from the first planner, not full source text.

## Determinism and safety

The planner sorts prompt results, reasons, and evidence links before serialization. Identical report and evidence inputs therefore produce byte-stable JSON. Applied receipts are checked before evidence collection requirements so a retry can safely suppress an already completed mutation.

The module is intentionally connector-neutral. A future apply worker must still:

- acquire a fenced account/window lease;
- re-read the canonical issue immediately before mutation;
- compare-and-set the operation receipt;
- perform update-before-create;
- run a final duplicate search before create;
- persist the successful receipt atomically with enough evidence to recover; and
- transfer unique requirements before recording a real `duplicateOf` relation during race repair.

## Validation

The unit suite covers deterministic serialization, prior-receipt suppression, update-before-create, default-branch landing, residual operational work, create planning, ambiguous candidates, incomplete evidence, unsafe URLs, and non-repository operational work.

Repository validation remains authoritative:

```sh
cargo fmt --all -- --check
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo test --locked --all
cargo doc --locked --no-deps
cargo build --locked --release
```
