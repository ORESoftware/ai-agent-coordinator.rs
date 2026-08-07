# Slack agent run envelope

Tracking issue: `DEN-1231`

`ai-agent-coordinator.rs` accepts `task_type=slack_agent_run` only when the payload matches the strict version-1 contract implemented in `src/slack_run.rs`.

The envelope carries correlation and bounded task context from `/ores-claude` or `/ores-chatgpt`:

- deterministic `ores-<24 lowercase hex>` run ID;
- bridge workflow ID;
- provider and action;
- bounded prompt;
- immutable Slack workspace, channel, and requester IDs;
- zero to twenty chronological non-bot context messages, each at most 4 KB and at most 32 KB combined;
- explicit `untrusted_channel_context` trust marker;
- repository and Linear routing IDs;
- optional Linear issue identifier;
- write policy;
- the canonical Slack, coordinator, bridge, Linear-run-queue, and GitHub evidence targets.

Unknown fields, unsupported schema versions/actions/providers/write policies, malformed IDs, noncanonical broadcast targets, oversized context, embedded NUL bytes, out-of-order timestamps, and repository/Linear routing errors fail before durable job creation.

This is the admission-contract slice. Live lifecycle projection into the Linear **AI Agent Run Queue** remains a separate step on DEN-1231 and must observe the coordinator job without claiming or completing the execution job prematurely.


## Idempotency-key admission

Every `slack_agent_run` request to `POST /v1/jobs` must carry an
`Idempotency-Key` header whose value exactly equals the validated
`payload.run_id`. Missing, malformed, whitespace-padded, or mismatched
values fail with `400 Bad Request` before the database lookup/insert.

`Database::create_job` repeats the same validation so internal callers
cannot bypass the HTTP admission boundary. Other task types keep their
existing optional-idempotency behavior.

The shared declarative PostgreSQL schema adds the final defense-in-depth
check: a persisted Slack run must use `ores-<24 lowercase hex>` as both
`jobs.idempotency_key` and `payload.run_id`, with schema version `1`.
The unique idempotency constraint then makes retries and concurrent
deliveries reuse one durable job rather than creating duplicates.
