# Canonical Rust formatting recovery for Lambda repository rollout

Tracking: `ORESoftware/ai-agent-coordinator.rs#183`

## Partial execution evidence

GraphQL recovery run `33791812817` passed its run-bound credential relay and exact organization/repository authority checks. It then:

1. created private `shared-auth/shared-auth-lambdas`;
2. initialized `main` with the reviewed neutral bootstrap;
3. rendered the full Rust foundation;
4. passed repository-contract validation, Clippy, unit tests, x86_64 checks, and arm64 checks;
5. committed and pushed `feat/issue-183-rust-lambda-foundation` at `213a598`;
6. opened `shared-auth/shared-auth-lambdas#1`;
7. created private `scintilla-run/scintilla-lambdas`; and
8. halted before committing or pushing the Scintilla foundation because `cargo fmt -- --check` found an import-order difference caused by the generated crate name.

The workflow did not merge either repository and destroyed all run-bound credential material. The remaining eight repositories were not created by that run.

## Root cause

The generated health entrypoint emits three imports in a fixed order. Rustfmt's canonical order varies with the generated library crate name. `shared_auth_lambdas` sorts after `serde_json`, while `scintilla_lambdas` sorts before it. A fixed textual order therefore cannot be canonical for every repository name.

This is a generator normalization defect, not a reason to suppress formatting.

## Recovery

The resumed workflow places a narrowly scoped `cargo` compatibility command ahead of the real Cargo binary. For commands other than a Rustfmt check, it delegates the exact argv unchanged. For a command containing `fmt` and `--check`, it:

1. invokes the same pinned Cargo/toolchain and manifest arguments without `--check`, allowing Rustfmt to canonicalize generated source;
2. invokes the original unmodified `fmt ... -- --check` command; and
3. leaves repository-local exact-head CI to run the independent format check again after the canonicalized files are committed and pushed.

The shim does not ignore exit status, remove Clippy or tests, use `|| true`, rewrite Git history, force-push, reset, rebase, or choose one side of a conflict.

## Idempotent resume behavior

- `shared-auth/shared-auth-lambdas` and its open PR are reused. The branch is regenerated, normalized, locally tested, and pushed only when content changes.
- `scintilla-run/scintilla-lambdas` is accepted only because its `main` tree remains the neutral bootstrap. Its unpushed runner-local branch from the failed run no longer exists.
- Missing repositories are created in the original reviewed order.
- Existing non-bootstrap content, a stale PR head, failed exact-head checks, or a merge conflict still halts execution.
- No PR merges until all four repository-local checks succeed at its exact current head.

## New verification

A dedicated test uses a fake Cargo executable to prove that a format-check invocation becomes exactly two calls—canonicalize, then the original fail-closed check—while non-format commands pass through byte-for-byte. The pull-request workflow also reruns the unchanged ten-repository manifest validation, GraphQL canary, source scan, and full coordinator CI before merged execution can resume.
