# Meta Agent observable event protocol v1

Linear: DEN-1057, DEN-1061, DEN-1062, DEN-1067, DEN-1069

This repository carries a transport-neutral conformance contract for the future canonical `meta-agents-demo/meta-agent-control-plane.rs` repository. It prevents the coordinator, Slack bridge, provider adapters, recovered Rust daemon, and future generated clients from inventing incompatible event shapes while canonical repository publication remains blocked.

The contract is source material for promotion, not a claim that the target repository or production service exists.

## Observable, not private reasoning

The envelope records externally useful facts: goals, tasks, decisions, assumptions, confidence, evidence, concise critiques/reflections, lessons, human controls, and transport diagnostics. It must never request, store, transmit, or display hidden chain-of-thought, private model reasoning, scratchpads, internal monologues, or reasoning-token traces.

Providers should send conclusions, brief rationale summaries, evidence references, declared assumptions, confidence, and validation outcomes. The validator rejects forbidden reasoning field names at any depth and credential-shaped fields or values.

## Envelope

Every event has:

- protocol version `1.0`;
- canonical UUID event ID and bounded idempotency key;
- UTC RFC3339 occurrence timestamp;
- agent/provider/model labels as metadata, never trusted authentication identity;
- correlation ID plus optional server, session, run, goal, task, causation, and parent-event IDs;
- one exhaustive event kind from the policy;
- payload confidentiality classification and redaction state;
- bounded evidence references with content SHA-256 identities;
- delivery metadata for HTTP, WebSocket, TCP, or UDP;
- a bounded JSON object payload.

Unknown fields and unknown event kinds fail within v1. A compatible additive revision must publish a new reviewed policy/schema version and fixtures rather than silently widening a deployed v1 decoder.

## Reliable transports

HTTP, WebSocket, and framed TCP share the same normalization and validation behavior. They may request acknowledgements, carry bounded sequence numbers, and transport any v1 event kind subject to authorization.

A receiver applies events by idempotency key:

1. unseen key and valid event: apply once;
2. same key and identical normalized digest: acknowledge as duplicate without another state mutation;
3. same key and different normalized digest: reject as an idempotency conflict.

Transport retries must preserve both the idempotency key and normalized event content.

## UDP boundary

UDP is best-effort telemetry only. It may carry:

- `heartbeat`;
- `task_progressed`;
- `observation_recorded`;
- `risk_declared`;
- `transport_diagnostic`.

UDP cannot request acknowledgements, claim a reliable sequence, retry with an attempt greater than one, or carry context delivery, approvals/rejections, pause/resume/cancel, tool commands, plans, decisions, or other privileged state transitions. The server must re-authorize every reliable privileged event independently; receipt of UDP telemetry never grants authority.

## Size and privacy limits

- complete event: 64 KiB canonical JSON;
- payload: 32 KiB canonical JSON;
- evidence references: 16;
- source metadata entries: 32;
- delivery attempts: 1–16; UDP exactly 1.

Durable events must not contain access tokens, refresh tokens, authorization headers, API keys, passwords, cookies, private keys, signed credentials, or secret-shaped fields. Sensitive payloads must be minimized and marked `sanitized` or `redacted` before storage or UI rendering.

## Files

- `contracts/meta-agent-observability/v1/event-envelope.schema.json`: JSON Schema 2020-12 shape.
- `contracts/meta-agent-observability/v1/event-policy.json`: exhaustive kinds, transports, limits, and privacy rules.
- `contracts/meta-agent-observability/v1/fixtures`: valid and intentionally invalid golden events.
- `scripts/validate_meta_agent_observability_contract.py`: dependency-free policy and replay validator.
- `scripts/test_validate_meta_agent_observability_contract.py`: negative and deterministic regression tests.

## Promotion contract

When the canonical Meta Agents repository exists, promote these exact reviewed artifacts or a semantically compatible successor. Rust types, generated JSON Schema, OpenAPI, provider examples, and all transport decoders must be tested against the same fixtures. The canonical target should add a drift job that compares its generated schema/policy digest with the promoted contract until this staging copy is retired.

A protocol change is complete only when:

- Rust exhaustive event-kind handling is updated;
- valid/invalid fixtures are updated;
- all reliable transport normalizers produce one equivalent normalized event;
- UDP privilege tests remain negative;
- idempotent replay remains apply/duplicate/conflict;
- the privacy and credential-leak tests remain green;
- exact PR head, checks, semantic conflict decisions, and merge commit are recorded in Linear.
