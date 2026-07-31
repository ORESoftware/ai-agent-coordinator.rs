# Email attention agent

The email attention agent is a read-only, scheduled workflow for finding messages that genuinely need the user's attention across connector-backed Gmail and Outlook mailboxes. It is disabled by default and fails closed when enabled without complete connector and notification configuration.

The implementation tracks [DEN-830](https://linear.app/denman/issue/DEN-830/inbox-agent-scan-connected-email-for-items-needing-attention-and).

## What the workflow does

At each configured local time, the coordinator:

1. obtains a PostgreSQL lease so only one replica owns the scheduled run;
2. retries previously persisted notification deliveries using their original idempotency keys;
3. calls each enabled mailbox adapter with `read_only: true`, its last durable cursor, and a bounded message limit;
4. classifies connector-provided metadata into `urgent` and `needs_reply_soon`;
5. persists only source cursors, opaque message identities, classification fingerprints, deadlines, run health, and pending delivery state;
6. suppresses unchanged items, while allowing material changes, urgency escalation, and configured urgent reminders to surface again;
7. sends a single bounded notification payload when there is something new to report; and
8. records source health separately so one failing mailbox does not discard successful results from the others.

A normal run with no new actionable item is silent.

## Trust boundaries

The coordinator never logs into Gmail or Microsoft accounts through browser automation. Gmail and Outlook access belongs in native connector adapters that hold provider credentials outside this repository. The coordinator calls those adapters over HTTPS, except that loopback HTTP is allowed for tests.

The scheduled scan does not send, reply, forward, archive, delete, move, label, unsubscribe, or change read state. Any mailbox mutation must be implemented as a separate, explicitly authorized user action.

Source IDs are operator-chosen aliases such as `primary-gmail` or `work-outlook`; do not put private mailbox addresses in repository files. Credential values remain environment- or secret-manager-only. Redirects are disabled for connector and notification HTTP calls.

## Connector request contract

Each configured source receives an HTTP `POST` with JSON:

```json
{
  "source_id": "primary-gmail",
  "provider": "gmail",
  "cursor": "opaque-provider-cursor-or-null",
  "max_messages": 200,
  "manual_test": false,
  "read_only": true
}
```

The adapter must enforce the `read_only` contract. It should use the native Gmail or Microsoft Graph APIs and return only the bounded metadata needed for classification:

```json
{
  "next_cursor": "opaque-next-cursor",
  "messages": [
    {
      "stable_id": "opaque-thread-or-message-id",
      "thread_id": "optional-opaque-thread-id",
      "sender": "Display Name <redacted@example.test>",
      "subject": "Please confirm the meeting",
      "received_at": "2026-07-30T13:00:00Z",
      "snippet": "Optional connector-provided bounded snippet",
      "direct_request": true,
      "user_is_next_responder": true,
      "automated": false,
      "importance": "normal",
      "categories": ["calendar"],
      "explicit_deadline": "2026-07-31T17:00:00Z",
      "material_version": "provider-etag-or-version"
    }
  ]
}
```

Supported providers are `gmail` and `outlook`. `next_cursor` is optional. When it is omitted or `null`, the coordinator preserves the previous scheduled cursor instead of resetting it.

The connector must distinguish evidence from inference:

- `explicit_deadline` may be set only when a date or deadline is actually present in the source message or authoritative thread metadata.
- `direct_request`, `user_is_next_responder`, `automated`, `importance`, and `categories` are connector classifications. The notification explains that these are connector-derived signals rather than quoting them as message text.
- Do not return full message bodies or attachments.

Responses are size-bounded while streaming and are rejected when IDs repeat, fields exceed their limits, unsupported control characters appear, or the message count exceeds the requested maximum.

## Classification and notification contract

The coordinator surfaces only two user-facing buckets:

- `urgent`: explicit deadlines due within 24 hours or overdue, security/account/billing/legal/incident categories, high-importance messages, or time-sensitive language;
- `needs_reply_soon`: direct requests, threads where the user is the next responder, future explicit deadlines, relationship follow-ups, and direct action cues.

Ordinary automated mail is suppressed unless it carries an urgent account/security/deadline/high-importance signal.

Each notification item includes the source alias, provider, a redacted stable reference, sender, subject, received time, evidence-backed reason, confidence, explicit deadline when present, and a recommended next action. Snippets, raw provider IDs, cursors, tokens, and message bodies are not included in notifications.

The notification endpoint receives an `Idempotency-Key` header and this JSON shape:

```json
{
  "schema_version": "email-attention/v1",
  "run_id": "uuid",
  "generated_at": "2026-07-30T13:05:00Z",
  "test_run": false,
  "urgent": [],
  "needs_reply_soon": [],
  "source_failure_count": 0,
  "truncated_item_count": 0
}
```

The notification adapter is responsible for the final user-visible channel, such as a ChatGPT notification bridge, push service, Slack DM, or another reviewed delivery service. It must deduplicate requests by `Idempotency-Key`.

## Durable deduplication and privacy

The agent uses namespaced tables in the coordinator SQLite database. It stores:

- per-source opaque cursors and redacted health;
- opaque source/message identities;
- SHA-256 material fingerprints;
- explicit deadline timestamps;
- last emitted fingerprint and time;
- pending notification payloads and idempotency keys; and
- aggregate run results.

It never stores snippets or message bodies as item state. A pending delivery temporarily contains the minimum user-visible sender/subject metadata required to retry the notification. Immediately after confirmed delivery, that payload is overwritten with a redacted marker while the idempotency and audit state remain.

If a message changes while an older fingerprint is still pending delivery, the old delivery completes with its original fingerprint and the changed item is emitted on a later run. This prevents silent loss and avoids merging two semantically different message states.

## Schedule

Defaults:

- timezone: `America/New_York`;
- weekdays: Monday through Friday;
- local time: 09:00;
- urgent reminder interval: 24 hours.

`America/New_York` includes deterministic US daylight-saving transitions. `UTC`, `Etc/UTC`, `Z`, and fixed offsets such as `-05:00` or `+05:30` are also accepted. A nonexistent New York local time during the spring transition is moved forward by one hour; an ambiguous fall time uses the first occurrence.

## Configuration

Non-secret settings:

| Variable | Default | Purpose |
| --- | --- | --- |
| `EMAIL_ATTENTION_ENABLED` | `false` | Enables startup validation and the scheduler. |
| `EMAIL_ATTENTION_TIMEZONE` | `America/New_York` | Schedule timezone. |
| `EMAIL_ATTENTION_WEEKDAYS` | `mon,tue,wed,thu,fri` | Comma-separated days, `daily`, or `*`. |
| `EMAIL_ATTENTION_LOCAL_HOUR` | `9` | Local hour, `0..23`. |
| `EMAIL_ATTENTION_LOCAL_MINUTE` | `0` | Local minute, `0..59`. |
| `EMAIL_ATTENTION_NOTIFICATION_URL` | unset | HTTPS notification adapter endpoint. |
| `EMAIL_ATTENTION_NOTIFICATION_TOKEN_ENV` | `EMAIL_ATTENTION_NOTIFICATION_TOKEN` | Name of the notification bearer-token environment variable. Set this to an empty string only for a deliberately unauthenticated internal test endpoint. |
| `EMAIL_ATTENTION_REQUEST_TIMEOUT_MS` | `15000` | Connector and notification timeout. |
| `EMAIL_ATTENTION_MAX_RESPONSE_BYTES` | `524288` | Maximum connector response and declared notification-response size. |
| `EMAIL_ATTENTION_MAX_MESSAGES_PER_SOURCE` | `200` | Per-source scan bound. |
| `EMAIL_ATTENTION_MAX_NOTIFICATIONS_PER_RUN` | `50` | User-visible item cap. |
| `EMAIL_ATTENTION_REMINDER_INTERVAL_HOURS` | `24` | Minimum interval for unchanged urgent reminders. |
| `EMAIL_ATTENTION_LEASE_SECONDS` | `3600` | Cross-replica scheduled-run lease. |
| `EMAIL_ATTENTION_PENDING_RETRY_LIMIT` | `20` | Pending deliveries retried per run. |
| `EMAIL_ATTENTION_USER_AGENT` | `ai-agent-coordinator-email-attention` | HTTP user agent. |

Private/runtime-only settings:

- `EMAIL_ATTENTION_SOURCES_JSON`: JSON array of source objects. Keep source aliases, endpoints, and token environment-variable names in runtime configuration rather than repository fixtures.
- `EMAIL_ATTENTION_NOTIFICATION_TOKEN`: default notification bearer token.
- each source's configured `token_env`: connector bearer token.

Example runtime source configuration, using aliases rather than addresses:

```json
[
  {
    "id": "primary-gmail",
    "provider": "gmail",
    "endpoint": "https://gmail-connector.internal/v1/read-attention-window",
    "token_env": "PRIMARY_GMAIL_CONNECTOR_TOKEN"
  },
  {
    "id": "work-outlook",
    "provider": "outlook",
    "endpoint": "https://outlook-connector.internal/v1/read-attention-window",
    "token_env": "WORK_OUTLOOK_CONNECTOR_TOKEN"
  }
]
```

## Protected API

The existing coordinator bearer-token policy protects both endpoints:

- `GET /v1/email-attention/status` reports enabled state, schedule, next run, last successful scheduled run, pending-delivery count, configured-source health, and the latest run.
- `POST /v1/email-attention/run-test` accepts `{"deliver": false}` by default. It scans with `manual_test: true`, never advances scheduled cursors, and returns a preview. Setting `deliver` to `true` sends a clearly marked test notification with a unique test idempotency key, still without advancing source cursors.

Example preview:

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer $COORDINATOR_API_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"deliver":false}' \
  http://127.0.0.1:8080/v1/email-attention/run-test
```

## Activation checklist

1. Deploy reviewed Gmail and Outlook native read adapters.
2. Configure source aliases and HTTPS endpoints through `EMAIL_ATTENTION_SOURCES_JSON`.
3. Inject connector and notification bearer tokens through the runtime secret manager.
4. Configure the user-visible notification adapter.
5. Start with `EMAIL_ATTENTION_ENABLED=false` and call the status endpoint.
6. Enable the agent in a non-production environment and run a preview with `deliver:false`.
7. Run a delivered test and verify the adapter deduplicates the idempotency key.
8. Verify source health, last run, next run, and pending count.
9. Enable the production schedule and observe at least one successful scheduled run.

Code merge alone does not prove that external mailbox connectors or the final notification channel are provisioned. Keep DEN-830 active until those operational checks are recorded.

## SeaORM migration compatibility

The storage boundary is isolated in `src/email_attention/store.rs`. If the coordinator's broader SQLite-to-SeaORM migration lands, preserve the email-attention table semantics, unique keys, transactional pending-delivery association, payload redaction after delivery, cursor preservation, and lease compare-and-swap behavior. Do not resolve that migration by dropping either side of the state model.
