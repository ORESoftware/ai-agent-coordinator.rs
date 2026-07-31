# MemeBank repository fleet bootstrap

This document tracks the fail-closed creation plan for the canonical repositories
owned by `github.com/memebank`.

**Tracking:** DEN-1005, DEN-1043, and DEN-319  
**Verified state:** blocked on organization authorization and special-name support
as of 2026-07-31 (America/New_York)  
**Creation evidence:** none yet

The ordinary connected GitHub App is not installed on the MemeBank organization,
and the coordinator's repository-administration deployment is disabled. Do not
interpret this manifest, a rendered plan, a pull request, a local archive, or a
Linear issue as evidence that a repository exists or that source was pushed.

The target fleet contains thirteen repositories. Twelve ordinary repository
names are eligible for the coordinator rendering path. The organization metadata
repository `.github` remains machine-readably deferred because the current
coordinator validator rejects leading-dot repository names. It must not be sent
to the coordinator until that validator explicitly supports the exact `.github`
special case, or be created through an authorized organization-administration
path with equivalent review and evidence.

The dedicated `memebank-media-worker.rs` repository is part of the canonical
fleet because OCR, visual labels, captions, deduplication, and image/text
embedding jobs have different scaling, retry, provider-egress, and idempotency
requirements from the synchronous API and web request paths. Omitting it would
silently discard a repository already present in the sealed implementation
source and would collapse an independently deployable failure domain back into
the API server.

## Files

- `repository-fleets/memebank.json` is the canonical machine-readable fleet,
  including active, deferred, and forbidden repository names.
- `scripts/render_repository_fleet.py` validates the active manifest entries and
  renders request bodies for `POST /v1/github/repositories`.
- `scripts/test_render_repository_fleet.py` covers the safety gates.
- `.github/workflows/repository-fleets.yml` compiles, tests, and exercises the
  plan on every relevant pull request.

The renderer performs no network I/O and reads no credentials. This keeps plan
review separate from authenticated execution.

## Canonical target order

Special prerequisite, currently deferred from coordinator rendering:

1. `.github`

Coordinator-renderable batch:

1. `mb-interfaces`
2. `mb-clients`
3. `mb-cli`
4. `memebank-api-server.rs`
5. `memebank-web-server.rs`
6. `memebank-media-worker.rs`
7. `memebank-flutter`
8. `mb-infra`
9. `memebank.github.io`
10. `memebank-mcp-server.rs`
11. `memebank-e2e`
12. `memebank-monorepo`

`mb-infra` is canonical. `memebank-infra` is forbidden because it is a
superseded working name. `homebrew-memebank` is deferred until the release
contract is concrete. The orchestration monorepo is created last so its
`apps/*` gitlinks can target real commits reachable from child `main` branches.

## Review the current plan

```bash
python3 scripts/render_repository_fleet.py \
  --manifest repository-fleets/memebank.json \
  --mode plan
```

The checked-in manifest intentionally has `visibility: null` for every active
repository and `live_creation_enabled: false`. Therefore the current plan must
report twelve visibility blockers, omit `.github` from executable requests, and
must not render an executable request.

Visibility is an explicit product and security decision. Review `public` or
`private` for every active repository in a pull request; do not infer it from a
sibling organization. The deferred `.github` repository requires the same
explicit visibility review when its creation route is enabled.

## Render coordinator dry-run requests

After all active visibility decisions are reviewed:

```bash
python3 scripts/render_repository_fleet.py \
  --manifest repository-fleets/memebank.json \
  --mode dry-run
```

This renders twelve requests with `dry_run: true`. It does not send them. The
authorized executor must submit them to the protected coordinator endpoint and
retain redacted responses. The deferred `.github` repository is intentionally
not present in this output.

A single active repository can be selected during investigation:

```bash
python3 scripts/render_repository_fleet.py \
  --manifest repository-fleets/memebank.json \
  --mode dry-run \
  --repository mb-infra
```

## Render one live request

Live rendering is deliberately one repository at a time. It requires:

1. every active visibility decision to be present;
2. `live_creation_enabled: true` in a reviewed, temporary change;
3. the exact active repository name;
4. the exact `owner/name` confirmation.

Example:

```bash
python3 scripts/render_repository_fleet.py \
  --manifest repository-fleets/memebank.json \
  --mode live \
  --repository mb-infra \
  --confirm-repository memebank/mb-infra
```

The output still performs no network I/O. Submit it only through the protected
coordinator after reviewing the dry-run result for the same repository.

## Required authorization before execution

An owner of the MemeBank organization must:

1. install the ordinary connected GitHub App on `memebank` for post-creation
   metadata, contents, branch, pull-request, issue, and Actions workflows;
2. install or authorize the separate least-privilege repository-administration
   GitHub App for `memebank`;
3. provision a short-lived installation token through the approved secret
   manager;
4. add only `memebank` to `GITHUB_REPOSITORY_ADMIN_ALLOWED_ORGS` for the
   execution window;
5. enable repository administration only after dry-run review;
6. either land and test exact `.github` special-name support in the coordinator
   or authorize an equivalent organization-admin creation path for that one
   repository.

Never paste an installation token into chat, Linear, GitHub issues, source,
manifests, logs, workflow inputs, or Argo parameters.

## Execution and evidence

Create repositories one at a time and immediately verify the returned full name,
repository ID, visibility, URL, initialization result, and default branch.
Initialize each repository on `main`; then add the meaningful baseline through a
feature branch and reviewed pull request. A ZIP or tarball is transport material,
not creation or push evidence.

For `.github`, retain evidence of the exact creation route and prove that no
broader leading-dot repository-name exception was introduced. For the active
batch, preserve the rendered dry-run and exact-confirmation evidence.

Attach the following evidence to DEN-1005 and DEN-1043:

- repository ID and canonical URL for all thirteen targets;
- reviewed visibility;
- first `main` commit SHA;
- baseline pull request and exact merged head;
- CI run or check-suite evidence;
- branch/ruleset evidence;
- exact `.github` creation-route evidence;
- final `memebank-monorepo` gitlink validation.

After the fleet is verified, restore `live_creation_enabled: false`, remove
`memebank` from the temporary repository-admin allowlist if no further creation
is approved, and revoke or allow the short-lived token to expire.
