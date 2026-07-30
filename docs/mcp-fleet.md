# Rust MCP server fleet

Audit date: **July 30, 2026**

This document is the human-readable companion to [`mcp-fleet.json`](./mcp-fleet.json). The JSON file is authoritative for automation.

## Result

The current combined GitHub/Linear inventory contains **26 organization projects**:

- **18** have an existing Rust MCP repository and the previously merged CI/hardening baseline;
- **6** are accessible through GitHub but do not yet have an MCP repository;
- **2** exist in Linear but are not currently visible through the GitHub connector.

The connected GitHub action surface can create branches, files, issues, and pull requests, but it does **not** expose repository creation. The separate fail-closed repository-bootstrap capability tracked by `DEN-319` is implemented in the coordinator application but is not yet activated with live repository-administration credentials.

## Existing hardened servers

| Organization | Repository | Transport | Current disposition |
| --- | --- | --- | --- |
| 3FA-app | `3FA-app/3FA-mcp-server.rs` | stdio | Keep current baseline; queue shared-library/conformance migration |
| messaging-intel | `messaging-intel/msgint-mcp-server.rs` | stdio | Keep current baseline; pilot shared libraries |
| akrion-sim | `akrion-sim/akrion-mcp-server.rs` | stdio | Keep current baseline; queue conformance migration |
| athlet-o | `athlet-o/athleto-mcp-server.rs` | stdio | Existing real-process handshake smoke; queue full matrix |
| benefactor-cc | `benefactor-cc/benefactor-cc-mcp-server.rs` | stdio | Keep current baseline; queue conformance migration |
| canonical-cloud | `canonical-cloud/canonical-mcp-server.rs` | stdio | Keep required CI contexts; pilot shared libraries |
| claritas-viz | `claritas-viz/claritas-viz-mcp-server.rs` | stdio | Keep current baseline; queue conformance migration |
| daedalus-fab | `daedalus-fab/daedalus-fab-mcp-server.rs` | stdio | Keep current baseline; queue conformance migration |
| declarative-migrations | `declarative-migrations/declarative-migrations-mcp-server.rs` | stdio | Reference real-process stdio implementation |
| fiducia-cloud | `fiducia-cloud/fiducia-mcp-server.rs` | stdio | Reference mutation annotations and exact opt-in gates |
| anticaptrad | `anticaptrad/act-mcp-server.rs` | Streamable HTTP | Reference authenticated and bounded HTTP implementation |
| quaestor-ledger | `quaestor-ledger/quaestor-ledger-mcp-server.rs` | stdio | Keep current baseline; queue conformance migration |
| sagitta-stack | `sagitta-stack/sagitta-mcp-server.rs` | stdio | Reference stdout-purity and process-recovery implementation |
| shared-auth | `shared-auth/shared-auth-mcp-server.rs` | stdio | Keep current baseline; pilot shared libraries |
| scintilla-run | `scintilla-run/scintilla-mcp-server.rs` | stdio | Keep current baseline; queue conformance migration |
| rust-ssr-demos | `rust-ssr-demos/rust-ssr-mcp-server.rs` | Streamable HTTP | Reference strict HTTP protocol implementation |
| sonus-auris | `sonus-auris/sonus-auris-mcp-server.rs` | stdio | Keep current baseline; pilot shared libraries |
| usa-acc | `usa-acc/usa-acc-mcp-server.rs` | stdio | Keep current baseline; queue conformance migration |

No churn-only pull requests should be opened against these repositories. Project-specific changes should be driven by demonstrated failures under `DEN-779`/`DEN-855` or by the reviewed shared-library migration under `DEN-957`/`DEN-965`.

## Verified repository-creation gaps

| Organization | Required repository | Canonical Linear work | Blocker |
| --- | --- | --- | --- |
| cliptown | `cliptown-mcp-server.rs` | `DEN-162` | `DEN-319` |
| opto-sync | `opto-sync-mcp-server.rs` | `DEN-163` | `DEN-319` |
| voxletra | `vxl-mcp-server.rs` | `DEN-164` | `DEN-319` |
| zed-pkg | `zed-mcp-server.rs` | `DEN-165` | `DEN-319` |
| zed-pkg-test | `zed-pkg-test-mcp-server.rs` | `DEN-166` | `DEN-319` |
| file-tunnel | `ftnl-mcp-server.rs` | project-scoped issue created by this re-audit | `DEN-319` |

`file-tunnel` is newly added to the fleet inventory. Its repository name follows the existing `ftnl-*` family.

## GitHub visibility pending

| Organization | Expected repository | Existing Linear work | Required action |
| --- | --- | --- | --- |
| hypesiege | `hypesiege-mcp-server.rs` | `DEN-893` | Install/authorize the GitHub connector, then verify or create the repository |
| streempilot | `streempilot-mcp-server.rs` | `DEN-913` | Install/authorize the GitHub connector, then verify or create the repository |

These rows must not be marked missing or compliant until GitHub visibility is available.

## Baseline for every new server

1. Rust with a declared MSRV and pinned toolchain.
2. Read-only tools by default; mutations require explicit authorization, confirmation, idempotency, auditability, and denial-path tests.
3. Stdio stdout contains protocol frames only; diagnostics and telemetry go to stderr.
4. HTTP deployments fail closed without authentication, use normalized Origin allowlists, cap bodies, bound surfaced diagnostics, and set connection/total timeouts.
5. Tool names and URIs are deterministic; input schemas are objects; annotations accurately describe read-only, destructive, idempotent, and open-world behavior.
6. CI uses least-privilege permissions, immutable action pins, locked format/lint/test/build commands, a real protocol smoke gate, secret/conflict-marker scanning, and pinned RustSec auditing.
7. New repositories consume immutable releases/revisions of `ORESoftware/mcp-rust-libs` once `DEN-957` is unblocked; domain tools, schemas, and authorization stay local.

## Protocol lifecycle policy

Keep production compatibility with MCP `2025-11-25`. Treat the `2026-07-28` stateless lifecycle as a controlled migration target: validate the final specification and official Rust SDK release, add `server/discover` and per-request version-metadata coverage, preserve legacy clients during rollout, and avoid fleet-wide adoption from a release candidate or moving branch.

## Canonical coordination

- `DEN-161` — portfolio repository and monorepo standardization
- `DEN-319` — enable and prove safe repository creation
- `DEN-779` — protocol, transport, safety, and operability conformance
- `DEN-855` — repository-by-repository evidence audit
- `DEN-852` — reusable conformance harness and machine-readable report
- `DEN-957` — shared Rust MCP libraries
- `DEN-965` — controlled fleet migration to pinned shared releases

The ordered next gate is to activate `DEN-319`, prove repository creation with the already selected low-risk canary, then create the six verified missing repositories and re-audit the two access-pending organizations.
