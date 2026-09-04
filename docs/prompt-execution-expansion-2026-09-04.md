# Prompt execution expansion — 20 workstreams

## Relationship to the base ledger

PR #207 contains the public-safe, fail-closed base ledger with fifteen workstreams and immutable canonical digest:

```text
e2ea566eb781b3a7174be4ec2ab06f5c5cb6c4c2047375d9ca541264952117c1
```

This expansion adds five non-duplicate, code-bearing workstreams selected from unresolved Linear programs. The base source-completeness boundary is unchanged: August 2–24 has a durable reconciliation receipt, while August 25–September 3 remains unverified and continues to block any every-prompt closure claim.

## Added workstreams

| Combined # | Workstream | Linear | GitHub | Immediate execution target |
|---:|---|---|---|---|
| 16 | Deterministic dependency-risk enforcement | `DEN-3449` | #208, PR #182 | Finish pure classifier evidence, exact-head CI, and test-org canaries. |
| 17 | Daily-briefing failure atomicity | `DEN-2466`, `DEN-2334`, `DEN-2333`, `DEN-824` | #209 | Correct partial-mutation paths and prove exactly-once delivery recovery. |
| 18 | Zed package-quartet clean-consumer E2E | `DEN-2060` | #210, `zed-pkg/zed-cli#173` | Resolve package/submodule blockers and execute clean multi-language consumers. |
| 19 | Fabrication checkpoint, fencing, and DLQ recovery | `DEN-2527`, `DEN-2528`, `DEN-2529`, `DEN-2525` | #211 | Add resumable stage checkpoints, commit-before-ACK, stale-writer rejection, and destructive recovery drills. |
| 20 | Portfolio capability catalog and release certification | `DEN-2354`, `DEN-2362`, `DEN-2371`, `DEN-2353` | #212 | Freeze a truthful release manifest and execute independent plus destructive evidence lanes. |

## Combined validation

The expansion validator reads both the base ledger and the expansion fixture, then enforces:

- a valid fifteen-workstream base ledger;
- an exact base digest and source PR identity;
- exactly five added workstreams and exactly twenty combined workstreams;
- no duplicate workstream IDs or GitHub anchors across either document;
- valid `DEN-N` Linear references and exact GitHub issue/PR URLs;
- dependencies that resolve across the combined graph with no self-edge or cycle;
- strict schemas, duplicate-key rejection, bounded UTF-8 inputs, and canonical SHA-256 output;
- rejection of raw-message fields, email addresses, credential-like content, private keys, authorization material, and credential-bearing URLs; and
- false-by-construction merge and live-mutation authorization.

Run locally:

```sh
python3 -m py_compile \
  scripts/prompt_execution_ledger.py \
  scripts/prompt_execution_ledger_validation.py \
  scripts/validate_prompt_execution_expansion.py \
  scripts/test_validate_prompt_execution_expansion.py

python3 -m unittest -v scripts/test_validate_prompt_execution_expansion.py

python3 scripts/validate_prompt_execution_expansion.py \
  fixtures/prompt-execution-ledger-2026-09-03.json \
  fixtures/prompt-execution-expansion-2026-09-04.json \
  --json
```

The dedicated GitHub Actions workflow repeats those checks at the exact pull-request head with pinned `actionlint`, validates the base digest, requires a 15 + 5 = 20 result, and verifies the worktree remains clean.

## Mutation boundary

This expansion adds planning contracts, tests, and CI only. It does not merge another pull request, run a package manager against untrusted code, send a briefing, activate a fabrication worker, deploy a release candidate, rotate credentials, or alter production state. Each downstream workstream remains subject to its repository-local instructions, exact-head checks, and independent review requirements.
