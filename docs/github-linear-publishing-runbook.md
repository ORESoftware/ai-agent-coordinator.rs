# GitHub and Linear publishing runbook

Tracking: DEN-834, DEN-319, and DEN-315.

This runbook prevents agents from confusing four different states:

1. the ChatGPT connector is connected and permitted to act;
2. the GitHub App is installed on the target account;
3. the target repository exists and is selected for that installation;
4. the requested operation is actually exposed by the connector.

A success or failure at one layer does not prove the state of the others.

## Verified control-plane state

As of July 31, 2026, America/New_York:

- the connected GitHub identity is `ORESoftware`;
- the ChatGPT permission setting for GitHub is **Allow all actions**;
- the ChatGPT permission setting for Linear is **Allow all actions**;
- GitHub branch, file, issue, pull-request, comment, review, merge, and selected
  Actions operations are exposed for accessible existing repositories;
- Linear search, fetch, create/update issue, status, relation, attachment, and
  comment operations are exposed through the connected workspace;
- GitHub repository creation is **not** exposed by the ChatGPT GitHub connector;
- a missing or unconfigured local `gh` executable is therefore not evidence that
  existing-repository connector writes are unavailable.

The GitHub App currently has installations for:

- `ORESoftware` (personal account)
- `3FA-app`
- `akrion-sim`
- `anticaptrad`
- `athlet-o`
- `benefactor-cc`
- `canonical-cloud`
- `claritas-viz`
- `cliptown`
- `daedalus-fab`
- `declarative-migrations`
- `fiducia-cloud`
- `messaging-intel`
- `opto-sync`
- `quaestor-ledger`
- `rust-ssr-demos`
- `sagitta-stack`
- `scintilla-run`
- `shared-auth`
- `sonus-auris`
- `usa-acc`
- `voxletra`
- `zed-pkg`
- `zed-pkg-test`

Recent requested organizations that are not in that installation snapshot include
`hypesiege`, `streempilot`, `memebank`, and `meta-agents-demo`. An agent cannot
write into an uninstalled organization through the connected app. Installing the
app still does not create repositories; it only makes selected existing
repositories eligible for connector access.

Treat this list as a dated observation, not permanent configuration. Always run
the installation and repository preflight below before a mutation.

## Non-negotiable safety and evidence rules

1. Never request, paste, echo, store, or reuse a personal access token in chat,
   GitHub, Linear, source, logs, artifacts, or CI output.
2. Prefer least-privilege GitHub App installation credentials and Linear OAuth.
3. Never commit directly to `main`; create a feature branch and pull request.
4. Never claim a branch, commit, pull request, issue, comment, status change, or
   merge exists until a follow-up read returns its identifier, URL, and state.
5. Never substitute a ZIP archive for a functioning GitHub write path.
6. Never send email to `notifications@linear.app` as a mutation mechanism. Use
   the Linear connector.
7. Search before creating. Update the canonical GitHub or Linear object rather
   than creating a duplicate.
8. Do not expose secrets or protected message contents in diagnostic comments.
9. Record exact API errors and the operation that produced them; do not collapse
   every failure into “GitHub is disconnected.”

## GitHub preflight for an existing repository

Run these checks in order:

1. Resolve the authenticated GitHub identity.
2. List GitHub App installations and verify that the target owner appears.
3. Fetch the exact `owner/repository` metadata.
4. Verify the returned permissions include `push: true` for branch/file writes.
5. Read the repository default branch; do not assume it is `main`.
6. Search existing branches and open/closed pull requests for the intended work.
7. Search Linear for the canonical issue and obtain its identifier.

Interpret results precisely:

- **Owner missing from installations:** GitHub App installation blocker.
- **Owner installed, repository fetch returns 404:** the repository does not
  exist, is not selected for the installation, or the name is wrong.
- **Repository resolves with `push: false`:** effective repository permission
  blocker.
- **Repository resolves with `push: true`, branch creation returns 403:** inspect
  installation permission changes, organization policy, repository rules, and
  account suspension; preserve the exact response.
- **Branch creation returns 422:** the branch may already exist, the base ref may
  be invalid, or the ref name may be rejected. Search before retrying.
- **File create returns 409/422:** the path may already exist or the branch head
  moved. Fetch the file/blob SHA and use an update operation where appropriate.
- **Pull-request creation returns 422:** search for an existing PR from the same
  head, verify the base, and inspect whether the branch has changes.

A repository metadata read is not sufficient proof of write access. Conversely,
a missing `gh` CLI is not proof that connector writes are unavailable.

## Existing-repository publishing workflow

For implementation work against an accessible repository:

1. Search for existing branches, commits, issues, and PRs covering the scope.
2. Fetch the canonical Linear issue, or perform a final duplicate search before
   creating one.
3. Create a branch from the repository's actual default branch. Prefer a name
   such as `agent/den-123-bounded-purpose`.
4. Create or update only the necessary files on that branch.
5. Include `Refs DEN-123`, `Fixes DEN-123`, or `Closes DEN-123` in commit/PR
   metadata according to whether the PR should close the issue when merged.
6. Compare the branch to the base and inspect the changed files.
7. Run available format, lint, tests, schema checks, and security checks.
8. Open a draft PR with scope, evidence, tests, risks, and residual work.
9. Fetch the PR after creation and verify its URL, head, base, draft state, and
   current mergeability.
10. Update Linear with the verified PR URL and exact validation state.
11. Do not report “submitted” or “merged” until a fresh GitHub read confirms it.

If a repository write succeeds, other agents should use the same connector
workflow rather than asking the user to download or manually apply generated
archives.

## Missing organization or repository workflow

The ChatGPT GitHub connector does not expose repository creation. Therefore:

1. List installations and determine whether the target organization is installed.
2. If it is not installed, record an explicit external blocker: install the
   ChatGPT GitHub App on that organization and select the intended repositories
   or all repositories according to policy.
3. If the organization is installed but the repository does not exist, do not
   fabricate a PR URL or claim the repository was created.
4. Route creation through the protected repository-bootstrap capability tracked
   by DEN-319, or an authorized organization administrator using GitHub's UI/API.
5. After creation, verify that the app installation includes the new repository.
6. Only then create branches, files, and PRs through the normal connector path.

For empty organizations such as the requested `hypesiege`, `streempilot`,
`memebank`, or `meta-agents-demo` cases, both steps are required: repository
creation and app installation/selection. Neither can be inferred from the other.

## Linear publishing workflow

Use the Linear connector directly:

1. Search by exact issue identifier when known.
2. Otherwise search by repository, product, requirements, and distinctive scope.
3. Fetch the strongest candidates and compare projects, descriptions, relations,
   attachments, and statuses.
4. Update the canonical issue or add one idempotent evidence comment.
5. Create a new issue only after a final duplicate search shows that no existing
   issue can safely absorb the residual scope.
6. Preserve exact GitHub evidence: repository, branch, PR number, commit, CI, and
   merge state.
7. Fetch the issue after mutation and verify its identifier, URL, project, status,
   and updated content.
8. Report the issue identifier, not merely “documented in Linear.”

A Linear write can succeed even when the GitHub target is externally blocked.
In that case, record the exact GitHub blocker and the next authorized action
rather than pretending implementation landed.

## Cross-system linking

When a canonical Linear issue exists:

- include its identifier in the branch name where practical;
- include it in commit messages and the PR title/body;
- use `Refs` for non-closing work and `Fixes`/`Closes` only when merge should
  actually complete the issue;
- attach or comment the verified PR URL in Linear;
- verify automatic GitHub–Linear linking rather than assuming webhook behavior.

Commit-message linking and PR linking are separate integration paths. A working
PR backlink does not prove that a GitHub push webhook for commit magic words is
configured.

## Failure classification

Every blocked write should be classified into exactly one primary category and
any relevant secondary category:

| Category | Typical evidence | Required response |
|---|---|---|
| ChatGPT permission layer | Plugin set to ask/deny writes | Adjust the explicit plugin permission setting with user authorization |
| GitHub App installation | Owner absent from installation list | Install the app on that account |
| Repository selection | Owner installed, exact repo inaccessible | Add the repository to the installation selection |
| Repository absent | Org exists but repo does not | Use DEN-319 bootstrap or an authorized org admin |
| Effective permission | Repo resolves but write scope is false/403 | Fix App permissions and approve the change on the installation |
| Organization policy | SAML/ruleset/App restrictions | Resolve with org administration; do not use a PAT workaround |
| Branch protection/rules | Ref/update rejected for protected target | Use a feature branch and reviewed PR |
| Connector capability | No action exists, such as repo creation | Route through the dedicated service or admin path |
| External CI/runner | Jobs fail before checkout or have zero steps | Record runner/policy/billing evidence; do not mislabel as code failure |
| Linear duplicate/idempotency | Competing canonical issues/comments | Search, reconcile, transfer unique scope, and establish duplicate relations |

## Minimal smoke tests

Use smoke tests only when the target's effective permissions are uncertain.
Prefer a low-noise existing issue/PR comment when that proves the needed write
scope. When branch/file/PR access must be proven:

1. create a feature branch from the default branch;
2. add a bounded documentation file;
3. open a draft PR linked to the verification issue;
4. confirm CI visibility;
5. close or merge according to the verification issue's acceptance criteria.

DEN-315 is the completed precedent for this flow on
`ORESoftware/k8s-cluster`. Do not repeatedly create new smoke-test tickets when
that evidence already covers the same installation and repository.

## Reporting template

Use this structure in agent responses and Linear comments:

```text
GitHub identity: <login>
Target: <owner/repository>
Installation present: yes/no
Repository resolves: yes/no
Effective push permission: true/false/unknown
Requested operation: <branch/file/issue/PR/repo creation/etc.>
Result: <verified URL/state or exact API error>
Linear issue: <DEN-id and URL>
Classification: <one primary blocker category>
Next authorized action: <specific action>
```

This evidence-first format lets another thread resume the work without repeating
incorrect authentication assumptions or asking the user for unusable ZIP files.
