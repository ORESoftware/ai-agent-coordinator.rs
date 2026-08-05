# {{ORG_DISPLAY_NAME}}

{{ORG_SUMMARY}}

This page is the public orientation point for people and authorized AI agents. Repository-specific READMEs and instructions remain authoritative for implementation details.

## Start here

### For people

- Explore {{PUBLIC_STARTING_POINTS}}.
- Use the [canonical Linear project]({{LINEAR_PROJECT_URL}}) for planning, priorities, dependencies, and delivery context.
- Read the organization [contribution guide](https://github.com/{{ORG_LOGIN}}/.github/blob/main/CONTRIBUTING.md), [governance notes](https://github.com/{{ORG_LOGIN}}/.github/blob/main/GOVERNANCE.md), [support guide](https://github.com/{{ORG_LOGIN}}/.github/blob/main/SUPPORT.md), and [security policy](https://github.com/{{ORG_LOGIN}}/.github/security/policy).
- Start in the README and local instructions of the exact repository being changed; this profile is an index, not a substitute for repository documentation.

### For AI agents

1. Read [`project-context.yaml`](https://github.com/{{ORG_LOGIN}}/.github/blob/main/project-context.yaml) for canonical GitHub owner, Linear project, and reviewed routing context.
2. Read [`repository-relationships.json`](https://github.com/{{ORG_LOGIN}}/.github/blob/main/repository-relationships.json) before inferring dependencies, ownership, or repository selection.
3. Read the organization [`AGENTS.md`](https://github.com/{{ORG_LOGIN}}/.github/blob/main/AGENTS.md), [`ORG_CONTEXT.md`](https://github.com/{{ORG_LOGIN}}/.github/blob/main/ORG_CONTEXT.md), and every applicable repository-local `AGENTS.md`, `agents.md`, provider instruction, and path-specific instruction.
4. {{REPOSITORY_SELECTION_RULE}}
5. Keep credentials, private repository content, customer information, incident details, and security-sensitive topology out of public outputs.

## Canonical identity and authority

- GitHub organization: [`{{ORG_LOGIN}}`](https://github.com/{{ORG_LOGIN}})
- Immutable GitHub owner ID: `{{GITHUB_OWNER_ID}}`
- Linear project: [`{{LINEAR_PROJECT_NAME}}`]({{LINEAR_PROJECT_URL}})
- Immutable Linear project ID: `{{LINEAR_PROJECT_ID}}`
- Linear team: `{{LINEAR_TEAM_KEY}}` (`{{LINEAR_TEAM_ID}}`)
- Organization defaults and public policies: [`{{ORG_LOGIN}}/.github`](https://github.com/{{ORG_LOGIN}}/.github)

The reviewed central registry is authoritative for GitHub/Linear identity and routing. Repository-local instructions are authoritative for builds, tests, architecture, migrations, and implementation. Exact repository overrides take precedence over owner-level defaults. Missing, unmapped, ambiguous, or contradictory context must stop and be reported; it must not be invented.

## Organization-specific operating principles

{{OPERATING_PRINCIPLES}}

- Preserve data and state non-destructively. Do not use history rewrites, blanket resets, destructive cleanup, or wholesale side selection to make a change appear simple.
- Keep application code and infrastructure repositories separate. An `*-infra` repository does not belong under a monorepo `apps/` directory as a Git submodule.
- Link substantial work to Linear and a GitHub issue or pull request so humans and agents can recover intent.
- Resolve Git conflicts semantically: inspect the merge base, both sides, path-scoped history, and 3–10 relevant commits when available; read linked issues, pull requests, tests, schemas, migrations, architecture decisions, and relevant same-organization or external repositories. Never accept `ours`, `theirs`, current, or incoming wholesale without conceptual review.
- Preserve compatible intent, APIs, schemas, tests, documentation, security controls, and operational safeguards from every relevant side, then scan the complete worktree for unresolved conflict markers and run all affected validation.

## Public context boundary

This profile and the `.github` repository are intentionally public. They may contain public identifiers, links, policies, and operating guidance. They must not contain credentials, private customer or user data, private issue content, incident details, security-sensitive topology, or unpublished business information.
