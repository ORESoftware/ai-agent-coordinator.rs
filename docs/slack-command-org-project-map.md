# alex-main-agent Slack command ownership map

Tracking: `DEN-1041`, `DEN-1231`, `DEN-1298`

This document makes the GitHub-organization, repository, Linear-project, and runtime ownership boundaries explicit for the `alex-main-agent` Slack commands.

## GitHub organization and repository ownership

All currently reviewed components are owned by the `ORESoftware` GitHub organization/account.

| Responsibility | Canonical repository | Linear project | Notes |
| --- | --- | --- | --- |
| Slack request admission, signature verification, command aliases, bounded context, provider selection, and bridge dispatch | `ORESoftware/ai-agent-bridge.rs` | `github.com/ORESoftware` | Canonical command service. Owns `/ores-claude`, `/ores-chatgpt`, `/x-claude`, `/x-chatgpt`, `/my-claude`, and `/my-chatgpt`. |
| Durable execution queue and strict `slack_agent_run` envelope/idempotency admission | `ORESoftware/ai-agent-coordinator.rs` | `github.com/ORESoftware` | Execution authority. Requires `Idempotency-Key == payload.run_id`. |
| Declarative PostgreSQL schema and final duplicate boundary | `ORESoftware/k8s-libs-and-shared-defs` | `github.com/ORESoftware` | Schema authority. Enforces canonical `ores-<24 lowercase hex>` run IDs and payload equality. |
| GitOps deployment, External Secrets, persistent state, ingress, network policy, and browser canaries | `ORESoftware/k8s-cluster` | `github.com/ORESoftware` | Operational deployment authority. Live promotion remains separate from code merge. |
| Legacy standalone Slack integration experiment | `ORESoftware/devops-slack` | `github.com/ORESoftware` | Not canonical for production command dispatch while the signed HTTP ingress is active. Avoid creating a second command authority. |

## Linear ownership

- Parent capability: `DEN-1041` — launch Claude and ChatGPT work from Slack.
- Runtime activation and signed ingress: `DEN-1298`.
- Durable run ledger and lifecycle projection: `DEN-1231`.
- Owning Linear project: `github.com/ORESoftware`.
- Run projection target: `AI Agent Run Queue`.

The coordinator job remains the durable execution authority. Linear is an idempotent lifecycle projection and must not claim or prematurely complete the execution job.

## Command-to-endpoint contract

The six reviewed command names use only two canonical request paths:

| Commands | Canonical endpoint |
| --- | --- |
| `/ores-claude`, `/x-claude`, `/my-claude` | `/slack/commands/ores-claude` |
| `/ores-chatgpt`, `/x-chatgpt`, `/my-chatgpt` | `/slack/commands/ores-chatgpt` |

Interactive modal submissions use `/slack/interactions`.

## Cross-repository invariants

1. Slack requests are acknowledged quickly and verified against the raw signed body.
2. Unknown command names, provider/path mismatches, stale signatures, unauthorized identities, and unreviewed repository targets fail before durable work creation.
3. Every accepted durable Slack run uses one deterministic run ID:

   ```text
   HTTP Idempotency-Key
     = validated payload.run_id
     = jobs.idempotency_key
     = ores-<24 lowercase hex>
   ```

4. Retries, concurrent delivery, process restart, and Slack redelivery reuse one coordinator job and one Linear run projection.
5. Raw credentials, unbounded Slack history, and unrestricted model transcripts are never copied into GitHub, Linear, logs, or test evidence.
6. Live dispatch remains gated independently from code merge by secret readiness, remote Slack manifest reconciliation, deployment health, and signed canary evidence.

## Current merged evidence

- `ORESoftware/ai-agent-bridge.rs#72` — all six command aliases exercised through the real signed HTTP router.
- `ORESoftware/ai-agent-coordinator.rs#67` — exact application-level idempotency admission; merged into `main`.
- `ORESoftware/k8s-libs-and-shared-defs#12` — PostgreSQL idempotency constraint and PostgreSQL 17 contract; merged.
- `ORESoftware/k8s-cluster#762` — signed dry-run GitOps deployment; merged to the operational branch.
- Later browser and rejection-contract PRs linked from `DEN-1231` continue to harden the same canonical path.

## Promotion boundary

Code merge does not by itself enable live provider work. Promotion requires:

- approved Slack bot token and signing secret delivered through the reviewed secret store;
- remote Slack manifest reconciliation and reinstall after command/scope changes;
- healthy ExternalSecret, persistent volume, deployment, service, ingress, and network policy;
- schema preflight and reviewed declarative migration;
- positive and negative signed dry-run canaries;
- replay evidence proving one delivery/retry produces one coordinator job and one Linear run issue;
- a separate reviewed change that disables dry-run.
