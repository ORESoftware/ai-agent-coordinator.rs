# Zed v0.2.3 fleet migration

**Linear:** DEN-3733

**Parent:** DEN-2056

**Core contract:** `zed-interfaces@8428bc574111fa148e590c8350c7855035ce2046`
**Released validator:** `zed-cli@v0.2.3` / `9dae597bcf22970e97b90c5ea336db19a9f02255`

## Authenticated fleet boundary

The 2026-08-14 inventory used the active `ORESoftware` GitHub identity and
enumerated 1,668 accessible repositories across 115 organization memberships and
the user account. The production/test topology contains 28 exact organization
pairs, including the case-insensitive `ORESoftware` / `oresoftware-test` pair.

An authenticated recursive scan of every default-branch Git tree found:

- 656 `.zpkg.toml` instances;
- 620 repositories containing at least one manifest;
- 608 root manifests and 48 nested manifests in 22 repositories;
- 634 unique manifest blobs; and
- no truncated Git trees in the final refresh.

The scan includes source templates, generated end repositories, monorepo copies,
test fixtures, private repositories, and exact `*-test` organization consumers.
It does not infer fleet completion from local checkout state.

## Contract and graph findings

The official v0.2.1 release accepted 338/634 unique blobs. Adding the documented
JavaScript, TypeScript, Go, polyglot, Astro, YAML, and YML aliases to
`zed-interfaces` raised current-source acceptance to 506/634 without weakening
strict rejection of unknown language names. The remaining failures were real
manifest drift:

- unsupported script keys outside the single `scripts.test` API;
- divergent names on whole-repository targets;
- package and dependency names that do not satisfy Zed slugs;
- duplicate source roots presented as separate runtime targets;
- four legacy combined package identities in generator/source pairs;
- one pre-contract ClipTown manifest; and
- one obsolete zed-vscode integration fixture.

The reconciled migration changes 158 unique source blobs, 169 manifest instances,
and 153 repositories. Every proposed blob passes released zed-cli v0.2.3 against
the exact interface revision above. Script migrations preserve every prior
command in source order under `scripts.test`; they do not discard lint, format,
code-generation, conformance, rendering, or dependency-check behavior.

Three additional manifests are exact, validation-provenanced reconciliations rather
than pending mutations. `opto-sync-test/contract-conformance-tests` landed its
semantic migration in PR 5 before the fleet plan. During the source-first phase,
`canonical-cloud/canonical-clients` independently landed reviewed PRs 22 and 23;
their combined default-branch blob includes the planned root-target correction,
current v0.2.3 target identifiers, and the new `canonical-lib` dependency.
`zed-pkg-test/awkward-lib` then independently landed reviewed PR 1, preserving
all four fixture layouts while canonicalizing the whole-repository target name.
The publisher now revalidates all three reconciled blobs on their default
branches during every full preflight instead of silently excluding them from
the mutation list.

One of the 656 scanned files is deliberately not a current package surface:
`3fa-app-test/clients-consumer-matrix/proof/den-2612/source/.zpkg.toml` is an
immutable, byte-addressed DEN-2612 snapshot of `3FA-app/3fa-clients` PR 37. Its
`proof-source.json` records source blob
`1a05a2c6850a375eb5720ff6bf23883b0a5fb63d`, and `verify_snapshot.py` rejects any
rewrite. The plan records this exact exclusion instead of corrupting historical
provenance or pretending its deliberately partial Java/Swift copy is a package
root.

The broader package graph contains 1,253 source/target coordinates and 859 direct
dependency edges. The plan repairs proven coordinate drift for 3FA, Fiducia,
Quaestor Ledger, flags-2-env, StreemPilot, opto-sync, Declarative Migrations, and
the four plural `*-libs` packages. Twelve coordinates intentionally remain
unresolved after those repairs:

- `acme/runtime`, `acme/subtool`, and `zed/zed-cli` are deliberate negative or
  recursive CLI fixtures;
- ClipTown, Shared Auth, and 3FA manifests explicitly document blocked future
  `cliptown-sync`, `shared-auth-cli`, and `threefa-lib` roles; and
- Claritas Viz clients, four Discrete Event Systems roles, and Memebank clients
  are concrete missing-package candidates rather than spelling aliases.

## Semantic migrations

Whole-repository targets retain the canonical package identity and lose only a
redundant or divergent target name. Runtime-neutral TypeScript implementations
in Fiducia, Scintilla, and Shared Auth now have one target that owns the shared
Node.js/Deno/Bun/edge source root; distinct directories remain distinct targets.
The ClipTown TypeScript target separates its Zed slug from its native npm package
`@cliptown/client`.

`cliptown/cliptown-lib-core` is converted from the combined `org/name`,
`publish.include`, and `[smoke_test]` shape to a current single-language Rust
package with repository provenance, safe exclusions, a native crates.io route,
and both package smoke and development test contracts. Rust repository names
ending in `.rs` and Pages repositories ending in `.github.io` retain their GitHub
URLs while receiving portable Zed package slugs.

Generator and source repositories are phase one. Generated and leaf repositories
are phase two. The publisher performs one all-repository preflight before either
phase, accepts only the exact inventoried or exact proposed blob at each managed
path, verifies target directories against the default-branch tree, and rejects a
partial repository state.

The authenticated phase-one publication completed against original reviewed plan
SHA-256
`36ac1b21bc266cdfffd6de1f1581020b430da5c5e5d23f36f086329caa299a8a`.
All 155 then-managed repositories were re-preflighted, seven exact-head pull
requests passed their repository checks and were merged, the already-migrated
controller was reverified, and all eight source/generator defaults were verified
at their proposed blobs with zero publisher failures.

Before phase two created any branch, the full preflight failed closed on the
concurrent `canonical-clients` blob. A separate read-only audit of all 155
originally managed repositories found exactly that one divergence: 146 remained
at source, eight phase-one repositories were proposed, and no second path had
drifted. The first reconciled plan SHA-256 was
`bde593a5618e829c5ca2a0adec419b6dc5e9f5986404ca518f573a7e901c04a3`.
A second fail-closed full preflight then caught independently reviewed
`awkward-lib` PR 1 before any phase-two branch was created. The current plan
removes both superseded mutations, records and validates all three exact
reconciled manifests, and retains a 156-repository live preflight (153 mutation
repositories plus three independently reconciled repositories). A third full
preflight then stopped at `shared-auth/shared-auth-clients` after reviewed PR 51
added explicit names to three TypeScript targets. That current manifest still
failed v0.2.3 because several older runtime aliases shared the same source root,
so it could not be excluded as already reconciled. The plan instead records the
snapshot blob, PR, merge commit, byte length, Git blob, and SHA-256 of the exact
current source, then derives and validates the isolation migration from those
current bytes. The exact current plan SHA-256 is
`c1197c4a7699b55ecf46e9e4fdc413cfe6f5486a46fd4ea4a8c00e3db185c479`.

## Repositories that should be considered for new Zed packages

1. `claritas-viz/claritas-monorepo` — it contains the `claritas-clients` app, while
   four `claritas-viz-test` consumers already declare the missing
   `claritas-viz/claritas-clients` coordinate.
2. `memebank/mb-clients` — a reviewed coordinator blueprint exists and two
   `memebank-test` client consumers expose the missing package boundary, but the
   repository has no root manifest.
3. `3FA-app/3fa-infra` — the active infra repository is the main uncovered role
   in an otherwise package-enabled 3FA production/test family.
4. `cliptown/cliptown-infra` — the active infra repository can make deployment
   inputs reproducible alongside the existing interfaces, clients, libraries,
   CLI, web, and test packages.
5. `discrete-event-systems/discrete-event-systems.github.io` — the active site is
   not packaged, while the DES web/MCP manifests expose a family that still lacks
   published interface, library, client, and CLI roles.

These are recommendations, not silently created packages. Each needs its own
source-layout, ownership, dependency, publish-exclusion, and corresponding test
organization review before activation.

## Publication evidence

The checked-in plan records the exact source blob, proposal blob, proposal
SHA-256, repository, default branch, privacy/fork state, path, semantic recipes,
and phase for every mutation. Live pull request, exact-head check, merge, and
post-merge default-branch evidence will be appended after both bounded phases
complete and the entire 656-manifest fleet is rescanned with the released binary.
