# Deep-test repository fleet for every connected `*-test` organization

**Tracking:** [ai-agent-coordinator.rs#139](https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/139)
**Linear:** [DEN-3288](https://linear.app/denman/issue/DEN-3288/add-four-deep-test-repositories-across-every-connected-test)
**Operation:** `deep-test-fleet-20260808`
**Reviewed scope:** 25 GitHub organizations × 4 repositories = 100 repositories
**Outcome:** 100 created, 100 PRs merged, 100 default branches hash-verified, 0 failures

## Purpose

The existing test organizations need more than a single end-to-end repository. This operation added four orthogonal, executable test suites to every connected organization whose login ends in `-test`:

| Repository | Deep-testing responsibility |
|---|---|
| `contract-conformance-tests` | Stateful reference models, canonical serialization, idempotency-key conflicts, retry equivalence, and multi-seed trace replay. |
| `chaos-recovery-tests` | Durable journals, crashes, dropped requests, timeouts after commit, duplicate delivery, delayed replicas, partition healing, and exactly-once side-effect assertions. |
| `upgrade-compatibility-tests` | Version negotiation, forward/backward reads, lossless migration round trips, explicit lossy-downgrade rejection, destructive-schema guards, and deterministic snapshot replay. |
| `security-boundary-tests` | Tenant/role isolation, path traversal, exact-host SSRF controls, HMAC tamper/replay/skew rejection, secret redaction, and immutable read-only CI verification. |

Every foundation is dependency-light Python, uses synthetic data, runs without production credentials or customer payloads, includes `.zpkg.toml`, and has pinned read-only GitHub Actions. The generated code is an executable oracle intended to receive product adapters through later focused pull requests.

## Exact organization inventory

1. `3fa-app-test`
2. `apostille-me-test`
3. `claritas-viz-test`
4. `cliptown-test`
5. `declarative-migrations-test`
6. `discrete-event-systems-test`
7. `embedded-alerts-test`
8. `evento-globolo-test`
9. `fiducia-cloud-test`
10. `file-tunnel-test`
11. `flags-2-env-test`
12. `hacker-house-medellin-test`
13. `hypesiege-test`
14. `memebank-test`
15. `messaging-intel-test`
16. `networking-components-test`
17. `opto-sync-test`
18. `ores-otel-test`
19. `quaestor-ledger-test`
20. `r2g-test`
21. `scintilla-run-test`
22. `shared-auth-test`
23. `sonus-auris-test`
24. `streempilot-test`
25. `zed-pkg-test`

`canonical-cloud-test` was not present in the connected GitHub installation and the public organization lookup returned `404`; it was excluded rather than guessed into an authorized mutation set.

## Fail-closed publication contract

Before creating any repository, the publisher validated the complete manifest and generated trees locally, then verified:

1. the authenticated identity was exactly GitHub user `ORESoftware` with account ID `11139560`;
2. that identity had active owner/admin membership in every listed organization;
3. every existing target would have the exact expected owner, public visibility, and non-archived state;
4. the checked-in manifest remained `live_creation_enabled=false`;
5. all 100 generated file sets had pinned Actions, read-only permissions, no conflict markers, no credential-shaped content, and the mandatory semantic-merge policy.

Only after the complete preflight succeeded did mutation begin. Repository creation was idempotent. Existing managed paths would have been accepted only when their Git blob hashes exactly matched; divergent paths would have failed closed for conceptual reconciliation instead of being overwritten. No pre-existing target repositories were encountered in this run.

Each foundation landed on `agent/deep-test-foundation-20260808`, opened a repository-local pull request, waited for the exact-head `verify` check, squash-merged only a successful head SHA, and verified every managed Git blob on the default branch. No force update or history rewrite was used.

## Semantic conflict policy

Any collision must be resolved semantically. Inspect the merge base, reread every affected file completely, and inspect 3–10 relevant commits from both sides when available. Include canonical interfaces, schemas, migrations, fixtures, CI behavior, same-organization repositories, and materially relevant external repositories. Never resolve by wholesale selection of `ours`, `theirs`, current, or incoming. Preserve compatible intent, add regression coverage for the reconciled behavior, scan the full tree for markers, and rerun the complete suite.

No target-repository collision occurred. The control branch was based on `25aff7209bde90d41494b4d6f98bf724aae33708`; `main` was rechecked after execution and still pointed to that exact commit, so there was no upstream conflict to reconcile before the audit pull request.

## Validation evidence

Before activation:

- exact 25-organization and 100-repository generation: pass;
- four representative repository verifiers: pass;
- 19 generated behavior tests: pass;
- 11 fleet/publisher contract tests: pass;
- duplicate JSON keys, unsafe organizations, live-enablement drift, owner-ID drift, mutable Actions, conflict markers, redirects, and credential echoes: rejected;
- credential-pattern and unresolved-conflict scans across staged source: clean.

The permanent exact-head control validation passed in [Actions run 31296343446](https://github.com/ORESoftware/ai-agent-coordinator.rs/actions/runs/31296343446).

## Live execution outcome

The encrypted live bootstrap completed successfully in [Actions run 31296365557](https://github.com/ORESoftware/ai-agent-coordinator.rs/actions/runs/31296365557). Issue #139 contains the bounded per-repository execution ledger.

| Metric | Count |
|---|---:|
| Repositories total | 100 |
| Repositories created | 100 |
| Pre-existing repositories reused | 0 |
| Pull requests created or reused | 100 |
| Pull requests merged | 100 |
| Pull requests left open | 0 |
| Already-initialized repositories | 0 |
| Default branches hash-verified | 100 |
| Failures | 0 |

The first, middle, and final cohorts were sampled independently through the GitHub API while the publisher ran. The final repository, `zed-pkg-test/security-boundary-tests`, merged PR #1 at exact head `2441d1ad26d071e696d1079e483d8daf4ac19e95` and reports Python content on `main`. A fleet-wide query found 100 merged foundation PRs and zero closed-but-unmerged foundation PRs.

## One-time credential boundary and retirement

The temporary live workflow contained no token. It created a fresh RSA-3072 keypair on the runner and published only the public key plus a random run ID to issue #139. It accepted exactly one ciphertext authored by `ORESoftware`, bound to that run ID and the exact confirmation `create:25-test-orgs:4-deep-test-repos-each:100`, using RSA-OAEP/SHA-256.

The decrypted credential was immediately masked, written only to a mode-0600 runner-temporary file, and removed with the private key and payload files in an unconditional cleanup step. No plaintext credential was committed, placed in workflow configuration or command arguments, posted to GitHub or Linear, uploaded as an artifact, or included in completion evidence.

The workflow's `Destroy credential material` step completed successfully. After completion evidence was published, both the one-time relay workflow and its trigger were deleted from the feature branch. The checked-in manifest remains disabled, so merging the permanent audit changes cannot recreate repositories or reactivate credential handling.

## Permanent artifacts

The audit pull request retains only:

- the exact disabled fleet manifest;
- deterministic repository foundation templates;
- the idempotent, fail-closed publisher implementation;
- publisher and generated-suite regression tests;
- this execution and security record;
- the permanent read-only contract-validation workflow.

The temporary relay, trigger, source materializer, source chunks, and all runner credential material are absent.
