# Organization `.github` repository relationship rollout

Tracking: DEN-629.

This rollout makes organization-level AI-agent context explicit, deterministic, and reviewable without pretending that a `.github` repository automatically injects arbitrary agent instructions into every checkout.

## Current verified inventory

The connected GitHub installations were queried for the exact repository name `.github` on 2026-08-04. Three mapped organizations currently have a public context repository:

- `fiducia-cloud/.github`
- `shared-auth/.github`
- `sonus-auris/.github`

The central registry contains 30 mapped GitHub owners. Of those, 29 are organizations eligible for the existing organization-repository bootstrap endpoint and one, `ORESoftware`, is a user account. The checked-in inventory therefore produces this fail-closed result:

- 3 existing public organization context repositories;
- 26 missing eligible organization context repositories;
- 1 mapped user account that is not sent to the organization creation endpoint;
- 7 installed but unmapped organizations excluded until their product identity and Linear project are reviewed.

Generated context is not the same as repository creation. CI must keep reporting the rollout as incomplete until every eligible organization has a verified public `.github` repository and its exact-head mirror PR has merged.

## Repository relationship declaration

`render_org_repository_relationships.py` produces `repository-relationships.json` for every mapped owner. Each declaration records only relationships already supported by the reviewed registry:

- the special `<owner>/.github` governance repository and its owner scope;
- the immutable central registry file and commit that generated the declaration;
- the eponymous Linear project mirror;
- the reviewed default runtime repository and runtime allowlist, when one exists;
- exact repository-level Linear project overrides;
- the rule that unregistered dependencies are unknown and must not be assumed.

The manifest deliberately sets `automatic_agent_instruction_inheritance` to `false`. Repository-local `AGENTS.md`, lowercase `agents.md`, Copilot instructions, and narrower path instructions still need to mirror or reference organization context explicitly. Repository-local implementation guidance remains higher precedence than organization-level defaults; the central registry remains authoritative for GitHub/Linear identity and routing.

The renderer emits all 30 owner declarations plus a deterministic index containing SHA-256 hashes for every generated file.

## Rollout audit and bootstrap contract

`audit_org_context_rollout.py` compares the reviewed registry with `config/org-context-rollout-inventory.json` and emits:

- exact existing, missing, visibility-mismatch, unsupported-account-type, and excluded-unmapped counts;
- one dry-run request for each missing eligible organization;
- no request for mapped user accounts;
- no request for unmapped organizations;
- no credentials and no live confirmation values;
- a nonzero exit when `--require-complete` is used before the rollout is actually complete.

The dry-run requests target the existing coordinator endpoint:

```text
POST /v1/github/repositories
```

A generated request is intentionally non-authorizing:

```json
{
  "organization": "3FA-app",
  "name": ".github",
  "visibility": "public",
  "initialization": "readme",
  "description": "Public organization-wide GitHub and Linear context for 3FA-app",
  "dry_run": true
}
```

Live creation remains guarded by the coordinator. It additionally requires authenticated coordinator access, `GITHUB_REPOSITORY_ADMIN_ENABLED=true`, an exact organization allowlist entry, a short-lived GitHub App installation token, `dry_run=false`, and `confirm_repository` equal to `<organization>/.github`. No personal access token belongs in source, workflow YAML, generated artifacts, Linear, logs, or comments.

## Validation

The rollout workflow performs all of the following on the exact pull-request head:

1. compiles the dependency-free registry, relationship, and audit tools;
2. runs unit and mutation tests against the checked-in 30-owner registry and observed inventory;
3. renders and parses all 30 relationship declarations;
4. verifies every index SHA-256 digest;
5. proves all 26 bootstrap requests remain dry-run and omit `confirm_repository`;
6. proves `--require-complete` exits nonzero while 26 eligible repositories are missing;
7. exercises the generated index, audit, and all owner manifests through Playwright Chromium;
8. validates the workflow with a digest-pinned `actionlint` container;
9. uploads public-safe evidence with hidden-file handling enabled.

Local commands:

```bash
python3 -m py_compile \
  scripts/validate_org_project_registry.py \
  scripts/render_org_project_context.py \
  scripts/render_org_repository_relationships.py \
  scripts/audit_org_context_rollout.py \
  tests/test_org_repository_relationships.py

python3 -m unittest -v tests/test_org_repository_relationships.py

python3 scripts/render_org_repository_relationships.py \
  --all \
  --registry-ref "$(git rev-parse HEAD)" \
  --output-dir .artifacts/org-repository-rollout/relationships

python3 scripts/audit_org_context_rollout.py \
  --registry-ref "$(git rev-parse HEAD)" \
  --output .artifacts/org-repository-rollout/rollout-audit.json
```

## Mandatory conflict-resolution contract

> resolve any and all git conflicts semantically, will full context, even looking back 3-10 commits in git log history for more context - never hastily pick sides in a conflict but merge things conceptually, using max context and complete conceptual awareness for a given github organization's repos and external org repos too

The relationship manifest preserves this directive verbatim and requires merge-base inspection, 3–10 relevant commits when available, both sides, path-scoped history, same-organization context, relevant external-organization context, and rejection of wholesale `ours`, `theirs`, current, or incoming selection.

## Publication sequence

1. Merge the central renderer, audit, inventory, tests, and workflow after exact-head CI passes.
2. Render each existing organization mirror against the resulting immutable central `main` commit.
3. Open per-organization PRs that add `repository-relationships.json` and extend integrity verification without weakening repository-specific policy.
4. Use the coordinator dry run for each of the 26 missing eligible organizations.
5. Enable live creation only in reviewed batches with a short-lived GitHub App token and exact organization allowlist.
6. Initialize each new repository on a feature branch, publish the full organization bundle, run exact-head checks, and merge through a reviewed PR.
7. Refresh the inventory by immutable repository ID and owner account ID after each batch.
8. Keep the seven unresolved organizations excluded until their linked Linear identity work is complete.

The checked-in implementation completes the deterministic relationship and rollout-control features. Actual creation of the 26 missing repositories remains an explicit, credentialed deployment action and must not be reported as complete until GitHub verifies those repositories and their mirror PRs.
