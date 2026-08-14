# Deep-test fleet Zed v0.2.1 manifest migration

**Linear:** DEN-3717
**Parent:** DEN-2056
**Scope:** 25 `*-test` organizations x 4 repositories = 100 root manifests

## Why this migration is required

The original 2026-08-08 generator emitted a pre-contract manifest with a combined
`package.name`, an unsupported `package.type`, and a removed `[develop]` table. The
official Zed v0.2.1 validator rejects that shape before it can validate package
identity or repository provenance.

The canonical template now declares separate lowercase `package.org` and
`package.name` fields, the exact GitHub repository URL, Python language metadata,
safe publish exclusions, an explicit no-op ecosystem adapter, and the single
supported `scripts.test` entry. The test command preserves both original commands.
For the one repository with additional reviewed commands, it preserves the complete
four-command sequence instead.

`opto-sync-test/contract-conformance-tests` was independently expanded after the
bootstrap. Its reviewed `0.2.0` identity, Rust/TypeScript/Dart description, and all
four test commands are recorded as an explicit fleet override and preserved in the
new manifest. The four earlier `zed-pkg-test` identity-only migrations are also
recognized as exact approved predecessors rather than being mistaken for drift.

## Fail-closed rollout boundary

The live publisher accepts an existing `.zpkg.toml` only when its Git blob hash is
byte-for-byte equal to a reviewed predecessor for that exact repository. A
repository already carrying the new template is an idempotent success. Any third
shape, a missing repository, an unexpected owner or visibility, or a non-admin
organization membership aborts before mutation; a later branch/PR head race stops
that repository without overwriting it. Existing
repositories publish only `.zpkg.toml`; independently changed tests, documentation,
workflows, and application code are outside the mutation set and remain untouched.

Each changed manifest lands through a repository-local branch and pull request. The
publisher waits for the exact-head GitHub Actions `verify` check, accepts only a
`success` conclusion, squash-merges only that successful head, and then re-reads the
default-branch tree to prove the canonical blob landed.
No force update, history rewrite, deletion, production credential, or application
code change is part of this migration.

## Validation before activation

- all 100 generated manifests parse as TOML and satisfy the canonical template
  assertions;
- all four representative generated deep-test suites pass;
- the complete publisher/fleet unit suite passes;
- the official Zed v0.2.1 binary accepts every generated manifest;
- the live remote preflight proves every source blob is either the exact legacy
  template or already migrated.

The authenticated 2026-08-14 live preflight found all 100 repositories and
classified all 100 default-branch manifests as exact approved predecessors, with
zero missing repositories, unknown source blobs, or writes.

## Live rollout evidence

The authenticated rollout opened repository-local pull requests from the exact
reviewed predecessor blobs and merged 99 manifests after their exact-head
`verify` checks succeeded. `opto-sync-test/contract-conformance-tests` had
independently strengthened its repository verifier after the original bootstrap,
so its first pull request correctly stopped instead of weakening or bypassing the
new check.

That repository was reconciled semantically: commit
`a5524ea5b289d9abe5ffa96ed3e9df0c18573a6f` moved the temporary Rust-workspace
lifecycle assertion from the removed `[develop]` table to the supported
`scripts.test` contract while retaining the Python, Node, and cross-language
matrix commands. Its verifier, 18 Python tests, two Node lifecycle tests, official
Zed validation, and both exact-head GitHub Actions checks passed before pull
request `opto-sync-test/contract-conformance-tests#5` squash-merged as
`58ecba6d48b3f776d613353f61d83bea111c9949`.

The final authenticated idempotency run then re-read all 100 default branches and
classified every repository as `already_initialized`: 100/100 verified, zero
open migration pull requests, zero missing repositories, and zero failures. The
result ledger is `deep-test-zpkg-v021-final-reconciled.json`; it contains no
credential material and records the exact default-branch commit for every
repository.
