# Artifact-recovery evidence admissibility

An evidence record is not permanently valid merely because it once passed. A pull-request head can move, its base branch can advance, a deployed image can be repinned, a dependency graph can change, or the governing workflow policy can be revised. `artifact_recovery_evidence_admissibility.v1` makes those changes explicit and prevents historical evidence from being reused as proof for a different revision or environment.

## Contract

The report contains three bounded inputs:

* a content-addressed policy with separate stale and expiry thresholds for `security`, `deployment`, `code`, and `documentation` evidence;
* current subjects, each identified only by digests for the stable subject, current revision, and relevant dependencies;
* immutable evidence records containing producer, owner, payload, subject-revision, policy-version, dependency, and capture-time digests.

The policy version is itself the canonical SHA-256 digest of its clock-skew allowance and risk-TTL table. Changing any threshold without changing the version is rejected.

The validator derives one state per evidence record:

* `current`: the subject, revision, dependencies, policy, and TTL are still admissible;
* `stale`: the recertification warning threshold elapsed, but the hard TTL did not;
* `expired`: the hard admissibility TTL elapsed;
* `superseded`: the subject revision changed;
* `invalidated`: a bound dependency or policy version changed;
* `unverifiable`: the current subject or one of its required dependencies cannot be resolved.

State is derived, not trusted from input. Unknown fields, malformed digests, duplicate identities, future timestamps, impossible TTLs, ambiguous owners, tampered summaries, tampered queues, and tampered report digests fail closed.

## Dependency binding

Evidence binds only to the dependencies it declares. An exact-head test normally includes `pr_head`, `pr_base`, `required_check_set`, and `workflow_policy`. Runtime health normally includes `deployed_image` and `environment_config`. Dependency certification normally includes `dependency_graph`. A changed digest invalidates the old evidence while preserving the original record and its `record_sha256`.

## Recertification queue

Every non-current assessment creates recertification work. The queue is deterministically deduplicated by:

```text
subject identity + evidence kind + current revision
```

Multiple stale or invalidated observations therefore produce one owned action instead of duplicate work. Conflicting owners for the same fingerprint are rejected rather than assigned arbitrarily. Historical evidence is never edited, rebound to a newer revision, or deleted by recertification.

## Commands

```bash
python3 tools/artifact_recovery_evidence_admissibility.py aggregate \
  --input raw-evidence.json \
  --output evidence-admissibility.json \
  --now 2026-08-10T20:00:00Z

python3 tools/artifact_recovery_evidence_admissibility.py validate \
  evidence-admissibility.json \
  --now 2026-08-10T20:00:00Z
```

The `example` command emits a deterministic synthetic contract fixture for CI only. It is not live portfolio evidence and must not be represented as a successful recertification run.

## Integration boundary

The contract workflow compiles and tests the engine, validates the schema, round-trips a synthetic report, and uploads bounded evidence. Source adapters can feed the same report into nightly artifact recovery and portfolio briefing. Those consumers must treat `stale` as partial and `expired`, `superseded`, `invalidated`, or `unverifiable` as blocked; they must never silently fall back to the last green record.

Refs DEN-3435 and DEN-2797.
