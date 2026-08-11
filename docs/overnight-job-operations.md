# Overnight ChatGPT reconciliation operations

Tracking: **DEN-2797**. Live worker/source-adapter blocker: **DEN-3474**.

## Canonical job

The user-facing task is **Recent-96-hours ChatGPT introspection and reconciliation**.

- Primary schedule: `00:30 America/Lima`.
- Recovery invocation: `02:07 America/Lima`.
- Independent evidence watchdog: `08:30 America/Lima`.
- Source window: the prior 96 hours, with six hours of overlap.
- Backlog rule: unresolved actionable items remain in scope even after they age outside the rolling window.
- Legacy workflow filename: `last-30-hours-introspection.yml`, retained only so existing references remain resolvable.

This task is separate from the generic `nightly-artifact-recovery.yml` schedule. The two workflows use distinct idempotency-key namespaces and distinct report artifacts.

## What “all chats” means

The job must enumerate every **accessible, authorized** ChatGPT thread or chat created or updated since the cutoff, follow all pages, and emit a fresh source-coverage receipt with a durable high-water mark. ChatGPT memory or “reference chat history” is not an enumeration API and is not accepted as proof that all threads were reviewed.

Each configured corroborating source—GitHub, Linear, local repository state, file library, and optional Claude exports—must also produce an explicit coverage result. A missing, unauthorized, stale, truncated, or partially paged required source makes the run fail.

Raw prompt bodies, participant data, credentials, and hidden reasoning must not appear in public workflow artifacts.

## Reconciliation algorithm

For every actionable request:

1. Calculate a stable source fingerprint and preserve a resolvable source locator.
2. Resolve the canonical project, repository, Linear issue, existing branch, and pull request before creating anything.
3. Prefer updating existing work over creating duplicate work.
4. Execute only bounded, authorized changes; use a feature branch and draft pull request, never a direct default-branch write or force push.
5. Verify current remote repository, branch, commit, checks, pull request, and Linear evidence before calling work complete.
6. Record exactly one durable disposition:
   - `complete`;
   - `already_landed`;
   - `in_review`;
   - `blocked_with_owner`;
   - `deferred_with_owner`.
7. For blocked or deferred work, record the owner, blocker or decision, next action, and review point.
8. Reuse the same operation key on every retry so an identical rerun creates no duplicate issue, comment, branch, commit, pull request, or email.

“Reconciliation complete” means every actionable item is classified and owned. “All work complete” is stricter: only `complete` and `already_landed` count as finished. Reports must show both states. The dedicated recent-chat workflow exits non-zero when `all_work_complete` is false, after first preserving the terminal receipt, ledger, report, and unfinished-item counts.

## Success evidence

A run is successful only when all of the following exist and agree:

- the schedule event or an in-window recovery event;
- a non-skipped `enqueue-and-verify` job;
- terminal coordinator status `succeeded`;
- `artifact_recovery_completion.v1` with complete source coverage;
- zero unclassified, unowned, or missing-evidence items;
- `all_work_complete=true` and zero in-review, blocked, or deferred items;
- a terminal receipt with a self-consistent SHA-256 digest;
- `overnight_run_ledger.v1` bound to the same run ID, attempt, logical date, receipt digest, and completion summary;
- a human-readable reconciliation report recording success;
- an exact artifact manifest naming both the terminal and report artifacts;
- a delivery receipt when a report recipient is configured.

HTTP 202, `queued`, a successful fixture-validation job, artifact names without content validation, a fully classified but unfinished ledger, or an assistant statement is not completion evidence.

## Failure and recovery behavior

- GitHub schedule delivery may be late. Both the primary and recovery invocation target the same daily key: `recent-chat-reconciliation:scheduled:YYYY-MM-DD`.
- Delivery up to 240 minutes after 00:30 is accepted and marked recovered after 60 minutes.
- The logical run settles at 05:45 America/Lima; the independent watchdog runs at 08:30.
- Missing activation variables, endpoint, token, worker, source coverage, terminal receipt, report, or completed work fails red.
- In-review, blocked, or deferred items remain visible in the durable ledger and cause the dedicated workflow and the independent watchdog to fail until they are completed or already landed.
- The live step uses `continue-on-error` only long enough to upload bounded failure evidence; a final gate restores the failure conclusion.
- At 08:30, the watchdog requires a successful schedule-event run, a successful executor, and exact terminal/report artifacts. It downloads both artifacts and validates the receipt digest, logical run key, source coverage, disposition totals, zero unfinished work, ledger identity, receipt binding, completion-summary equality, artifact manifest, and human-readable success report.
- A green run with only validation, missing evidence, tampered evidence, mismatched run identity, or unfinished work is rejected.
- Reports and watchdog evidence are retained for 90 days.

## Protected configuration

Environment `nightly-artifact-recovery` must contain:

| Name | Type | Requirement |
|---|---|---|
| `ARTIFACT_RECOVERY_ENQUEUE_ENABLED` | variable | exactly `true` |
| `AI_AGENT_COORDINATOR_URL` | variable | pinned reachable HTTPS coordinator URL |
| `AI_AGENT_COORDINATOR_API_TOKEN` | secret | least-privilege coordinator bearer token |
| `ARTIFACT_RECOVERY_REPORT_TO` | protected value | optional; delivery is mandatory when configured |

No token from a chat transcript may be reused or committed.

## Native ChatGPT Scheduled Task

The native ChatGPT task is a separate control plane. Open ChatGPT’s **Scheduled** page, locate the prior “Last-30-hours ChatGPT introspection and reconciliation” task, and update it to:

- Name: `Recent-96-hours ChatGPT introspection and reconciliation`.
- Schedule: every day at `00:30 America/Lima`.
- Notifications: enabled.
- Prompt:

> Review every accessible authorized ChatGPT thread or chat created or updated during the prior 96 hours, plus every unresolved actionable item from older reconciliation runs. Do not rely on memory alone: enumerate the available source records and report coverage, pagination, cutoff, and high-water marks. For every actionable request, find and update existing Linear issues, GitHub branches, commits, and pull requests before creating new ones. Execute safe bounded work where authorized; otherwise record an owner, blocker, next action, and review point. Never use credentials copied from chat, force-push, bypass branch protection, write directly to a default branch, auto-merge, or claim completion without current remote evidence. Produce a durable ledger and artifact manifest; distinguish reconciliation-complete from all-work-complete; report completed, already-landed, in-review, blocked, deferred, unclassified, unowned, and missing-evidence counts. Fail visibly when any required source, worker, receipt, report, or actionable task remains incomplete. Send the configured report after preserving the ledger.

After editing, verify the task remains enabled and inspect its next-run time in the Scheduled page. The repository workflows do not prove that the native task itself is enabled.

## Production certification

Production is certified only after:

1. PR 146’s source-coverage contract is merged and deployed.
2. PR 149’s generic fail-closed terminal verifier and report are merged.
3. The dedicated recent-chat workflow and content-validating watchdog are merged.
4. DEN-3474 deploys an authorized `artifact_recovery` worker and live ChatGPT source adapter.
5. The protected environment is configured without committed secrets.
6. A manual canary returns a terminal receipt with real complete source coverage, `all_work_complete=true`, and zero unfinished items.
7. An identical rerun proves zero duplicate mutations.
8. The 00:30 and 02:07 invocations resolve to the same daily job.
9. The 08:30 watchdog downloads both artifacts and validates their contents, receipt digest, ledger binding, and zero unfinished work.
10. The native ChatGPT Scheduled page shows the updated task enabled with the correct next run.

Until all ten gates pass, the design is hardened against false success but is not production-certified.
