# Nightly artifact recovery and GitHub delivery

Tracking: **DEN-2797**. Prompt-intake and landing-evidence foundation: **DEN-834**. Repository-creation capability: **DEN-319**.

This workflow recovers tangible code and documentation work that an authorized ChatGPT or Claude session intended to land in GitHub but left in a partial state. It does not turn ordinary conversation into repositories, and it does not treat a ZIP, local commit, branch name, or chat claim as delivery.

## Durable delivery contract

A ledger entry is keyed by:

```text
<origin source>:<origin task/chat/file ID>::<lowercase owner>/<lowercase repository>
```

The key is stable across reruns. A materially changed observation increments the entry attempt counter; an identical rerun is a no-op. Each entry retains bounded public-safe metadata, a deterministic observation digest, a separate evidence digest, classification, next action, and canonical GitHub evidence links.

Only these tangible artifact classes are eligible:

- source code, tests, configuration, infrastructure, or generated repository scaffolds;
- product, architecture, operations, security, release, or implementation documentation intended for a named repository.

A source whose target artifact kind is `none`, or whose intent says no artifact was expected, is explicitly classified `excluded`. No repository or tracker item is manufactured for it.

## Partial-delivery states

The classifier detects all required failure and partial-delivery states:

| Finding | Meaning |
|---|---|
| `repository_missing` | Exact remote lookup completed and the intended repository does not exist. |
| `artifact_only` | The only durable source is a file, archive, directory, or draft rather than a Git repository. |
| `repository_has_no_remote` | A local Git history exists without a configured canonical remote. |
| `changes_uncommitted` | Intended paths remain dirty and have no commit identity. |
| `commits_unpushed` | The local full SHA is absent from verified remote branches and commit evidence. |
| `branch_not_created` | The intended branch exists neither locally nor remotely. |
| `branch_not_published` | The branch exists locally but not on the canonical remote. |
| `branch_without_pull_request` | The remote branch exists but no pull request uses it as the head. |
| `claimed_*_unverified` | A claimed repository, commit, branch, or PR cannot be resolved in current GitHub evidence. |
| `remote_evidence_incomplete` | Evidence collection did not complete; recovery fails closed. |
| `ownership_ambiguous` | Owner or repository resolution is not authoritative; recovery fails closed. |

A work item is complete only when the required repository, branch, commit, and pull-request evidence is resolvable. Default-branch deliveries may explicitly set `pull_request_required: false` when a verified merged or sealed commit is the intended end state.

## Executor split

### Connected GitHub App lane

For existing accessible repositories, the authenticated GitHub App is preferred. A trusted executor must:

1. re-read the exact repository, default branch, intended branch, matching commits, and pull requests;
2. reuse existing repositories, branches, and PRs rather than creating duplicates;
3. scan only the intended paths for credentials and private material;
4. create a feature branch when needed;
5. commit only the bounded intended scope;
6. push without force;
7. open or reuse one **draft** pull request; and
8. record the verified repository URL, remote branch, full SHA, and PR URL.

It must never write directly to the default branch, broadly stage a mixed worktree, rewrite shared history, bypass protection, or merge automatically.

### Local CLI recovery lane

The ChatGPT connector does not expose repository creation and cannot access arbitrary local artifact bytes. Those entries are emitted into `artifact_recovery_cli_queue.v1` for local Codex task:

```text
019fd526-f34d-7f72-94fa-2da6185f2d74
```

The hourly heartbeat **Recover ChatGPT work to GitHub** has authenticated local `gh` access. Each queue row carries the exact owner, repository, explicit or default visibility, artifact digest/locator, intended branch, findings, required operation order, and prohibitions. The local worker must still re-read GitHub before acting and must record immutable evidence after the push and draft PR exist.

No PAT, API token, bearer value, private key, or credential assignment is accepted in an observation, ledger, queue, report, issue, or PR. Credentials pasted into prior chats are never quoted or reused.

## Scheduling

`.github/workflows/nightly-artifact-recovery.yml` runs at **02:00 America/Chicago** using GitHub's timezone-aware schedule. The enqueue helper also accepts one recovery invocation at 03:00 local time with the same scheduled idempotency key, so a delayed dispatch can recover without creating a duplicate logical run.

The workflow:

- compiles the ledger and scheduler tools;
- runs positive and adversarial unit tests;
- validates policy, schema, and the checked-in public-safe backfill fixture;
- proves byte-stable reconciliation on an identical rerun;
- uploads bounded ledger, CLI queue, and summary evidence;
- optionally enqueues one coordinator job through the protected `nightly-artifact-recovery` environment.

The production worker processes at most 50 observations per batch, persists the returned cursor, revisits changed sources, and retries transient blockers on later runs. A batch can be incomplete without losing already reconciled ledger rows.

## Tracker reconciliation

DEN-2797 is the canonical implementation and operations issue. DEN-834 remains the completed prompt-intake foundation and is not reopened. After a repository recovery succeeds, the deterministic publisher must:

- amend the canonical Linear issue before creating any new issue;
- attach the verified PR and commit evidence;
- update the mapped Linear project documentation;
- synchronize the owning organization's GitHub Project item using the same idempotency key; and
- record a blocker rather than fabricating success when Project permission is unavailable.

GitHub issue and PR objects are the source items for Project synchronization. Project field mutation is not inferred from issue creation alone; it requires a verified Project item read after the mutation.

## Initial bounded backfill — 2026-08-07

Generator: `scripts/build_artifact_recovery_backfill.py` (renders the reviewed `artifact_recovery_observation.v1` fixture).

The first accessible-library batch contains **17** owner/repository ledger rows:

- **4 complete** by exact private default-branch evidence;
- **13 actionable**;
- **8** exact missing repositories routed to `cli_create_repository`;
- **5** existing-repository artifact recoveries routed to `cli_recover_local_artifact`;
- **0** ambiguous or evidence-incomplete entries in this bounded batch.

### Complete immutable evidence

| Repository | Verified `main` SHA |
|---|---|
| `hypesiege/hypesiege-analytics.rs` | `3eb8efba49bd4f932b7cc673c66b3788e3f458c1` |
| `hypesiege/hypesiege-publishing-worker.rs` | `0278b9cc86e7ea3b11d33dd987be6689dc06aba0` |
| `hypesiege/hypesiege-scheduler.rs` | `e8a739d9e658e9cef8f1dc938a412b923dbff57d` |
| `StreemPilot/streempilot-media-router.rs` | `a3b01146f85ee61400b72ed3f333c76b4413a4fa` |

The bounded control-plane evidence is merged in `ORESoftware/k8s-cluster#1069` as merge commit `4e9df62da54479c9f52d850c16703b5e112bb282`. Artifact `8946360080` has SHA-256 `c87ff38d687d81def5c419297dc28445d6cf659ef1d262c3c02d6b4a18ed99ec`.

### Missing repositories in the CLI queue

The exact current lookup is absent for:

- `apostille-me/apme-mcp-server.rs`
- `apostille-me/apme-e2e`
- `embedded-alerts/eal-mcp-server.rs`
- `embedded-alerts/eal-e2e`
- `evento-globolo/evgl-mcp-server.rs`
- `evento-globolo/evgl-e2e`
- `hacker-house-medellin/hhm-mcp-server.rs`
- `hacker-house-medellin/hhm-e2e`

The tested local source is `zed-fleet-reconcile.sh`, SHA-256 `70e7bcdfa3a8a3e15bcbf8bd635a240baca53c9b95a36f01f4aa312f66fd18ae`. Explicit public visibility is preserved from the corresponding product fleet; an omitted visibility would default to private.

### Existing-repository artifact recoveries

The following artifacts have a resolved existing repository but no remote evidence for the exact local delivery identity:

- four `canonical-quote-stack.zip` repository bundles for `canonical-api-server.rs`, `canonical-infra`, `canonical-lib`, and `canonical-flutter`;
- `alex-main-agent-v0.3.0-converged.zip` targeting the existing `ORESoftware/slack-ores-integrations` ownership boundary.

These are not safe blind patches. The local worker must inspect current repository history and tests, semantically combine compatible intent, create a new feature branch, and open a draft PR. It must not push the bundle's local `main` commit directly or overwrite newer remote work.

## Operator commands

Validate one observation batch:

```bash
python3 tools/artifact_recovery_ledger.py validate observation.json
```

Merge one bounded batch and emit the local CLI queue:

```bash
python3 tools/artifact_recovery_ledger.py reconcile \
  --input observation.json \
  --ledger state/artifact-recovery-ledger.json \
  --output state/artifact-recovery-ledger.json \
  --cli-queue state/artifact-recovery-cli-queue.json \
  --batch-size 50
```

Render a public-safe summary:

```bash
python3 tools/artifact_recovery_ledger.py summarize \
  --ledger state/artifact-recovery-ledger.json \
  --output state/artifact-recovery-summary.json
```

Dry-run the scheduled coordinator request:

```bash
python3 tools/enqueue_artifact_recovery.py \
  --endpoint https://coordinator.example.invalid \
  --now 2026-08-07T07:00:00Z \
  --dry-run
```
