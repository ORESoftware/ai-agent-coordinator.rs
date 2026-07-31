# Promotion procedure

## 1. Repository authorization

Install the connected GitHub App on `memebank`, create `memebank/mb-infra`, and push this exact reviewed history without rewriting it. Record the repository ID, canonical URL, first `main` commit, pull request, checks, and branch/ruleset evidence in DEN-1021.

## 2. Replace placeholders

For every image and model in `artifact-locks/bundle.json`:

- build or mirror the reviewed artifact;
- pin its immutable digest/checksum;
- attach source commit, SBOM, provenance, license, processor/runtime revision, and compatibility evidence;
- set `bootstrap_placeholder: false` only after the artifact is independently retrievable and verified;
- set `promotable: true` only after the complete compatibility set passes E2E qualification.

Never make a placeholder promotable merely to satisfy validation.

## 3. Add real components

Implement each `component_path` from `control-plane/fleet.json`. Prefer reusable Kustomize bases or pinned Helm charts, but keep environment policy in this repository. Validate Kubernetes schemas and policy with pinned tools, and commit invalid fixtures for every enforced safety rule.

## 4. Materialize children

Run:

```bash
make agent-check
```

Review `.artifacts/rendered/bootstrap/argocd/<environment>/`. Promote the deterministic child Applications into the corresponding canonical paths only with the component manifests and exact source revision they reference.

## 5. Bootstrap local development

Install the pinned Argo CD version into a clean local cluster, apply the rendered `root-application.json`, and wait for explicit child health. Capture:

- cluster/tool versions;
- exact root and child revisions;
- sync/health output;
- default-deny and secret-reference policy evidence;
- CPU-only sanitized import → enrich → index → search result;
- failure and rollback evidence.

## 6. Staging and production

Enable an environment only after its source revision is an immutable canonical commit and its compatibility bundle is promotable. Staging and production auto-sync remain disabled. Production promotion requires reviewed migration, backup/restore, rollout, smoke, SLO, and rollback evidence. Emergency changes must be reconciled back to Git immediately.
