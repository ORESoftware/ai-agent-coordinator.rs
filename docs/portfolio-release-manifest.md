# Truthful portfolio release manifest

## Purpose

This contract supports `DEN-2354`, `DEN-2362`, `DEN-2371`, and the parent `DEN-2353` E2E program. It turns a portfolio release decision into a machine-verifiable statement tied to exact repository heads, independently executed lanes, immutable artifacts, explicit blockers, and named approval.

The checked-in fixture is intentionally a **hold** planning snapshot. It demonstrates the structure without asserting that the portfolio is ready for production.

## Manifest sections

### Coverage

Coverage records whether the live catalog is complete and reconciles:

```text
observed repositories + unresolved repositories = expected repositories
```

The observed count must equal the actual repository array length. Closure requires known counts, zero unresolved repositories, and exact equality between expected and observed counts.

### Repositories

Each repository record contains:

- one unique lowercase identifier;
- an exact GitHub repository URL without query or fragment data;
- role and environment classification;
- current status;
- exact 40-character head commit when verified;
- default branch and visibility;
- archived and superseded flags; and
- a canonical `DEN-N` Linear anchor.

A closure candidate rejects planned or blocked repositories, missing heads, archived repositories, and superseded repositories.

### Capabilities

Every capability identifies its implementation repositories and independent validation lanes. Closure requires:

- verified status;
- maturity level four or five;
- no capability blocker;
- at least one referenced validation lane; and
- every referenced lane green and independent.

Repository and lane references must resolve within the same manifest.

### Artifacts

Every release artifact is bound to an inventoried repository and records its kind, status, content SHA-256, and provenance SHA-256. A verified artifact without both digests is invalid. Closure requires every artifact to be verified.

### Evidence lanes

The release gate requires at least one certified independent lane for each kind:

```text
contract
sdk-clean-consumer
browser
security
migration
recovery
scale
rollback
```

Green lanes require an exact head, immutable evidence digest, and no blockers. Security, recovery, scale, and rollback lanes must also be marked destructive; a non-destructive simulation cannot satisfy those closure classes.

### Decision

The only decision values are `go`, `hold`, and `stop`. A `go` decision is structurally invalid unless every closure requirement is already satisfied. Closure additionally requires at least one named approver and a timestamped rationale.

A passing certification still does not deploy anything. Promotion is a separate operation with separate authorization.

### Safety

The manifest is read-only by construction. It requires all of the following to remain false:

- deployment authorization;
- migration authorization;
- credential-rotation authorization; and
- real-user enrollment authorization.

The parser rejects duplicate JSON keys, email addresses, common credential formats, authorization material, private keys, credential-bearing query strings, raw prompts, message bodies, and secret fields.

## Planning versus closure

Planning mode validates structure, reference integrity, bounded metadata, and truthful status while allowing explicit incompleteness and a `hold` or `stop` decision.

Closure mode additionally requires complete inventory, exact heads, mature independently validated capabilities, verified artifacts, all required evidence classes, no blockers, and an approved `go` decision.

The checked-in planning fixture must pass:

```sh
python3 scripts/portfolio_release_manifest.py \
  fixtures/portfolio-release-manifest-2026-09-04.json \
  --mode planning --json
```

The same fixture must fail closure:

```sh
python3 scripts/portfolio_release_manifest.py \
  fixtures/portfolio-release-manifest-2026-09-04.json \
  --mode closure --json
```

The unit tests build a synthetic fully certified copy of the planning fixture and prove that closure is reachable. They also exercise premature `go`, count mismatches, duplicate repositories and evidence entities, malformed URLs/SHAs/branches/Linear anchors, unresolved references, weak maturity, missing independence, non-destructive destructive-class lanes, missing lane classes, unverified artifacts, archived/superseded repositories, blockers, absent approval, unsafe flags, unknown fields, duplicate keys, credential-like content, personal addresses, and canonical digest stability.

## Validation

```sh
python3 -m py_compile \
  scripts/portfolio_release_manifest.py \
  scripts/test_portfolio_release_manifest.py

python3 -m unittest -v scripts/test_portfolio_release_manifest.py
```

The dedicated GitHub Actions workflow validates itself with pinned `actionlint`, runs the tests, checks the planning receipt, proves the expected closure rejection, and requires a clean worktree.

## Integration sequence

1. Populate the live repository catalog from authenticated read-only GitHub inventory.
2. Resolve archived, superseded, missing, and wrong-organization records rather than silently omitting them.
3. Bind every capability to exact implementation heads and independently executed lanes.
4. Freeze artifacts and provenance at immutable digests.
5. Run contract, clean-consumer, browser, security, migration, recovery, scale, and rollback lanes in dependency order.
6. Reconcile flakes and stale heads; do not waive absent evidence by changing the decision.
7. Produce a reviewed `go`, `hold`, or `stop` receipt.
8. Request production promotion separately only after a valid certification.

No deployment, migration, credential rotation, production write, or real-user enrollment is authorized by this validator or its fixture.
