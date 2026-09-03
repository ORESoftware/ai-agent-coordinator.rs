# Continuous 50-day artifact recovery

This design turns `DEN-3179` into a durable, bounded control-plane loop without
pretending a chat session remains resident.

## Runtime shape

1. A Cloudflare Worker receives `*/3 * * * *` Cron Triggers and enqueues one
   deterministic coordinator job per three-minute UTC bucket.
2. The coordinator's idempotency key makes retries in the same bucket converge.
3. One worker-pool supervisor starts one to three lease-bound consumers. Four or
   more workers fail configuration validation.
4. A filesystem fence protects the existing shared cursor and ledger state while
   the first deployment remains on a single persistent volume.
5. Every job asks source adapters for a rolling 1,200-hour window, six hours of
   overlap, and all unresolved ledger rows outside that window.

## Current-truth and anti-reanimation rules

Before a write, adapters and workers must reconcile current Linear issues,
GitHub issues, commits, branches, pull requests, and existing recovery receipts.
Archived, cancelled, duplicate, merged/superseded, explicitly outmoded, or
otherwise closed work is not recreated. New work uses feature branches and draft
pull requests; merge and deployment remain separate reviewed gates.

The policy authority is `ORESoftware/my-ai/AGENTS.md`. Credentials pasted into
chat are never a runtime fallback and are never printed, stored, revoked, or
rotated by this loop.

## Activation gates

Source code and a pull request are not runtime evidence. Production is enabled
only after all of the following are true:

- `AI_AGENT_COORDINATOR_API_TOKEN` is installed as a Cloudflare secret;
- `COORDINATOR_URL` points to the authenticated coordinator over HTTPS;
- the protected ChatGPT/Codex, GitHub, Linear, local-repository, and file-library
  source adapters are deployed and produce fresh complete receipts;
- the worker image is published and deployed with durable state;
- one unique canary and an identical rerun prove zero duplicate Linear comments,
  issues, branches, and pull requests;
- `ACTIVATION_MODE` changes from `disabled` to `enabled` in a reviewed deployment.

Until these gates pass, the scheduler deliberately performs no write and the
system must be reported as **not running**.
