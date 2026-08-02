# GitHub organization context and Linear project mapping

Tracking: DEN-629, DEN-1298, DEN-1320, and DEN-1339.

## Outcome

`config/org-project-registry.yaml` is the canonical, reviewed identity registry for GitHub owners and Linear projects. It records immutable GitHub account IDs and Linear project UUIDs, exact repository-level overrides, current runtime-route pointers, aliases, and explicit unresolved gaps.

The file uses JSON syntax as a strict YAML 1.2 subset. This keeps the requested `.yaml` contract while allowing dependency-free, duplicate-key-aware validation and stable canonical hashing.

The registry is authoritative for identity and routing. Generated GitHub organization files and Linear documents are mirrors, not competing sources of truth.

## GitHub surfaces

GitHub now offers several different organization-level surfaces; they do not have identical behavior.

### Organization custom instructions

GitHub organization owners can enter Copilot custom instructions under **Organization settings → Copilot → Custom instructions**. This is the closest true, non-version-controlled organization-wide instruction field. It requires Copilot Business or Enterprise and currently affects Copilot Chat, code review, and cloud agent on GitHub.com. It is not a portable context source for Codex, Claude, Gemini, local tools, or arbitrary MCP clients.

Keep the settings text short: identify the canonical Linear project, point to the `.github/project-context.yaml` mirror, require repository-local instructions, and require fail-closed resolution. Do not duplicate long project context there.

Reference: [Adding organization custom instructions for GitHub Copilot](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/add-custom-instructions/add-organization-instructions).

### Special `.github` repository

A public repository named `.github` is the durable, discoverable organization anchor.

* `profile/README.md` is displayed on the organization profile.
* `project-context.yaml` is the generated machine-readable GitHub ↔ Linear mapping.
* `agents/org-context.agent.md` defines an organization-level Copilot custom agent.
* Supported community-health files can become defaults for repositories that do not define their own versions.

GitHub requires the `.github` repository to be public for most organization-wide community defaults. Therefore every generated file must be safe for public disclosure. Private architecture, customer data, incident details, credentials, and operational secrets belong elsewhere. If an organization later needs private Copilot custom-agent material, use a separate `.github-private` repository and preserve the public mapping as the stable discovery anchor.

References: [default community-health files](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file) and [organization-level custom agents](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/create-custom-agents).

### Repository-local instructions remain necessary

GitHub does not inherit arbitrary `AGENTS.md`, `agents.md`, or `.github/copilot-instructions.md` files from an organization `.github` repository into every repository clone or agent workspace. Repository-local instructions remain the portable mechanism for build, test, architecture, and path-specific rules.

Precedence is therefore deliberate:

1. exact repository overrides choose the Linear project when present;
2. the owner mapping chooses the organization-level Linear project otherwise;
3. repository-local instructions control implementation details;
4. organization context supplies broad public policy and identity;
5. missing or ambiguous mappings are rejected rather than guessed.

This complements, rather than replaces, DEN-114, DEN-282, and DEN-292.

## Mandatory semantic Git conflict policy

Every generated `project-context.yaml`, organization profile, custom agent, and Linear **AI Agent Context** mirror carries this directive verbatim:

> resolve any and all git conflicts semantically, will full context, even looking back 3-10 commits in git log history for more context - never hastily pick sides in a conflict but merge things conceptually, using max context and complete conceptual awareness for a given github organization's repos and external org repos too

The executable interpretation is deliberately fail-closed:

1. inspect the merge base and 3–10 relevant commits from both sides when available;
2. use path-scoped history for every conflicted file and read linked pull requests, Linear issues, architecture decisions, migrations, schemas, tests, and documentation;
3. inspect relevant repositories in the same GitHub organization and relevant repositories in external GitHub organizations whenever APIs, SDKs, schemas, deployment contracts, or shared libraries cross repository boundaries;
4. never blindly or wholesale select `ours`, `theirs`, current, or incoming, and never discard one side without conceptual analysis;
5. produce a conceptual merge that preserves compatible intent, invariants, APIs, schemas, migrations, tests, documentation, security controls, and operational safeguards;
6. record non-obvious decisions, scan the entire worktree for conflict markers, and run every affected validation contract.

“Max context” means all relevant context the acting principal is authorized to read. It does not authorize exposing credentials, private data, customer information, or hidden reasoning. When required context is inaccessible or contradictory, the agent must identify the blocker rather than invent intent or make a hasty side-selection.

The machine-readable representation lives under `git_conflict_resolution` in `project-context.yaml`. The deterministic renderer and tests lock the verbatim directive, the 3–10 commit bounds, both-side and merge-base inspection, same-organization context, external-organization context, and forbidden side-selection shortcuts.

## Linear mirror

Each pilot Linear project receives one document titled **AI Agent Context**. The document contains:

* the immutable GitHub account ID and Linear project UUID;
* links to the organization `.github` repository and `project-context.yaml`;
* the central registry path, reviewed revision, and canonical SHA-256;
* routing/default-repository status;
* the mandatory semantic Git conflict policy;
* the marker `org-project-context:v1` for idempotent updates;
* a warning that the document is generated and must not become an independent authority.

Project descriptions should carry only a short pointer to this document. Long context belongs in the document so normal project summaries remain useful.

## Resolver contract

Consumers must resolve in this order:

1. Parse an exact `owner/repository` identity from GitHub metadata; never infer it from a display name or prompt.
2. Apply an exact repository override when one exists.
3. Otherwise match the immutable GitHub account ID to exactly one owner mapping.
4. Use aliases only as a discovery aid, case-insensitively, and require them to converge on the same immutable ID.
5. Return the immutable Linear project UUID, project URL, and any reviewed runtime allowlist.
6. Reject unmapped owners, multiple matches, repository escape, or a requested repository outside the reviewed runtime allowlist.

`scripts/validate_org_project_registry.py` enforces uniqueness, exact schema fields, public-safety constraints, runtime-owner boundaries, repository-override ownership, and fail-closed policies. `scripts/render_org_project_context.py` renders deterministic organization bundles.

## Synchronization model

The supported direction is one-way:

```text
reviewed central registry
  -> generated GitHub .github context
  -> generated Linear AI Agent Context document
```

Two-way editing would create split-brain state and semantic merge ambiguity. A future coordinator worker may update both mirrors after a registry merge, using the existing protected Linear delivery design. It must use a dedicated least-privilege Linear OAuth app or managed API key injected as `LINEAR_API_TOKEN`; no credential belongs in the registry, GitHub content, Linear document, logs, or workflow artifacts.

Until that worker is activated, updates use the connected GitHub and Linear tools idempotently and record the exact registry revision in both mirrors.

## Pilot and rollout

The initial pilot covers:

* `fiducia-cloud`: mixed public and private repository fleet;
* `sonus-auris`: mostly private repository fleet and highest-priority product;
* `shared-auth`: all-private repository fleet and cross-portfolio dependency.

Before wider rollout:

* verify each `.github` repository is publicly readable and visible through the installed GitHub App;
* verify the organization profile renders the intended link set;
* verify the custom agent appears where the organization's Copilot plan supports it;
* verify public defaults do not expose private reporting or repository-local controls;
* compare the GitHub and Linear mirror marker, registry revision, canonical digest, and semantic-conflict policy;
* keep seven currently installed but unmapped organizations fail-closed until their canonical Linear project is reviewed.

The four empty recent organizations `channelsiege`, `OmniBlitz`, `streamkore`, and `hypeblitz` remain governed by DEN-1339. Do not create a project mapping or context that invents their product identity.
