# ChatGPT work reconciliation — 2026-08-08

## Scope

This ledger records the synchronous recovery and publication pass over work discussed in the user's work/chat conversations from **2026-06-14 through 2026-08-08** (55 days), excluding every `dancing-dragons` space, repository, archive path, and target.

The purpose is preservation and reviewability: move recoverable code out of transient chat artifacts, map it to the current GitHub architecture, open draft pull requests, and update matching Linear work without force-pushing, silently replacing newer implementations, or changing production infrastructure.

## Safety boundary

- No force push was used. All ref updates were normal fast-forwards with `force: false`.
- No pull request was merged by this recovery pass.
- No Cloudflare Worker, route, DNS record, R2 bucket/object, database, Kubernetes workload, or production service was changed or deployed.
- No user-supplied GitHub, Linear, Cloudflare, or R2 credential was committed intentionally.
- Concrete scans for GitHub/Linear/Cloudflare/OpenAI/Slack-style tokens, AWS-style access keys, and private-key headers found **zero matches** across recovered working trees and all reachable Git objects in the 56-history corpus. This is a bounded heuristic scan, not a proof that arbitrary secrets cannot exist.
- Repositories that had evolved beyond their archived layout received an exact snapshot under `recovery/provider-cross-posting/` or a documented semantic-successor mapping instead of a blind overwrite.

## Recovered corpus

Primary corpus: `github-org-repositories-complete-2026-08-04.zip`

| GitHub organization | Independent Git histories | Treatment |
| --- | ---: | --- |
| `apostille-me` | 12 | Existing native PR coverage was retained; `apme-api` received the missing production-scaffold draft. |
| `embedded-alerts` | 12 | Existing bootstrap-family PR coverage was retained and indexed. |
| `evento-globolo` | 12 | Every history now has a native draft PR or a draft in its correctly renamed live successor. |
| `hacker-house-medellin` | 12 | Existing bootstrap-family PR coverage was retained and indexed. |
| `StreemPilot` | 8 | Archived layouts were mapped to narrower live successor PRs; newer architecture was not overwritten. |
| **Total** | **56** | Credential-clean source preserved or mapped to an existing review surface. |

Additional source bundles reviewed during the pass included the final ChatGPT reconciliation bundle, graceful-shutdown rollout, logger-hardening review, Hacker House continuation, and shared-auth publication archives.

## Cross-program preservation PRs

- `ORESoftware/ai-agent-coordinator.rs#133` — recovered coordinator work; exact-head CI green.
- `ORESoftware/ai-agent-coordinator.rs#134` — recovered coordinator work; exact-head CI green.
- `ORESoftware/chat.vibe#7` — recovered chat work; draft retained because GitHub Actions jobs were blocked before runner execution by account-level Actions allocation/billing rather than an identified code failure.
- `canonical-cloud/canonical-api-server.rs#19` — graceful shutdown and lifecycle recovery on `agent/graceful-shutdown`, exact recovered head `455931cc847f32a6f83c9790f53c4b73543b1a4a`; formatting and lockfile repairs were applied through a self-removing validation workflow.
- `apostille-me/apme-api#4` — production scaffold recovery on `agent/production-scaffold-review`, head `429f2c89996079c4db833e5b54e515cf12c5d5dc`.

## Evento Globolo native review surfaces

All branches below are `agent/provider-cross-posting`, based on the live repository's then-current `main`, and remain draft-only.

| Live repository | PR / branch | Recovered head | Preservation decision |
| --- | --- | --- | --- |
| `evento-globolo/evento-globolo.github.io` | PR #6 | `b9eca635fb7dcf2d6c627a4744d9241f136d560f` | Astro marketing site and Pages workflow recovered directly on the draft branch. Requires ordinary merge review because archived root files differ from newer live work. |
| `evento-globolo/evgl-api` | PR #8 | `8380feac851741dd04acf8de9567b1eb19a72757` | Full provider API recovered; strict Clippy and tests passed in the recovery workflow. A real IPv6/private-address webhook validation bypass was fixed with explicit `url::Host` handling and IPv4/IPv6/mapped-IPv6 tests. PR checks still require normal approval where configured. |
| `evento-globolo/evgl-cli` | PR #7 | `074b131ee0a0fc6a96301980bea43b90d627d9db` | `flags-2-env` operations CLI recovered directly on the draft branch. Requires ordinary merge review because archived root files differ from newer live work. |
| `evento-globolo/evgl-clients` | PR #5 | `e4e4b05a20aceace3ba147b0a979495e02474eb4` | Exact Go/TypeScript/Rust/Dart/OpenAPI snapshot stored under `recovery/provider-cross-posting/`; canonical `clients/` fleet untouched. |
| `evento-globolo/evgl-infra` | PR #7 | `1a8bd7eab0b1ad2e942682ddfa6c9d0d7f7c1714` | Exact Worker/webhook snapshot namespaced; newer hardened ingress and all live Cloudflare configuration untouched. |
| `evento-globolo/evgl-interfaces` | PR #4 | `70d92055312b61c85ed8f17a552d64437cd412f6` | OpenAPI, AsyncAPI, provider policy, and option schemas namespaced; current universal Zed interface package untouched. |
| `evento-globolo/evgl-libs` | PR #5 | `de7e858f9707e32e1950bb645e424ff6308bca6a` | Recovered Rust crates; exact-head dependency review, format, strict Clippy, tests, configuration integrity, zeroization, and audit checks green. |
| `evento-globolo/evgl-monorepo` | PR #14 | `e9dc4a56e832f0bf474737d87f57117e22ae4c9c` | Docker/submodule composition namespaced; current Zed graph, root gitmodules, and project-authority documentation untouched. |
| `evento-globolo/evgl-sync` | PR #6 | `e6f926ea98d15c4ab8365479d488e01648ee6ea9` | Opto Sync lease/fencing implementation namespaced; current dependency/runtime line untouched. |
| `evento-globolo/evgl-dioxus-web` | PR #11 | `c4402ed7ee947cc874a93f4842cb14d4e5edb387` | Archived `evgl-web-dioxus` recovered into its renamed live successor under a namespace. |
| `evento-globolo/evgl-leptos-web` | draft branch / PR | `01b147c8cf11eb35e7475fa4f554789d669e8edf` | Archived `evgl-web-leptos` recovered into its renamed live successor under a namespace. |
| `evento-globolo/evgl-mash-web` | draft branch / PR | current branch head | Archived `evgl-web-mash` recovered into its renamed live successor under a namespace. The exact 21,001-byte Rust server is retained losslessly as compressed base64 with reconstruction script and SHA-256 `78958a6a48831e495b2e5bf61f002b769bdaf348df0925e373e1bd3bf14d8496`. |

Rename mappings were deliberate:

- `evgl-web-dioxus` → `evgl-dioxus-web`
- `evgl-web-leptos` → `evgl-leptos-web`
- `evgl-web-mash` → `evgl-mash-web`

No obsolete duplicate repositories were created.

## Existing-family coverage retained

### Apostille Me

The archive's clients, libraries, monorepo, and marketing-site histories already had native review coverage. The missing API production scaffold was published as `apostille-me/apme-api#4`. Existing infrastructure/Worker work remains linked through its current GitHub and Linear review surfaces rather than being replayed over newer code.

### Embedded Alerts

The 12-history bootstrap family already had native pull-request coverage. The recovery pass retained those branches and did not replay the August 4 seed commits over later repository work.

### Hacker House Medellín

The 12-history bootstrap family and the later continuation/reconciliation archive already had native pull-request coverage. Existing branches were retained; no duplicate repository or force update was introduced.

### StreemPilot

The eight archived histories were compared with the live organization. Where repository names, boundaries, or implementation direction had changed, the recovered work was mapped to the narrower successor PRs already present instead of restoring an obsolete aggregate layout. This ledger is the durable pointer for that semantic mapping.

## Review and integration rules

1. Treat every recovery PR as a preservation surface, not an automatic merge candidate.
2. For namespaced snapshots, port missing behavior into the canonical live path through normal reviewed commits; do not move the archived tree wholesale.
3. Require exact-head CI, dependency review, credential scanning, and repository-specific approval before changing draft status.
4. Preserve the newer implementation when archived and live files conflict; use the archive to recover intent, tests, edge cases, and missing capabilities.
5. Keep `dancing-dragons` excluded from all follow-up automation and reporting for this corpus.
6. Production deployment, Cloudflare changes, database migrations, and merges require separate explicit authorization and their ordinary safety gates.

## Linear reconciliation

Matching Linear projects/issues are updated with repository and exact-head references. Canonical graceful shutdown is tracked on `DEN-3175`; Apostille API scaffold recovery is tracked on `DEN-1951`; Evento Globolo receives a project-level repository-family summary rather than twelve duplicate tickets.
