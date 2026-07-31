# Weekly Platform/SRE/CTO opportunity digest

This workflow implements the read-only recurring portion of Linear
[DEN-826](https://linear.app/denman/issue/DEN-826/job-agent-deliver-a-weekly-high-fit-platformsrecto-opportunity-digest).
It creates one durable `job_opportunity_digest` coordinator job per ISO week.

## Schedule and idempotency

GitHub Actions invokes the scheduler at both UTC offsets that can correspond to
09:17 Monday in `America/New_York`. The Python scheduler converts the current
instant with `zoneinfo` and rejects the invocation that is not in the local
09:00 hour. The idempotency key is:

```text
job-agent:platform-sre-cto:<ISO year>-W<ISO week>
```

The coordinator's existing `Idempotency-Key` handling is the final duplicate
barrier. A manual `enqueue` dispatch bypasses only the time gate; it retains the
same weekly identity and therefore cannot create a second job for the week.

## Discovery-only boundary

The enqueued contract may:

- discover current Platform, SRE, cloud-infrastructure, developer-platform, and
  CTO roles;
- normalize role metadata and explain evidence-backed fit;
- deduplicate requisitions, mirrors, agencies, and known prior outcomes;
- maintain a reviewed application queue;
- prepare a grounded follow-up draft.

It may not submit an application, send or reply to email, enter protected
fields, solve CAPTCHA, perform MFA, accept legal attestations, or fabricate
candidate facts. Browser submissions remain routed through DEN-256 and every
outbound message requires a separate explicit action. The earlier 150–350
application request is represented only as a guarded scale target: fit,
deduplication, confirmation, and verified batch health take precedence, and
this workflow never auto-submits.

## Protected configuration

The `weekly-job-digest` GitHub environment must provide:

- repository variable `AI_AGENT_COORDINATOR_URL`, using HTTPS;
- environment secret `AI_AGENT_COORDINATOR_API_TOKEN`.

The workflow has only `contents: read`, disables persisted checkout
credentials, never prints the bearer, and refuses redirects so a token cannot
be forwarded to another origin. Pull-request and push validation use a
non-routable placeholder endpoint and never read the protected secret.

## Manual operations

A `workflow_dispatch` in `dry-run` mode runs only validation. `enqueue` performs
one current-week enqueue after environment protection rules pass. The resulting
worker output should update DEN-826 with discovered, shortlisted, queued,
submitted, confirmed, replied, waiting, interview, blocked, skipped, and closed
counts; submission and messaging counts remain zero unless separately verified
through their owning workflows.
