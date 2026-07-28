# Cross-organization Linear commit-link pilot

This runbook validates signed GitHub push intake for `sonus-auris` and
`daedalus-fab` after the DEN-453 intake PR is merged and deployed. It does not
store webhook secrets in source, shell arguments, workflow files, or Linear.

## Prerequisites

- coordinator deployment includes the signed push intake;
- unique webhook secrets exist for both organizations;
- the deployment injects those secrets through environment variables;
- `GITHUB_WEBHOOK_ORG_SECRET_ENVS` maps each organization to its secret variable;
- `GITHUB_AUTO_ENQUEUE_PUSHES=true`;
- pilot repositories and default branches are explicitly allowlisted;
- disposable Linear issues exist in the matching Sonus Auris and Daedalus Fab projects.

Recommended pilot repositories:

```text
sonus-auris/sonus-auris-site.web=main
daedalus-fab/daedalus-clients=main
```

## Dry run

The helper accepts the *name* of a secret environment variable, never the secret
value itself. Dry-run output redacts the HMAC signature.

```bash
export GITHUB_WEBHOOK_SECRET_SONUS_AURIS='...'

python scripts/pilot_github_push.py \
  --endpoint=https://coordinator.example/webhooks/github \
  --organization=sonus-auris \
  --repository=sonus-auris/sonus-auris-site.web \
  --commit=<40-or-64-character-commit-id> \
  --issue=<disposable-sonus-linear-issue> \
  --keyword=refs \
  --secret-env=GITHUB_WEBHOOK_SECRET_SONUS_AURIS \
  --dry-run
```

## Execute the intake tests

Run one non-closing push for each organization. Confirm each response returns one
`github_push` job and that the parsed directive is present under
`payload.coordinator.linear_directives`.

Repeat the exact request with the same repository and commit but a different
`--delivery` UUID. The returned job ID must remain unchanged.

Then exercise negative cases against disposable data:

1. wrong organization secret — request must be unauthorized;
2. repository absent from the allowlist — no job is created;
3. non-default branch — event is ignored;
4. force push — event is ignored;
5. all-zero or malformed `after` value — request is rejected;
6. closing keyword — directive is classified as closing, but this intake stage
   must not mutate Linear directly.

## Linear mutation phase

After signed intake is proven, a separate reviewed adapter may apply Linear
references and workflow transitions. Before that adapter is enabled, require:

- issue identifier belongs to the expected Linear workspace and matching project;
- default-branch condition is satisfied for closing directives;
- delivery and commit idempotency are preserved;
- audit records, metrics, retry, and dead-letter recovery are observable;
- invalid or replayed requests produce no duplicate mutation.

## Cleanup

- remove disposable commits/branches where appropriate;
- close disposable Linear verification issues;
- retain only redacted response evidence and job/audit identifiers;
- rotate pilot webhook secrets if they appeared in any terminal recording or log;
- document the final organization/repository allowlist and operational owner.
