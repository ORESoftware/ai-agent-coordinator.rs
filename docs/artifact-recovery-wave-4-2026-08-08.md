# Artifact recovery backfill — Wave 4

Date: 2026-08-08  
Linear umbrella: `DEN-2797`  
Window: 2026-06-14 through 2026-08-08

## Scope and safety boundary

This wave reconciles code-bearing ChatGPT file-library artifacts against current GitHub evidence. It excludes the `dancing-dragons` space from targets and writes. It does not use credentials copied into prompts, does not place credentials in source or metadata, and performs no Cloudflare, R2, DNS, Kubernetes, database, or production-service mutation.

Recovery status and product acceptance are separate. An open or draft PR means the artifact is durably preserved on GitHub; it does not mean the change is approved or merge-ready.

## Result

The deterministic observation batch contains **20 rows**:

- 13 repository-boundary rows: one merged test-org canary and twelve production remediations;
- one Zed CLI lock-ownership candidate;
- one MemeBank–ClipTown headless test-fleet candidate;
- one merged namespace-contract successor;
- four Shared Auth source/test lanes.

Every row has a live repository, commit/branch evidence, and a pull request. No repository-creation action is queued by this wave.

## Repository boundary bundle

Policy: `repo-boundary/infra-not-app-submodule/v1`  
Library archive: `file_00000000f330822f878003559199ac9b`  
Archive SHA-256: `7686cda2e91b8d0c55a1cbb7c557bd8be3c66b525f9e8cb2c27b78345600c2d4`  
Linear: `DEN-3166`

The canary `zed-pkg-test/workspace-monorepo#2` is merged at head `7afae61978ab9603a5844dba8fc7a052940db691`; all three exact-head workflows passed before merge.

Production matrix:

| Repository | PR | Exact head | State |
| --- | ---: | --- | --- |
| `athlet-o/athleto-monorepo` | #5 | `e100159a13b1020e18b6f07a64ca7d512eb83b2c` | merged |
| `daedalus-fab/daedalus-monorepo` | #5 | `190dd61393547653a483ec06a51f59916ebedc78` | open |
| `drone-mngr/drone-mngr-monorepo` | #5 | `4886596f51ea736bacf73ac6b3b21423d5c8f0b4` | merged |
| `drone-mngr/laser-ptr-ctrl-monorepo` | #1 | `bd68c6ad0e577faeee43c6ed4d8bfda941b5ded2` | merged |
| `fiducia-cloud/fiducia-monorepo` | #37 | `6b3f64ede8a678b95d691b0ce24796aee8d4d4c3` | merged |
| `file-tunnel/ftnl-monorepo` | #15 | `b4a25278a0f8db8334a8ea3e625254209ef41397` | merged |
| `hypesiege/hypesiege-monorepo` | #19 | `98a6ebb70f11d3836f666cb5731be05814a836e7` | merged |
| `memebank/memebank-monorepo` | #2 | `30baee38939cdbc672c5a53c43fb2747e4f58402` | merged |
| `quaestor-ledger/quaestor-monorepo` | #9 | `2db5247c6709eca59b68d1fc9d0981ef54c987d3` | open |
| `scintilla-run/scintilla-run-monorepo` | #5 | `8b20c8142e68bf5039d189099be40c548d7862a2` | open |
| `sonus-auris/sonus-auris-monorepo` | #23 | `8dccad31bbb93fd7573b359062ad4aecee754a0d` | merged |
| `StreemPilot/streempilot-monorepo` | #19 | `72ab9bf261b4841d7d03bad1335e24242fa6f537` | open |

The four open PRs are preserved without merge claims. They require their ordinary repository CI/review gates; this wave does not bypass runner-allocation or protection failures.

## Other recovered artifacts

| Artifact | GitHub evidence | Linear | State |
| --- | --- | --- | --- |
| Zed CLI Windows attribution archive, SHA-256 `f2dd3a4c5adc14d204d0df6154f69ceb327bab125634bfb18f1ee277d8df6344` | `zed-pkg/zed-cli#246`, head `7897b7f82df13729672c4b12dbb7934ed47a024a` | `DEN-2076` | draft; exact six-file candidate certified, serially blocked |
| MemeBank–ClipTown headless archive, SHA-256 `1297a7f0ff9b263767f662c10015bd862d3db4e78288debb5b4cb37ce45b58b1` | `memebank-test/cliptown-image-interop-e2e#4`, head `02edd3d375a49fc93fe92ce5d2889e892cd45a4d` | `DEN-2259`, `DEN-2918` | draft; public lanes pass, private source reads fail closed |
| Namespace migration evidence, SHA-256 `1f2a47b1cc7ff14e6279205f2897e9fbf9be75e853c78bba06f4883c00527f80` | `ORESoftware/k8s-cluster#1123`, final head `d742269474f844236492f9185a5640f193ea9817` | `DEN-2786`, `DEN-2949` | merged; archived ratchet head `4a4ea0e...` is a superseded intermediate |

## Shared Auth reconciliation

Library archive: `file_000000003d1c822fa0ef62d4c2eb4d9a`  
Archive SHA-256: `f0aabf6916461aba60089db3942222cb6f14059add9bf1d0a05074934c19091f`

| Lane | GitHub evidence | Linear | State / non-claim |
| --- | --- | --- | --- |
| SSH public-key replay | `shared-auth/shared-auth-server.rs#66`, head `6450ae7a6bef7e294f062802a58cd66f1382a44a` | `DEN-2768` | draft; no opaque-token migration or provider approval claimed |
| OpenPGP/Kerberos source contracts | `shared-auth/shared-auth-server.rs#67`, head `2c42e81395fb75fbf1fa3e2985f38be59270491d` | `DEN-2771`, `DEN-2772` | open; contracts only, no runtime implementation claim |
| OpenPGP provenance certification | `shared-auth-test/server-api-contract-e2e#10`, head `b98e8f744a7137cebdbe54955310a7dc41e94106` | `DEN-2771` | open fallback publication; no authentication authority granted |
| Kerberos/SPNEGO certification | `shared-auth-test/server-api-contract-e2e#16`, head `4308542deb55ca53f7dfb51aed84fe30a53e1d77` | `DEN-2772` | open fallback publication; deterministic local KDC and 7/7 policy tests, no production bridge claim |

The proposed dedicated repositories `shared-auth-test/openpgp-provenance-e2e` and `shared-auth-test/kerberos-spnego-e2e` do not exist. Wave 4 therefore records only the existing fallback repository and does not fabricate repository or PR evidence.

## Validation

The Wave 4 implementation is standard-library Python. Its tests enforce:

- exactly 20 rows and no queued repository creation;
- exclusion of the omitted space from target identities;
- exact PR, branch, and commit evidence for every claim;
- nine merged and four open repository-boundary rows, counting the merged canary;
- no merge claim for ten open candidates across all workstreams;
- semantic-successor handling for the namespace archive;
- fallback-only Shared Auth test publication;
- deterministic byte-for-byte JSON output.

## Remaining gates

- Review and ordinary CI remain authoritative for every open PR.
- `zed-pkg/zed-cli#246` stays draft until its predecessor lands, the branch is semantically rebased, and exact-head certification is rerun.
- `memebank-test/cliptown-image-interop-e2e#4` stays draft until the scoped private-source read path is available; no broad token fallback is allowed.
- Shared Auth source/runtime work remains split by credential-plane boundary and must not be collapsed into one merge.
- The four absent sibling E2E repositories remain tracked separately under `DEN-319`, `DEN-2286`, `DEN-2288`, `DEN-2291`, and `DEN-2294`; this wave does not claim they were created.
