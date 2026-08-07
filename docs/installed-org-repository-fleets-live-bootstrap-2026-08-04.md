# Live installed-organization repository bootstrap — August 4, 2026

This execution unit converted the reviewed 41-repository fleet manifests into audited GitHub delivery without committing a personal access token. The one-time execution completed successfully and its credential relay and trigger were removed before this branch was merged.

## Exact scope and result

Only these installed organizations and public repositories were accepted:

- `apostille-me` — 5 repositories, tracked by DEN-1951;
- `evento-globolo` — 12 repositories, tracked by DEN-1889;
- `embedded-alerts` — 12 repositories, tracked by DEN-1949;
- `hacker-house-medellin` — 12 repositories, tracked by DEN-1950.

`liberty-cal` remained excluded because its organization installation and visibility decision were unresolved.

The run processed exactly **41** repositories:

| Result | Count |
|---|---:|
| Repositories newly created | 13 |
| Existing repositories reused in place | 28 |
| Initial repository-local pull requests created | 41 |
| Initial foundation pull requests merged | 39 |
| Superseded placeholder pull requests closed without merge | 2 |
| Additive replacement metadata pull requests merged | 2 |
| Repositories with reviewed delivery on `main` | 41 |
| Execution failures | 0 |

The runner merged 31 initial pull requests. Eight more became mergeable after GitHub finished calculating their state; each passed exact-head CI and was then squash-merged with its expected head SHA.

Two generated marketing placeholders were intentionally not merged:

- `apostille-me/apostille-me.github.io#2` would have replaced the richer Astro site already merged in `#1`;
- `evento-globolo/evento-globolo.github.io#2` would have replaced the richer product-specific Astro site already merged in `#1`.

Those two proposals were closed as regressions. Additive replacement PRs `apostille-me/apostille-me.github.io#3` and `evento-globolo/evento-globolo.github.io#3` then added fleet metadata, architecture, agent guidance, a repository verifier, and contract CI without modifying existing public pages, styling, package scripts, build behavior, SEO, or Pages configuration. Both replacement PRs passed the existing site CI and the new contract workflow before squash merge.

## Credential transport and retirement

The workflow generated a fresh 3072-bit RSA keypair for one run and published only the public key and run identifier to issue `#87`. It accepted exactly one RSA-OAEP/SHA-256 ciphertext bound to that run. The decrypted token was immediately masked, written to a mode-0600 runner-temporary file, read by the bootstrap process, and removed in an `always()` cleanup step.

The successful GitHub Actions run was `30970704229`. Its credential-receipt, repository execution, completion-evidence, and credential-destruction steps all completed successfully. No plaintext credential was committed, stored in workflow configuration, copied into an issue or pull request, uploaded as an artifact, or included in result evidence.

After execution:

- `.github/workflows/live-installed-org-repository-bootstrap.yml` was deleted from the branch;
- `.github/repository-bootstrap-trigger/live-installed-org-fleets-20260804.txt` was deleted from the branch;
- the run-bound private key and decrypted credential material were destroyed on the runner;
- the checked-in manifests remained `live_creation_enabled=false`.

## Repository delivery contract

For each manifest entry, the bootstrap:

1. verified or created the public organization repository idempotently;
2. enforced issues on, projects and wiki off, squash/merge on, rebase off, and delete-branch-on-merge on;
3. created a Linear-linked feature-branch commit containing project metadata, architecture guidance, contract validation, and an archetype-specific foundation;
4. opened a repository-local pull request;
5. attempted an intentional squash merge at the exact generated head SHA;
6. recorded repository, branch, commit, pull-request, merge, and bounded failure evidence.

Delivered archetypes include Axum/WebSocket APIs, Maud/Axum/HTMX web servers, Leptos and Dioxus adapters, offline-first synchronization, flags-2-env CLIs, Rust libraries, polyglot clients, OpenAPI/AsyncAPI/JSON Schema interfaces, bounded Cloudflare Workers, Astro marketing sites, and orchestration monorepos.

These merged changes establish testable foundations and repository contracts. They do not claim every product feature is complete.
