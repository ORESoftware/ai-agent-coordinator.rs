# Cross-organization Linear commit-link pilot

This runbook validates signed GitHub push intake and protected Linear delivery for
`sonus-auris` and `daedalus-fab`. The implementation is split deliberately:

1. signed GitHub pushes create durable `github_push` jobs;
2. dry-run planning validates issue, team, project, branch, commit, and directive policy;
3. a separately reviewed activation may deliver idempotent Linear references and
   approved workflow transitions.

No webhook secret, Linear token, HMAC, or other credential belongs in source,
manifest values, shell arguments, workflow files, Linear, or chat.

## Pilot overlay

The base deployment remains fail-closed. The pilot is selected explicitly with:

```text
deploy/k8s/overlays/cross-org-linear-pilot
```

The overlay enables only these repositories and default branches:

```text
sonus-auris/sonus-auris-site.web=main
daedalus-fab/daedalus-clients=main
```

It enables multi-organization push intake and the Linear delivery worker while
keeping:

```text
LINEAR_DELIVERY_DRY_RUN=true
```

The overlay does **not** configure completed-state IDs, so a closing directive
cannot be activated accidentally through the checked-in dry-run policy.

## Protected secret bundle

Before applying the overlay, provision this remote secret object through the
cluster's approved secret-management path:

```text
dd/remote-dev/ai-agent-coordinator-linear-pilot
```

It must contain exactly these properties:

```text
LINEAR_API_TOKEN
GITHUB_WEBHOOK_SECRET_SONUS_AURIS
GITHUB_WEBHOOK_SECRET_DAEDALUS_FAB
```

The checked-in `ExternalSecret` contains only references to those properties.
Use different high-entropy webhook secrets for the two organizations. A Linear
token is not needed for local planning, but the protected bundle is provisioned
before cluster rollout so the same deployment can later be activated through a
separate reviewed policy change.

## Render and apply

Inspect the rendered deployment before applying it:

```bash
kubectl kustomize deploy/k8s/overlays/cross-org-linear-pilot > /tmp/linear-pilot.yaml
```

Verify that the rendered output contains an `ExternalSecret`, not a plaintext
`Secret`, and that the Deployment carries:

```text
oresoftware.dev/cross-org-linear-pilot=dry-run
```

Apply only after the protected remote secret exists:

```bash
kubectl apply -k deploy/k8s/overlays/cross-org-linear-pilot

kubectl -n ai-agent-coordinator wait \
  --for=condition=Ready \
  externalsecret/ai-agent-coordinator-linear-pilot \
  --timeout=120s

kubectl -n ai-agent-coordinator rollout status \
  deployment/ai-agent-coordinator \
  --timeout=180s
```

If the ExternalSecret is not Ready, stop. Do not make the secret reference
optional and do not paste a credential into a Kubernetes manifest.

## Send signed pilot pushes

The helper accepts the *name* of a secret environment variable, never a secret
value. Dry-run output redacts the HMAC signature.

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

After reviewing the redacted preview, remove `--dry-run` to send the signed
request. Repeat for Daedalus Fab with its own secret environment variable.

Confirm each response returns one `github_push` job and that the parsed directive
is present under `payload.coordinator.linear_directives`. Repeat the same
repository and commit with a different delivery UUID; the returned job ID must
remain unchanged.

## Review Linear delivery plans

For every accepted job, call the authenticated planning endpoint:

```bash
curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${COORDINATOR_API_TOKEN}" \
  "https://coordinator.example/v1/linear/plan/JOB_ID"
```

The response must report `dry_run: true`, the exact organization/repository,
the configured `main` branch, and the expected `reference` or
`reference_and_transition` action. Planning performs no Linear request, does not
claim the job, and does not modify the mutation ledger.

Calling `/v1/linear/deliver-next` while this overlay is active must fail because
dry-run is enabled. The job must remain queued with zero delivery attempts.

## Negative matrix

Exercise these cases against disposable data:

1. wrong organization secret — request is unauthorized;
2. repository absent from the allowlist — no job is created;
3. non-default branch — event is ignored;
4. force push — event is ignored;
5. deleted branch — event is ignored;
6. all-zero or malformed `after` value — request is rejected;
7. fork repository — event is ignored;
8. duplicate commit with a different delivery UUID — no duplicate job;
9. closing keyword — planned as `reference_and_transition`, but no mutation occurs.

Retain only redacted job IDs, plan output, workflow URLs, and audit evidence.

## Live activation is a separate change

Do not edit the checked-in pilot overlay in place during testing. A separate
reviewed activation must:

1. prove both dry-run plans resolve to the correct Linear projects;
2. configure exact completed-state UUIDs only for organizations allowed to use
   closing directives;
3. set `LINEAR_DELIVERY_DRY_RUN=false`;
4. process one non-closing canary before any closing directive;
5. verify attachment/comment idempotency and retry/dead-letter behavior;
6. retain a one-step rollback to dry-run.

## Cleanup and rollback

To stop ingestion and mutation, restore the base deployment:

```bash
kubectl apply -k deploy/k8s
kubectl -n ai-agent-coordinator rollout status deployment/ai-agent-coordinator
```

Then:

- close disposable Linear verification issues;
- remove disposable commits or branches where appropriate;
- retain only redacted evidence and job/audit identifiers;
- rotate either webhook secret if it appeared in a terminal recording or log;
- rotate the Linear token after any suspected exposure;
- document the final organization/repository allowlist and operational owner.
