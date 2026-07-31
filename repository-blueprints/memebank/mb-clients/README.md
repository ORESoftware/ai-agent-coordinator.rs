# mb-clients

Behaviorally consistent MemeBank client foundations for Rust, Dart/Flutter, and TypeScript/browser/Node consumers.

> **Promotion boundary:** this directory is a tested staging blueprint while `github.com/memebank/mb-clients` is unavailable to the connected GitHub App. It is not a published SDK family and has not passed live development-API conformance. Promotion is complete only after the exact reviewed history is pushed to the canonical repository, concrete shared-auth bindings replace the current access-token-provider interface, every generated package compiles in its native toolchain, and release evidence is attached to DEN-1009.

## What exists now

The blueprint consumes the five operations currently present in the staged `mb-interfaces` OpenAPI surface:

- `createImport`
- `getAsset`
- `searchAssets`
- `createClipboardManifest`
- `listSyncEvents`

It defines one language-neutral behavior policy and generates immutable operation metadata for all three language families. The hand-written runtime layer establishes:

- a shared-auth `AccessTokenProvider` boundary rather than a second login implementation;
- request IDs and bounded deadlines;
- exact path/query planning;
- required/optional/forbidden idempotency-key behavior;
- bounded retries only for safe reads or idempotent writes;
- no retry after a response body starts;
- final-page synchronization cursor advancement from `through_cursor`;
- streaming body types for future large image transfers;
- default debug redaction of credentials and sensitive image intelligence;
- deterministic generated-source and fixture validation.

The Rust behavior core is dependency-free and has executable unit tests. Dart and TypeScript contain transport-neutral interfaces, generated operation metadata, and native smoke tests. CI compiles/tests Rust, analyzes/runs Dart, and executes the TypeScript source through Node’s native type-stripping parser; concrete HTTP transports, shared-auth binding, live API parity, and package publication remain explicit blockers rather than being reported as complete.

## One-command gate

A clean checkout needs Python 3.12 plus the pinned Rust, Node/TypeScript, and Dart toolchains declared in CI:

```bash
make agent-check
```

This command:

1. regenerates Rust/Dart/TypeScript operation metadata;
2. fails if generated source differs from the reviewed tree;
3. validates the behavior and publication contracts;
4. runs 11 positive/negative cross-language contract tests;
5. compiles and tests the Rust client core;
6. analyzes and smoke-tests Dart;
7. syntax-checks and executes the TypeScript core with Node’s native TypeScript support.

The report is written to `.artifacts/client-report.json` and intentionally records outstanding blockers.

## Source ownership

```text
contract/operations.json          current operation metadata derived from mb-interfaces
contract/client-policy.json       retries, auth, redaction, pagination, observability, publication
generator/generated sources       operation metadata only
clients/*/runtime files            ergonomic, hand-written behavior and transport boundaries
fixtures/golden-scenarios.json    sanitized cross-language behavior matrix
scripts/                           deterministic generation and policy/conformance validation
```

Generated transport models must ultimately come from tagged `mb-interfaces` releases. Generated code is not an excuse to expose raw transport types throughout product code: Rust web/CLI/MCP, Flutter, and TypeScript integrations should consume ergonomic service methods backed by this common policy.

## Minimal usage shape

Rust:

```rust
use mb_clients::{build_request_plan, OperationId};
use std::collections::BTreeMap;

let headers = BTreeMap::from([(
    "Idempotency-Key".to_owned(),
    "import-20260731-0001".to_owned(),
)]);
let plan = build_request_plan(
    OperationId::CreateImport,
    &BTreeMap::new(),
    &BTreeMap::new(),
    &headers,
)?;
```

Dart and TypeScript expose equivalent operation metadata, token-provider, transport, cancellation/streaming, retry, and redaction boundaries. Concrete HTTP implementations will be added only after DEN-1008 fixes the shared-auth integration and the canonical repository exists.

## Deliberately deferred API families

The current `mb-interfaces` blueprint does not yet define complete operations for libraries, resumable uploads, provider imports, job streams, tag corrections, similar-image search, variant downloads, storage connections, or exports. These families are recorded in `contract/operations.json` as deferred coverage; the generator does not invent endpoints.

## Non-claims

This blueprint does not prove:

- canonical repository creation;
- concrete shared-auth token refresh against a real server;
- full TypeScript static type-checking and package publication to Cargo, pub.dev, or npm;
- resumable streaming upload behavior;
- live development API parity;
- package signing, provenance, or registry publication;
- consumption by Flutter, CLI, web, or MCP applications.

Those remain acceptance gates on DEN-1009.
