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
* `org-context-manifest.json` records deterministic hashes for every other managed file without a recursive self-hash.
* `.github/workflows/org-context-integrity.yml` verifies the bundle against the exact central registry commit without using a credential.
* Supported community-health files can become defaults for repositories that do not define their own versions.

GitHub requires the `.github` repository to be public for most organization-wide community defaults. Therefore every generated file must be safe for public disclosure. Private architecture, customer data, incident details, credentials, and operational secrets belong elsewhere. If an organization later needs private Copilot custom-agent material, use a separate `.github-private` repository and preserve the public mapping as the stable discovery anchor.

Organization-level custom agents are currently a public-preview GitHub feature. The generated profile uses only documented frontmatter (`name`, `description`, `tools`, and `target`) and limits the agent to read/search tools.

References: [default community-health files](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/creating-a-default-community-health-file), [preparing an organization for custom agents](https://docs.github.com/en/copilot/how-tos/administer-copilot/manage-for-organization/prepare-for-custom-agents), and [custom-agent configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration).

### Repository-local instructions remain necessary

GitHub does not inherit arbitrary `AGENTS.md`, `agents.md`, or `.github/copilot-instructions.md` files from an organization `.github` repository into every repository clone or agent workspace. Repository-local instructions remain the portable mechanism for build, test, architecture, and path-specific rules.

Precedence is therefore deliberate:

1. exact repository overrides choose the Linear project when present;
2. the owner mapping chooses the organization-level Linear project otherwise;
3. repository-local instructions control implementation details;
4. organization context supplies broad public policy and identity;
5. missing or ambiguous mappings are rejected rather than guessed.

This complements, rather than replaces, DEN-114, DEN-282, and DEN-292.

## Linear mirror

Each pilot Linear project receives one document titled **AI Agent Context**. The document contains:

* the immutable GitHub account ID and Linear project UUID;
* links to the organization `.github` repository and `project-context.yaml`;
* the central registry path, reviewed revision, and canonical SHA-256;
* routing/default-repository status;
* the marker `org-project-context:v1` for idempotent updates;
* a warning that the document is generated and must not become an independent authority.

Project descriptions should carry only a short pointer to this document. Long context belongs in the document so normal project summaries remain useful.

## Resolver contract

Consumers must resolve in this order:

1. Parse an exact `owner/repository` identity from GitHub metadata; never infer it from a display name or prompt.
2. Apply an exact repository override when one exists.
3. Otherwise match the immutable GitHub account ID to exactly one owner mapping.
4. Use aliases only as a discovery aid, case-insensitively, and require them to converge on the same immutable ID.
5. Return the immutable Linear project UUID and project URL for identity resolution.
6. For an executable runtime route, resolve through the separate reviewed runtime allowlist and reject an owner without a route or a repository outside that allowlist.
7. Reject unmapped owners, multiple matches, cross-owner repository escape, malformed repository identities, or mutable source references.

`scripts/validate_org_project_registry.py` enforces strict UTF-8/JSON parsing, real dates, unique IDs and project URLs, exact schema fields, Linear workspace URL boundaries, public-safety constraints, runtime-owner boundaries, repository-override ownership, and fail-closed policies. `scripts/render_org_project_context.py` accepts only an immutable 40-character commit SHA and renders deterministic organization bundles. `scripts/verify_org_project_context.py` compares every managed byte to a fresh render and rejects missing files, symlinks, path escape, or drift.

## Synchronization model

The supported direction is one-way:

```text
reviewed central registry
  -> generated GitHub .github context
  -> generated Linear AI Agent Context document
```

Two-way editing would create split-brain state and semantic merge ambiguity. A future coordinator worker may update both mirrors after a registry merge, using the existing protected Linear delivery design. It must use a dedicated least-privilege Linear OAuth app or managed API key injected as `LINEAR_API_TOKEN`; no credential belongs in the registry, GitHub content, Linear document, logs, or workflow artifacts.

Until that worker is activated, updates use the connected GitHub and Linear tools idempotently and record the exact registry revision in both mirrors.

Each generated organization workflow downloads the registry, renderer, validator, and verifier from the exact central commit recorded in `project-context.yaml`. It then regenerates the bundle and requires a byte-for-byte match. The workflow has read-only repository permissions, persists no checkout credential, and never receives a GitHub or Linear token.

## Browser and CI validation

The central GitHub Actions workflow has two gates:

* dependency-free Python validation renders and re-verifies all three pilot bundles, validates both handwritten and generated workflows with the pinned `actionlint` image, and uploads short-lived public evidence;
* Playwright Chromium tests serve the generated profile Markdown and machine-readable files through a loopback-only preview, exercise the visible organization identity and Linear links, reject unsafe link schemes and executable markup, validate documented custom-agent frontmatter, and recompute every manifest hash from browser-fetched content.

The browser fixture blocks non-loopback requests. Tests inspect external GitHub and Linear links without navigating to them, so CI does not depend on a signed-in session or transmit repository context to third parties.

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
* compare the GitHub and Linear mirror marker, registry revision, and canonical digest;
* require the per-organization integrity workflow and central Playwright gate to pass at the exact proposed heads;
* keep seven currently installed but unmapped organizations fail-closed until their canonical Linear project is reviewed.

The four empty recent organizations `channelsiege`, `OmniBlitz`, `streamkore`, and `hypeblitz` remain governed by DEN-1339. Do not create a project mapping or context that invents their product identity.
