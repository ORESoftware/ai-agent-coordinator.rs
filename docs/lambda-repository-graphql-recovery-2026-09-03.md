# GraphQL recovery for the ten-organization Lambda repository rollout

Tracking: `ORESoftware/ai-agent-coordinator.rs#183`

## Incident

The first merged execution, workflow run `33790145792`, completed all plan validation and the run-bound credential relay. Before creating any target repository, the authenticated account's REST core budget rejected the first identity read with HTTP 403. The workflow then destroyed the ephemeral private key, decrypted credential file, and ciphertext file. No target repository, feature branch, or pull request was created during that attempt.

## Recovery decision

Do not weaken validation, bypass review, add a broad persistent secret, or wait blindly inside every REST call. Preserve the merged executor and interpose a narrow compatibility command that translates only its reviewed GitHub API surface to GraphQL:

- viewer identity;
- organization administrator and repository-creation authority;
- repository existence;
- private repository creation;
- open pull-request discovery and creation;
- exact-commit status-check rollup;
- pull-request head, draft, and mergeability state;
- expected-head squash merge; and
- merged file verification.

Git clone, fetch, commit, branch merge, and push continue over the Git transport. Unknown commands or API shapes delegate to the real GitHub CLI rather than being guessed.

## Repository initialization

GraphQL repository creation produces an empty private repository. The recovery layer initializes `main` with a neutral README through a normal Git commit and push. The original executor then inspects that tree, creates `feat/issue-183-rust-lambda-foundation`, renders and tests the complete Rust scaffold, pushes it, and opens the repository-local pull request.

## Tests and gates

The recovery pull request must prove:

1. shell syntax for the original executor and compatibility layer;
2. a static one-to-one mapping between every reviewed executor API call and a compatibility handler;
3. live GraphQL viewer and current-repository reads using the pull-request workflow's own restricted token;
4. unchanged manifest digest and successful rendering of all ten repository contracts;
5. no admin bypass, rebase, reset, force push, or credential-shaped source;
6. exact-head repository-wide CI, dependency audit, PostgreSQL integration, repository-admin E2E, and OCI tests; and
7. zero branch drift and zero unresolved review threads before merge.

Only the merged `main` revision may request a new run-bound RSA-OAEP/SHA-256 ciphertext and retry the idempotent rollout.

## Cleanup

After ten repositories are verified on `main`, remove both one-time live workflows and both trigger files in a reviewed cleanup pull request. Preserve the immutable manifest, executor, compatibility layer, tests, runbooks, issue history, workflow run references, repository-local PR links, tested head SHAs, merge SHAs, and final receipt.
