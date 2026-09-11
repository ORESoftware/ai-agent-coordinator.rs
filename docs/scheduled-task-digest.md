# Scheduled-task 24-hour digest

This workflow sends **one consolidated email** at 07:00 Central each day. The
recipient is bound in `config/scheduled-task-digest.json` to
`alexander.d.mills@gmail.com`.

## What it reports

The collector inventories every repository visible to
`SCHEDULE_DIGEST_GH_TOKEN`, then:

1. reads each GitHub Actions workflow definition and extracts `schedule.cron`;
2. calculates which occurrences were due during the prior 24 hours;
3. loads the corresponding `event=schedule` runs and their job evidence;
4. distinguishes certified workload success from failures, skipped work,
   disabled workflows, missed runs, and false greens;
5. adds the explicitly registered non-Actions lanes in
   `config/scheduled-task-digest.json`; and
6. renders one plain-text and HTML message plus a complete JSON audit artifact.

A run is **false green** when GitHub reports success but the substantive job
(for example `enqueue`, `maintain`, `recovery`, `deliver`, or `audit`) was
skipped while only validation/reporting jobs ran. A success with no job
evidence or only validation jobs is labeled **unverified green**, not certified
success.

The message is bounded for readability. The Actions artifact retains the full
record, coverage errors, job summaries, digest SHA-256, and delivery receipt for
90 days.

## Schedule, DST, and delayed dispatch

GitHub Actions cron is UTC-only. The workflow invokes at both `12:07Z` and
`13:07Z`; a timezone gate accepts only the invocation that falls in the 07:00
hour in `America/Chicago`. This preserves 07:00 Central across daylight and
standard time. The alternate invocation exits through the timezone guard as
`NOT DUE`, not as a failure or false green. The digest excludes its own currently
executing workflow from the evidence scan to avoid a self-referential `RUNNING`
warning.

GitHub may dispatch a scheduled event late. A scheduled event that arrives
after the intended 07:07 local time may recover through the bounded
`--same-day-catchup` path, but only while the local calendar date still matches
the logical delivery date. A delayed event never rolls into a later local date,
and a manual `--force` remains a separate operator action.

A terminal `schedule-outcome` job verifies the timezone decision and the
substantive digest result. When the digest is due, skipped or failed delivery
makes the workflow fail. This prevents a validation-only run from being reported
as a successful scheduled delivery.

The logical delivery key is:

```text
scheduled-task-digest:YYYY-MM-DD
```

Before sending, the collector checks for an unexpired artifact named
`scheduled-task-digest-receipt-YYYY-MM-DD`. This prevents ordinary retries, the
second DST cron alternative, and delayed same-day recovery from sending a second
message. Manual dry runs use a different artifact name.

## Required GitHub configuration

Create the `scheduled-task-digest` environment in
`ORESoftware/ai-agent-coordinator.rs` and configure:

### Repository discovery

`SCHEDULE_DIGEST_GH_TOKEN` is a GitHub Actions secret. Use a read-only token
that can see every intended organization and repository, including private
repositories, with at least:

- repository metadata: read;
- repository contents: read; and
- Actions workflows/runs/jobs/artifacts: read.

When this secret is absent, the workflow falls back to its repository-scoped
`GITHUB_TOKEN`. The email will then mark coverage as partial rather than
claiming an all-organization audit.

### Email delivery

Configure exactly one provider.

**SendGrid**

- secret: `SCHEDULE_DIGEST_SENDGRID_API_KEY` (the common
  `SENDGRID_API_KEY` name is also accepted);
- environment variable: `SCHEDULE_DIGEST_FROM_EMAIL`, set to a verified sender.

**Gmail SMTP**

- secret: `GMAIL_SMTP_USERNAME`;
- secret: `GMAIL_SMTP_APP_PASSWORD`.

The Gmail username is used as the sender. The password must be an app password,
not the account password.

**Generic SMTP**

- environment variable: `SCHEDULE_DIGEST_SMTP_HOST`;
- environment variable: `SCHEDULE_DIGEST_SMTP_PORT` (`465` for implicit TLS or
  another port for STARTTLS);
- secret: `SCHEDULE_DIGEST_SMTP_USERNAME`;
- secret: `SCHEDULE_DIGEST_SMTP_PASSWORD`;
- environment variable: `SCHEDULE_DIGEST_FROM_EMAIL` when the username is not
  the desired sender.

A scheduled run fails closed when no provider is configured or when the
provider does not accept the message. A failed delivery is uploaded under a
distinct failure-artifact name. Only an accepted delivery receives the daily
receipt artifact name, so a failed attempt cannot poison retry deduplication.

## Explicit non-Actions inventory

The configuration currently registers these lanes so they cannot disappear
from the daily report merely because GitHub Actions has no run:

- Google Apps Script cron ignition;
- GitHub permission check;
- recent-chat reconciliation;
- global PR governance and Linear reconciliation;
- fleet interdependency rebuild;
- Messaging Intel contact discovery; and
- Benefactor outreach.

Pull-request and repository-file probes update source/deployment state
automatically where possible. Lanes with no connected runtime API are reported
as `NO RUNTIME EVIDENCE` rather than carrying old observations forward as if
they were current.

## Manual verification

Render without sending:

```bash
python3 tools/scheduled_task_digest.py decision \
  --now 2026-08-12T12:07:00Z

python3 tools/scheduled_task_digest.py decision \
  --now 2026-09-02T17:06:00Z \
  --same-day-catchup

python3 tools/scheduled_task_digest.py run \
  --config config/scheduled-task-digest.json \
  --now 2026-08-12T12:07:00Z \
  --ignore-schedule-gate \
  --delivery-mode stdout \
  --output-dir /tmp/scheduled-task-digest
```

Run the focused test suite:

```bash
python3 -m unittest discover \
  -s tests \
  -p 'test_scheduled_task_digest*.py' \
  -v
```

A manual `workflow_dispatch` defaults to `dry-run`. Selecting `send` uses the
same fail-closed provider and daily-receipt rules as the scheduled invocation.