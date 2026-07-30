# Telemetry ticket automation

The coordinator can receive Alertmanager webhook groups, synthesize a redacted
incident report with the shared multi-model bridge, create or update matching
GitHub and Linear issues, and queue overnight repository remediation.

The integration is fail-closed and disabled unless
`TELEMETRY_AUTOMATION_ENABLED=true`.

## Signal flow

1. Prometheus and the Loki ruler evaluate sustained alert conditions.
2. Alertmanager groups and deduplicates alerts by repository, namespace, and
   alert name.
3. `POST /webhooks/alertmanager` verifies a dedicated bearer and creates one
   durable `telemetry_incident` job per fingerprint per UTC day.
4. The delivery CronJob calls `POST /v1/telemetry/process-next`. The coordinator
   starts an `ai-agent-bridge` consensus workflow with independent Gemini,
   Claude, and ChatGPT workers plus a distinct ChatGPT reviewer instance.
5. The reviewer output is wrapped in a deterministic evidence and safety
   template. GitHub and Linear issues are upserted with the same stable
   fingerprint.
6. At 04:00 `America/New_York`, the remediation CronJob dispatches three ordered
   tasks to the repository worker: Gemini investigation, Claude review, then
   Codex implementation.
7. The implementation prompt requires a feature branch, repository tests,
   GitHub Actions coverage, an intentional commit, a push, and a draft pull
   request. It forbids direct default-branch work and merging.

Raw log lines, request bodies, credentials, customer data, pod names, trace IDs,
and other high-cardinality fields are not copied to models or tickets. The
allowlisted evidence is limited to repository ownership, alert identity,
severity, cluster/namespace/deployment/service/job/environment labels, bounded
summaries, and dashboard/runbook links.

## Repository and Linear routing

Every alert should carry `repository=owner/repo`. During migration, the
coordinator can resolve deployment, service, app, or job labels through
`TELEMETRY_REPOSITORY_MAP`.

Linear project resolution is deterministic:

1. exact mapping in `TELEMETRY_LINEAR_REPOSITORY_PROJECT_NAMES`;
2. project named `github.com/<owner>/<repo>`;
3. organization mapping in `LINEAR_PROJECT_NAMES`;
4. project named `github.com/<owner>`;
5. `Shared Platform & Portfolio Architecture`.

The default branch comes from `TELEMETRY_BASE_BRANCH_MAP`; unmapped repositories
use `main`. The cluster superproject and repositories such as `live-mutex` that
develop from `dev` must be mapped explicitly.

## Required protected configuration

Store the following values in AWS Secrets Manager at
`dd/remote-dev/telemetry-ticket-automation`. The checked-in ExternalSecret
extracts the JSON object without committing values.

- `TELEMETRY_WEBHOOK_TOKEN`
- `TELEMETRY_GITHUB_TOKEN` — prefer a GitHub App installation token with Issues
  write and Metadata read for only managed repositories
- `LINEAR_API_TOKEN`
- `TELEMETRY_BRIDGE_BEARER`
- `TELEMETRY_WORKER_BROKER_AUTH`

The provider runner secret at `dd/remote-dev/ai-agent-runner-secrets` must define
the provider array and credential environment variables for:

- `google-gemini-3.1-pro`
- `anthropic-claude-5`
- `openai-chatgpt-5.6-sol`
- `openai-chatgpt-5.6-sol-reviewer`

The reviewer may use the same model and credential as the ChatGPT worker, but it
must have a distinct bridge agent key.

## Non-secret configuration

The deployment supports these environment variables:

```text
TELEMETRY_AUTOMATION_ENABLED=true
TELEMETRY_AUTOMATION_DRY_RUN=false
TELEMETRY_GITHUB_ISSUES_ENABLED=true
TELEMETRY_LINEAR_ISSUES_ENABLED=true
TELEMETRY_MODEL_ENRICHMENT_ENABLED=true
TELEMETRY_MODEL_AGENT_KEYS=google-gemini-3.1-pro,anthropic-claude-5,openai-chatgpt-5.6-sol
TELEMETRY_MODEL_REVIEWER_KEY=openai-chatgpt-5.6-sol-reviewer
TELEMETRY_REMEDIATION_PROVIDERS=gemini-sdk,claude-sdk,openai-codex-cli
LINEAR_TEAM_KEY=DEN
```

Start with `TELEMETRY_AUTOMATION_DRY_RUN=true`, submit a synthetic warning, and
inspect the durable job and model workflow before enabling destination writes.
Do not activate the Alertmanager receiver until both the coordinator and
provider runner are ready.

## Alert contract

Alerts eligible for ticket creation must be `warning` or `critical` and should
include:

```yaml
labels:
  severity: warning
  repository: ORESoftware/example.rs
  deployment: dd-example
annotations:
  summary: Sustained error rate in the example request boundary
  description: Error counter increased for ten minutes.
  dashboard_url: https://example.invalid/telemetry/...
  runbook_url: https://github.com/ORESoftware/example.rs/blob/main/docs/runbook.md
```

Never place a token, raw log body, request payload, email address, or customer
identifier in an alert label or annotation.

## Recovery

Set `TELEMETRY_AUTOMATION_DRY_RUN=true` to stop external mutations while keeping
the ingestion path observable. Set `TELEMETRY_AUTOMATION_ENABLED=false` to stop
all intake and processing. Alertmanager grouping and the coordinator job queue
provide independent deduplication; GitHub and Linear bodies also carry
`telemetry-fingerprint:<sha256>` markers so retries upsert rather than fan out.
