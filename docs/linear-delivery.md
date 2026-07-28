# Protected Linear commit delivery

GitHub push intake and Linear mutation are deliberately separate phases.

1. `/webhooks/github` verifies the organization-specific GitHub HMAC, repository allowlist, fork status, force/deletion policy, and default branch.
2. Accepted pushes become durable `github_push` jobs with parsed Linear directives.
3. An authenticated operator or scheduler reviews a dry-run plan by job ID.
4. Only after protected secret injection and explicit activation may `/v1/linear/deliver-next` claim and deliver a queued push job.

The Linear API token is never accepted as a command-line option or payload field. It must be injected through the approved runtime secret manager as `LINEAR_API_TOKEN`.

## Fail-closed defaults

```text
LINEAR_DELIVERY_ENABLED=false
LINEAR_DELIVERY_DRY_RUN=true
LINEAR_API_URL=https://api.linear.app/graphql
LINEAR_API_AUTH_SCHEME=api_key
LINEAR_TEAM_KEY=DEN
```

Enable the adapter in dry-run first and configure exact project mappings:

```text
LINEAR_DELIVERY_ENABLED=true
LINEAR_DELIVERY_DRY_RUN=true
LINEAR_PROJECT_NAMES=sonus-auris=github.com/sonus-auris,daedalus-fab=github.com/daedalus-fab
```

Do not configure `LINEAR_API_TOKEN` for the initial planning pass.

## Review a queued job without consuming it

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${COORDINATOR_API_TOKEN}" \
  "https://coordinator.example/v1/linear/plan/JOB_ID"
```

Planning validates the durable job envelope, repository scope, default-branch evidence, issue identifiers, keywords, and configured organization-to-project map. It performs no Linear request, does not claim the job, and does not write the mutation ledger.

## Activate live delivery

After reviewing the plan:

1. Create a dedicated Linear OAuth app token or narrowly managed API key.
2. Inject it as `LINEAR_API_TOKEN` through the protected runtime environment.
3. Set `LINEAR_DELIVERY_DRY_RUN=false`.
4. Configure exact completed-state UUIDs for organizations allowed to use closing directives.
5. Restart the coordinator and verify readiness.

```text
LINEAR_COMPLETED_STATE_IDS=sonus-auris=STATE_UUID,daedalus-fab=STATE_UUID
```

Process one queued push job:

```bash
curl --fail-with-body --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${COORDINATOR_API_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data '{
    "worker_id": "linear-delivery-prod-1",
    "orgs": ["sonus-auris", "daedalus-fab"],
    "repositories": [
      "sonus-auris/sonus-auris-site.web",
      "daedalus-fab/daedalus-clients"
    ],
    "lease_seconds": 180
  }' \
  https://coordinator.example/v1/linear/deliver-next
```

A successful delivery completes the durable job. A retryable timeout, rate limit, or server error requeues it with a bounded delay. A permanent policy error fails the job without retry. The live endpoint refuses to claim work while dry-run is enabled, so planning cannot accidentally consume a queued event.

## Mutation policy

For each directive, the worker:

- resolves the exact issue identifier;
- verifies the configured team key and exact matching project name;
- creates an attachment using the canonical GitHub commit URL;
- adds a marked structured comment only when that marker is absent;
- applies a configured completed-state UUID only for a closing directive whose signed intake already passed default-branch policy.

Linear attachments are keyed by issue and URL, while the coordinator also records a SQLite mutation ledger keyed by organization, repository, commit, issue, keyword, and action. This makes retries and duplicate GitHub deliveries idempotent.

## Transport and failure controls

- HTTPS is required except loopback HTTP in tests.
- Redirects are refused.
- Requests and responses are bounded.
- `429` and `5xx` responses receive bounded exponential retry with jitter and `Retry-After` support.
- Per-organization request pacing prevents one organization from monopolizing delivery.
- Authorization values and response bodies are never logged.
- Stored errors are generic, bounded, and credential-free.

## Validation before activation

The implementation must pass actionlint, committed Rust formatting, Clippy with warnings denied, locked tests, documentation and release builds, RustSec audit, flags-contract tests, and the locked-down OCI runtime smoke on the exact reviewed head. Live delivery remains disabled until those checks and the dry-run plan both pass.

## Recovery and rollback

To stop mutations immediately, set either:

```text
LINEAR_DELIVERY_DRY_RUN=true
```

or:

```text
LINEAR_DELIVERY_ENABLED=false
```

Then restart the coordinator. Existing durable jobs remain available for inspection. Rotate the Linear token at the provider, replace the protected runtime value, review failed mutation-ledger entries and queued jobs, and resume with dry-run planning before re-enabling live delivery.
