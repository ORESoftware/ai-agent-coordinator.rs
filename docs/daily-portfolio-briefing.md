# Daily portfolio briefing composition

This is the first executable composition slice for DEN-824. It combines normalized child-agent output into one deterministic Markdown and JSON briefing without reimplementing collection from GitHub, Linear, email, research sources, job sources, browsers, or prompt history.

The source collectors remain independently responsible for authentication, retention, redaction, and their own bounded normalized output:

| Lane | Source contract |
| --- | --- |
| Must act today | explicit normalized urgent items |
| GitHub and Linear | normalized portfolio work state |
| Engineering research | DEN-828 |
| AI and technology | normalized technology scan |
| Career | DEN-826 |
| Inbox and relationships | DEN-830 |
| Business growth | normalized business/fundraising state |
| Prompt coverage | DEN-834 |

All eight lanes are required in the input envelope. A failed lane uses `degraded` or `unavailable` with a bounded error summary; the other seven lanes continue. The composer never fetches raw mailbox messages, hidden reasoning, credentials, or customer payloads.

## Core contract

`tools/compose_daily_portfolio_briefing.py` provides two explicit phases:

1. `plan` reads normalized lane envelopes plus the last confirmed state and produces a mutation-free JSON/Markdown plan.
2. `commit` records a separately confirmed delivery and advances the scheduled baseline only for a scheduled run.

A manual run has its own run key and delivery record but does not change the scheduled run identity or scheduled comparison baseline.

The composer:

- requires exact source-lane schemas and rejects unknown fields;
- merges the same stable identity across lanes only when its material facts agree;
- fails closed when two lanes disagree about the same identity;
- fingerprints status, urgency, evidence, deadline, owner, recommended action, coverage state, disposition, evidence status, and confidence;
- suppresses unchanged items after a confirmed scheduled delivery;
- re-emits an identity only when those material facts change;
- ranks deterministically with an explainable score;
- emits at most 12 substantive items and exactly the top three non-ignore priorities when available;
- distinguishes confirmed fact, inference, and unverified status;
- keeps exact dates, confidence, source links, and one next action;
- strips URL queries/fragments and redacts common credential shapes;
- renders `Do today`, `Monitor`, and `Ignore` sections plus a final count summary;
- records no state until the caller explicitly confirms delivery.

The ranking formula is versioned as `portfolio_rank.v1`:

```text
deadline_risk * 100
+ blocking_impact * 80
+ project_priority * 60
+ expected_value * 40
+ confidence * 20
+ (5 - reversibility) * 10
```

Every integer dimension is bounded from 0 through 5. Tie-breaking is disposition, descending score, earliest relevant date, then stable identity.

## Input and state

A complete example is checked in at:

- `tests/fixtures/daily_portfolio_briefing_input.json`
- `tests/fixtures/daily_portfolio_briefing_state.json`

Unknown root, lane, item, rank, material, or source fields fail parsing so an upstream child cannot silently widen the ingestion boundary.

Plan a scheduled briefing:

```bash
python3 tools/compose_daily_portfolio_briefing.py plan \
  --input tests/fixtures/daily_portfolio_briefing_input.json \
  --state tests/fixtures/daily_portfolio_briefing_state.json \
  --run-mode scheduled \
  --run-key daily-portfolio:scheduled:2026-07-31 \
  --scheduled-run-key daily-portfolio:scheduled:2026-07-31 \
  --scheduled-for 2026-07-31T13:00:00Z \
  --generated-at 2026-07-31T13:02:00Z \
  --timezone America/Chicago \
  --output-json /tmp/daily-portfolio-plan.json \
  --output-markdown /tmp/daily-portfolio-briefing.md
```

After an independently authorized delivery succeeds, commit only the delivery/state transition:

```bash
python3 tools/compose_daily_portfolio_briefing.py commit \
  --plan /tmp/daily-portfolio-plan.json \
  --state tests/fixtures/daily_portfolio_briefing_state.json \
  --delivered-at 2026-07-31T13:05:00Z \
  --output-state /tmp/daily-portfolio-next-state.json
```

Reusing an already delivered run key with the same normalized source state becomes a no-op. Reusing it with different material source state fails closed.

## Scheduling and missed-run recovery

`tools/enqueue_daily_portfolio_briefing.py` enqueues one internal coordinator job. It does not collect source data or deliver the briefing itself.

The default is **08:00 America/Chicago**. The workflow runs hourly and the script gates execution against `DAILY_BRIEFING_TIMEZONE` and `DAILY_BRIEFING_LOCAL_TIME`; the configured local minute must remain `00` for the hourly workflow. The default one-hour recovery attempt uses the same scheduled idempotency key, so:

- the 08:00 invocation enqueues the canonical run;
- a missed or failed invocation is retried at 09:00;
- if 08:00 already succeeded, coordinator idempotency prevents duplicate work;
- later hourly invocations are not due.

Configuration is supplied through environment or repository/environment variables:

```text
DAILY_BRIEFING_TIMEZONE=America/Chicago
DAILY_BRIEFING_LOCAL_TIME=08:00
DAILY_BRIEFING_RECOVERY_MINUTES=60
DAILY_BRIEFING_ENQUEUE_ENABLED=false
```

Scheduled enqueue remains fail-closed until the protected environment variable
`DAILY_BRIEFING_ENQUEUE_ENABLED` is explicitly set to `true`. Manual
`workflow_dispatch` enqueue still requires the protected environment and an
explicit `manual_id`.

A manual rerun requires both `--force` and an explicit `--manual-id`; its run key is separate from the scheduled key.

Dry-run locally:

```bash
python3 tools/enqueue_daily_portfolio_briefing.py \
  --endpoint https://coordinator.example.invalid \
  --now 2026-07-31T13:00:00Z \
  --dry-run
```

The scheduled workflow uses the protected `daily-portfolio-briefing` environment and environment-only coordinator credentials. Endpoint URLs may not contain credentials, queries, or fragments; non-loopback HTTP is rejected; redirects are refused; responses are size-bounded; authorization is redacted from dry-run output.

## Safety boundary

The composition slice is read-only with respect to all external systems. Its coordinator job contract explicitly forbids issue mutation, pull-request merge, deployment, email delivery without separate authorization, message replies, job applications, repository creation, secrets, and hidden reasoning.

State commit is not proof of delivery by itself. The eventual delivery adapter must confirm the destination result first, then invoke the commit phase with the exact plan digest.

## Validation

```bash
python3 -m py_compile \
  tools/compose_daily_portfolio_briefing.py \
  tools/enqueue_daily_portfolio_briefing.py \
  tests/test_daily_portfolio_briefing.py

python3 -m unittest -v tests/test_daily_portfolio_briefing.py
```

The test suite covers the eight-lane contract, child-issue adapters, ranking, the 12-item cap, top-three selection, source-link sanitization, fact/inference labeling, material-change suppression, manual baseline isolation, duplicate delivery, digest conflicts, cross-lane identity conflicts, lane failure isolation, redaction, daylight/standard time, one-hour missed-run recovery, and the safe job payload.

## Remaining DEN-824 phases

This slice does not claim the full ticket complete. The remaining work is:

1. land the normalized output contracts and durable result locations for every child collector;
2. implement the coordinator worker that reads those exact outputs and the last confirmed state;
3. add the approved delivery adapter and destination-specific confirmation evidence;
4. persist briefing state transactionally with a fenced lease and compare-and-set delivery record;
5. connect the parent run to child-agent status, bounded telemetry, alerts, and runbooks;
6. execute a real scheduled canary, a one-hour recovery canary, and a manual rerun canary without altering the scheduled baseline.
