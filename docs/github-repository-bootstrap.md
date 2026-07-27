# GitHub repository bootstrap

This capability implements DEN-319: a fail-closed way for the coordinator to create organization repositories without storing a broad credential in source, configuration files, logs, tickets, or responses.

## Security model

Repository creation is disabled by default. A live request requires all of the following:

1. normal coordinator bearer-token authentication;
2. `GITHUB_REPOSITORY_ADMIN_ENABLED=true`;
3. the target organization in `GITHUB_REPOSITORY_ADMIN_ALLOWED_ORGS`;
4. a short-lived token in `GITHUB_REPOSITORY_ADMIN_TOKEN`;
5. `dry_run: false` in the request;
6. `confirm_repository` exactly equal to the requested `organization/name`.

Use a GitHub App installation token with repository **Administration: write** permission. Do not use a token with access to organizations that are not present in the coordinator allowlist.

The coordinator never returns or logs the token. GitHub API errors are bounded before they are returned.

## Endpoint

`POST /v1/github/repositories`

The endpoint is idempotent by repository name. Before creating anything, it reads `GET /repos/{organization}/{name}`. If the repository already exists with the requested visibility, it returns the existing repository with `existing: true`. A visibility mismatch is rejected.

### Required request fields

- `organization`: exact GitHub organization login;
- `name`: exact repository name;
- `visibility`: `private` or `public`;
- `initialization`: `readme` or `empty`.

`dry_run` defaults to `true`. Unknown JSON fields are rejected.

The create request enables issues, disables projects and wikis, allows squash and merge commits, and disables rebase merges. Repository implementation should continue on a feature branch and land through a reviewed pull request.

## Dry-run example

```bash
curl http://localhost:8080/v1/github/repositories \
  -H "Authorization: Bearer $COORDINATOR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "declarative-migrations",
    "name": "declarative-migrations-monorepo",
    "visibility": "private",
    "initialization": "readme",
    "description": "Organization monorepo for declarative migration tooling"
  }'
```

Because `dry_run` is omitted, this cannot create a repository.

## Live creation example

Review the dry-run response first. Then enable only the required organization and provide a short-lived token:

```bash
export GITHUB_REPOSITORY_ADMIN_ENABLED=true
export GITHUB_REPOSITORY_ADMIN_ALLOWED_ORGS=declarative-migrations
export GITHUB_REPOSITORY_ADMIN_TOKEN='short-lived-installation-token'
```

Submit the exact confirmation string:

```bash
curl http://localhost:8080/v1/github/repositories \
  -H "Authorization: Bearer $COORDINATOR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "organization": "declarative-migrations",
    "name": "declarative-migrations-monorepo",
    "visibility": "private",
    "initialization": "readme",
    "description": "Organization monorepo for declarative migration tooling",
    "dry_run": false,
    "confirm_repository": "declarative-migrations/declarative-migrations-monorepo"
  }'
```

After creation:

1. create a feature branch from the initialized default branch;
2. add repository files and real mode-`160000` application gitlinks;
3. add pinned, read-only CI and contract checks;
4. open a draft pull request;
5. merge only after exact-head checks pass;
6. resolve any conflicts semantically and scan for conflict markers.

## Validation covered by unit tests

- dry-run defaults on;
- live mode requires exact confirmation;
- organization matching is allowlisted and case-insensitive;
- path-escape repository names are rejected;
- public/private visibility is explicit;
- rebase merges are disabled in the GitHub creation body.
