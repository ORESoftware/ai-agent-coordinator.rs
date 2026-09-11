# H/HAUS standard repository fleet bootstrap

## Scope

The sealed manifest at `repository-fleets/hhaus-org-standard.json` creates and initializes these private repositories in dependency order:

1. `hhaus-interfaces`
2. `hhaus-lib-core`
3. `hhaus-orm-core`
4. `hhaus-clients`
5. `hhaus-sync`
6. `hhaus-flutter`
7. `hhaus-desktop-app.rs`
8. `hhaus-lambdas`

The repository roles and dependency boundaries follow `ORESoftware/my-ai/AGENTS.md`. `main` remains production, each initial commit lands through `agent/bootstrap-standard-foundation-20260903`, and publication uses squash merge with an expected-head SHA.

## Contract and dependency gates

TypeSpec and JSON Schema Draft 2020-12 are independent peer authorities. The generator normalizes model fields, requiredness, field types, and enum values from both declarations against one semantic model. Any discrepancy stops rendering and publication. The interfaces and clients repositories expose 17 language targets through `zed-pkg`.

`hhaus-orm-core` is backend-only. Diesel and SeaORM projections must match before publication. `hhaus-sync` exposes `hhaus-orm-core` only through its separately declared backend adapter and never through its public client dependency surface.

The sealed dependency graph includes `shared-auth`, `ORESoftware/ores-middleware`, `ores-otel`, `ores-rate-limit`, `opto-sync`, and `zed-pkg`. The rate-limit contract requires all five layers in this order:

1. Cloudflare edge
2. gateway/load balancer
3. bounded service-runtime LRU
4. distributed Redis/coordinator
5. durable security/billing quota

## Live-administration boundary

Validation is read-only. The publish job runs only after the reviewed workflow reaches `main`, and it uses the protected `nightly-org-maintenance` environment to mint a short-lived GitHub App installation token scoped to `hhaus-org`. No personal access token, private key, or credential value is accepted through workflow inputs, command-line arguments, committed files, pull-request text, artifacts, or logs.

Each live invocation is restricted to one exact repository and requires all of the following to match the sealed manifest:

- repository administration enabled;
- the allowed organization set equal to only `hhaus-org`;
- exact `organization/repository` confirmation;
- exact manifest SHA-256 confirmation;
- exact repository-plus-digest merge confirmation.

Repository creation is idempotent. Existing repositories must already be private. Existing bootstrap branches are updated only when they still point at the current `main`; otherwise the job stops for semantic review rather than rewriting history.

## Validation evidence

The workflow performs:

- Python compilation and 21 unit tests;
- deterministic render comparison and repository-local verification;
- TypeSpec compilation and TypeSpec/JSON Schema semantic parity checking;
- JavaScript syntax checking for Cloudflare adapters;
- Rust formatting, Clippy with warnings denied, all-feature checking, and tests for the generated Rust foundations;
- pinned `actionlint` validation of the workflow itself;
- 30-day validation and publication ledgers containing repository, PR, head, and merge evidence without credentials.

The associated Linear project is `github.com/hhaus-org`. Creating new Linear issues was blocked on September 3, 2026 because the workspace had reached its free issue limit; project status updates remain the fallback evidence channel until capacity is restored.
