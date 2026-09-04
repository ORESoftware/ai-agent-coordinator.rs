# Twenty-workstream execution map — 2026-09-04

## Scope and evidence boundary

This map combines the fifteen previously selected portfolio workstreams with five additional concrete tasks. It records remotely resolvable tracking and the implementation slice completed in this execution. It is not a claim that every source prompt, every Linear issue, or every repository has been closed.

The fixed Google Chat interval requested previously was August 2 through September 3, 2026. Durable reconciliation evidence covers August 2–24; August 25–September 3 remains an explicit source gap under issue #192. Therefore all corpus-wide closure claims remain fail-closed.

## Workstreams

| # | Workstream | GitHub | Linear | Current execution state |
|---:|---|---|---|---|
| 1 | Prove the August 25–September 3 Google Chat source delta | #192 | `DEN-3921`, `DEN-4047`, `DEN-3473` | Blocked on an independent export/pagination receipt |
| 2 | Add the fail-closed Chat → Linear → GitHub execution ledger | #193 | `DEN-834`, `DEN-3921`, `DEN-4047` | Implemented in draft PR #207; closure remains blocked by #192 |
| 3 | Rotate exposed Chat intake credentials and remove token egress paths | #194 | `DEN-3382` | Security rotation and transport hardening tracked; no credential copied into GitHub or Linear |
| 4 | Require independent ChatGPT and Claude review receipts | #195 | `DEN-3570`, `DEN-3793` | Acceptance contract tracked; no merge bypass authorized |
| 5 | Add layered shared-auth abuse controls and durable MFA lockouts | #196 | `DEN-3063`, `DEN-3000` | Acceptance contract tracked with distributed/failover tests |
| 6 | Standardize cancellation and eliminate shutdown/quit hangs | #197 | `DEN-3175`, `DEN-4140` | Lifecycle and Sonus Auris regression contract tracked |
| 7 | Make TypeSpec and JSON Schema independent peer authorities | #198 | `DEN-3959`, `DEN-3828` | Independent generation and semantic parity contract tracked |
| 8 | Publish a verified 10–15 package Zed tranche | #199 | `DEN-3908`, `DEN-3495` | Clean-consumer, native-artifact, lock, provenance, and rollback gates tracked |
| 9 | Enforce isolated production/test organization fleets | #200 | `DEN-3927`, `DEN-3915`, `DEN-2213` | Topology, credential/state isolation, E2E receipt, and rollback contract tracked |
| 10 | Complete Praxonne/Elenkos shared-platform adoption | #201 | `DEN-3911`, `DEN-3799`, `DEN-3806` | Fleet, shared dependency, schema, ORM, and test-mirror contract tracked |
| 11 | Unify Nix, SOPS/age, and OCI deployment evidence | #202 | `DEN-323`, `DEN-2641`, `DEN-2887`, `DEN-2888` | Reproducibility, identity, recovery, offboarding, and rollback contract tracked |
| 12 | Enforce role-scoped agent identities and mutation guards | #203 | `DEN-3138`, `DEN-1873` | Planner/coder/reviewer/tester/publisher/merger/deployer separation tracked |
| 13 | Enforce 15+ language SDK and validation parity | #204 | `DEN-4154`, `DEN-4166` | Clean external consumer and shared conformance-vector contract tracked |
| 14 | Verify shared-auth adoption through isolated canaries | #205 | `DEN-2197`, `DEN-2194` | Browser, Flutter, server, edge, organization, factor, and revocation canaries tracked |
| 15 | Reconcile generated artifacts to exact Linear/GitHub entities | #206 | `DEN-4174`, `DEN-2817`, `DEN-2800` | Provenance, mismatch rejection, and idempotent backfill contract tracked |
| 16 | Validate durable normalized daily-briefing lane receipts | #208 | `DEN-2333`, `DEN-824` | Implemented on `feat/den-2333-durable-briefing-receipts-20260904` |
| 17 | Preserve delivery-state atomicity and exactly-once receipts | #209 | `DEN-2334`, `DEN-2466`, `DEN-824` | Pure fenced state machine implemented on the same focused branch |
| 18 | Build a verified weekly engineering-role queue | #210 | `DEN-826`, `DEN-256`, `DEN-812` | Official-source, deterministic, non-applying queue implemented on `feat/den-826-828-digest-provenance-20260904` |
| 19 | Produce a provenance-complete Friday research shortlist | #211 | `DEN-828` | Primary-source, claim-bound, deterministic shortlist implemented on the same focused branch |
| 20 | Turn portfolio audit results into a remediation queue | #212 | `DEN-598`, `DEN-2225`, `DEN-2331` | Deterministic, duplicate-aware, dry-run queue implemented on `feat/den-598-portfolio-remediation-queue-20260904` |

## New implementation slices

### Durable briefing receipts

The new briefing contract validates one immutable receipt per configured lane, rejects missing or stale evidence, distinguishes explicit no-results from failure, and derives a stable sorted input envelope. The delivery state machine uses operation IDs and monotonically newer fencing tokens, requires reconciliation of ambiguous remote responses, and validates a complete copied successor before returning it.

### Opportunity and research provenance

The opportunity contract accepts official employer sources only, deduplicates requisitions, records explicit fit gaps/unknowns, and requires exactly two or three engineering roles for every Fiducia-linked company. The research contract accepts primary sources only, binds factual claims to fragment hashes, separates interpretation and uncertainty, excludes withdrawn records, and ranks by a deterministic evidence-oriented composite.

### Portfolio remediation queue

The queue binds repository inventory to a recent baseline and distinguishes actionable, blocked, completed, exempt, and duplicate dispositions. It prevents duplicate canonical tracking, requires safe exact-base branch plans for actionable work, enforces dependency ordering, and keeps every administrative or production mutation disabled.

## Merge boundary

Code is pushed and draft pull requests are opened for the three new implementation slices. A pull request is mergeable only after:

- its final head is synchronized with current `main`;
- all exact-head workflows are terminal and green;
- independent review required by repository policy is present;
- review findings and threads are resolved; and
- no source, credential, deployment, or administrative blocker is being hidden.

No green check is treated as permission to fabricate an approval or bypass review.
