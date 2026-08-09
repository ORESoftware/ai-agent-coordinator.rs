# Artifact recovery reconciliation — Wave 6

**Window:** June 29, 2026 through August 8, 2026  
**Linear umbrella:** `DEN-2797`  
**Excluded target:** `dancing-dragons`

## Result

This wave reconciles the remaining recoverable ChatGPT/Library artifacts against current GitHub state. It does not claim access to deleted chats or private model reasoning. It uses the artifact index, Waves 1–5, and current GitHub/Linear reads as the durable evidence surface.

No force push, rebase-based branch rewrite, automatic merge, repository deletion, deployment, Cloudflare/R2 mutation, or credential commit was performed.

## Newly preserved artifacts

| Path | SHA-256 | Bytes | Disposition |
|---|---|---:|---|
| `.artifacts/chatgpt-work-recovery-wave6/zed-fleet-reconcile.original.sh` | `70e7bcdfa3a8a3e15bcbf8bd635a240baca53c9b95a36f01f4aa312f66fd18ae` | 56173 | archived-exactly; do-not-execute |
| `scripts/zed-fleet-reconcile-no-force.payload.sh` | `1f0415e2452b6b7fc2c3a6e9e2b28e3934327e13c2460905f411307274fe4e0e` | 56024 | reviewable execution candidate; dry-run by default |
| `.artifacts/chatgpt-work-recovery-wave6/zed_pkg_dependency_graph_audit_status.md` | `504cc76ff0e529d78f149601d3212d44379de0c59b412850b7f8ba9073d084f9` | 1425 | published as audit evidence; no implementation patch existed |
| `.artifacts/chatgpt-work-recovery-wave6/apply-pr659-product-only.sh` | `b05944e04ff8d2a8c59d03fbd4f3692ce5e9e3f084f7fa0705ea4a963917b667` | 21643 | historical semantic-transplant helper; partially superseded by merged PRs #789 and #823 |
| `.artifacts/chatgpt-work-recovery-wave6/pr711_product_only_repair.py` | `59d39132d1eebb34b7e766c8b7b68614f3af2ea23bbf9930191c37e4d03c990d` | 11482 | historical repair helper; stale PR #711 closed, permanent pieces decomposed |
| `.artifacts/chatgpt-work-recovery-wave6/four-org-next-steps-2026-08-08.md` | `cdcf466a00360a412ef3b37b7f2127bd9cd96b2623e9ca6c0fa00294b4ec0b85` | 7808 | published as current-state handoff; four canonical E2E repositories remain missing |

The five historical files are stored byte-for-byte in deterministic, ordered `data/wave6-recovered-artifacts.part*.b64` carrier chunks. The no-force derivative is likewise stored in deterministic `data/zed-fleet-reconcile-no-force.part*.b64` chunks, with a small digest-pinned wrapper at `scripts/zed-fleet-reconcile-no-force.sh`. The validator reconstructs both payloads, verifies gzip/tar member paths, individual byte lengths and SHA-256 values, and rejects credentials or path traversal.


### Reconciler safety repair

The recovered `zed-fleet-reconcile.sh` is preserved byte-for-byte as **non-executable evidence** because its existing-branch normalization path used `git rebase` and `git push --force-with-lease`, and it exposed automatic merge/archive modes.

`scripts/zed-fleet-reconcile-no-force.payload.sh` is the reconstructed semantic derivative; `scripts/zed-fleet-reconcile-no-force.sh` verifies and executes it. The payload:

- audits by default;
- refuses `--merge-ready` and `--archive-superseded`;
- never runs `git rebase`;
- never force-pushes;
- merges the current default branch into an existing recovery branch;
- aborts the merge when Git reports a conflict and requires a conceptual resolution in a fresh branch;
- uses a normal upstream push and opens/reuses draft PRs.

Its built-in offline self-test passed before publication.

## Current GitHub evidence

- `ORESoftware/ai-agent-coordinator.rs#133` — review-only recovery archive
- `ORESoftware/ai-agent-coordinator.rs#134` — review-only Wave 4 ledger
- `ORESoftware/ai-agent-coordinator.rs#136` — review-only Wave 5 ledger
- `ORESoftware/k8s-cluster#1215` — durable-worker patch published; exact-base materializer green; permanent PR workflows still gate merge
- `ORESoftware/k8s-cluster#789` — Messaging Intel fixed profiles and exact repository policy landed
- `ORESoftware/k8s-cluster#823` — Messaging Intel reserved workflow/planner contract landed
- `meta-agents-demo/metacog#1` — previously unpushed E2E/CI patch landed
- `ORESoftware/project-registry#33` — minor-only nightly dependency policy represented on GitHub
- `ORESoftware/chat.vibe#7` — code published; hosted job never started due billing/spending restriction

The durable-worker patch is not lost: `ORESoftware/k8s-cluster#1215` publishes the exact recovered four-file change and has a successful one-use exact-base native certification. It remains a draft because the permanent PR workflow is still the merge authority.

The historical Messaging Intel helper is not a branch to replay. Its permanent profile/policy semantics landed in #789 and its strict workflow/planner contract landed in #823. The remaining actual-binary dispatch/retry/zero-submission evidence belongs to current `dev` under `DEN-977`.

The earlier Metacog E2E/CI patch also landed in `meta-agents-demo/metacog#1`. The project-registry minor-only policy is represented in `ORESoftware/project-registry#33` and follow-up ledgers.

## Missing repositories

- `apostille-me/apme-e2e` — verified missing; pair with `apostille-me/apme-interfaces`
- `embedded-alerts/eal-e2e` — verified missing; pair with `embedded-alerts/eal-interfaces`
- `evento-globolo/evgl-e2e` — verified missing; pair with `evento-globolo/evgl-interfaces`
- `hacker-house-medellin/hhm-e2e` — verified missing; pair with `hacker-house-medellin/hhm-interfaces`

All four canonical interface repositories exist. The corresponding E2E repositories do not. The connected GitHub tool surface has no repository-creation operation, `gh` is absent, and the container cannot resolve GitHub, so these four repository creations could not be completed in this run. The supplied PAT was not written to disk, Git configuration, source, Actions, GitHub comments, or Linear.

## Zed dependency-inventory audit

The exact audit report is now durable. It confirms four implementation gaps but produced no patch, base SHA, head SHA, or local conformance run:

1. GitHub Enterprise API prefixes such as `/api/v3` can be duplicated while following absolute pagination links.
2. Zed build-dependencies are omitted from source-inventory edges.
3. absent `sha256` values can be compared with strings while sorting contradictions, raising `TypeError` instead of deterministic diagnostics.
4. Git tree mode/type combinations need explicit fail-closed validation for blobs, executable blobs, symlinks, trees, and gitlinks.

Wave 6 deliberately publishes the evidence and creates no invented implementation claim.

## Conflict policy

When a recovery branch already exists, the no-force derivative first fetches current remote history and merges the default branch into the recovery branch. A conflict stops execution. The operator must review both meanings, preserve newer behavior and the intended recovered invariant, validate the combined tree, and publish a new normal commit. Choosing one side wholesale or rewriting the remote branch is prohibited.

## Remaining gates

- `DEN-319` — restore a repository-creation-capable, least-privilege GitHub App or connected action and create the four missing E2E repositories.
- `DEN-2957` — implement the four dependency-inventory hardening findings on current main with tests.
- `DEN-977` — implement actual-binary Messaging Intel evidence on current `dev`.
- `DEN-2253` — keep durable-worker #1215 draft until permanent exact-head workflows pass.
- `DEN-2797` — review recovery ledgers independently from product acceptance.

## Credential boundary

No credential value appears in this wave. Credentials pasted into chat must be treated as exposed and rotated. Future automation should use a narrowly scoped GitHub App installation credential and the connected Linear integration rather than reusable chat-pasted tokens.
