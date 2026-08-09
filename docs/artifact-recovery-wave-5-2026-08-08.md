# Artifact recovery reconciliation — Wave 5

Generated: `2026-08-09T01:36:40Z`  
Linear umbrellas: `DEN-2797`, `DEN-3175`  
Window: 2026-06-29 through 2026-08-08

## Scope and safety boundary

Wave 5 reconciles the late ChatGPT/library artifacts created after the Wave 4 cutoff. It excludes `dancing-dragons` from all targets and writes. It uses ordinary fast-forward commits and draft pull requests only: no force push, history rewrite, automatic merge, production deployment, Cloudflare/DNS/Worker/R2 change, Kubernetes mutation, or database mutation.

Pasted credential values are not stored in this ledger. Binary ZIPs are not copied into Git history; each derivative archive is anchored by its byte length and SHA-256, while the live GitHub commits and pull requests remain the authoritative source.

Recovery is not acceptance. An open or draft pull request means the source is durable and reviewable, not approved for merge.

## Late artifacts

| Artifact | SHA-256 | Disposition |
| --- | --- | --- |
| `next-loggers-hardening-review-bundle.zip` | `026d3809b5468629d86c91632b03f73e79901f2d0eb3a0b711069ad496eb353b` | source is preserved in `ORESoftware/next-loggers.ts#14` |
| `canonical-cloud-canonical-api-server-rs.patch` | `2366b020941b48d451e3b6ca15b255edc163055601200643bbdc521c5589c024` | semantically reconciled into source-only `canonical-cloud/canonical-api-server.rs#20` |
| `graceful-shutdown-rollout(1).zip` | `2f792e204ac9ea17d5db0f785cb451233a43b4b2d8eca445c18f8744d8d63381` | mapped to the live polyglot PR/test matrix below |
| `chatgpt-work-recovery-2026-08-08.zip` | `552b0ebc094d35c6de25518cb4f021a6dbe07977c6575f39493669192ab8733a` | retained as a failed-attempt non-claim; it reported zero mounted repositories and zero code PRs |
| `3fa-app-project-registry-main-dc013985.zip` | `10533a6a63162c85c27b24721e7384fea2c1256e3b2c9c3226e2b5525b1aaba7` | registry documents are merged in `3FA-app/.github#18` |
| `api_docs_rollout_execution_report.md` | `75b09c4c322b5f90a0c0f8311166b1851049f8fa8ee740e21711eba98c6f7972` | retained as a blocked/not-published execution report |
| `api_docs_rollout_status.json` | `2e14c0989d0de8c864578b2b28b32732d3508639866d421aa42c4cd6a96bfb1c` | retained as a blocked/not-published status record |

## Canonical semantic reconciliation

The original `canonical-cloud/canonical-api-server.rs#19` transport branch mixed the intended Rust source with a packed migration payload and workflows that decoded, rewrote, committed, pushed, or deleted branch content. It was closed unmerged.

The replacement `#20` applies only `Cargo.toml`, `Cargo.lock`, `src/main.rs`, and `src/shutdown.rs` as one ordinary source commit, head `843add7e3124b800032427c653df8d069a90ed2d`. Both exact-head workflows passed:

- `ci`, run `31287827400`;
- `declarative-postgres`, run `31287827409`.

## Polyglot shutdown matrix

| Lane | Pull request | Exact head | Exact-head status |
| --- | --- | --- | --- |
| shared test contract | `fiducia-cloud-test/mcp-contract-e2e#7` | `ede6848cf207c8622834b63648e19b9ab34dfa1e` | two workflows green |
| Rust/Axum canary | `anticaptrad/act-api-server.rs#8` | `c61c9185bd5b76c3de53713b5d672b246a8444b5` | CI run `31288527614` green |
| Node/Fastify canary | `anticaptrad/act-ai-server.ts#2` | `d62d86ec927ef6b79ec2f7c2287ff314dbb17dfc` | CI green |
| Gleam/OTP canary | `ORESoftware/server.gleam#1` | `610ce59beb14f4f492607d8891790e3e989479c8` | test workflow green |
| Go canary | `ORESoftware/cm-pay-api#1` | `8ec708fbf3ccb3a54914ac8be735f92fb75077e2` | job never started: Actions billing/spending restriction |
| Go canary | `ORESoftware/cp-go-api#1` | `3f6598b91ccb9a7fa332b67e73b080a5cf38e345` | job never started: Actions billing/spending restriction |
| logging/runtime contract | `ORESoftware/next-loggers.ts#14` | `9b27646b0eb25c8888a1b24406430f9e5f1774f6` | six workflows green |
| REST test gate | `memebank-test/rest-api-contract-e2e#2` | `97ef7aeea9e663a3a3b817efaedf26cc9b830ab6` | two workflows green; pins product `a6d2975…` |
| OCR test gate | `memebank-test/ocr-pipeline-e2e#2` | `e80fcfcf04846eacb3d2f8a4e508832d438e5190` | two workflows green; pins product `1e18a41…` |
| MemeBank REST product | `memebank/mbk-rest-api#9` | `a6d2975345c5b7ca7d0dec6ccf4be3288778a9f5` | product workflows green |
| MemeBank OCR product | `memebank/mbk-ocr-api#26` | `1e18a41a3370a243646f0402be5c162a15267b85` | all nine fetched workflows green |

The Rust branch was resolved semantically without rewriting history: a source-level `watch_stdin_eof` boundary keeps real Ctrl-D support in production and disables process-global stdin observation in unit tests. The temporary FIFO CI workaround was then reverted in a later fast-forward commit.

## Provenance corrections

The two MemeBank test PR descriptions and two product PR descriptions were updated to the current immutable test heads, product heads, and successful run IDs. The snapshot manifests now agree exactly with the corresponding production heads.

## Explicit non-claims and blockers

- The API-docs evidence does not establish a Messaging-Intel rollout. It records missing local checkouts, an unavailable validator, no source PRs, no test-org gate, and no tracker synchronization.
- The two Go workflow failures are infrastructure-only on the available evidence: GitHub created no runner steps and annotated both checks with the billing/spending-limit restriction.
- No open pull request in this wave is claimed merged.
- No edge, deployment, database, or production mutation occurred.
- Credentials pasted into chat are intentionally absent from this commit and should be rotated.

## Validation

`validate_artifact_recovery_wave5.py` and its tests enforce:

- exact recovery window and excluded-target boundary;
- seven SHA-256-anchored late artifacts;
- sixteen live GitHub evidence rows;
- one merged PR, one closed superseded PR, and fourteen open review candidates;
- green Canonical source-only successor;
- exact MemeBank test-to-product pin equality;
- infrastructure-blocked classification for both Go lanes;
- deterministic JSON output and credential-pattern rejection.
