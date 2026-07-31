# MemeBank database agent instructions

These instructions govern the database contract tree. Load every ancestor lowercase `agents.md` from filesystem root to `$PWD` in root-to-leaf order before editing.

## Merge policy

- avoid git rebase in favor of git merge.
- Never force-push, rewrite shared history, discard concurrent schema intent, or bypass required checks.
- Resolve conflicts semantically. Preserve compatible tables, constraints, RLS policies, grants, source weights, indexes, lease fencing, fixtures, lifecycle states, and documentation from both sides.
- After every merge, scan the full worktree outside `.git` for conflict markers and run `make agent-check`.

## Declarative ownership

1. `schema/order.txt` is the reviewed apply order for the empty-database desired state.
2. Do not add incremental timestamp migrations to `schema/`; historical transitions belong in a separate reviewed migration plan generated from desired-state diffs.
3. Do not use `CREATE TABLE IF NOT EXISTS`, destructive `DROP`, or production auto-migration at application startup.
4. Environment roles belong under `bootstrap/`; tenant data and database objects belong under `schema/`.
5. Use SeaORM for ordinary CRUD. Keep parameterized SQL for RLS helpers, generated search documents, pgvector indexes, leasing, locking, and bounded hybrid retrieval.

## Security and tenancy

- Every tenant-owned table must carry `library_id`, enable and force RLS, and have explicit app and scoped-worker policy coverage.
- Worker access must require transaction-local `memebank.library_id`; never grant a fleet worker unrestricted tenant access.
- Do not give application or worker roles `BYPASSRLS`, schema ownership, or migration rights.
- Never store provider access tokens, refresh tokens, passwords, private keys, presigned URLs, or durable secret values. Store opaque secret-manager references only.
- Security-definer functions must set a restricted search path and must not interpolate caller-controlled SQL.

## Search and vectors

- Keep native visual, OCR-text, caption-text, and tag-text spaces explicit.
- Never mix incompatible vector dimensions in one typeless or misleading index. Add a reviewed physical table and indexes for each approved dimension.
- Keep model name, immutable revision, processor version, artifact checksum, license, metric, dimension, and status in the registry.
- Preserve reviewed `tsvector` source weights and keep user-confirmed data distinguishable from generated observations.

## Durable work

- Job claims require idempotency, `FOR UPDATE SKIP LOCKED`, bounded leases, fencing epochs, attempts, and append-only events.
- Export, deletion, reconciliation, and index cutover must use explicit states. Never infer completion from a missing row.

## Validation

Run while iterating and before review:

```sh
make agent-check
make report
```

Record the exact source commit, rendered schema digest, CI run, limitations, and deferred real-database evidence in the PR and Linear issue.
