# Example Organization

Example Organization builds dependable, privacy-conscious services and reusable software components for people and teams that need clear public documentation, safe automation, and reviewable operational practices.

This page is the public orientation point for people and authorized AI agents. Repository-specific READMEs and instructions remain authoritative for implementation details.

## Start here

### For people

- Explore the [public application repository](https://github.com/example-org/example-app).
- Use the [canonical Linear project](https://linear.app/example/project/githubcomexample-org-123456789abc) for planning, priorities, dependencies, and delivery context.
- Read the organization [contribution guide](https://github.com/example-org/.github/blob/main/CONTRIBUTING.md), [governance notes](https://github.com/example-org/.github/blob/main/GOVERNANCE.md), [support guide](https://github.com/example-org/.github/blob/main/SUPPORT.md), and [security policy](https://github.com/example-org/.github/security/policy).
- Start in the README and local instructions of the exact repository being changed; this profile is an index, not a substitute for repository documentation.

### For AI agents

1. Read [`project-context.yaml`](https://github.com/example-org/.github/blob/main/project-context.yaml) for canonical GitHub owner, Linear project, and reviewed routing context.
2. Read [`repository-relationships.json`](https://github.com/example-org/.github/blob/main/repository-relationships.json) before inferring dependencies, ownership, or repository selection.
3. Read the organization [`AGENTS.md`](https://github.com/example-org/.github/blob/main/AGENTS.md), [`ORG_CONTEXT.md`](https://github.com/example-org/.github/blob/main/ORG_CONTEXT.md), and every applicable repository-local instruction.
4. Resolve the exact repository explicitly. Missing, unmapped, or ambiguous work must stop and be reported rather than guessed.
5. Keep credentials, private repository content, customer information, incident details, and security-sensitive topology out of public outputs.

## Canonical identity and authority

- GitHub organization: [`example-org`](https://github.com/example-org)
- Immutable GitHub owner ID: `123456789`
- Linear project: [`github.com/example-org`](https://linear.app/example/project/githubcomexample-org-123456789abc)
- Immutable Linear project ID: `12345678-1234-4abc-8def-123456789abc`
- Linear team: `EX` (`87654321-4321-4cba-8fed-cba987654321`)
- Organization defaults and public policies: [`example-org/.github`](https://github.com/example-org/.github)

The reviewed central registry is authoritative for GitHub/Linear identity and routing. Repository-local instructions are authoritative for builds, tests, architecture, migrations, and implementation. Missing or contradictory context must stop and be reported; it must not be invented.

## Organization-specific operating principles

- Preserve user data and state non-destructively and maintain compatibility across reviewed interfaces.
- Link substantial work to Linear and a GitHub issue or pull request so humans and agents can recover intent.
- Resolve Git conflicts semantically: inspect the merge base, both sides, path-scoped history, and 3–10 relevant commits when available; read linked issues, pull requests, tests, schemas, migrations, architecture decisions, and relevant same-organization or external repositories. Never accept `ours`, `theirs`, current, or incoming wholesale without conceptual review.
- Preserve compatible intent, APIs, schemas, tests, documentation, security controls, and operational safeguards from every relevant side.

## Public context boundary

This profile and the `.github` repository are intentionally public. They may contain public identifiers, links, policies, and operating guidance. They must not contain credentials, private customer or user data, private issue content, incident details, security-sensitive topology, or unpublished business information.
