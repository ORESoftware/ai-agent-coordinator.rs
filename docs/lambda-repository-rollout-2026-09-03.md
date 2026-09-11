# Ten-organization Rust Lambda repository rollout — September 3, 2026

Tracking: `ORESoftware/ai-agent-coordinator.rs#183`

## Reviewed scope

This execution unit creates or verifies exactly one private `*-lambdas` repository in each of ten organizations:

| Organization | Repository |
|---|---|
| `shared-auth` | `shared-auth-lambdas` |
| `scintilla-run` | `scintilla-lambdas` |
| `fiducia-cloud` | `fiducia-lambdas` |
| `opto-sync` | `opto-sync-lambdas` |
| `quaestor-ledger` | `quaestor-lambdas` |
| `messaging-intel` | `msgint-lambdas` |
| `3FA-app` | `3fa-lambdas` |
| `sonus-auris` | `sonus-auris-lambdas` |
| `zed-pkg` | `zed-lambdas` |
| `canonical-cloud` | `canonical-lambdas` |

The executable manifest is byte-bound to SHA-256 `d5025256feb8834a258199052f1b261358a0ee8cecce4b266b91eab00d7e3df3`. The executor also embeds the exact ordered allowlist and refuses extra, missing, reordered, public, archived, or wrong-owner repositories.

## Delivery flow

1. Validate the exact manifest and render all ten repository templates without network access.
2. After this change is reviewed, tested, and merged to `main`, generate a run-bound 3072-bit RSA keypair on the GitHub-hosted runner.
3. Publish only the run identifier and public key on issue `#183`.
4. Accept exactly one matching RSA-OAEP/SHA-256 ciphertext, decrypt it in runner-temporary storage, mask the credential, and never place it in Git, workflow inputs, artifacts, issues, pull requests, Linear, or logs.
5. Verify that the authenticated identity is GitHub user `ORESoftware` with account ID `11139560` and has active administrator membership in all ten organizations.
6. Create missing repositories as private, auto-initialized repositories. Existing repositories are accepted only when they are empty/bootstrap-only or already match the complete Lambda repository contract.
7. Create a feature branch in every incomplete repository, render the Rust Lambda foundation, run local formatting, Clippy, unit tests, repository-contract validation, and x86_64/arm64 checks, then push and open a pull request.
8. Wait for the four exact-head GitHub Actions checks in each repository: `contract`, `quality`, `architecture-x86_64-unknown-linux-gnu`, and `architecture-aarch64-unknown-linux-gnu`.
9. Re-read the PR head and mergeability immediately before an expected-head squash merge. No failed, stale, conflicted, or draft PR is merged.
10. Verify the merged metadata on each repository's `main` branch and emit a machine-readable receipt requiring ten completed repositories in ten distinct organizations.

## Repository contract

Every repository receives:

- one dependency-light Rust `health` Lambda;
- AWS `provided.al2023` runtime and one-root-`bootstrap` ZIP requirements;
- explicit x86_64 and arm64 support;
- size-oriented release settings and `unsafe_code = "forbid"`;
- pinned Rust dependencies and a committed `Cargo.lock`;
- a `.zpkg.toml` package/toolchain contract;
- immutable `*-lib-core` and `*-orm-core` dependency policy;
- cold-start guidance forbidding eager ORM, UI framework, listener, or unrelated graph initialization;
- pinned GitHub Actions checkout and exact check names;
- credential-shape and merge-marker rejection;
- repository-local `AGENTS.md`, architecture documentation, and machine-readable ownership metadata.

## Conflict and recovery policy

The executor never rebases, force-pushes, resets, or chooses `ours`/`theirs`. A pre-existing non-bootstrap repository, unexpected main-branch file, stale PR head, failed check, or real merge conflict halts the affected rollout. That state must be inspected using repository history and resolved semantically in a normal follow-up branch.

The operation is idempotent. A repository already containing the complete verified contract on `main` is recorded as `already_complete`; a partially created feature branch and open PR are reused only when their exact head still matches the rendered work.

## One-time cleanup

After the receipt proves all ten repositories complete, remove the live workflow and trigger in a separate reviewed cleanup PR. Preserve the manifest, executor, documentation, issue discussion, PR links, merge SHAs, and receipt as durable evidence; remove only the one-time activation surface.
