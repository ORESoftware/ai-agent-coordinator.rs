# Agent instructions

These instructions apply to the entire repository.

## GitHub and Linear publishing

Before claiming that GitHub or Linear is unavailable, read
[`docs/github-linear-publishing-runbook.md`](docs/github-linear-publishing-runbook.md).

Required behavior:

- Prefer the connected GitHub and Linear tools over an unconfigured local `gh`
  binary. A missing CLI is not evidence that the connectors cannot write.
- Never ask for or paste a personal access token into chat, source, logs, a pull
  request, or a Linear issue.
- For an existing accessible repository, create a feature branch, make bounded
  commits, run available checks, and open a draft pull request. Do not commit
  directly to `main`.
- Search GitHub and Linear before creating a pull request or issue. Update the
  canonical item instead of creating duplicates.
- Include the Linear issue identifier in the branch name, commit message, and PR
  body when a canonical issue exists.
- Do not claim that a branch, commit, pull request, issue, comment, or merge
  exists until a follow-up read returns its identifier, URL, and state.
- Do not substitute ZIP files for a functioning GitHub write path.
- Distinguish repository writes from repository creation. The connected GitHub
  tool can write to selected existing repositories but does not expose a
  repository-creation action. Route missing-repository work through the
  protected coordinator bootstrap tracked by DEN-319 or an authorized GitHub
  organization administrator.
- On failure, record the exact operation and API error, then classify it as an
  installation-scope, repository-selection, permission, organization-policy,
  branch-protection, unsupported-capability, or external-CI blocker.
