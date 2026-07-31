# Prompt intake reconciliation

`prompt-intake` is the first implementation slice of DEN-834. It converts an explicitly authorized export of user-visible ChatGPT prompts into a deterministic **dry-run** reconciliation plan.

The command does not connect to ChatGPT, GitHub, or Linear. It does not create issues, comments, branches, pull requests, or status changes. `--mode apply` fails closed until connector-backed mutation, leases, and compare-and-set idempotency are implemented.

## Security boundary

The input may contain only:

- an opaque account identifier;
- thread and message identifiers;
- timestamps;
- thread titles;
- user-visible prompt text.

Do not include hidden model reasoning, connector credentials, unrelated private messages, mailbox content, API keys, access tokens, or production payloads.

The report emits hashes for the account, source identity, content, scope, and mutation identity. Prompt and title summaries are whitespace-normalized, redacted for common credential forms, and capped at 180 Unicode characters. Full prompt bodies are not written to the report.

## Prompt export schema

```json
{
  "account_id": "opaque-account-id",
  "prompts": [
    {
      "thread_id": "thread-123",
      "message_id": "message-456",
      "created_at": "2026-07-31T03:00:00Z",
      "thread_title": "Portfolio prompt audit",
      "text": "Implement prompt intake in github.com/ORESoftware/ai-agent-coordinator.rs"
    }
  ]
}
```

Unknown fields fail parsing so an upstream export format cannot silently widen the ingestion boundary.

## Project catalog schema

```json
{
  "repositories": [
    {
      "repository": "ORESoftware/ai-agent-coordinator.rs",
      "linear_project": "github.com/ORESoftware/ai-agent-coordinator.rs",
      "aliases": ["ai agent coordinator"]
    },
    {
      "repository": "fiducia-cloud/fiducia-node.rs",
      "linear_project": "github.com/fiducia-cloud",
      "aliases": ["fiducia node"]
    }
  ]
}
```

Catalog aliases are explicit and reviewable. They should be short enough to avoid accidental substring matches and must not contain secrets.

## Usage

```bash
cargo run --bin prompt-intake -- \
  --input authorized-prompts.json \
  --catalog project-catalog.json \
  --window-hours 240 \
  --now 2026-07-31T12:00:00Z \
  --output prompt-intake-report.json
```

Omit `--now` for the current UTC time. Omitting `--catalog` is permitted for diagnostics, but actionable prompts will remain `unmapped` and require review.

The command rejects windows outside 1–8760 hours, prompts without source identifiers, duplicate source identities in one export, malformed JSON, and future/out-of-window records.

## Deterministic identities

For each prompt, the planner derives:

- **source identity** from account, thread, message, and timestamp;
- **content fingerprint** from normalized user-visible text;
- **scope signature** from bounded semantic tokens plus repository identities;
- **mutation key** from schema version, source identity, and content fingerprint.

Exact duplicate content is grouped across threads. Material variants inside one thread are grouped as possible refinements when they retain the same normalized scope but differ in content fingerprint. Numeric rolling-window values and their `day`/`hour` units are treated as scope modifiers for refinement grouping, while each variant keeps its distinct bounded summary and content fingerprint so the changed requirement remains visible and auditable.

## Classification and planning

The first slice distinguishes:

- repository work;
- recurring automation;
- operational programs;
- product work;
- informational questions;
- ambiguous prompts.

Translations, rewrites, informational questions, empty prompts, and prompts without a durable deliverable are excluded with a bounded reason code. Actionable prompts resolve to one Linear project, multiple projects, or an unmapped state through the supplied catalog.

For every resolved repository, the report requests later evidence checks for:

- default-branch implementation;
- remote feature branches;
- open, closed, and merged pull requests;
- resolvable commits and ancestry;
- release or repository-issue evidence when relevant.

No item is declared landed merely because it was claimed in chat.

## Report contract

The JSON report contains:

- input/window/actionable/excluded/review counts;
- bounded per-prompt decisions;
- project and repository resolution;
- GitHub evidence query plans;
- Linear search terms;
- exact duplicate groups;
- possible refinement groups.

The report is deterministic when the export, catalog, window, and `--now` value are unchanged.

## Remaining DEN-834 work

This PR intentionally does not claim the full ticket complete. Later slices must add:

1. approved conversation export/connector ingestion with retention and deletion controls;
2. canonical portfolio catalog loading rather than a caller-maintained file alone;
3. authenticated GitHub evidence collection;
4. Linear search, update-before-create, and explicit apply mode;
5. fenced account/window leases and durable compare-and-set mutation records;
6. post-create duplicate race repair with requirement transfer and `duplicateOf` relations;
7. a July 20–30 corpus integration test reproducing DEN-822 mappings;
8. bounded operational telemetry and daily-briefing output.
