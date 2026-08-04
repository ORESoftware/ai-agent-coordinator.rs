# Authenticated prompt-reconciliation adapters

This DEN-1610 implementation turns an exact reviewed `ReconciliationPlan` into bounded GitHub evidence reads and guarded Linear amendments or creates. It deliberately separates deterministic planning from authenticated execution:

1. `prompt_intake` normalizes authorized user-visible prompt exports without remote writes.
2. `prompt_reconciliation` combines that report with bounded evidence and produces a byte-stable plan.
3. `prompt-reconciliation-apply` revalidates the exact plan bytes, optionally resolves GitHub evidence, and applies only the mutations already present in that plan.

The adapters do not accept raw chat exports, hidden reasoning, arbitrary GraphQL, arbitrary URLs, or dynamically selected repositories.

## Exact-plan authorization

Every invocation requires:

- `--account` equal to the plan's `account_fingerprint`;
- `--digest` equal to the lowercase SHA-256 of the exact plan file bytes; and
- `--confirmation 'APPLY PROMPT RECONCILIATION'`.

A live mutation additionally requires `PROMPT_RECONCILIATION_APPLY_ENABLED=true`. A dry-run intentionally does **not** require that live-write switch, but its authorization object is marked read-only and cannot later be reused with a live Linear client.

The plan is deserialized with unknown-field denial. The authorization is checked again immediately before plan execution, so changing even whitespace in the reviewed plan invalidates the digest.

## GitHub evidence reads

Set:

```text
PROMPT_RECONCILIATION_GITHUB_TOKEN
PROMPT_RECONCILIATION_GITHUB_REPOSITORIES=owner/repo,owner/other-repo
```

`PROMPT_RECONCILIATION_GITHUB_TOKEN` is credential-type agnostic. Prefer a short-lived, repository-scoped GitHub App installation token from the connected App session. A personal access token is not required. The token must exist only in the process environment, must cover every exact allowlisted repository needed by the reviewed plan, and must never be placed in command arguments, the plan, reports, issue text, URLs, logs, or telemetry.

`PROMPT_RECONCILIATION_GITHUB_API_URL` defaults to `https://api.github.com/` and exists only to support explicit loopback mock tests inside the crate.

The client accepts only canonical links of these forms:

```text
https://github.com/<owner>/<repo>/commit/<full-lowercase-40-character-sha>
https://github.com/<owner>/<repo>/pull/<positive-number>
```

Before issuing a request it checks an exact, case-insensitive repository allowlist. Redirects are disabled. Credentials are supplied only through the authorization header and render as `[REDACTED]` under `Debug`. The response body, retry count, delay, and `Retry-After` value are bounded. Only safe reads retry, and returned object IDs and canonical URLs must exactly match the reviewed link.

## Linear reads and writes

Set:

```text
PROMPT_RECONCILIATION_LINEAR_TOKEN
PROMPT_RECONCILIATION_LINEAR_TEAM_ID
PROMPT_RECONCILIATION_LINEAR_AUTH_SCHEME=api_key
```

`PROMPT_RECONCILIATION_LINEAR_API_URL` defaults to `https://api.linear.app/graphql`. `PROMPT_RECONCILIATION_LINEAR_AUTH_SCHEME` may be `api_key` or `bearer`.

For every planned operation the worker:

1. resolves exactly one Linear project with the reviewed name;
2. validates operation IDs, idempotency keys, title, body, issue identifiers, issue URLs, project identity, response sizes, candidate counts, and comment counts;
3. derives a non-secret marker from the exact plan digest and operation ID;
4. searches for an existing canonical issue before create;
5. amends the single existing candidate instead of creating;
6. fails closed when candidate selection is ambiguous;
7. performs a second duplicate search immediately before create to catch a competing writer;
8. creates at most once and verifies that the response has the exact project, title, operation marker, and canonical Linear URL; and
9. treats transport loss, server failures, malformed mutation responses, or GraphQL mutation errors as ambiguous rather than retrying blindly.

An amendment is a marker-bearing comment. This preserves the original issue description and creates an auditable requirements delta. A rerun that finds the marker returns `already_applied` with zero Linear mutations.

Dry-run mode performs the authenticated project and duplicate reads, returns `planned_amend` or `planned_create`, and refuses any mutation request inside the adapter itself.

## Command

```bash
sha256sum reviewed-plan.json

prompt-reconciliation-apply \
  --plan reviewed-plan.json \
  --account '<account-fingerprint>' \
  --digest '<lowercase-sha256>' \
  --confirmation 'APPLY PROMPT RECONCILIATION' \
  --validate-github-evidence \
  --dry-run
```

Remove `--dry-run` only after reviewing the dry-run output and setting `PROMPT_RECONCILIATION_APPLY_ENABLED=true` in the execution environment. Do not put tokens in command arguments, plans, reports, issue bodies, URLs, or telemetry.

The report contains only the account fingerprint, plan digest, operation IDs, canonical issue IDs/URLs, outcomes, and resolved GitHub object metadata. It excludes credentials and prompt bodies.

## Failure and recovery rules

Safe reads may retry only explicit transient transport failures or HTTP 408, 425, 429, 500, 502, 503, and 504 responses, within the configured attempt and delay bounds. Redirects, authentication failures, not-found responses, malformed data, allowlist failures, project mismatches, and ambiguous candidate sets do not retry.

A mutation is never automatically repeated after its request begins. An ambiguous outcome must be recovered by re-running the same exact plan: the update-before-create search and operation marker determine whether the canonical issue already exists. Durable cross-host fencing, compare-and-set receipts, and duplicate-of repair remain the DEN-1611 layer and must not be inferred from this local adapter slice.

## Validation

The unit suite uses loopback-only Axum servers and covers:

- redacted credentials and exact repository allowlisting;
- authenticated commit and pull-request resolution;
- canonical-object mismatch, oversized response, and transient safe-read retry;
- authenticated Linear reads and bounded retry;
- update-before-create;
- final-search duplicate races;
- ambiguous candidate refusal;
- exact rerun no-op behavior;
- project mismatch refusal;
- non-retried ambiguous create and amend outcomes;
- created-object marker verification; and
- mutation-free dry-run behavior.

Repository gates remain authoritative:

```bash
cargo fmt --all -- --check
cargo test --locked --all
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo doc --locked --no-deps
cargo build --locked --release
```
