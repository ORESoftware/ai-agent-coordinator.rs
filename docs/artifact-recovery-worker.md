# Artifact-recovery worker

`tools/run_artifact_recovery_worker.py` is the queue consumer for
`task_type=artifact_recovery`. It converts the nightly enqueue contract into a
lease-bound execution with durable source, ledger, completion, and delivery
receipts.

The worker is deliberately fail closed. A job is successful only when every
required source is fresh and completely paginated, the strict observation and
source-coverage validators accept the collected data, and the durable ledger
contains zero actionable, blocked, unowned, unclassified, or missing-evidence
items. A green enqueue workflow is not treated as execution evidence.

## Runtime contract

The process claims only `artifact_recovery` jobs and heartbeats the lease at one
third of the configured lease duration. It rejects a returned job with any
other task type, a mismatched worker lease, an unknown payload schema, or a
payload missing the mandatory no-credential/no-force-push/no-default-branch
guardrails.

Each configured source is an HTTPS page adapter. Loopback HTTP is accepted for
tests only. Redirects are refused, responses are byte bounded, cursors are
shape checked and cycle detected, and complete pages cannot advertise another
cursor. The worker always requests:

- the rolling 96-hour window plus six hours of overlap;
- unresolved items outside the rolling window;
- a bounded page size;
- the prior durable high-water mark, when present.

A page adapter returns:

```json
{
  "schema_version": "artifact_recovery_source_page.v1",
  "source": "chatgpt",
  "captured_at": "2026-08-11T04:00:00Z",
  "watermark_at": "2026-08-11T03:59:00Z",
  "items": [],
  "complete": true,
  "next_cursor": null
}
```

`items` use the existing `artifact_recovery_observation.v1` item schema. Prompt
bodies, hidden reasoning, source credentials, and authorization headers are
outside that schema and must never be returned. The worker runs the repository's
strict observation and source-coverage validators before persisting any
source-derived value.

## Durable artifacts

A persistent volume is required at `ARTIFACT_RECOVERY_STATE_DIR`. Each run is
stored under a SHA-256-derived directory name and contains:

- `observation.json`
- `source-coverage.json`
- `ledger.json`
- `cli-queue.json`
- `completion.json`
- `report-delivery.json`

The shared `ledger.json` and `cursors.json` are atomically replaced and fsynced.
High-water marks advance only for sources whose pagination and freshness gates
are complete. The completion receipt binds every artifact by SHA-256 and records
all terminal gates.

When `ARTIFACT_RECOVERY_REPORT_TO` is configured, delivery is mandatory. SMTP
uses STARTTLS by default, implicit TLS when requested, or plaintext only on
loopback. Recipients and worker identities appear in receipts only as digests.

## Required environment

```text
AI_AGENT_COORDINATOR_URL
AI_AGENT_COORDINATOR_API_TOKEN
ARTIFACT_RECOVERY_SOURCE_MANIFEST
ARTIFACT_RECOVERY_STATE_DIR
```

Recommended explicit settings:

```text
ARTIFACT_RECOVERY_WORKER_ID
ARTIFACT_RECOVERY_LEASE_SECONDS=300
ARTIFACT_RECOVERY_REQUEST_TIMEOUT_SECONDS=20
ARTIFACT_RECOVERY_MAX_RESPONSE_BYTES=2097152
ARTIFACT_RECOVERY_MAX_PAGES_PER_SOURCE=200
ARTIFACT_RECOVERY_SOURCE_PAGE_SIZE=50
ARTIFACT_RECOVERY_RETRY_DELAY_SECONDS=900
ARTIFACT_RECOVERY_POLL_SECONDS=30
```

Each adapter declares a `token_env` in the protected source manifest. Those
variables must come from a secret manager or projected Kubernetes Secret, not
from Git, a chat transcript, command-line arguments, or generated receipts.

Optional report settings:

```text
ARTIFACT_RECOVERY_REPORT_TO
ARTIFACT_RECOVERY_REPORT_FROM
ARTIFACT_RECOVERY_SMTP_HOST
ARTIFACT_RECOVERY_SMTP_PORT=587
ARTIFACT_RECOVERY_SMTP_SECURITY=starttls
ARTIFACT_RECOVERY_SMTP_USERNAME
ARTIFACT_RECOVERY_SMTP_PASSWORD_ENV=ARTIFACT_RECOVERY_SMTP_PASSWORD
```

## Operations

Validate configuration without contacting the coordinator or a source:

```bash
python3 tools/run_artifact_recovery_worker.py --check-config
```

Claim at most one job:

```bash
python3 tools/run_artifact_recovery_worker.py --once
```

Run as a durable deployment:

```bash
python3 tools/run_artifact_recovery_worker.py
```

The example source manifest is intentionally non-routable. Production
activation requires protected endpoints for ChatGPT history, GitHub, Linear,
authorized local repositories, and the file library; Claude is optional.
Deployment must remain disabled until those endpoints, projected credentials,
persistent storage, and report delivery are configured and a unique manual run
plus an identical rerun both produce exact-head, duplicate-free receipts.

## Certification

The pull-request contract workflow compiles the worker, runs adversarial tests,
validates the example protected configuration, and uploads an exact-head
configuration receipt. Production certification additionally requires:

1. a live claimed job and recurring lease heartbeats;
2. fresh complete receipts from every required source;
3. one terminal `artifact_recovery_completion.v1` receipt with every gate true
   or zero;
4. a delivered report receipt when email is configured;
5. an identical rerun that creates no duplicate Linear comments, branches, or
   pull requests;
6. observation of both scheduled invocation and watchdog recovery using the
   same daily idempotency key.
