# Weekly Platform/SRE/CTO opportunity digest

This workflow implements the read-only recurring portion of Linear
[DEN-826](https://linear.app/denman/issue/DEN-826/job-agent-deliver-a-weekly-high-fit-platformsrecto-opportunity-digest).
It creates one durable `job_opportunity_digest` coordinator job per ISO week.

## Schedule and idempotency

GitHub Actions invokes the scheduler at both UTC offsets that can correspond to
09:17 Monday in `America/New_York`. The Python scheduler converts the current
instant with `zoneinfo` and accepts only the invocation whose local time is
exactly Monday 09:17. The idempotency key is:

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

## Protected candidate profile

The repository is public, so candidate-specific facts and preferences must not
be committed to source. Target roles, skills, work-authorization facts,
location/arrangement preferences, compensation guidance, and similar reviewed
inputs are loaded at runtime from the protected environment secret
`AI_AGENT_JOB_PROFILE_JSON`.

The value must be a bounded JSON object with a non-empty `target_roles` list.
The scheduler rejects credential, SSN, payment, MFA, private-key, and similar
sensitive fields. Dry-run output replaces the entire candidate profile with a
redaction marker; it never prints the profile or bearer token.

A synthetic non-personal profile is embedded only in pull-request validation.
Production profile changes should be reviewed through the protected environment
rather than through a public source-code diff.

## Protected configuration

The `weekly-job-digest` GitHub environment must provide:

- repository variable `AI_AGENT_COORDINATOR_URL`, using HTTPS;
- environment secret `AI_AGENT_COORDINATOR_API_TOKEN`;
- environment secret `AI_AGENT_JOB_PROFILE_JSON` containing the reviewed
  candidate profile.

The workflow has only `contents: read`, disables persisted checkout
credentials, never prints the bearer or candidate profile, and refuses redirects
so a token cannot be forwarded to another origin. Pull-request and push
validation use a non-routable placeholder endpoint and synthetic profile; they
never read protected production values.

## Manual operations

A `workflow_dispatch` in `dry-run` mode runs only validation. `enqueue` performs
one current-week enqueue after environment protection rules pass. The resulting
worker output should update DEN-826 with discovered, shortlisted, queued,
submitted, confirmed, replied, waiting, interview, blocked, skipped, and closed
counts; submission and messaging counts remain zero unless separately verified
through their owning workflows.
