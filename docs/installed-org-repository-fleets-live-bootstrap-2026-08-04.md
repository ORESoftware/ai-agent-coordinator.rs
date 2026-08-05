# Live installed-organization repository bootstrap — August 4, 2026

This execution unit turns the reviewed 41-repository planning manifests into a one-time, auditable GitHub Actions bootstrap without committing a personal access token.

## Exact scope

Only these installed organizations and public repositories are accepted:

- `apostille-me` — 5 repositories, tracked by DEN-1951;
- `evento-globolo` — 12 repositories, tracked by DEN-1889;
- `embedded-alerts` — 12 repositories, tracked by DEN-1949;
- `hacker-house-medellin` — 12 repositories, tracked by DEN-1950.

The loader requires exactly 41 unique repository names. `liberty-cal` remains excluded because its organization installation and visibility decision are unresolved.

## Credential transport

The workflow generates a fresh 3072-bit RSA key for one run and publishes only the public key and run identifier to issue 87. It accepts exactly one RSA-OAEP/SHA-256 ciphertext bound to that run. The decrypted token is immediately masked, written to a mode-0600 runner-temporary file, read by the bootstrap process, and removed in an `always()` cleanup step. Plaintext credentials are never valid source, issue, pull-request, artifact, or result content.

## Repository delivery contract

For each manifest entry, the bootstrap:

1. verifies or creates the public organization repository idempotently;
2. enforces issues on, projects and wiki off, squash/merge on, rebase off, and delete-branch-on-merge on;
3. creates a Linear-linked feature-branch commit containing project metadata, architecture guidance, contract validation, and an archetype-specific foundation;
4. opens a repository-local pull request;
5. attempts an intentional squash merge at the exact generated head SHA;
6. records repository, branch, commit, pull request, merge state, and bounded failure evidence.

Archetypes include Axum/WebSocket APIs, Maud/Axum/HTMX web servers, opt-in Leptos and Dioxus adapters, sync and CLI foundations, Rust libraries, polyglot clients, OpenAPI/AsyncAPI/JSON Schema interfaces, bounded Cloudflare Workers, Astro marketing sites, and orchestration monorepos.

The initial pull requests establish testable foundations; they do not claim every product feature is complete.
