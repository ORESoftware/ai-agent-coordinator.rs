# Canonical public repository

The canonical open-source home of the generic AI-agent coordinator is now:

- [`agent-pontifex/ai-agent-coordinator.rs`](https://github.com/agent-pontifex/ai-agent-coordinator.rs)

The reusable protocol contracts and typed Rust clients live in:

- [`agent-pontifex/agent-sdk.rs`](https://github.com/agent-pontifex/agent-sdk.rs)

This `ORESoftware/ai-agent-coordinator.rs` repository remains available for
historical provenance and ORESoftware-specific portfolio automation. New generic
leased-job behavior, public API changes, releases, and community issues belong
in Agent Pontifex. The repositories are independent GitHub repositories; changes
are not assumed to mirror automatically in either direction.

## Product-specific downstream implementation

Fiducia Cloud maintains a separate supervised coordinator/control-plane
implementation. Fiducia CAS/election authority, linearizable leases, fencing,
review workflows, tenancy, and customer-operated persistence are advertised as
`fiducia.*` extensions and remain downstream.

The Fiducia coordinator must not claim direct `coordinator.jobs.*` compatibility
until an explicit adapter exists. That adapter must preserve lease ownership and
fencing semantics.

## Contribution routing

- Generic coordinator bugs and leased-job features:
  `agent-pontifex/ai-agent-coordinator.rs`
- Protocol, compatibility, and SDK changes: `agent-pontifex/agent-sdk.rs`
- ORESoftware-only portfolio automation and historical maintenance: this repository
- Fiducia-specific supervision and authority: the corresponding private
  `fiducia-cloud` repository

Before changing a public wire contract, update the SDK fixtures and the
independent conformance lane in
[`fiducia-cloud-test/control-plane-e2e`](https://github.com/fiducia-cloud-test/control-plane-e2e).
The initial bridge-and-coordinator release gate is reviewed in
[`control-plane-e2e#3`](https://github.com/fiducia-cloud-test/control-plane-e2e/pull/3).
