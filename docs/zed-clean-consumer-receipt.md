# Zed package-quartet clean-consumer receipt

## Purpose

This contract supports `DEN-2060` and the canonical implementation issue `zed-pkg/zed-cli#173`. It defines the evidence required to certify a generated E2E repository that combines the four canonical Zed package roles with recursive Git submodules and clean external consumers.

The receipt is intentionally separate from the package manager implementation. It cannot publish a package, create a repository, merge a pull request, or mutate a production registry.

## Canonical package quartet

Exactly four package records are required, with exactly one of each role:

```text
clients
library
interfaces
cli
```

Every package uses registry identifier `zed`, a semantic version, and an exact GitHub repository URL. A verified package must bind:

- exact 40-character source commit;
- package-manifest SHA-256;
- lockfile SHA-256; and
- published artifact SHA-256.

Missing, duplicated, placeholder, or extra package roles are rejected.

## Mixed recursive submodules

Submodule records include a safe relative path, exact repository URL, exact commit when verified, recursive flag, role, and status. Absolute paths, traversal, `.git` escape paths, malformed repositories, and infrastructure repositories are rejected.

The infrastructure exclusion applies to both repository names and submodule path components. A repository or path named `infra`, ending in `-infra`, or ending in `_infra` cannot enter the mixed package/submodule graph. This preserves the portfolio rule that `*-infra` repositories remain outside monorepo submodules.

## Clean consumers

A consumer must reference the complete four-package set and only declared submodules. Closure requires at least these language classes:

```text
rust
typescript
dart
go
native
```

Every green consumer must prove:

- a clean checkout;
- no local-path dependency;
- no monorepo leakage;
- successful compilation;
- successful execution; and
- an immutable evidence digest.

A generated directory or package file that is never imported, compiled, installed, or executed does not count as a client or clean consumer.

## Lifecycle operations

Exactly one operation of each kind is required:

```text
install
restore
offline-reuse
uninstall
downgrade
concurrent-install
```

A green operation requires immutable evidence and no blocker. `offline-reuse` must record actual offline execution. `concurrent-install` must record real contention rather than a sequential simulation.

The operation evidence is intended to cover deterministic lockfile bytes, cache integrity, frozen/locked restore, recursive-submodule cleanup, downgrade and rollback, registry outage, and cross-process locking behavior.

## Combined dependency graph

The receipt accepts package-dependency and Git-submodule edges. Every endpoint must resolve to a declared package or submodule. Duplicate edges, self-edges, unknown nodes, and dependency cycles are rejected across the combined graph.

This allows a clean consumer to prove package dependency diamonds and recursive submodule behavior without permitting a hidden cyclic materialization plan.

## Planning and closure

Planning mode validates schema, safety, exact package roles, consumer references, lifecycle classes, and graph integrity while allowing planned or blocked work and explicit blockers.

Closure mode additionally requires:

- all four packages verified with immutable evidence;
- all declared submodules verified at exact commits;
- required language classes present;
- every consumer green, clean, compiled, and executed;
- all six lifecycle operations green;
- actual offline reuse;
- actual concurrent contention; and
- zero receipt blockers.

The checked-in fixture is a truthful blocked plan. Tests construct a synthetic fully verified copy and prove that closure is reachable.

## Security

The parser enforces UTF-8 and size bounds, rejects duplicate JSON keys and unknown fields, and rejects email addresses, common bearer/PAT/API-key formats, private keys, credential-bearing query strings, raw prompts, message bodies, and secret fields.

The safety section requires all of the following to remain false:

- credential inclusion;
- production registry mutation authorization;
- repository creation authorization; and
- merge authorization.

## Validation

```sh
python3 -m py_compile \
  scripts/zed_clean_consumer_receipt.py \
  scripts/test_zed_clean_consumer_receipt.py

python3 -m unittest -v scripts/test_zed_clean_consumer_receipt.py

python3 scripts/zed_clean_consumer_receipt.py \
  fixtures/zed-clean-consumer-receipt-2026-09-04.json \
  --mode planning --json
```

The same checked-in fixture must fail closure:

```sh
python3 scripts/zed_clean_consumer_receipt.py \
  fixtures/zed-clean-consumer-receipt-2026-09-04.json \
  --mode closure --json
```

The dedicated GitHub Actions workflow validates itself with pinned `actionlint`, compiles the validator, runs the adversarial and reachable-closure tests, checks the planning receipt, proves the expected closure failure, and requires a clean worktree.

## Owning-repository integration

The `zed-pkg/zed-cli#173` implementation follow-up should generate this receipt from real clean test repositories and bind it to:

1. immutable quartet package versions and source heads;
2. exact recursive gitlinks and safe paths;
3. generated lockfile byte identity;
4. clean registry-only installation with no local paths;
5. offline restore from verified cache;
6. uninstall and recursive-submodule cleanup;
7. downgrade to an immutable previous quartet;
8. simultaneous installers exercising the actual lock protocol;
9. clean compile and process execution in the required language classes; and
10. retained content-free artifacts and receipts.

No production registry write or downstream merge is authorized by this acceptance contract.
