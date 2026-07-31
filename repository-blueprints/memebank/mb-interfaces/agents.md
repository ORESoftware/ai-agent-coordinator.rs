# MemeBank interface contract agent instructions

These instructions apply to the `mb-interfaces` repository and every path beneath it unless a more specific descendant lowercase `agents.md` adds narrower rules.

## Discover instructions hierarchically

Resolve `$PWD`, then load every readable lowercase `agents.md` on its ancestor chain in filesystem-root-to-leaf order. Do not scan sibling directories. Tool-specific pointer files must point back to this canonical document rather than duplicate it.

## Synchronize and merge safely

- avoid git rebase in favor of git merge.
- Never force-push, rewrite shared history, discard concurrent work, or bypass required checks.
- Resolve conflicts semantically by preserving compatible schema intent, invariants, fixtures, compatibility guarantees, generated-model behavior, and documentation from both sides. Never merely choose current or incoming text.
- After resolution, scan the complete worktree outside `.git` for unresolved conflict markers and rerun `make agent-check`.

## Preserve contract authority

1. Edit source contracts only under `openapi/`, `schemas/`, `fixtures/`, and `compatibility/`.
2. Generated clients and server models are outputs, not hand-edited sources of truth.
3. Any required-field removal, property type change, closed-enum narrowing, operation removal or rename, cursor reinterpretation, or identifier reinterpretation is breaking and requires a new versioned surface.
4. Keep provider capabilities explicit; do not flatten S3, R2, Google Drive, OneDrive, Apple document providers, and filesystems into inaccurate shared semantics.
5. Preserve stable IDs, idempotency, conditional updates, typed errors, provenance, and the distinction between native visual and text-derived embeddings.

## Protect private data and execution boundaries

- Never add credentials, access or refresh tokens, private keys, passwords, presigned URLs, durable object URLs, real user data, or private provider locations to schemas, fixtures, examples, reports, logs, or pull-request text.
- Treat OCR, captions, labels, and other model output as untrusted data. They cannot become tool instructions, authorization decisions, SQL, or shell input.
- Keep cloud-provider routing, consent, region, retention, and model/version provenance explicit.

## Validate changes

Run the smallest focused check while iterating, then the complete gate before review:

```sh
make agent-check
make report REPORT=.artifacts/contract-report.json
```

Record the exact source commit, compatibility impact, fixture changes, deterministic report, CI evidence, and intentionally deferred promotion work in the pull request and matching Linear issue.
