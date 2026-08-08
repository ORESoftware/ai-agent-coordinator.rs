# Organization homepage standard for humans and AI agents

Tracking: [DEN-629](https://linear.app/denman/issue/DEN-629/bootstrap-organization-level-github-governance-and-community-health) and [GitHub issue #90](https://github.com/ORESoftware/ai-agent-coordinator.rs/issues/90).

## Purpose

A GitHub organization homepage is often the first public context seen by a contributor, operator, reviewer, or AI agent. The profile must therefore do two jobs without becoming a data dump:

1. explain the organization in plain language and help a person find planning, contribution, support, and security information;
2. direct an authorized agent to canonical machine-readable identity, routing, relationship, and instruction sources before it acts.

The reference implementation is `fiducia-cloud/.github`. Product-specific organizations may add stronger local guidance, as `sonus-auris/.github` and `shared-auth/.github` do, but they must preserve the common contract below.

## Required profile contract

Every public `.github/profile/README.md` should contain:

- a descriptive title and a plain-language mission or scope paragraph;
- `Start here` guidance split into `For people` and `For AI agents`;
- human links to the canonical Linear project, contribution guide, support guide, governance notes, and security reporting path;
- agent links to `project-context.yaml`, `repository-relationships.json`, `AGENTS.md`, and `ORG_CONTEXT.md`;
- immutable GitHub owner and Linear project identifiers;
- reviewed default-repository or runtime-allowlist information when one exists;
- authority and precedence rules: the central registry controls identity and routing, while repository-local instructions control implementation;
- fail-closed behavior for unmapped, ambiguous, missing, or contradictory context;
- semantic Git conflict-resolution guidance using the merge base, both sides, path-scoped history, and 3–10 relevant commits when available;
- organization-specific safety, privacy, compatibility, data-integrity, or architecture invariants;
- a public-context boundary excluding credentials, private data, incident details, and security-sensitive topology.

Use [`templates/org-profile-readme.md`](../templates/org-profile-readme.md) as the starting point. Do not copy product-specific claims from one organization into another.

## Source-of-truth precedence

The profile is an index, not an implementation specification.

1. The reviewed central registry is authoritative for immutable GitHub/Linear identity and approved routing.
2. `project-context.yaml` and `repository-relationships.json` are the public machine-readable mirrors.
3. Organization policy files establish shared safety and conflict-resolution requirements.
4. Repository-local `AGENTS.md`, `agents.md`, provider instructions, READMEs, architecture decisions, schemas, migrations, and tests are authoritative for implementation.
5. Exact repository overrides take precedence over owner-level defaults.
6. Unmapped or ambiguous work stops and reports the missing context; it is never guessed.

Organization-level files do not automatically grant access to private repositories or inject arbitrary instructions into every checkout. Repository-local mirrors and the acting principal's existing authorization are still required.

## Human-facing content

Human sections should answer these questions quickly:

- What does this organization build or operate?
- Which public repositories or product surfaces are appropriate starting points?
- Where are priorities and delivery decisions tracked?
- How does someone contribute or request support?
- How is a vulnerability reported privately?
- Which safety or compatibility constraints are especially important here?

Avoid unexplained identifiers and internal jargon in the opening sections. Put immutable IDs and routing details in the canonical identity section.

## Agent-facing content

Agent sections should make the safe discovery sequence explicit:

1. read the machine-readable project context;
2. read the relationship declaration rather than inferring dependencies from names;
3. read organization and repository-local instructions;
4. resolve the exact repository and work item;
5. inspect linked Linear and GitHub context;
6. stop on ambiguity, missing authorization, or contradictory instructions;
7. keep private context out of public output.

Do not claim that an agent has permissions merely because a repository or route is named in a public profile.

## Public-safety boundary

Public profiles may contain public identifiers, links, operating guidance, architecture summaries, and reviewed repository names. They must not contain:

- credentials, tokens, keys, passwords, cookies, OTP material, or session identifiers;
- customer, patient, user, tenant, recording, identity, legal, or payment data;
- private issue text, incident timelines, unpublished vulnerabilities, or internal deliberation;
- private infrastructure topology, allowlist secrets, deployment credentials, or forensic evidence;
- copied content from private repositories unless separately reviewed for publication.

When public and private guidance are both needed, keep the public orientation here and place member-only details in an approved private system.

## Semantic conflict resolution

Profiles should summarize, and policy files should preserve, the mandatory semantic conflict contract:

> resolve any and all git conflicts semantically, will full context, even looking back 3-10 commits in git log history for more context - never hastily pick sides in a conflict but merge things conceptually, using max context and complete conceptual awareness for a given github organization's repos and external org repos too

At minimum, contributors and agents must inspect the merge base and both sides; review 3–10 relevant commits when available; use path-scoped history; consult linked issues, pull requests, architecture decisions, tests, schemas, migrations, documentation, same-organization repositories, and relevant external repositories; reject wholesale `ours`, `theirs`, current, or incoming selection; and preserve compatible intent and safeguards.

## Deterministic rendering

The renderer consumes the public JSON-formatted `project-context.yaml`, injects only explicitly reviewed human-facing text, renders the shared template, validates the result, and writes it atomically. It rejects duplicate JSON keys, user accounts, missing public-context declarations, invalid owner or Linear identity, inconsistent default-repository allowlists, unknown placeholders, and any rendered profile that fails the homepage contract.

```bash
python3 scripts/render_org_homepage.py path/to/project-context.yaml \
  --display-name "Example Organization" \
  --summary "Example Organization builds dependable public services and reusable components for its product portfolio." \
  --public-starting-point "the [public application](https://github.com/example-org/example-app)" \
  --operating-principle "Preserve reviewed privacy, compatibility, and data-integrity constraints." \
  --output path/to/profile/README.md
```

`--public-starting-point` and `--operating-principle` may be repeated. Mission, repository-map, and product-safety claims remain explicit review inputs; the renderer does not infer or fabricate them from repository names.

## Validation

Validate a rendered profile before publication:

```bash
python3 scripts/validate_org_homepage.py path/to/profile/README.md \
  --expect-org example-org
```

The validator checks structural, human-facing, agent-facing, identity, fail-closed, conflict-resolution, public-safety, secret-scanning, placeholder, UTF-8, and final-newline requirements. It does not prove that product claims are true; reviewers must compare those claims with the organization's repositories and planning context.

## Test-organization canaries

Test organizations are not production routing aliases. They use the separate reviewed registry [`config/test-org-homepage-canaries.yaml`](../config/test-org-homepage-canaries.yaml), which records immutable test-owner identity, its own Linear project and GitHub Project, one explicit production parent, and public acceptance scope. The test registry forbids runtime routes and fails closed on missing or ambiguous owners.

A declared parent relationship means only that the test organization provides black-box acceptance or release-certification evidence for the named production organization. It does not grant access to private repositories, production systems, databases, credentials, customer data, incident context, or unpublished topology. The production registry remains authoritative for production identity and runtime routing.

Existing specialized acceptance notes and readiness tables must be preserved. The test context renderer therefore manages only:

- `project-context.yaml`;
- `repository-relationships.json`;
- `ORG_CONTEXT.md`;
- `agents/org-context.agent.md`; and
- `test-org-context-manifest.json`.

The visible profile remains a semantic, reviewed composition. It must add the common human/agent contract without replacing test-specific portfolio, maturity, evidence, or blocked-readiness information.

Validate and render the registry with an immutable central revision:

```bash
python3 scripts/validate_test_org_homepage_canaries.py \
  config/test-org-homepage-canaries.yaml

python3 scripts/render_test_org_homepage_context.py \
  --registry config/test-org-homepage-canaries.yaml \
  --owner claritas-viz-test \
  --registry-ref 0123456789abcdef0123456789abcdef01234567 \
  --output-dir /tmp/claritas-viz-test
```

A test `.github` repository verifies its checked-in managed files and semantic profile by downloading the registry, renderer, validators, and verifier from the same immutable commit, then running:

```bash
python3 scripts/verify_test_org_homepage_context.py \
  --registry config/test-org-homepage-canaries.yaml \
  --owner claritas-viz-test \
  --registry-ref 0123456789abcdef0123456789abcdef01234567 \
  --bundle-dir .
```

The verifier performs exact-byte comparison for every managed file and separately validates the specialized profile. Local additions must go in repository-owned files or reviewed profile sections rather than modifying generated context independently.

## Rollout sequence

1. Confirm the GitHub owner and Linear project mapping in the reviewed central registry.
2. Confirm that the target is an organization and that its public `.github` repository exists or is approved for guarded creation.
3. Render the baseline profile, then semantically add organization-specific mission, repository map, and safety guidance.
4. Preserve existing repository-local policies and generated integrity metadata.
5. Validate the profile and all repository policy workflows.
6. Open a pull request linked to both a Linear issue and a GitHub issue.
7. Merge only after exact-head checks pass and review threads are resolved.
8. Keep unresolved or unmapped organizations fail-closed rather than inventing context.

For test organizations, first re-audit concurrent pull requests, choose a clean canary, preserve specialized acceptance notes, and pin the exact merged central registry commit in the repository verifier workflow. Active fleet-execution, governance, or SDK PRs take precedence over homepage rollout.

The presence of a generated bundle is not evidence that a missing repository was created or that a private repository is accessible.
