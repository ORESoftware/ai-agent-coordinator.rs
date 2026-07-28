# Signed multi-organization GitHub push intake

The coordinator can turn allowlisted default-branch `push` events into durable
`github_push` jobs. It does not mutate Linear directly in this stage; downstream
workers consume the parsed, audited directives only after webhook policy passes.

## Security model

Push ingestion is disabled by default. Enabling it requires all of the following:

1. `GITHUB_AUTO_ENQUEUE_PUSHES=true`.
2. An explicit `GITHUB_PUSH_ALLOWED_REPOSITORIES` list.
3. A unique webhook secret for every organization, supplied indirectly through
   `GITHUB_WEBHOOK_ORG_SECRET_ENVS`.
4. The corresponding secret environment variables populated by the runtime
   secret manager.

Example configuration:

```text
GITHUB_WEBHOOK_ORG_SECRET_ENVS=sonus-auris=GITHUB_WEBHOOK_SECRET_SONUS_AURIS,daedalus-fab=GITHUB_WEBHOOK_SECRET_DAEDALUS_FAB
GITHUB_WEBHOOK_SECRET_SONUS_AURIS=...
GITHUB_WEBHOOK_SECRET_DAEDALUS_FAB=...
GITHUB_AUTO_ENQUEUE_PUSHES=true
GITHUB_PUSH_ALLOWED_REPOSITORIES=sonus-auris/sonus-auris-site.web,daedalus-fab/daedalus-clients
GITHUB_PUSH_DEFAULT_BRANCHES=sonus-auris/sonus-auris-site.web=main,daedalus-fab/daedalus-clients=main
```

Secret values must remain in the runtime secret manager or environment. The map
contains environment-variable names, not secret values.

## Accepted events

The endpoint verifies `X-Hub-Signature-256` with the organization-specific
secret before enqueueing. Pushes are ignored unless all policy checks pass:

- repository is explicitly allowlisted;
- repository is not a fork;
- branch was not deleted;
- push was not forced;
- `ref` is the configured default branch;
- `after` is a nonzero 40- or 64-character hexadecimal commit identifier.

The idempotency key is scoped to repository plus `after` commit, so replaying a
GitHub delivery under a new delivery ID returns the existing durable job.

## Linear directives

Commit messages are parsed for closing and non-closing Linear magic words.
Parsed directives are stored under
`job.payload.coordinator.linear_directives` with the commit ID, normalized issue
identifier, keyword, and whether the keyword is closing.

Closing keywords:

- `fixes`
- `closes`
- `resolves`
- `completes`
- `implements`

Non-closing keywords:

- `refs`
- `references`
- `part of`
- `related to`
- `contributes to`

A later adapter may apply Linear mutations after branch policy, issue-project
matching, delivery audit, retry, and dead-letter behavior are proven. This intake
stage deliberately stops at a durable job boundary.
