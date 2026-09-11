# Merge-payload recovery for ten-organization Lambda rollout

Tracking: `ORESoftware/ai-agent-coordinator.rs#183`

## Prepared repository state

GraphQL/Rustfmt recovery run `33793011410` completed the repository-preparation phase for the complete reviewed set:

- all ten private `*-lambdas` repositories exist;
- every repository has `feat/issue-183-rust-lambda-foundation` pushed;
- every repository has pull request `#1` open;
- the runner locally passed contract validation, canonical Rust formatting, Clippy with warnings denied, unit tests, `x86_64-unknown-linux-gnu`, and `aarch64-unknown-linux-gnu` checks before each push; and
- the runner reached the exact-head merge pass only after the complete set was prepared.

No target pull request was merged by that run.

## Failure and semantic correction

The first exact-head target, `shared-auth/shared-auth-lambdas#1`, had all four repository-local Actions checks green. The merge compatibility layer then issued a GraphQL mutation selecting `mergeCommit` directly from `MergePullRequestPayload`. GitHub rejected the query because that payload exposes the returned `pullRequest`; the merge commit is a field on that returned pull request.

The corrected selection is:

```graphql
mergePullRequest(input: $input) {
  pullRequest {
    merged
    mergedAt
    mergeCommit {
      oid
    }
  }
}
```

The corrected JSON path is:

```text
.data.mergePullRequest.pullRequest.mergeCommit.oid
```

## Narrow compatibility layer

Rather than rewrite the already validated repository, pull-request, check-rollup, and Git compatibility behavior, the recovery adds a merge-only wrapper. It delegates every non-merge command byte-for-byte to the existing GraphQL fallback. For an exact merge endpoint it:

1. parses the expected 40-character head SHA;
2. rereads the pull request ID, current head, draft state, open state, and mergeability;
3. rejects a stale head, draft, closed PR, unknown/conflicting mergeability, or malformed endpoint;
4. invokes `mergePullRequest` with `expectedHeadOid` and `SQUASH`;
5. reads `mergeCommit.oid` from the returned pull request; and
6. returns the same normalized merge receipt expected by the original executor.

It contains no administrative bypass, force push, reset, rebase, history rewrite, or check suppression.

## Regression evidence

A fake-GraphQL test proves:

- the corrected schema selection and JSON path;
- successful normalization of a 40-character merge SHA;
- fail-closed stale-head behavior before the mutation result is accepted;
- exact delegation of non-merge commands to the existing compatibility layer; and
- absence of the obsolete payload-level `mergeCommit` selection and path.

The pull-request workflow also reruns the immutable manifest digest, all ten rendered repository contracts, Bash syntax, the existing GraphQL and Cargo compatibility tests, a live GraphQL viewer/repository canary, credential scanning, merge-marker scanning, and the coordinator's complete exact-head CI.

## Idempotent final pass

The merged recovery reuses all ten repositories, branches, and pull requests. It regenerates and locally verifies each exact branch without recreating it, waits for the four named repository checks, rereads the current PR head and mergeability immediately before each expected-head squash merge, verifies `lambda-repository.json` on `main`, and emits a receipt that is complete only for ten repositories in ten distinct organizations.

Any newly failed check, changed branch head, draft state, conflict, or unexpected repository content remains a hard stop. A partial merge is reconciled on the next idempotent run; it is never replayed blindly.
