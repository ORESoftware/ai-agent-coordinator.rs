# Deep-test repository fleet for every connected `*-test` organization

**Tracking:** [ai-agent-coordinator.rs#139](https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/139)
**Operation:** `deep-test-fleet-20260808`
**Reviewed scope:** 25 GitHub organizations × 4 repositories = 100 repositories

## Purpose

The existing test organizations need more than a single end-to-end repository. This operation adds four orthogonal, executable test suites to every connected organization whose login ends in `-test`:

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

`canonical-cloud-test` was not present in the connected GitHub installation and the public organization lookup returned `404`; it is therefore excluded rather than guessed into an authorized mutation set.

## Fail-closed publication contract

Before creating any repository, the publisher validates the complete manifest and generated trees locally, then verifies:

1. the authenticated identity is exactly GitHub user `ORESoftware` with account ID `11139560`;
2. that identity has active owner/admin membership in every listed organization;
3. every existing target has the exact expected owner, public visibility, and non-archived state;
4. the checked-in manifest remains `live_creation_enabled=false`;
5. all 100 generated file sets have pinned Actions, read-only permissions, no conflict markers, no credential-shaped content, and the mandatory semantic-merge policy.

Only after the entire preflight succeeds may mutations begin. Repository creation is idempotent. Existing managed paths are accepted only when their Git blob hashes exactly match; divergent paths fail closed for conceptual reconciliation instead of being overwritten. A new GitHub-generated README is the only permitted initial replacement.

Each foundation lands on `agent/deep-test-foundation-20260808`, opens a repository-local pull request, waits for the exact-head `verify` check, squash-merges only a successful head SHA, and verifies all managed Git blob hashes on the default branch. No force update or history rewrite is used.

## Semantic conflict policy

Any collision must be resolved semantically. Inspect the merge base, reread every affected file completely, and inspect 3–10 relevant commits from both sides when available. Include canonical interfaces, schemas, migrations, fixtures, CI behavior, same-organization repositories, and materially relevant external repositories. Never resolve by wholesale selection of `ours`, `theirs`, current, or incoming. Preserve compatible intent, add regression coverage for the reconciled behavior, scan the full tree for markers, and rerun the complete suite.

## One-time credential boundary

The temporary live workflow does not contain a token. It creates a fresh RSA-3072 keypair on the runner and publishes only the public key plus a random run ID to issue #139. It accepts exactly one ciphertext authored by `ORESoftware`, bound to that run ID and the exact confirmation `create:25-test-orgs:4-deep-test-repos-each:100`, using RSA-OAEP/SHA-256.

The decrypted credential is immediately masked, written only to a mode-0600 runner-temporary file, and removed with the private key and payload files in an `always()` cleanup step. No plaintext credential is committed, placed in workflow configuration or command arguments, posted to GitHub or Linear, uploaded as an artifact, or included in completion evidence.

After live execution, the relay workflow and trigger must be deleted from the feature branch before the permanent audit PR is merged. The manifest stays disabled.

## Local validation evidence before activation

- exact 25-organization and 100-repository generation: pass;
- four representative repository verifiers: pass;
- 19 generated behavior tests: pass;
- 11 fleet/publisher contract tests: pass;
- duplicate JSON keys, unsafe organizations, live-enablement drift, owner-ID drift, mutable Actions, conflict markers, redirects, and credential echoes: rejected;
- credential-pattern and unresolved-conflict scans across staged source: clean.

## Completion evidence

Issue #139 is the bounded execution ledger. A completion comment records counts for created/existing repositories, PRs created/merged/open, default-branch verification, and failures, followed by one state line per repository. This document must be updated with the exact workflow run and outcome after the one-time relay is retired.
