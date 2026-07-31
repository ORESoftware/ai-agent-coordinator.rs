# MemeBank database contract

This directory defines the reviewed PostgreSQL desired state for MemeBank’s multi-tenant catalog, portable storage, attributed image intelligence, hybrid search, durable jobs, export/deletion, and reconciliation workflows.

> **Promotion boundary:** this is a staging blueprint while `github.com/memebank/memebank-api-server.rs` is unavailable. It is not a production migration, a deployed database, or canonical SeaORM output until the exact reviewed history is promoted and verified in that repository.

## Ownership model

- Declarative SQL under `schema/` is the database source of truth.
- SeaORM entities are generated or checked against the applied schema; they do not own advanced PostgreSQL semantics such as RLS, generated `tsvector`, vector indexes, job leases, or security-definer functions.
- Environment roles and grants are bootstrapped separately under `bootstrap/`.
- Runtime queries under `queries/` are parameterized contracts for operations that ordinary ORM methods cannot express safely or efficiently.
- Production services never run migrations automatically at process startup.

## One-command contract gate

```bash
make agent-check
```

The gate uses only Python 3.12. It validates the ordered desired-state files, RLS coverage, vector-dimension separation, search weighting, durable lease semantics, lifecycle states, role boundaries, parameterized query templates, and deterministic SHA-256 report generation.

To render the exact SQL bundle and report:

```bash
make report
```

Artifacts are written under `.artifacts/` and are not committed.

## Apply to an empty development database

A local PostgreSQL 16+ database with `pgcrypto`, `pg_trgm`, and `vector` available may be initialized explicitly:

```bash
export PGHOST=127.0.0.1 PGPORT=5432 PGDATABASE=memebank_dev PGUSER=postgres
make apply-dev
```

`apply-dev` is intentionally separate from `agent-check`. It requires an explicit database environment and runs role bootstrap, the rendered desired state, structural verification, and RLS behavior tests with `ON_ERROR_STOP` enabled.

## Design highlights

- Exact blob identity is global and internal; tenant-facing records reference stable blob IDs without exposing provider URLs or credentials.
- Every tenant-owned table carries `library_id`, optimistic revision state where mutable, and forced RLS.
- Worker access is constrained by a transaction-local `memebank.library_id` scope to prevent confused-deputy cross-library processing.
- Search documents hold explicit user, confirmed-tag, OCR, and selected-caption source columns with reviewed `tsvector` weights.
- Embeddings use separate physical tables for 384, 768, and 1024 dimensions. Model, revision, space, metric, artifact checksum, processor version, and lifecycle remain explicit.
- Job claims use `FOR UPDATE SKIP LOCKED`, fencing epochs, bounded leases, idempotency keys, attempts, and append-only events.
- Export, deletion, storage-orphan reconciliation, and model cutover have explicit durable states.

## Remaining work before DEN-1007 can close

The canonical repository must add a real PostgreSQL/pgvector CI service, generate and diff SeaORM entities, load a representative scale dataset, retain `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` evidence, test backup/restore, and prove model cutover with both old and new indexes live. The staging validator does not substitute for those database executions.
