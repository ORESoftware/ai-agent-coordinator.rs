# Portfolio remediation queue

## Purpose

A repository audit is useful only when each finding has a current baseline, a canonical tracking record, a deterministic priority, and a bounded next action. Repeatedly creating similar audit tickets or counting repositories does not establish conformance.

`portfolio-remediation-queue/v1` converts a normalized portfolio inventory into a read-only, dry-run remediation queue. The validator is dependency-free and performs no GitHub, Linear, deployment, DNS, database, or credential operation.

## Repository inventory

Every repository record binds:

- exact `owner/repository` identity;
- visibility and lifecycle state;
- canonical architectural role;
- default branch and exact observed head for active repositories;
- Linear and GitHub Project routing;
- agent-instruction digest and pinned toolchain inventory;
- CI, security-policy, license, and release-path state;
- independent test maturity;
- deployment boundary;
- expectation profile; and
- a canonical current-state digest.

The baseline carries its observation time, catalog digest, policy digest, and repository count. It fails when future-dated, older than 31 days, or inconsistent with the supplied inventory.

## Findings and dispositions

Each finding records its repository, category, state, severity, evidence identities, canonical Linear issue, exact GitHub issue or pull request, owner, dependencies, bounded risk scores, and one remediation disposition.

Supported dispositions are:

- `draft_pr` — prepare a bounded draft branch plan;
- `amend_existing` — add unique scope to the canonical existing record;
- `noop_blocked` — preserve an explicit external blocker;
- `noop_completed` — preserve evidence that the finding is already remediated;
- `noop_exempt` — preserve a reviewed intentional exemption; and
- `noop_duplicate` — point to one canonical non-duplicate finding.

Actionable dispositions require a safe branch name, exact base commit, at least two acceptance checks, and residual-risk text. No-op dispositions cannot claim a branch or base commit. Every remediation has `dry_run=true` and `apply_authorized=false`.

Duplicate Linear or GitHub anchors are rejected unless the later finding is explicitly `noop_duplicate` and names a canonical non-duplicate target. Dependencies must exist, remain acyclic, and rank before their dependents.

## Deterministic ranking

Rank is checked in this order:

1. actionable work;
2. externally blocked no-op work;
3. completed, exempt, and duplicate no-op work;
4. severity;
5. a weighted score over security, data integrity, user impact, dependency centrality, operational risk, inverse effort, and available test evidence; and
6. finding ID as a stable final tie-breaker.

Ranks must be unique and contiguous. This makes queue changes auditable: a changed order must be explained by changed evidence, state, severity, or scores.

## Commands

```sh
python3 -m py_compile \
  scripts/portfolio_remediation_queue.py \
  scripts/validate_portfolio_remediation_queue.py \
  scripts/test_portfolio_remediation_queue.py
python3 -m unittest -v scripts/test_portfolio_remediation_queue.py
python3 scripts/validate_portfolio_remediation_queue.py \
  fixtures/portfolio-remediation-queue-v1.json \
  --json
```

The checked-in fixture is synthetic planning evidence. It demonstrates five repository records and seven findings spanning actionable, blocked, and completed dispositions; it does not assert current live fleet state.

## Safety boundary

The queue requires:

```text
read_only=true
dry_run=true
repository_creation_authorized=false
visibility_changes_authorized=false
branch_protection_changes_authorized=false
merge_authorized=false
deployment_authorized=false
dns_changes_authorized=false
database_changes_authorized=false
```

Any apply, merge, administrative, deployment, or infrastructure operation remains a separate repository-specific change with exact-head checks, independent review, scoped credentials, and rollback evidence.

Tracking: `DEN-598`, `DEN-2225`, `DEN-2331`, and the portfolio-conformance GitHub issue.
