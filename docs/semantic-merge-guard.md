# Fail-closed semantic merge preview

Tracking: `DEN-2775`

`semantic_merge_guard.py` prevents an automation controller from converting a Git conflict into an unreviewed default-branch commit. It previews one exact base/head pair inside a detached temporary worktree, records content-free Git evidence, and removes the worktree without creating a commit or updating any branch ref.

This is a guardrail, not an automatic conflict resolver. A conceptual merge still requires an authorized reviewer to inspect the merge base, both sides, relevant history, APIs, schemas, migrations, tests, documentation, and related repository contracts.

## Contract

The guard:

- resolves base and head to immutable 40-character commit identities;
- records three to ten commit identities and timestamps from each side without serializing messages, authors, emails, or source text;
- disables rerere and runs `git merge --no-commit --no-ff` in an isolated detached worktree;
- on a real conflict, records each unmerged path and the stage-1/base, stage-2/base-side, and stage-3/head-side blob identities and sizes;
- on a clean textual merge, runs `git diff --check` and scans changed files for unresolved conflict markers, duplicate ordinary TOML tables, duplicate JSON keys, duplicate Rust module declarations, and duplicate Rust fields at the same structural depth;
- writes an atomic mode-`0600` JSON review artifact containing identities, paths, counts, and findings only;
- verifies that the source repository's `HEAD` and every local branch ref remain unchanged;
- exits nonzero on a conflict, suspicious clean merge, moving input ref, missing merge base, or operational error.

The guard never force-pushes, updates `main`, selects `ours`/`theirs`, commits a synthetic concatenation, or claims that a clean text merge passes the repository's compiler and test suite.

## Usage

Run from a complete local clone after fetching the exact reviewed refs:

```bash
python3 scripts/semantic_merge_guard.py \
  --repo . \
  --base origin/main \
  --head origin/agent/example \
  --history-depth 10 \
  --output /tmp/semantic-merge-review.json
```

Exit codes:

| Code | Meaning |
|---:|---|
| `0` | Textual merge and heuristic validation produced a clean preview tree. Publish only as a feature-branch PR and run the repository-specific gates. |
| `2` | Manual conceptual resolution is required. The JSON artifact identifies the exact commits, conflict stages, and/or suspicious output. |
| `3` | The guard could not establish a safe preview or detected unexpected repository/ref movement. Stop automation. |

## Required controller integration

Any automation path that currently performs a direct local merge must call this guard before it can create a feature-branch commit. The controller must then:

1. Pin the exact base and head SHAs from the review artifact.
2. Refuse any default-branch push or automatic merge.
3. For exit code `2`, attach the content-free artifact to the canonical issue/PR and stop.
4. For exit code `0`, reconstruct the exact preview tree on a fresh feature branch.
5. Run the repository's compile, lint, test, documentation, schema, migration, and deployment contracts.
6. Open a PR and require immutable-head checks and review before merge.
7. Rerun the guard if either input ref moves.

The controller must not interpret “clean preview” as product acceptance.

## Incident discovery baseline

`semantic-merge-incident-sweep-2026-08-09.json` records the first indexed sweep for the exact commit-message signature `semantic hunk reconciliation`: 50 commits across 13 repositories. Every repository remains `not_run` for build certification in that artifact. The inventory is deliberately honest about that limitation and must not be used to mark `DEN-2775` complete.

Two independently documented breakages are tracked separately in the same inventory:

- `shared-auth/shared-auth-server.rs` commit prefix `ecee3b0`, reconstructed from history in Shared Auth PR #47 after compile/security corruption;
- `zed-pkg/zed-cli` commit prefix `fa67411`, with remaining source repair and certification under `DEN-2777`.

## Validation

```bash
python3 -m py_compile scripts/semantic_merge_guard.py tests/test_semantic_merge_guard.py
python3 -m unittest -v tests.test_semantic_merge_guard
```

The adversarial fixtures prove conflict-stage capture without source serialization, ref immutability, clean preview behavior, duplicate TOML rejection, unresolved-marker rejection, duplicate Rust field rejection, and deterministic 50-commit inventory accounting.
