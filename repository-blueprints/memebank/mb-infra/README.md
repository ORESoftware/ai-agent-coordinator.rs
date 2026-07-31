# mb-infra

Canonical GitOps control-plane source for MemeBank environments, artifact/model promotion, worker isolation, and Argo CD app-of-apps ordering.

> **Promotion boundary:** this directory is a reviewed staging blueprint while `github.com/memebank/mb-infra` is unavailable to the connected GitHub App. It is not the canonical repository, it has not converged a cluster, and its placeholder artifact digests are deliberately marked non-promotable. Promotion is complete only after the exact reviewed history is pushed to the canonical repository and real cluster evidence is attached to DEN-1021.

The superseded name `memebank-infra` is forbidden. There must be one canonical control-plane repository: `memebank/mb-infra`.

## What this blueprint establishes

- an explicit, acyclic Argo CD child graph with reviewed sync waves;
- aligned `dev`, `staging`, and `prod` environment contracts;
- separate local-CPU, local-GPU, and cloud-dispatch worker security profiles;
- immutable image and model lock shapes with provenance and compatibility metadata;
- fail-closed production promotion rules;
- secret-reference and egress boundaries that keep cloud credentials away from local inference workers;
- deterministic root/child Application rendering and a machine-readable validation report;
- committed negative tests for mutable production references, plaintext secrets, privilege, dependency-order drift, and incomplete model locks.

## One-command gate

A clean checkout needs Python 3.12 or newer:

```bash
make agent-check
```

The command validates the control-plane documents, runs the negative/positive unit suite, and deterministically renders the three environment plans under `.artifacts/rendered/`.

To inspect the report:

```bash
make report
```

## Source layout

```text
artifact-locks/bundle.json                  compatibility bundle and immutable artifact metadata
bootstrap/argocd/root-application.template.json
components/README.md                        component ownership and promotion contract
control-plane/fleet.json                    child Applications, dependency graph, and sync waves
control-plane/worker-profiles.json           isolation, resources, probes, scaling, secrets, and egress
environments/{dev,staging,prod}.json         cluster binding and promotion policy
scripts/validate_infra.py                    fail-closed policy and provenance validator
scripts/render_infra.py                      deterministic Argo Application renderer
```

The renderer writes the eventual canonical shape at:

```text
.artifacts/rendered/bootstrap/argocd/<environment>/
  root-application.json
  children/
    kustomization.json
    applications/<child>.json
  plan.json
```

Generated output is review evidence, not an alternate source of truth. After repository authorization, reviewed generated children may be promoted into `bootstrap/argocd/<environment>/` together with real component manifests and artifact locks.

## Deliberate fail-closed state

- `dev` is renderable with explicitly marked bootstrap placeholders but is not promotion-ready.
- `staging` and `prod` are disabled until a non-placeholder compatibility bundle and real canonical commit SHA exist.
- `prod` cannot enable automated synchronization.
- Local workers cannot carry cloud-provider secrets or unrestricted egress.
- Cloud dispatch cannot be enabled without both scoped secret references and a reviewed endpoint allowlist.
- No environment becomes promotion-ready while any image, model, interface revision, SBOM, provenance statement, or source commit is a placeholder.

## Remaining evidence before DEN-1021 can close

The canonical repository must exist, the GitHub App must be authorized, real service images and model artifacts must replace placeholders, component manifests must render with pinned Kubernetes tooling, and a clean local cluster must install Argo CD, apply the root Application, converge, and pass sanitized import → enrich → index → search smoke tests. Staging and production additionally require policy, backup/restore, migration, rollout, and rollback evidence.
