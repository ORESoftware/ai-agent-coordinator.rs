# Generic nightly artifact-recovery hardening — 2026-08-10

Tracking: **DEN-2797**. Prompt-intake foundation: **DEN-834**. Source-coverage contract: **DEN-3434**. Runtime blocker: **DEN-3474**.

## Scope and boundary

This document governs the existing generic `nightly-artifact-recovery.yml` workflow. It is adjacent to, but distinct from, the user-facing **00:30 America/Lima recent-chat introspection/reflection task**. A dedicated recent-chat workflow and watchdog are layered separately so the two schedules, run keys, reports, and failure states cannot silently substitute for one another.

The repository workflow is a durable execution-and-evidence control. It does not claim to edit or inspect the native ChatGPT scheduled-task record; the ChatGPT Scheduled page remains a separate control plane.

## Incident finding

The GitHub Actions schedule run created at `2026-08-10T08:24:56Z` was reported as successful although only deterministic validation ran and the live `enqueue` job was skipped. The previous minute-matching helper also accepted only the nominal minute or one exact recovery minute, so delayed GitHub dispatch could become a successful `not_due` no-op.

A green validation job therefore did not prove that a coordinator job was accepted, claimed, completed, or evidenced.

## Hardened generic schedule

The generic workflow uses two timezone-aware daily invocations in `America/Chicago`:

- primary: **02:17**;
- recovery invocation: **03:47**.

Both invocations derive the same daily idempotency key. A run delivered up to 240 minutes after 02:17 still targets the same logical job; after 60 minutes it is marked recovered. A schedule event outside that bounded window fails instead of becoming a silent no-op.

Every schedule event enters `enqueue-and-verify`. `ARTIFACT_RECOVERY_ENQUEUE_ENABLED` must be exactly `true`; a missing endpoint, token, worker, source receipt, or terminal result produces a red run and a bounded failure receipt.

## Rolling source contract

Every live run requests a 96-hour rolling window with six hours of overlap. Unresolved items from older runs remain eligible until they have a durable disposition.

Every configured source must emit one fresh, fully paginated `artifact_recovery_source_coverage.v1` receipt with a durable high-water mark. A run cannot claim complete when any required source is absent, unauthorized, stale, truncated, partially paged, or internally inconsistent. Raw prompts, credentials, participant data, and hidden reasoning are excluded from published receipts.

## Completion contract

HTTP 202 or a queued record is only an intermediate state. The workflow polls `GET /v1/jobs/:id` and passes only when the coordinator job is terminal `succeeded` and returns `artifact_recovery_completion.v1` with:

- source coverage status `complete`;
- every actionable item in one allowed durable disposition: `complete`, `already_landed`, `in_review`, `blocked_with_owner`, or `deferred_with_owner`;
- zero unclassified, unowned, or missing-evidence items;
- digest-bound run-ledger, source-coverage, reconciliation-report, and manifest artifacts;
- resolvable current remote evidence for every item called complete;
- owner, blocker, and next action for blocked or deferred work.

“Reconciliation complete” means every item is classified and owned. “All work complete” is stricter: only `complete` and `already_landed` are finished. Reports must preserve that distinction.

A redacted `artifact_recovery_schedule_receipt.v1` is uploaded even when activation or verification fails. A separate always-run report job writes `overnight_run_ledger.v1` plus a human-readable report and then fails the workflow when validation or execution did not succeed.

## Failure semantics

| Condition | Required result |
|---|---|
| schedule delayed but within 240 minutes | recover the same daily run key |
| primary and recovery invocation both fire | return the same logical job through idempotency |
| activation variable, endpoint, or token absent | fail red with bounded receipt |
| worker cannot claim the job | fail red after terminal timeout |
| any required source is stale, partial, or unauthorized | fail red; never claim complete coverage |
| an actionable item is unclassified or unowned | fail red |
| an item is blocked, deferred, or in review | report as unfinished with owner and next action |
| a mutation is retried | reuse its operation key; never duplicate work |
| durable terminal evidence is absent | fail the report gate |

## Required protected configuration

Environment `nightly-artifact-recovery` must provide:

| Name | Type | Required value or property |
|---|---|---|
| `ARTIFACT_RECOVERY_ENQUEUE_ENABLED` | variable | exactly `true` |
| `AI_AGENT_COORDINATOR_URL` | variable | reachable pinned HTTPS coordinator base URL |
| `AI_AGENT_COORDINATOR_API_TOKEN` | secret | role-scoped bearer token accepted by the coordinator |
| `ARTIFACT_RECOVERY_REPORT_TO` | protected variable or secret | optional recipient; when configured, delivery is mandatory |

The coordinator deployment must also have an authorized worker that claims `task_type=artifact_recovery`, reads every configured source, persists durable cursors, and emits the required completion receipt. Repository search on 2026-08-10 found the enqueue/ledger APIs but no discoverable production worker in `ORESoftware/ai-agent-coordinator.rs` or `ORESoftware/k8s-cluster`. DEN-3474 owns that blocker.

## Certification sequence

1. Merge and deploy DEN-3434 source-coverage validation.
2. Merge this fail-closed schedule, terminal verifier, and always-run report.
3. Deploy the DEN-3474 `artifact_recovery` worker and live source adapters.
4. Configure the protected environment without committing credentials.
5. Run one unique manual canary and require a terminal complete receipt.
6. Repeat the same logical run and prove zero duplicate Linear issues/comments, branches, commits, pull requests, or reports.
7. Observe both scheduled invocations and prove they resolve to the same daily idempotency key/job.
8. Verify stored ledger, terminal receipt, reconciliation report, and manifest digests agree.
9. Keep the independent overnight watchdog armed for a missing or incomplete post-schedule report.

Until all nine gates pass, the workflow is hardened against false success but is not production-certified.
