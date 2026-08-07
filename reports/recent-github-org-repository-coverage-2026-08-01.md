# Recent GitHub organization repository coverage

Audit time: 2026-08-02 UTC. Organization window cutoff: 2026-07-28T01:52:00Z (2026-07-27 20:52 America/Lima).

Repository counts below use the GitHub App installation-scoped inventory, so private repositories are included. The HypeSiege/StreemPilot canonical comparison uses the reviewed schema-v2 ledger at `5d9a0c2cb44dff607bc3953954ce4b9af08e5789`.

## Organization inventory

| Organization | Repositories visible to installation | State |
|---|---:|---|
| `networking-components` | 6 | populated |
| `file-tunnel` | 11 | populated; monorepo submodule PR blocked by private sibling checkout |
| `StreemPilot` | 11 | populated but canonical fleet incomplete |
| `streamkore` | 0 | empty; no reviewed repository charter found |
| `OmniBlitz` | 0 | empty; no reviewed repository charter found |
| `hypesiege` | 11 | populated but canonical fleet incomplete |
| `hypeblitz` | 0 | empty; no reviewed repository charter found |
| `channelsiege` | 0 | empty; no reviewed repository charter found |
| `unreal-unity-poc` | 24 | populated |
| `meta-agents-demo` | 2 | populated |

## HypeSiege canonical coverage

The reviewed ledger requires 15 canonical repositories. Ten canonical names currently exist. The organization also contains the noncanonical legacy name `hypesiege-cli`, while the ledger requires `hypesiege-cli.rs`.

Missing canonical repositories:

- `hypesiege-analytics.rs`
- `hypesiege-cli.rs`
- `hypesiege-connectors`
- `hypesiege-publishing-worker.rs`
- `hypesiege-scheduler.rs`

## StreemPilot canonical coverage

The reviewed ledger requires 17 canonical repositories. Ten canonical names currently exist. The organization also contains the noncanonical legacy name `streempilot-cli`, while the ledger requires `streempilot-cli.rs`.

Missing canonical repositories:

- `streempilot-chat.rs`
- `streempilot-cli.rs`
- `streempilot-compositor.rs`
- `streempilot-destinations`
- `streempilot-media-router.rs`
- `streempilot-recording.rs`
- `streempilot-webrtc-adapter.rs`

## Delivery status during this audit

Merged with green CI:

- `hypesiege/hypesiege-interfaces` PR #1
- `hypesiege/hypesiege-api-server.rs` PR #10
- `hypesiege/hypesiege-mcp-server.rs` PR #1
- `StreemPilot/streempilot-interfaces` PR #1
- `StreemPilot/streempilot-api-server.rs` PR #8
- `StreemPilot/streempilot-clients` PR #2
- `StreemPilot/streempilot-mcp-server.rs` PR #1

Still gated:

- `hypesiege/hypesiege-api-server.rs` PR #9: semantic two-parent auth/outbox merge and full Rust validation in progress.
- `file-tunnel/ftnl-monorepo` PR #5: blocked because the repository-scoped Actions token cannot clone the private sibling submodule; do not merge until checkout is reproducible and all suites pass.
- Twelve canonical HypeSiege/StreemPilot repositories remain unpublished even though deterministic histories are sealed in the reviewed fleet ledger.
- The four empty recent organizations require reviewed product/repository charters before repositories are invented or published.
