# Nightly ChatGPT reconciliation hardening — 2026-08-10

Tracking: **DEN-2797**. Prompt-intake foundation: **DEN-834**. Source-coverage contract: **DEN-3434**. Runtime blocker: **DEN-3474**.

## User-facing intent

The retained August 10 report identifies the user-facing job as **Last-30-hours ChatGPT introspection and reconciliation**, scheduled for **12:30 AM**, and records no post-schedule ledger, completion time, outcome, artifact manifest, or error summary. The hardened implementation expands the rolling review to **96 hours** so it covers the requested prior three to four days while retaining a six-hour overlap between runs.

The repository workflow is the durable enforcement and evidence path. It does not claim to edit or inspect the native ChatGPT scheduled-task record; that editor/history is a separate control plane and must be reconciled independently when access exists.

## Incident finding

The GitHub Actions run created at `2026-08-10T08:24:56Z` was reported as successful, but its `enqueue` job was skipped. Only deterministic fixture validation ran. The old scheduler helper also accepted only exactly the planned minute or exactly 60 minutes late. A delayed green validation run therefore did not prove that any live ChatGPT source was read, any recovery job was enqueued, or any requested task was completed.

## Hardened schedule

The production workflow has two timezone-aware daily invocations in `America/New_York`:

- primary: **12:37 AM**;
- watchdog: **2:07 AM**.

The seven-minute offset preserves the 12:30 AM intent while avoiding the busiest scheduling boundary. Both invocations use the same daily idempotency key. A run may arrive up to 240 minutes after 12:37 AM and still recover the same logical job; after 60 minutes it is marked as a recovered invocation. A dispatch outside that bounded window fails instead of becoming a silent no-op.

The live job is no longer conditionally omitted. Every schedule event enters `enqueue-and-verify`; `ARTIFACT_RECOVERY_ENQUEUE_ENABLED` must be exactly `true`, and a missing endpoint, token, worker, source receipt, or terminal result produces a red run and a bounded failure receipt.

## Rolling source contract

Every live run examines a 96-hour rolling window with a six-hour overlap. The source selector includes every accessible authorized ChatGPT thread or chat created or updated since the cutoff. Unresolved items from older runs remain eligible until they have a durable disposition.

Configured corroborating sources include GitHub, Linear, local repository state, the file library, and optional Claude history. A source is not considered covered merely because its adapter was configured: it must emit one fresh, fully paginated `artifact_recovery_source_coverage.v1` receipt with a durable high-water mark.

A run cannot claim reconciliation complete when any required source is absent, unauthorized, stale, truncated, partially paged, or internally inconsistent. Raw prompts, hidden reasoning, participant data, and credentials are excluded from published receipts.

## Task normalization and execution

Each actionable request must have a stable fingerprint, source locator, canonical project/repository, and one owner. The worker must update existing Linear/GitHub work before creating new work, preserve exact remote-head evidence, and make every mutation idempotent.

Allowed reconciliation dispositions are:

- `complete`: implementation and current remote evidence both exist;
- `already_landed`: equivalent work predated this run and remains verifiable;
- `in_review`: a current pull request or equivalent review artifact owns the work;
- `blocked_with_owner`: execution cannot continue and the blocker, owner, and next action are recorded;
- `deferred_with_owner`: an explicit policy or priority decision records owner and next review point.

“Reconciliation complete” means every item is classified and owned. “All work complete” is stricter: only `complete` and `already_landed` count as finished. Reports must show both states and must never describe blocked, deferred, or in-review work as finished.

## Completion contract

HTTP 202 or a queued coordinator record is not success. The workflow polls `GET /v1/jobs/:id` and passes the transport/execution gate only when the job is terminal `succeeded` and returns `artifact_recovery_completion.v1` with all of the following:

- source coverage status `complete`;
- every actionable item in one allowed durable disposition;
- zero unclassified, unowned, or missing-evidence items;
- a digest-bound run ledger, source-coverage artifact, reconciliation report, and artifact manifest;
- resolvable remote evidence for work called complete;
- owner, blocker, and next action for blocked or deferred work;
- one report-delivery receipt when `ARTIFACT_RECOVERY_REPORT_TO` is configured.

The human-facing report must include separate counts for scanned prompts, normalized tasks, completed/already-landed work, in-review work, blockers, deferrals, retries, source coverage, and duplicate mutations prevented.

A redacted `artifact_recovery_schedule_receipt.v1` is uploaded even when activation or verification fails. It contains no bearer token, prompt body, email address, or raw provider error.

## Failure semantics

| Condition | Required result |
|---|---|
| schedule delayed but within 240 minutes | recover the same daily run key |
| primary and watchdog both fire | return the same job through idempotency |
| activation variable, endpoint, or token absent | fail red with bounded receipt |
| worker cannot claim the job | fail red after terminal timeout |
| any required source is stale, partial, or unauthorized | fail red; never claim full coverage |
| an actionable item is unclassified or unowned | fail red |
| an item is blocked/deferred/in review | report as unfinished with owner and next action |
| a mutation is retried | reuse the same operation key; never duplicate work |
| report delivery is configured but absent | fail the delivery gate and preserve the run report |

## Required production activation

The protected `nightly-artifact-recovery` environment must provide:

| Name | Type | Required value or property |
|---|---|---|
| `ARTIFACT_RECOVERY_ENQUEUE_ENABLED` | variable | exactly `true` |
| `AI_AGENT_COORDINATOR_URL` | variable | reachable pinned HTTPS coordinator base URL |
| `AI_AGENT_COORDINATOR_API_TOKEN` | secret | role-scoped bearer token accepted by the coordinator |
| `ARTIFACT_RECOVERY_REPORT_TO` | protected variable/secret | optional recipient; when configured, delivery is mandatory |

The coordinator deployment must have at least one authorized worker that claims `task_type=artifact_recovery`, reads every configured source, persists durable cursors, and completes the job with the required receipt. Repository search on 2026-08-10 found the enqueue and ledger code but no discoverable worker implementation in `ORESoftware/ai-agent-coordinator.rs` or `ORESoftware/k8s-cluster`. Activation must therefore remain red until DEN-3474 deploys and certifies that worker and a live ChatGPT source adapter.

## Certification sequence

1. Merge and deploy DEN-3434 source-coverage validation.
2. Merge the fail-closed scheduler and terminal verifier from PR 149.
3. Deploy the DEN-3474 `artifact_recovery` worker with live ChatGPT, GitHub, Linear, local-repository, and report-delivery adapters.
4. Configure the protected environment without committing credentials.
5. Run `workflow_dispatch` in `enqueue` mode with a unique manual ID.
6. Require one terminal receipt showing complete source coverage, zero untracked items, and an honest unfinished-work count.
7. Repeat with identical logical inputs and verify zero duplicate Linear issues/comments, branches, commits, pull requests, or emails.
8. Observe both 12:37 AM and 2:07 AM schedule events and verify the watchdog reuses the same daily job/idempotency key.
9. Verify the email report and stored manifest resolve to the same run/report digests.
10. Leave a missed-run alert armed for the absence of a terminal receipt after the watchdog window.

Until all ten gates pass, the system is hardened against false success but is not certified as a functioning nightly reconciliation service.
