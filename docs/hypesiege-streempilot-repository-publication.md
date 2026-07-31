# HypeSiege and StreemPilot repository publication

**Tracking:** DEN-877, DEN-881, DEN-896, and DEN-319  
**Evidence date:** July 31, 2026 (America/Lima)

This document records the publication boundary for the 15-repository HypeSiege
family and the 17-repository StreemPilot family. It does not claim that a target
repository exists merely because source files, a local Git history, a manifest
row, a workflow, or a Linear issue exists.

## Canonical source and semantic reconciliation

Parallel implementations represented different parts of the intended system:

- one carried the complete repository-content generator and an unsafe
  all-at-once publication attempt;
- another carried the deterministic ledger, one-repository publisher, and
  child-before-monorepo verification model;
- current `main` contained later unrelated work that neither old branch could be
  allowed to overwrite.

The canonical repair combines the compatible intent instead of choosing a branch
wholesale:

1. preserve the complete six-part source-generator payload;
2. verify its decoded SHA-256 identity as
   `a57b00961ee57ae09bf3bb2e2d09afbdd1ddbbbde832b027802f82a1fc5dfa84`;
3. reconstruct all repositories in a caller-owned directory and relocate the
   generator's tree, archive, and checksum outputs together;
4. preserve each repository's complete final indexed tree;
5. seal all child repositories before the two monorepositories;
6. materialize clean local child checkouts while committing exact mode-`160000`
   gitlinks and canonical `.gitmodules` URLs;
7. compare the regenerated schema-v2 manifest byte-for-byte with the checked-in
   ledger; and
8. retain the fail-closed, one-repository-at-a-time publisher with exact
   confirmation and remote-head verification.

The reviewed generator may use setup commits while assembling a monorepository.
Reconstruction preserves the final indexed tree and latest reviewed commit
message, then creates a parentless fixed-author/fixed-date root commit. This
removes timestamp-dependent setup history without discarding generated files or
child gitlinks.

The checked-in ledger represents **32 deterministic independent Git histories,
888 tracked files, and 30 immutable gitlinks**. Running reconstruction twice
must produce identical commit SHAs for every repository.

The superseded `deploy/k8s/bootstrap` all-at-once publisher and its bundled
payload are not part of the canonical boundary. That path accepted a broad
repository-administration credential and attempted the entire fleet in one
execution, contradicting per-repository confirmation, preflight, publication
order, and remote-head verification.

## Reconstruct and validate locally

The gzip/base64 parts under `repository-fleets/hypesiege-streempilot/` contain
the complete reviewed generator. The wrapper verifies the decoded source before
executing it:

```bash
python scripts/reconstruct_hypesiege_streempilot_fleet.py \
  --output-root /secure/path/to/hypesiege-streempilot-fleet \
  --manifest-out /tmp/reconstructed-manifest.json

cmp /tmp/reconstructed-manifest.json \
  repository-fleets/hypesiege-streempilot.json
```

Reconstruction fails closed on payload drift, malformed output, missing source
commits, branch or origin drift, dirty worktrees, Git corruption, tracked-file
drift, missing or incorrect gitlinks, mismatched submodule checkouts, manifest
drift, or totals other than 32 repositories, 888 files, and 30 gitlinks.

Transport archives and their checksums are recovery material only. They are not
substitutes for the per-repository commit ledger, authenticated remote metadata,
or post-push head verification.

## Current remote boundary

At the evidence date:

- the connected GitHub App installation is present for the canonical
  `StreemPilot` organization, but no canonical fleet repository has been proven
  through that installation;
- the connected GitHub App is not installed for `hypesiege`;
- the existing-repository connector can manage selected repositories, branches,
  files, pull requests, issues, and checks, but does not expose organization
  repository creation; and
- the protected repository-bootstrap path still requires a short-lived,
  least-privilege GitHub App installation token and an exact organization
  allowlist under DEN-319.

Do not redirect HypeSiege repositories into another organization, rename sealed
repositories, create README-only substitutes, or treat an empty organization
installation as publication evidence.

## Safe one-repository publisher

Planning is network-free and requires no credential:

```bash
python scripts/publish_hypesiege_streempilot_fleet.py \
  --repository hypesiege/hypesiege-api-server.rs
```

Live execution requires all of the following:

1. the reconstructed source root containing the exact independent Git history;
2. the exact manifest owner/name;
3. `--execute`;
4. `--confirm-repository` exactly equal to that owner/name;
5. a short-lived GitHub App installation token in
   `GITHUB_REPOSITORY_ADMIN_TOKEN`, injected by an approved secret manager and
   scoped only to the required organization and operation; and
6. successful local preflight before any GitHub mutation.

```bash
python scripts/publish_hypesiege_streempilot_fleet.py \
  --source-root /secure/path/to/hypesiege-streempilot-fleet \
  --repository hypesiege/hypesiege-api-server.rs \
  --execute \
  --confirm-repository hypesiege/hypesiege-api-server.rs
```

The publisher refuses unknown owners or repositories, malformed ledgers, wrong
branches, commit/origin drift, dirty trees, file or gitlink drift,
`.gitmodules` mismatch, unmaterialized or changed child checkouts, Git
corruption, missing confirmation, missing credentials, visibility mismatch,
bounded GitHub API errors, non-fast-forward pushes, and post-push remote-head
mismatches.

## Publication order and monorepository guard

Publish each organization's standalone release units first. The publisher must
not publish `hypesiege-monorepo` or `streempilot-monorepo` until every child
repository's remote `main` resolves to the exact commit pinned by the ledger.
The monorepositories are published last.

For every successful publication retain:

- GitHub repository ID, canonical URL, visibility, and default branch;
- exact pushed `main` commit from the ledger;
- an authenticated remote read proving that commit is reachable;
- repository ruleset and security-setting evidence;
- bootstrap CI/check-suite result;
- Linear project mapping and one reversible issue/PR synchronization proof; and
- final monorepository gitlink verification after every child is reachable.

No repository or foundation ticket is complete until those remote reads and
checks exist.
