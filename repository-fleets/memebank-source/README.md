# Sealed MemeBank repository source

This directory contains the deterministic source payload described by `../memebank-source.json`.

The protected publisher must:

1. Read the ledger from a pinned `ORESoftware/ai-agent-coordinator.rs` commit.
2. Concatenate the chunk files in lexical order.
3. Verify every chunk size and SHA-256 digest.
4. Base64-decode the payload and verify the archive size and SHA-256 digest.
5. Reject absolute paths, parent traversal, links, devices, or files outside `memebank-source/` during extraction.
6. Reconstruct each repository with the fixed Git identity, timestamp, commit message, expected tree, and expected root commit from the ledger.
7. Reconstruct `memebank-monorepo` last, using the ledger’s mode-160000 gitlinks to the exact child repository commits.
8. Fail closed if a nonempty remote differs from the expected root commit; never force-push an unexpected repository.
9. Verify every remote `main` SHA after publication.

`mb-infra` is the sole canonical GitOps repository. The superseded name `memebank-infra` is forbidden.

The payload contains source files only—no `.git` directories, credentials, tokens, private keys, generated user data, model binaries, or application secrets. Publication credentials remain in the protected `ORESoftware/k8s-cluster` AWS/SSM execution boundary and must never be copied into this repository.
