# ores-legal signing platform repository fleet

This change is the reviewed publication control plane for the initial `ores-legal`
organization repository fleet. It intentionally does not claim that the product is
already at production parity with DocuSign, Dropbox Sign, Adobe Acrobat Sign, or the
other comparison platforms. It creates a compile-checked foundation and records the
remaining parity work explicitly.

Tracked under Linear `DEN-319` because the workspace rejected a dedicated issue after
reaching its free issue quota. The corresponding Linear project is `ores-legal`.

## Publication inventory

The sealed manifest creates these repositories in dependency order:

1. `ores-legal-interfaces`
2. `ores-legal-lib-core`
3. `ores-legal-pub-lib-core`
4. `ores-legal-orm-core`
5. `ores-legal-clients`
6. `ores-legal-api-server.rs`
7. `ores-legal-admin-api-server.rs`
8. `ores-legal-web-server.rs`
9. `ores-legal-admin-web-server.rs`
10. `ores-legal-flutter`
11. `ores-legal-desktop-app.rs`
12. `ores-legal-lambdas`
13. `ores-legal-infra`
14. `ores-legal-docs`
15. `ores-legal-mcp-server.rs`
16. `ores-legal.github.io`
17. `ores-legal-monorepo`

The monorepo is last because the publisher resolves each sibling repository's exact
`main` commit and writes real mode-`160000` gitlinks under `apps/`.

## Visibility boundary

Public repositories:

- interfaces;
- generated client/SDK foundations;
- public Rust library;
- architecture and integration documentation;
- Astro organization site.

Private repositories:

- internal domain and ORM cores;
- APIs and web applications;
- Flutter and Rust desktop applications;
- lambdas, infrastructure, MCP server, and monorepo.

The publisher fails closed if an existing repository has a different visibility from
the reviewed manifest.

## Contract authorities

TypeSpec and JSON Schema Draft 2020-12 are independent peer authorities. Neither is
treated as generated from the other. The interfaces repository contains a parity
checker that compares the required top-level business declarations across both
surfaces. Protobuf is a separate wire-contract authority for transport interoperability.

The initial language matrix contains twenty targets:

- Rust, TypeScript, JavaScript, Dart, Kotlin, Swift, Java, C#, Go, Python;
- Ruby, PHP, C, C++, Objective-C, Scala, Elixir, Gleam, Haskell, and Clojure.

Rust, TypeScript, and Dart receive executable starter clients in the first publication.
The remaining targets receive governed generation and implementation manifests rather
than fabricated complete SDKs. Subsequent work must generate, compile, package, and test
each target before calling it supported in production.

## Signing-domain foundation

The generated contracts cover:

- templates and immutable template versions;
- envelopes, recipients, signing sessions, fields, and captured values;
- signature, initials, signed-date, text, checkbox, attachment, stamp, and witness roles;
- ordered, parallel, conditional, delegated, declined, expired, and cancelled routes;
- identity evidence, electronic-signature consent, document acceptance, and timestamps;
- append-only, hash-linked audit events and completion certificates;
- idempotency keys, webhook signatures, delivery attempts, and replay boundaries;
- B2B organization tenancy and B2C individual signer ceremonies.

A sample mutual confidentiality agreement demonstrates visible signature lines plus
machine-readable signature, initials, date, and consent anchors. It is explicitly marked
`COUNSEL REVIEW REQUIRED`; the repository bootstrap does not turn generated prose into
jurisdiction-specific legal advice.

## MASH, Leptos, Dioxus, and Flutter

The customer web server is HTML-first and emits MASH-compatible fragments using Maud and
HTMX semantics. A framework-neutral Rust component contract exposes the same signing
field and event model to MASH, Leptos, and Dioxus adapters. The Flutter repository uses
the same field types and lifecycle events for mobile web, iOS, Android, macOS, Windows,
and Linux. The Rust desktop repository provides a Dioxus desktop feature without making
React a desktop dependency.

The first scaffold is deliberately small: it demonstrates a signing room, field
rendering, save/commit events, accessible labels, and server-owned state transitions.
Production work still requires document pagination, coordinate transforms, PDF overlay,
offline conflict handling, accessibility audits, localization, and cross-client golden
tests.

## Storage and tenant routing

Neon is the primary transactional PostgreSQL target. Supabase is scaffolded under the
near-term workspace exception: the shared `oresoftware` Supabase organization hosts the
`ores_legal_canonical` and `ores_legal_auth` namespaces. Every runtime reads those
locations through `RuntimeConfig`; no application may bake the temporary shared-org
layout into business code.

Every envelope also carries a `tenant_storage_profile_id`. The outbox/replication
boundary writes a tenant-owned projection to the client organization's configured
location. A completion state is not considered fully replicated until the idempotent
projection receipt is recorded. Storage adapters must never expose one tenant's document,
signature, evidence, or webhook material to another tenant.

## Evidence and signature meaning

A drawn or uploaded signature image is not the legal evidence by itself. The evidence
package binds:

- immutable document bytes and SHA-256 digest;
- exact template/envelope version;
- signer and organization identity references;
- consent and acceptance events;
- authentication method references;
- canonical UTC timestamps;
- session, network, and device evidence under retention policy;
- field values and signature artifact hashes;
- ordered audit-event hashes;
- completion-certificate hash and storage receipts.

The threat model treats signature-image replay, document substitution, session takeover,
tenant confusion, audit deletion/reordering, webhook forgery, leaked document bodies,
and credential exposure as explicit threats.

## Feature-parity boundary

The manifest records eight comparison platforms: DocuSign, Dropbox Sign, Adobe Acrobat
Sign, PandaDoc, signNow, Zoho Sign, OneSpan Sign, and Yousign. The scaffold covers the
shared architectural spine, not complete commercial parity.

Major remaining work includes jurisdiction and identity policy packs, qualified/advanced
electronic signature providers, knowledge-based or government-ID verification, remote
online notarization, payment collection, advanced bulk-send UX, reusable public forms,
conditional field builders, organization directories, content negotiation, localization,
SCIM/SAML administration, enterprise retention/legal holds, PDF/A and long-term validation,
SMS/WhatsApp delivery, analytics, accessibility certification, full SDK generation and
packaging, and migration/import connectors.

## Safety and publication controls

The GitHub Action validates on pull requests but creates nothing until this coordinator
change reaches `main`. The publish job then:

1. runs in the protected `nightly-org-maintenance` environment;
2. mints an `ores-legal`-scoped GitHub App token;
3. validates the installation owner set;
4. sets exact organization, repository, digest, and confirmation values;
5. creates or reconciles one repository at a time;
6. writes an exact feature-branch commit;
7. opens a `DEN-319` pull request;
8. verifies the PR head and base;
9. squash-merges only the exact validated head;
10. waits until `repository.contract.json` is observable on `main`;
11. uploads a redacted publication ledger.

Repository settings disable rebase merges and ordinary merge commits for these bootstrap
repositories. The publisher never force-updates a branch. If a prior bootstrap branch
is not at `main`, publication stops for semantic review.

## Local validation

```bash
python3 -W error -m py_compile \
  scripts/bootstrap_ores_legal_fleet_live.py \
  scripts/test_bootstrap_ores_legal_fleet_live.py \
  scripts/ores_legal_bootstrap/*.py
python3 -W error -m unittest -v scripts/test_bootstrap_ores_legal_fleet_live.py

digest="$(python3 -c 'import sys; sys.path.insert(0,"scripts"); from ores_legal_bootstrap.core import load_manifest, manifest_digest; print(manifest_digest(load_manifest()))')"
python3 scripts/bootstrap_ores_legal_fleet_live.py render \
  --manifest-sha256 "$digest" \
  --output /tmp/ores-legal-rendered \
  --result /tmp/ores-legal-render-result.json
```

Run every rendered `scripts/verify.py`, the interfaces peer-authority checker, TypeSpec
compile, TypeScript compile, Astro build, JavaScript syntax check, and all generated Rust
format/clippy/test gates before publication. GitHub Actions performs the networked toolchain
steps with pinned actions and exact generated manifests.
