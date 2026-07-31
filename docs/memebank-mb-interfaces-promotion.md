# Promote the MemeBank `mb-interfaces` blueprint

**Tracking:** DEN-1005, DEN-1006, DEN-1010, and DEN-1043

This document defines the evidence-preserving move from the staging blueprint at
`repository-blueprints/memebank/mb-interfaces` into the canonical
`github.com/memebank/mb-interfaces` repository.

The blueprint is real reviewed source, but it is not delivery while the target
organization is unavailable. Do not publish a package, create a release, or mark
DEN-1006 complete from the staging path.

## Preconditions

1. The connected GitHub App is installed on `memebank` with contents, branch,
   pull-request, issue, and Actions access.
2. `memebank/mb-interfaces` exists with reviewed visibility and default branch
   `main`.
3. The repository-creation evidence in DEN-1043 records the canonical repository
   ID and URL.
4. No legacy `mbk-*` repository is silently selected as the successor; DEN-1015
   must record the explicit migration mapping.

## Promotion method

Preserve the blueprint's reviewed history rather than copying an untracked ZIP.
Use a temporary branch based on the canonical repository's initialized `main`
and import the exact blueprint tree. Record the source coordinator commit SHA in
both the promotion commit message and pull-request body.

Before opening the PR, run:

```bash
make agent-check
make report REPORT=.artifacts/contract-report.json
```

The promotion PR must contain only the intended `mb-interfaces` tree. It must not
include coordinator deployment files, repository-fleet administration state, or
credentials.

## Required checks

The canonical PR must prove:

- duplicate-key-safe JSON parsing;
- local `$ref` closure with no remote schema fetches;
- all sanitized fixtures validate;
- the v1 compatibility floor rejects removed required fields, changed property
  signatures, removed operations, or operation-ID drift;
- `.zpkg.toml` and `.zpkg.lock` parse and agree with the contract baseline;
- no secret-bearing durable fields or fixture values are present;
- the deterministic report artifact identifies every promoted file by SHA-256.

## Merge and release evidence

Merge only with the exact expected head SHA after required checks pass. Attach to
DEN-1006:

1. canonical repository URL and repository ID;
2. source coordinator commit SHA;
3. promotion PR URL and exact merged head SHA;
4. successful `Contract check` workflow run;
5. SHA-256 report artifact;
6. first canonical `main` commit containing the contracts;
7. zed-pkg package/version decision and, when approved, release/tag evidence.

After promotion, downstream API, worker, web, CLI, Flutter, MCP, and client
repositories must consume a pinned `mb-interfaces` release or commit. They must
not copy schemas into private, drifting contract directories.
