# Nightly ChatGPT reconciliation hardening — 2026-08-10

Tracking: **DEN-2797**. Prompt-intake foundation: **DEN-834**. Source-coverage contract: **DEN-3434**.

## Incident finding

The scheduled workflow run created at `2026-08-10T08:24:56Z` was reported by GitHub Actions as successful, but its `enqueue` job was skipped. Only deterministic fixture validation ran. The configured helper would also have treated that delayed start as `not_due`: the intended local schedule was 02:00 America/Chicago, while the helper accepted only exactly 02:00 or exactly 03:00. A delayed green validation run therefore did not prove that any live ChatGPT source was read, any reconciliation job was enqueued, or any task was completed.

## Hardened schedule

The production schedule moves away from the top of the hour and has two daily invocations:

- primary: 02:17 America/Chicago;
- watchdog: 03:47 America/Chicago.

Both invocations use the same daily idempotency key. A run may arrive up to 240 minutes after 02:17 and still recover the same logical job; after 60 minutes it is marked as a recovered invocation. A dispatch outside that bounded window fails instead of becoming a no-op.

The scheduled job is no longer conditionally omitted. It always enters `enqueue-and-verify`; `ARTIFACT_RECOVERY_ENQUEUE_ENABLED` must be exactly `true`, and missing endpoint/token configuration is a hard failure with a bounded receipt.

## Rolling source contract

Every live run must examine a 96-hour rolling window with a six-hour overlap. The source selector includes every accessible authorized ChatGPT thread or chat created or updated in that window, while unresolved items from older runs remain eligible until they have a durable disposition.

No run can claim complete when a configured source is absent, unauthorized, stale, partially paged, or truncated. Every configured source must emit one fresh `artifact_recovery_source_coverage.v1` receipt. Raw prompts, hidden reasoning, participant data, and credentials are excluded from published receipts.

## Completion contract

HTTP 202 or a queued coordinator record is not success. The workflow polls `GET /v1/jobs/:id` and passes only when the job is terminal `succeeded` and returns `artifact_recovery_completion.v1` with all of the following:

- source coverage status `complete`;
- every actionable item in one allowed disposition: `complete`, `already_landed`, `in_review`, `blocked_with_owner`, or `deferred_with_owner`;
- zero unclassified, unowned, or missing-evidence items;
- a digest-bound run ledger, source-coverage artifact, reconciliation report, and artifact manifest;
- resolvable remote evidence for work called complete;
- owner, blocker, and next action for blocked or deferred work.

A redacted `artifact_recovery_schedule_receipt.v1` is uploaded even when activation or verification fails. It contains no bearer token, prompt body, or raw provider error.

## Required production activation

The protected `nightly-artifact-recovery` environment must provide:

| Name | Type | Required value or property |
|---|---|---|
| `ARTIFACT_RECOVERY_ENQUEUE_ENABLED` | variable | exactly `true` |
| `AI_AGENT_COORDINATOR_URL` | variable | reachable HTTPS coordinator base URL |
| `AI_AGENT_COORDINATOR_API_TOKEN` | secret | bearer token accepted by the coordinator |
| `ARTIFACT_RECOVERY_REPORT_TO` | protected variable/secret | optional recipient; when configured, delivery becomes required by the worker contract |

The coordinator deployment must have at least one authorized worker that claims `task_type=artifact_recovery`, reads every configured source, persists durable cursors, and completes the job with the required receipt. Repository search on 2026-08-10 found the enqueue and ledger code but no discoverable worker implementation in `ORESoftware/ai-agent-coordinator.rs` or `ORESoftware/k8s-cluster`; activation must therefore remain red until that worker and the live ChatGPT source adapter are deployed and certified.

## Certification sequence

1. Merge and deploy DEN-3434 source-coverage validation.
2. Deploy an `artifact_recovery` worker with live ChatGPT, GitHub, Linear, and local-repository adapters.
3. Configure the protected environment without committing credentials.
4. Run `workflow_dispatch` in `enqueue` mode with a unique manual ID.
5. Require one terminal receipt showing complete source coverage and zero untracked items.
6. Rerun with the same logical inputs and verify no duplicate Linear issue, branch, commit, pull request, or report delivery.
7. Observe both daily schedule events and verify the watchdog reuses the same job ID/idempotency key.

Until all seven gates pass, the system is hardened against false success but is not certified as a functioning nightly reconciliation service.
