# HypeSiege and StreemPilot live installation audit — 2026-08-03

Linear: DEN-877, DEN-881, DEN-896, DEN-319

## Executive result

Both GitHub organizations are connected through admin-capable GitHub App installations and both currently expose eleven private repositories on `main`. Neither canonical fleet is complete.

| Organization | Connected repos | Exact canonical names | Legacy aliases | Missing canonical repos | Current monorepo gitlinks | Required gitlinks |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `hypesiege` | 11 | 10 | 1 | 5 | 10 | 14 |
| `StreemPilot` | 11 | 10 | 1 | 7 | 10 | 16 |

An installed App or a nonempty organization is not completion evidence. Completion requires every canonical identity, the intended private execution visibility, `main`, admin-capable connected access, reviewed repository history, and the full child-before-monorepo topology.

## HypeSiege gaps

Missing canonical repositories:

1. `hypesiege/hypesiege-analytics.rs`
2. `hypesiege/hypesiege-cli.rs`
3. `hypesiege/hypesiege-connectors`
4. `hypesiege/hypesiege-publishing-worker.rs`
5. `hypesiege/hypesiege-scheduler.rs`

`hypesiege/hypesiege-cli` exists but does not satisfy the canonical `hypesiege-cli.rs` identity. Preserve its history while resolving migration or archival; do not silently rename the sealed ledger or count both identities as the same repository.

The current monorepo lists ten child gitlinks. After the five canonical gaps and CLI migration are resolved, reseal the monorepo against exactly fourteen canonical child repositories.

## StreemPilot gaps

Missing canonical repositories:

1. `StreemPilot/streempilot-chat.rs`
2. `StreemPilot/streempilot-cli.rs`
3. `StreemPilot/streempilot-compositor.rs`
4. `StreemPilot/streempilot-destinations`
5. `StreemPilot/streempilot-media-router.rs`
6. `StreemPilot/streempilot-recording.rs`
7. `StreemPilot/streempilot-webrtc-adapter.rs`

`StreemPilot/streempilot-cli` is likewise a legacy alias rather than the canonical `.rs` identity. The current monorepo lists ten child gitlinks and must eventually pin exactly sixteen canonical children.

## Publication safety

The repository-administration contract merged through `ORESoftware/k8s-cluster#583` keeps the sealed schema-v2 public-intent ledger immutable while projecting a deep-copied private execution manifest. Publication remains create-only, no-force, exact-SHA verified, and private-visibility verified. Existing repositories with later legitimate history must never be reset to the old sealed root commit.

The correct order is:

1. create and push missing leaf repositories from their sealed histories;
2. reconcile legacy CLI histories into the canonical `.rs` identities without data loss;
3. verify every connected repository remains private, on `main`, and accessible through the App;
4. update each monorepo to exact current canonical child commits;
5. run clean-checkout, CI, security, and GitHub↔Linear lifecycle evidence;
6. close the foundation issues only after direct remote verification.

## Reproducible audit

Run:

```bash
python3 scripts/audit_hypesiege_streempilot_remote.py \
  --manifest repository-fleets/hypesiege-streempilot.json \
  --snapshot repository-fleets/hypesiege-streempilot.remote-audit-2026-08-03.json
```

Use `--require-complete` only for a protected final-certification gate. It intentionally exits nonzero while either organization has a canonical gap, alias, topology drift, visibility drift, default-branch drift, or missing admin access.
