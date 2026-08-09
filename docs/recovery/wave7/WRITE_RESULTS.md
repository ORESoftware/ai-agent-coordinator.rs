# Wave 7 remote-write results — 2026-08-09

This document records the remote writes performed after the 40-day ChatGPT/Library reconciliation covering **2026-06-30 through 2026-08-09**.

## Published branches and pull requests

| Program | Repository | Branch | Head | Pull request | State |
|---|---|---|---|---|---|
| Led Dynamo recovery | `led-dynamo/.github` | `agent/den-3159-led-dynamo-recovery-seeds` | `fb772d0f65113ebcb6c8026afc4467ddf168e977` | #24 | Draft; privacy passed; baseline rerun after narrow `.b64` carrier policy fix |
| Canonical Docs recovery | `canonical-cloud/.github` | `agent/den-1049-canonical-docs-recovery-seed` | `18214bdc1bdf6d22d99eed4f4c100d0574e848d1` | #19 | Draft; mergeable; both organization checks passed |
| 40-day recovery coordination | `ORESoftware/ai-agent-coordinator.rs` | `agent/den-2797-artifact-recovery-wave-6` | this branch | #137 | Draft; Wave 7 evidence appended here |

## Semantic conflict decisions

- Existing tested Led Rust source and Zed manifests were retained rather than replaced by older donor trees.
- The safer recovered publication behavior was merged into the current Led publishers: fail closed on existing targets, dirty/non-`main` worktrees, unrelated remotes, and missing prerequisites; accept authenticated `gh` or an environment token without embedding credentials in Git URLs.
- Exact complete Git histories for `leddy-sync` and `leddy-mcp-server.rs` were preserved as checksum-pinned bundle carriers without rewriting the `.github` repository history.
- The Led baseline validator keeps credential and workflow checks intact while exempting only checksum-pinned `.b64` carriers from the prose trailing-newline rule.
- Canonical Docs is preserved as a verified source archive because the retained recovery did not include its original `.git` object database. Any future target commit must be represented as a new reconstructed commit, not an original historical head.
- Already-merged Evento and HHM publisher work was not replayed. Review-gated Apostille and Embedded Alerts work was not falsely represented as validated or published.

## Still blocked at repository creation

The connected GitHub app does not expose organization repository creation, and the shell runner has no working GitHub transport. These target repositories remain absent:

- `led-dynamo/leddy-sync`
- `led-dynamo/leddy-mcp-server.rs`
- `canonical-cloud/canonical-docs`
- `evento-globolo/evgl-e2e`
- `hacker-house-medellin-test/hhm-e2e`
- `apostille-me/apme-e2e`
- `embedded-alerts/eal-e2e`

The exact histories/source seeds are durable in the PRs above. Repository creation and final normal pushes remain tracked by `DEN-319`; no force push, rebase, or history replacement is authorized.

## Package evidence

The complete local Wave 7 delivery remains verified with these primary digests:

- ZIP: `7612da788830571e1ffbbc1b13766e6d1b940c3b42c8fe017f2a4a053ddf9d3c`
- TAR.GZ: `73f540a22ee554673e342c50d1662b45c1fc58115e777c14fbf192878579377e`
- Git bundle: `e7fe4b6491c1d8a84e579462b611e601befca88aecc219a67a6fe7778c804441`

No pasted GitHub or Linear credential value is present in this branch or any recovery carrier.
