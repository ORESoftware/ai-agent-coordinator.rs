# Nightly multi-organization Codex maintenance

Tracking: **DEN-801**.

This workflow runs bounded coding maintenance across the GitHub organizations in
`config/org-project-registry.yaml`. That registry remains the authority for the
GitHub owner, GitHub App installation, Linear project, and optional default
repository; the scheduler does not maintain a second organization list.

## Schedule and concurrency

The workflow is scheduled for **01:00 America/Lima** with GitHub Actions'
native IANA-timezone schedule field. The organization matrix uses
`max-parallel: 3`, so no more than three organizations execute at once. Each
organization job has a 30-minute hard timeout and a 20-minute model work budget.
At 40 organizations and 10–20 minutes of work per organization, the idealized
three-worker makespan is about 2 hours 13 minutes to 4 hours 27 minutes, plus
queue, setup, CI, API, and retry overhead.

Every mapped organization whose login ends in `-test` is processed as its own
matrix entry. A base organization records a paired `<owner>-test` organization
only when that test organization is also present in the canonical registry.
Unmapped test organizations are never inferred or mutated.

## Per-organization behavior

For each organization, the workflow:

1. mints a short-lived GitHub App installation token scoped to that owner;
2. reads a bounded repository/open-PR inventory and the mapped Linear backlog;
3. gives Codex only a sanitized snapshot and asks for one to three low/medium-risk
   PR tasks plus up to three existing-PR decisions;
4. validates the structured plan against the snapshot;
5. clones only selected repositories into an isolated workspace;
6. runs Codex offline with workspace-only write access;
7. validates changed paths, size, metadata, conflict markers, credential-shaped
   content, tests, and protected-area review flags;
8. pushes feature branches and opens **draft** PRs labeled `agent:nightly`;
9. freshly re-reads existing merge candidates and merges only those satisfying
   every deterministic gate;
10. uploads a redacted per-organization ledger.

If an organization has no eligible repository, the job records a blocked ledger
rather than fabricating work. New draft PRs are not merged in the same run. A
later run may merge them after they are non-draft, labeled, current, mergeable,
review-compliant, and green.

## Merge and conflict policy

An existing PR can be auto-merged only when all of these are true at publication
time:

- it still has the planned head SHA;
- it carries the `agent:nightly` label;
- it is not a draft;
- GitHub reports it mergeable and `CLEAN`;
- all reported checks are completed with success, skipped, or neutral results;
- no review has requested changes;
- protected paths have an approved review.

Conflicted PRs are never resolved with blanket `ours`/`theirs`, timestamp choice,
force-pushes to another author's branch, or conflict-marker deletion. Valuable
intent is reconstructed on current `main` in a new semantic replacement PR, with
the original PR number and head SHA recorded as evidence.

## Credential boundary

Codex never receives GitHub or Linear credentials. Deterministic controller steps
perform snapshots and publication. Snapshot and workspace GitHub App tokens are
explicitly revoked before each model invocation. Linear credentials and the App
private key exist only on controller steps. Codex runs as a dedicated unprivileged
OS user against sanitized files and isolated checkouts; the planner is read-only
and the implementer can write only inside its workspace, with network access
disabled by their permission profiles.

Configure the protected GitHub Actions environment `nightly-org-maintenance` with
the following values. The workflow requests environment secrets without creating
a deployment record:

- repository variable `PORTFOLIO_GITHUB_APP_ID`;
- secret `PORTFOLIO_GITHUB_APP_PRIVATE_KEY`;
- secret `LINEAR_API_TOKEN`;
- optional variable `LINEAR_API_AUTH_SCHEME` (`api_key` by default, or `bearer`);
- secret `OPENAI_API_KEY`;
- optional variable `NIGHTLY_CODEX_MODEL`.

The GitHub App installation needs access to the registry organizations and the
minimum repository permissions required to read metadata/checks and create
branches, labels, pull requests, and merges. Do not use a personal access token.

## Manual operation

`workflow_dispatch` defaults to `dry-run`, which validates and prints the selected
organization matrix without running Codex or mutating GitHub. Choose `enqueue` to
run it. The optional `owners` input accepts a comma-separated, exact subset of
registry logins for a canary run.

The workflow is active only after its implementation PR is merged and the listed
Actions environment configuration exists. A missing installation, repository,
secret, environment variable, Linear project, or permission fails closed and is
reported; it is never interpreted as permission to broaden access.
