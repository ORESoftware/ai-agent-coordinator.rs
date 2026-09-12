# Prompt execution ledger — 2026-09-03

## Purpose

This ledger converts the current cross-surface review into fifteen bounded, remotely resolvable workstreams. It is deliberately a planning and evidence artifact rather than a completion claim. Each workstream has existing Linear anchors, a dedicated GitHub tracking issue, acceptance checks, dependencies, and a safety boundary.

The fixed Google Chat interval follows the user’s explicit date instruction: **August 2, 2026 00:00 EDT through September 3, 2026 23:59:59 EDT**. That is 33 calendar days inclusive, not a literal rolling 45-day interval. The broader prioritization inventory covers Linear and prior-chat activity from July 5 through September 3, 2026.

## Evidence state

The durable reconciliation receipt in `DEN-4047` reports the August 2–24 segment as complete: 250 unique human messages, 234 threads, 17 replies, zero bots, zero deleted records, and three empty or attachment-only records. The validator treats these as receipt fields and does not infer raw message content.

No independently readable export or continuous pagination receipt was available for August 25–September 3 during this execution. Issue #192 owns that delta. The ledger therefore:

- marks the second source segment incomplete;
- leaves total actionable/mapped/unresolved prompt counts unknown;
- validates successfully only in explicit planning mode; and
- fails closure mode with exit code `3`.

This prevents a false “every prompt is covered” claim while preserving the work that can be verified now.

## Fifteen workstreams

| # | Workstream | Linear anchors | GitHub | State |
|---:|---|---|---|---|
| 1 | Chat source delta | `DEN-3921`, `DEN-4047`, `DEN-3473` | #192 | Blocked on source receipt |
| 2 | Prompt execution gate | `DEN-834`, `DEN-3921`, `DEN-4047` | #193 | Active in this PR |
| 3 | Credential containment | `DEN-3382` | #194 | Active / urgent rotation |
| 4 | Independent dual-AI review | `DEN-3570`, `DEN-3793` | #195 | Queued |
| 5 | Shared-auth abuse controls | `DEN-3063`, `DEN-3000` | #196 | Queued |
| 6 | Lifecycle and shutdown contract | `DEN-3175`, `DEN-4140` | #197 | Queued |
| 7 | Schema peer-authority parity | `DEN-3959`, `DEN-3828` | #198 | Queued |
| 8 | Zed package release tranche | `DEN-3908`, `DEN-3495` | #199 | Queued |
| 9 | Production/test topology | `DEN-3927`, `DEN-3915`, `DEN-2213` | #200 | Queued |
| 10 | Praxonne/Elenkos rollout | `DEN-3911`, `DEN-3799`, `DEN-3806` | #201 | Queued |
| 11 | Nix/SOPS/OCI evidence | `DEN-323`, `DEN-2641`, `DEN-2887`, `DEN-2888` | #202 | Queued |
| 12 | Role-scoped agent identity | `DEN-3138`, `DEN-1873` | #203 | Queued |
| 13 | Fifteen-plus language parity | `DEN-4154`, `DEN-4166` | #204 | Queued |
| 14 | Shared-auth consumer rollout | `DEN-2197`, `DEN-2194` | #205 | Queued |
| 15 | Generated-artifact reconciliation | `DEN-4174`, `DEN-2817`, `DEN-2800` | #206 | Queued |

The machine-readable fixture is `fixtures/prompt-execution-ledger-2026-09-03.json`.

## Validator contract

`validate_prompt_execution_ledger.py` performs structural and semantic checks without network access or external dependencies:

- UTF-8 and 256 KiB input bounds;
- duplicate JSON-key rejection;
- exact root, window, segment, count, coverage, workstream, and safety fields;
- an exact count of fifteen unique workstreams;
- valid `DEN-N` anchors and exact GitHub issue/PR URLs without query strings;
- continuous, non-overlapping source segments covering the full fixed window;
- complete-source/count/mapping consistency;
- known acyclic workstream dependencies;
- deterministic canonical SHA-256 output;
- false-by-construction live-write and merge authorization;
- rejection of raw-message fields, email addresses, common credential formats, authorization material, private keys, and credential-bearing query strings.

### Planning validation

```sh
python3 scripts/validate_prompt_execution_ledger.py \
  fixtures/prompt-execution-ledger-2026-09-03.json \
  --mode planning --json
```

Expected result for the checked-in fixture: exit `0`, `valid=true`, `closure_ready=false`, fifteen workstreams, and one incomplete source segment.

### Closure validation

```sh
python3 scripts/validate_prompt_execution_ledger.py \
  fixtures/prompt-execution-ledger-2026-09-03.json \
  --mode closure --json
```

Expected result until issue #192 is resolved: exit `3` and an explicit closure error. Closure can pass only after every source segment is complete, total prompt counts are present, every actionable prompt is mapped, unresolved count is zero, and the blocker list is empty.

### Tests

```sh
python3 -m py_compile \
  scripts/prompt_execution_ledger.py \
  scripts/prompt_execution_ledger_validation.py \
  scripts/validate_prompt_execution_ledger.py \
  scripts/test_validate_prompt_execution_ledger.py
python3 -m unittest -v scripts/test_validate_prompt_execution_ledger.py
```

The tests cover valid planning evidence, successful fully complete closure, current fail-closed behavior, exact workstream count, duplicate IDs and anchors, malformed Linear/GitHub anchors, source gaps and overlaps, chronological declaration, window edges, dependency errors and cycles, safety flags, raw-message fields, count imbalance, completeness mismatch, missing blockers, out-of-window timestamps, canonical hash stability, duplicate JSON keys, secret-like assignments, email addresses, and unknown fields.

## Security and mutation boundary

The bridge credential supplied outside an approved secret channel is treated as compromised. It is not stored in this repository, issue bodies, fixture, documentation, commands, or test data. Issue #194 tracks rotation and transport hardening.

This change creates a validator, tests, a redacted fixture, documentation, and tracking issues. It does not read production message bodies, merge pull requests, deploy workloads, create repositories, mutate DNS or databases, rotate credentials, or authorize any downstream live operation.
