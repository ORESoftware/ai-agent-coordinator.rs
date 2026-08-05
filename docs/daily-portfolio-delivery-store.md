# Daily portfolio delivery PostgreSQL repository

`src/daily_portfolio_delivery_store.rs` is the runtime persistence adapter for the fenced delivery contract in `src/daily_portfolio_delivery.rs`.

The declarative schema remains authoritative in:

```text
ORESoftware/k8s-libs-and-shared-defs/
  pg-defs/schema/databases/ai_agent_coordinator/schema.sql
```

The coordinator never creates or migrates these tables at startup. Operators apply and review the schema through the declarative PostgreSQL workflow before enabling the repository or destination delivery.

## Responsibilities

The repository performs schema-qualified PostgreSQL operations for:

- immutable scheduled, recovery, and manual delivery plans;
- idempotent plan replay and immutable-plan conflict detection;
- exclusive fenced lease claims;
- lease renewal and safe release;
- generation compare-and-set transitions;
- failed and ambiguous attempt outcomes;
- expired in-flight recovery;
- destination receipt replay and conflict detection;
- transactional scheduled-baseline reconciliation;
- durable reload after coordinator restart.

It stores no prompt bodies, source message bodies, source URLs, credentials, authorization headers, model responses, or unbounded destination responses.

## Planning

`plan` inserts the immutable `PlanSpec` with `ON CONFLICT (run_key) DO NOTHING` and then compares every immutable field.

- An identical retry returns `AlreadyPlanned`.
- Any source, plan, delivery, destination, mode, scheduled-key, or idempotency drift returns `RunConflict`.
- The destination idempotency key remains identical to the logical run key.

## Fenced claims

`claim` uses a serializable transaction and locks the run row before changing lease state.

1. The run must exist and must not be delivered.
2. An unexpired lease blocks another owner.
3. PostgreSQL allocates a new value from `daily_portfolio_delivery_fence_seq`.
4. Owner, fence, expiry, and update timestamp are committed together.

A stale token cannot renew, release, begin delivery, record an outcome, or commit a receipt after another claim advances the fence.

Lease duration is bounded to one hour. Application callers should use the shortest duration that safely covers one bounded destination attempt.

## Generation compare-and-set

Every mutating transition includes the expected generation, live owner, live fence, and unexpired lease in its `WHERE` clause.

```text
planned --begin--> delivering --receipt--> delivered
                          |--failure--> failed --begin retry--> delivering
                          |--expiry/uncertainty--> ambiguous --verified receipt--> delivered
```

Attempts increment only when entering `delivering`. Merely claiming a lease does not count an attempt.

Repeated `begin` or terminal-outcome calls return `AlreadyApplied` only when the durable state exactly matches the requested result. Generation drift, token drift, or transition drift fails closed.

## Crash recovery

The integration test exercises the required restart boundaries with separate repository connections:

- **Before send:** the lease may expire and another worker claims with a higher fence.
- **After send, before receipt:** `recover_expired_delivery` converts the run from `delivering` to `ambiguous` and clears the expired lease.
- **After external confirmation:** a reconciler claims the ambiguous run and stores the verified receipt without resending.
- **After receipt commit:** a newly connected repository reloads the delivered run and scheduled baseline.

A process restart is not simulated by cloning an in-memory value; the test drops connections, reconnects, and reloads PostgreSQL state.

## Destination receipts

`record_receipt` runs in a serializable transaction. It:

1. locks the run;
2. proves the live fence and expected generation;
3. compares destination and outbound body digest with the immutable plan;
4. reconciles the scheduled baseline for scheduled or recovery modes;
5. commits the receipt, delivered status, generation increment, lease removal, and baseline together.

An exact receipt replay is idempotent. Receipt, destination, digest, or generation drift conflicts.

Manual delivery never updates the scheduled baseline. Older scheduled dates cannot regress it. A conflicting delivery for the same scheduled date fails before the run or baseline is mutated.

## Schema verification

`verify_schema` checks only for the exact required sequence and tables. It does not attempt repair.

A missing schema is an operator error. Apply the reviewed schema revision and restart or retry the coordinator; do not bypass verification or create tables from application code.

## Local validation

Start PostgreSQL 17, apply the checked-in schema fixture, and run:

```bash
export TEST_DATABASE_URL='postgres://postgres:postgres@127.0.0.1:5432/coordinator'

cargo fmt --all -- --check
cargo test --locked --test daily_portfolio_delivery_state -- --nocapture
cargo test --locked --test daily_portfolio_delivery_store -- --nocapture
cargo clippy --locked --lib \
  --test daily_portfolio_delivery_state \
  --test daily_portfolio_delivery_store \
  -- -D warnings
```

The focused GitHub Actions workflow applies `tests/fixtures/ai_agent_coordinator.schema.sql` to PostgreSQL 17, runs the in-memory and durable suites, performs Clippy and release-library checks, and retains bounded evidence for 14 days.

## Production enablement

This repository adapter is necessary but not sufficient for live delivery. Before enabling a destination:

- merge one approved bounded destination adapter;
- deny redirects and unapproved hosts;
- bound request and response sizes and timeouts;
- redact authorization and destination payloads from logs;
- use the logical run key as the provider idempotency key;
- classify uncertain responses as ambiguous;
- reconcile the provider before resending;
- complete the scheduled, recovery, manual, and ambiguous-response canaries tracked by `DEN-2336`.
