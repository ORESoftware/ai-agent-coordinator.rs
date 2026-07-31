# HypeSiege and StreemPilot repository publication

**Tracking:** DEN-877, DEN-881, DEN-896, and DEN-319  
**Evidence date:** July 31, 2026 (America/New_York)

This document records the publication boundary for the 15-repository HypeSiege
family and the 17-repository StreemPilot family. It does not claim that a target
repository exists merely because its source history, manifest row, or Linear
issue exists.

## Verified source state

The complete fleet has been generated as 32 independent Git repositories with
sealed initial `main` commits and exact GitHub origins. The checked-in
[`repository-fleets/hypesiege-streempilot.json`](../repository-fleets/hypesiege-streempilot.json)
is the authoritative publication ledger for:

- repository owner and name;
- release-unit kind and description;
- explicit public/private visibility;
- exact 40-character commit to publish;
- tracked-file count;
- exact HTTPS origin; and
- child-before-monorepo publication order.

The source validation pass covered:

- 15 HypeSiege repositories and 17 StreemPilot repositories;
- 856 tracked files;
- clean `main` working trees;
- exact manifest commit and origin agreement;
- `git diff --check` and `git fsck --full --no-dangling`;
- JSON, TOML, YAML, XML, Python, and shell parsing;
- repository-local contract and secret/conflict-marker checks; and
- Go client tests where a Go client package exists.

Transport archives are recovery material only. Their byte checksum is not a
substitute for the per-repository Git commit ledger, a remote metadata read, or
a successful push verification.

## Current remote boundary

At the evidence date:

- the connected GitHub App installation is present for the canonical
  `StreemPilot` organization, but it exposes zero repository objects;
- the connected GitHub App is not installed for `hypesiege`;
- the existing-repository GitHub connector can write branches, files, pull
  requests, issues, and checks for selected repositories, but it does not expose
  an organization repository-creation action;
- the protected coordinator repository-bootstrap feature remains disabled and
  lacks the live repository-administration credential and organization
  allowlist required by DEN-319.

The separately installed empty `channelsiege` organization is not treated as an
alias for `hypesiege`. Repository ownership must not be silently redirected.

## Safe publisher

[`scripts/publish_hypesiege_streempilot_fleet.py`](../scripts/publish_hypesiege_streempilot_fleet.py)
uses the checked-in ledger and publishes exactly one repository per live
invocation. Planning is network-free and requires no credential:

```bash
python scripts/publish_hypesiege_streempilot_fleet.py \
  --repository hypesiege/hypesiege-api-server.rs
```

Live execution requires all of the following:

1. the sealed source root containing the independent Git history;
2. the exact manifest repository name;
3. `--execute`;
4. `--confirm-repository` exactly equal to that owner/name;
5. a short-lived GitHub App installation token in
   `GITHUB_REPOSITORY_ADMIN_TOKEN` with only the permissions and organizations
   needed for the current repository; and
6. successful source preflight before any GitHub mutation.

Example shape, with the token supplied by an approved secret manager rather
than pasted into source, chat, tickets, workflow inputs, or logs:

```bash
python scripts/publish_hypesiege_streempilot_fleet.py \
  --source-root /secure/path/to/hypesiege-streempilot-fleet \
  --repository hypesiege/hypesiege-api-server.rs \
  --execute \
  --confirm-repository hypesiege/hypesiege-api-server.rs
```

The publisher fails closed on an unknown organization or repository, malformed
manifest, wrong branch, commit drift, origin drift, dirty tree, tracked-file
count drift, Git corruption, missing confirmation, missing credential,
visibility mismatch, bounded GitHub API error, or failed push. Its temporary
Git askpass helper reads the environment-only token, is owner-only, and is
removed after the push attempt.

## Publication order

Publish each organization's standalone release units first. Publish
`hypesiege-monorepo` and `streempilot-monorepo` last, after child `main` commits
are remotely reachable and can be pinned as real mode-`160000` gitlinks. Do not
convert copied source directories into a second release authority.

For every successful repository publication, retain:

- GitHub repository ID, canonical URL, visibility, and default branch;
- exact pushed `main` commit from the ledger;
- a connector read proving that commit is remotely reachable;
- branch/ruleset and security-setting evidence;
- bootstrap CI/check-suite result;
- Linear project mapping and one reversible issue/PR synchronization proof; and
- final monorepo gitlink verification after all children are available.

No repository should be marked complete in Linear until those remote reads and
checks exist.
