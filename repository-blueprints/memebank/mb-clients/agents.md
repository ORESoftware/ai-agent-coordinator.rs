# MemeBank client agent instructions

Load every ancestor lowercase `agents.md` from filesystem root to `$PWD` in root-to-leaf order before editing this tree.

## Merge policy

- avoid git rebase in favor of git merge.
- Never force-push, rewrite shared history, bypass required checks, or resolve a conflict by choosing one side wholesale.
- Resolve conflicts semantically. Preserve compatible behavior policy, operation metadata, generated-source provenance, fixtures, redaction rules, and language-specific ergonomics from both branches.
- After every merge, scan the full worktree outside `.git` for conflict markers and run `make agent-check`.

## Contract ownership

1. `mb-interfaces` owns API and durable data schemas. Do not invent an endpoint or model in a language client.
2. `contract/operations.json` records only operations verified in the reviewed interface source.
3. `contract/client-policy.json` owns cross-language behavior: auth, request IDs, deadlines, retries, idempotency, pagination, redaction, observability, and publication gates.
4. Generated files contain operation/model metadata only. Hand-written runtime/service layers own ergonomics, transport abstractions, streaming, cancellation, and platform integration.
5. Rust, Dart, and TypeScript must remain behaviorally equivalent even when their APIs are idiomatic rather than textually identical.

## Security

- Use shared-auth clients or the versioned `AccessTokenProvider` boundary; never create a competing password/session store.
- Never log tokens, cookies, signed URLs, provider secrets, raw provider IDs where sensitive, image bytes, OCR text, captions, or event payloads by default.
- Bound retries, response/error bodies, deadlines, refresh attempts, concurrency, and buffers.
- Retry writes only when the operation is explicitly idempotent and carries a caller-owned or stable idempotency key.
- Stop retrying once response-body consumption begins.
- Stream large uploads/downloads rather than buffering entire image payloads.

## Generated code

- Edit source contracts or the generator; do not hand-edit `generated.rs`, `generated.dart`, `generated.ts`, or `generated/manifest.json`.
- Run `make agent-check`; generated drift must fail CI.
- Pin interface package version, generator revision, and contract digest in release evidence.

## Evidence

A staging report is not live conformance. Record canonical repository/commit, generated-tree digest, language-native test runs, mock-server matrix, live development API run, package provenance, and downstream consumer evidence before closing DEN-1009.
