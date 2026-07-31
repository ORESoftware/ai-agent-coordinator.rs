# PostgreSQL runtime and Kubernetes rollout contract

Tracking: the SeaORM persistence pull request and the canonical PostgreSQL schema in `ORESoftware/k8s-libs-and-shared-defs`.

## Why the work is split

The coordinator source now targets PostgreSQL through SeaORM, but the checked-in Kubernetes base is intentionally left on the previously verified SQLite runtime until the PostgreSQL image and operational prerequisites exist.

An image-only promotion is unsafe. The PostgreSQL runtime requires the canonical `ai_agent_coordinator` schema and `AI_AGENT_COORDINATOR_DATABASE_URL`; the current SQLite deployment instead uses a single replica, `Recreate`, and a PVC. Removing the PVC while an older SQLite image is still pinned would strand the deployment between storage models.

Therefore the rollout has two reviewed phases:

1. **Runtime landing:** merge the SeaORM code, declarative-schema tooling, container build, and PostgreSQL integration tests. Keep the live Kubernetes base and immutable image pin unchanged.
2. **Cluster cutover:** after the new immutable image is published, apply the canonical schema, provision the protected database URL, reconcile or backfill any required SQLite state, and update the manifests plus deployment contract in one bounded pull request.

## Phase-one invariants

- `src/**` uses SeaORM and schema-qualified `ai_agent_coordinator` entities.
- The application never creates or migrates tables at startup.
- `scripts/dpm.sh` consumes the schema owned by `k8s-libs-and-shared-defs`.
- PostgreSQL integration CI applies the canonical schema and runs the complete Rust test suite with `TEST_DATABASE_URL` set.
- The checked-in `deploy/k8s` base remains the last verified SQLite deployment until phase two.
- The Sonus Auris and Daedalus Fab cross-organization Linear pilot overlay remains intact and fail-closed.

The ordinary pull-request `GITHUB_TOKEN` cannot read the private sibling shared-definitions repository. For deterministic pull-request testing, `tests/fixtures/ai_agent_coordinator.schema.sql` is an exact byte-for-byte mirror of the canonical schema at shared-definitions commit `86ff698a77c9458b6d6ab27bbb3c6d2e80d381bc`, Git blob `1158f5586538492d7f1415f165457a34a117691c`. CI verifies that blob identity before applying the fixture. The fixture is test input only; it does not become migration authority and must be updated in the same reviewed change whenever the canonical schema revision advances.

## Phase-two prerequisites

All of these must be evidenced before the cutover PR can merge:

1. A successful OCI publication for the exact merged SeaORM commit and a locked-down container smoke test.
2. The canonical schema applied with reviewed `dpm diff`, `dpm review`, and `dpm apply` evidence.
3. `AI_AGENT_COORDINATOR_DATABASE_URL` present in the approved protected secret bundle; no credential in source, GitHub, Linear, logs, or workflow inputs.
4. A decision and tested procedure for any SQLite data that must be retained. An empty or disposable PVC must be documented as such; non-empty production state requires a separately reviewed one-time backfill.
5. Base deployment changed to two replicas and `RollingUpdate` with `maxUnavailable: 0` and `maxSurge: 1`.
6. PVC and `/app/data` mount removed only when the promoted image is PostgreSQL-backed.
7. NetworkPolicy updated for the actual PostgreSQL destination without broadening unrelated egress.
8. The deployment contract starts PostgreSQL with the canonical schema and proves `/readyz`, queue creation, claiming, and repository-admin dry-run behavior.
9. The cross-org Linear pilot remains limited to its exact repositories/default branches, keeps delivery in dry-run, contains no completed-state IDs, and uses reference-only ExternalSecrets.

## Rollout order

1. Review the target database and schema diff.
2. Snapshot the SQLite PVC when retention is required.
3. Apply or verify the PostgreSQL schema.
4. Provision and verify the protected database URL.
5. Smoke the immutable image against the target-compatible schema.
6. Merge the bounded deployment PR.
7. Watch the two-replica rollout and readiness.
8. Exercise queue create/claim/complete, webhook intake, Linear planning, and repository-admin dry-run.
9. Remove or archive the old PVC only after the rollback window closes.

## Rollback

Rollback means restoring the previous immutable image and SQLite manifest together. Do not point the PostgreSQL binary at the SQLite deployment contract or restore the PVC mount beneath the PostgreSQL binary. Preserve the database and PVC snapshots until the cutover and backfill have been verified.
