# DEN-2797 cumulative artifact-recovery backfill

Generated from three public-safe, bounded accessible-Library waves on 2026-08-07 and 2026-08-08 and refreshed against current GitHub repository, branch, commit, pull-request, workflow, and Linear evidence.

## Result

| Metric | Count |
|---|---:|
| Ledger rows | 22 |
| Verified complete | 18 |
| Actionable | 4 |
| Missing repositories | 4 |
| Existing-repository artifacts already recovered, superseded, merged, or on verified draft PRs | 14 |
| Blocked/ambiguous | 0 |

The deterministic generator is not a transcript export. It retains stable source IDs, owner/repository identities, artifact digests and locators, bounded GitHub evidence, and classification inputs. It contains no prompt body, hidden reasoning, email address, token, private key, or credential assignment.

## First and second waves

The first two waves retain exact evidence for four sealed private repositories, Canonical and Slack artifacts, merged DEN-602 work, and green current-main draft PRs for DEN-99 and DEN-569. Existing remote delivery suppresses duplicate recovery even when product review remains open.

## Third bounded wave

Fresh cross-thread reads changed the local queue materially:

- `apostille-me/apme-mcp-server.rs` now exists publicly and has green draft PR #1 at `a255bcbafc7a3e6f69b05fd6502f66b43ffb4a43`;
- `embedded-alerts/eal-mcp-server.rs` now exists publicly and has green draft PR #1 at `0ba444300f16327fc07fe4e2f419031eb87cfad4`;
- `evento-globolo/evgl-mcp-server.rs` now exists publicly and has green draft PR #1 at `6a2cc7e239243b2a4d580953c576294e5bf4c557`;
- `hacker-house-medellin/hhm-mcp-server.rs` now exists publicly and has green draft PR #1 at `6eb3b797a73f7d7bd68de3f249793de0caf836cb`.

Each PR preserves the published implementation, removes stale post-publication instructions, pins and bounds CI, records immutable recovery evidence, and leaves real Zed lock/frozen-install work open. The sibling E2E repositories remain separate creation targets.

The DEN-2745 local transformer is not replayed. Its intended policy is already merged through `ORESoftware/project-registry#33`, branch-tip follow-up #34, `ORESoftware/k8s-cluster#1101`, and tracked-tip rollout #1103. Current repository reads and merged PR evidence mark both repository targets complete.

## Recovery handoff

Only four genuinely missing repositories now emit deterministic rows for local Codex task `019fd526-f34d-7f72-94fa-2da6185f2d74`:

1. `apostille-me/apme-e2e`
2. `embedded-alerts/eal-e2e`
3. `evento-globolo/evgl-e2e`
4. `hacker-house-medellin/hhm-e2e`

Every row requires a fresh GitHub read, intended-content secret scan, create-only behavior, exact path staging, non-force push, draft PR, and post-write evidence read. Later batches must use the persisted cursor and revisit materially changed sources.

See `docs/nightly-artifact-recovery.md` for the complete contract and evidence inventory.
