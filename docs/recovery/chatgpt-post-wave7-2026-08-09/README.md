# ChatGPT post-Wave-7 artifact reconciliation — 2026-08-09

This ledger records the evidence-backed delta discovered after the merged Wave 5–7 recovery work. It covers the requested rolling 30-day window because the retained recovery carriers span 40 days, but it does **not** claim access to a complete raw export of every ChatGPT transcript. The reconciliation sources were retained conversation context, persistent Library artifacts, and current immutable GitHub and Linear state.

This is recovery and tracker evidence, not product acceptance, release approval, deployment authorization, or proof that every historical chat assertion was true.

## Safety invariants

- No force push, `--force-with-lease`, rebase, default-branch direct write, or shared-history rewrite.
- No automatic merge and no conflict-side shortcut such as `ours` or `theirs`.
- A semantic conflict must be reviewed against the merge base, both sides, relevant history, API/schema/migration intent, and tests; ambiguity remains review-required.
- No chat-pasted GitHub PAT, Linear token, private key, decrypted environment value, recipient secret, participant data, or production credential was copied into GitHub, Linear, commands, logs, or artifacts.
- Existing newer work is preferred over replaying a stale archive. Superseded changes are recorded rather than duplicated.
- Documentation-only recovery is labeled as such and cannot satisfy an executable acceptance criterion.

## Retained carriers

The following local recovery inputs were checksum-verified during this pass. They are identified here but are not committed as binary payloads.

| Carrier | Bytes | SHA-256 | Disposition |
|---|---:|---|---|
| `chatgpt-40-day-reconciliation-wave7-2026-08-09.zip` | 600293 | `7612da788830571e1ffbbc1b13766e6d1b940c3b42c8fe017f2a4a053ddf9d3c` | Cumulative 40-day recovery source; older durable GitHub evidence already landed through Wave 6/7. |
| `chatgpt-work-reconciliation-2026-08-08-final.zip` | 23283 | `6bb8ab58891987ca1f9c685553db2be5e901b9d78703e1ee16a53bf1956bb1b2` | Earlier bounded recovery source; superseded where Wave 5–7 contains richer evidence. |
| `linear_github_next_work_bundle.zip` | 29871 | `fd796174340f2c218d7b44462edb1124f3b1a62f125cc2efb03ec5682e4b19e4` | Post-Wave-7 targeted patch and tracker queue; reconciled item by item below. |
| `fleet-audit-2026-08-09.zip` | 27448 | `779f2e328d7377b27f614059529a3bcc2f3d92f32f3ebe7fbacc83983c84b647` | Post-Wave-7 fleet findings; stale or already-landed patches were suppressed. |

The cumulative recovery histories were already made durable before this delta pass:

- `ORESoftware/ai-agent-coordinator.rs#136`, Wave 5, merged as `78f4ca76e725fd8cac45589cf83458d672a7c2fd`.
- `ORESoftware/ai-agent-coordinator.rs#137`, Wave 6/7, merged as `973fb8a6b345e50dce09260028dfca283890449c` after a normal semantic merge with current `main`.

Those histories were not replayed.

## Newly published review artifacts

### 1. 3FA production SOPS release policy

- Repository: `3FA-app/3FA-desktop.rs`
- Draft PR: `#27`
- Base: `c8fd0e880643b6bd3fe946433b783a559188501f`
- Head: `c6be67d0913a0f879b65cc2efb950f127bb9abbf`
- Branch: `agent/den-3399-prod-sops-release-gate`

The branch adds a fail-closed production policy verifier, calls it before the production release build decrypts values, and runs it in the existing security workflow. Production must have at least two independently controlled age recipients and at least one recipient not used by dev.

The current policy still has one bootstrap recipient shared by dev and prod. Therefore the exact-head gate is intentionally red until an owner-authorized recipient is added and production ciphertext is rekeyed. No private key, recipient secret, decrypted value, ciphertext, or dependency lock changed.

### 2. Bounded MCP response-state telemetry

- Repository: `3FA-app/3FA-mcp-server.rs`
- Draft PR: `#24`
- Base: `5f659c3dca32e8696632d5b2199f99bcac2fb7d5`
- Head: `b0a9a73822e281ed92f76ae5e27a09fee8428681`
- Branch: `agent/den-3394-response-state-telemetry`

The patch preserves the existing error bit and adds one bounded response-state dimension: `complete`, `input_required`, `task`, `transport_error`, or `unknown`. It does not record tool arguments, output bodies, task payloads, exception details, credentials, account/device identifiers, or ciphertext/key material.

GitHub rejected the exact-head Actions run before runner assignment because of the organization billing/spend-limit state. No code executed, so the PR remains infrastructure-blocked rather than classified as a code failure.

### 3. Messaging Intel executable route hardening plan

- Repository: `messaging-intel/msgint-api-server.rs`
- Draft PR: `#24`
- Base: `41a17aac301e1226f17c91b61c18c7b792933b59`
- Head: `79875ef57e007620aa45e9eddf60dccac30698d3`
- Branch: `agent/den-986-self-report-route-plan`

Current `main` contains capability, origin, cookie, and body-limit primitives but no database-backed Axum handlers for:

- `POST /v1/self-report/exchange`
- `GET /v1/self-report/session`
- `POST /v1/self-report/submit`

Registering route strings without the atomic invitation/session storage boundary would falsely claim completion. The draft therefore preserves the exact implementation, body-stream, replay/concurrency, cookie-lifecycle, and listener-level test plan without enabling a public route, sending email, collecting participant data, or changing production behavior.

### 4. Sonus retention/shutdown lifecycle plan

- Repository: `sonus-auris/sonus-auris-desktop.rs`
- Draft PR: `#32`
- Base: `b16fcdfac5940d33b63b519f81aae234e39b1300`
- Head: `a2652128c8f323eae53c0e426ad88d8d8e6a3d39`
- Branch: `agent/den-661-retention-shutdown-plan`

The current head repaired duplicated merge debris and moved quit cleanup off the UI thread. A blind blocking `Drop` implementation could restore the macOS quit hang. The plan instead requires explicit framework-exit coverage, a bounded nonblocking abnormal-drop fallback or proof of framework guarantees, restoration of the retention regression, worker failure/panic/timeout matrices, retry-gate proof, background-on-close coverage, and exact-head Linux/macOS/Windows release evidence.

This PR changes documentation only and does not claim release certification.

## Existing work confirmed and not duplicated

### Semantic merge safety

- Foundational fail-closed preview guard: `ORESoftware/ai-agent-coordinator.rs#140`, merged.
- Review-only contract follow-up: draft `#143`, exact head `ceed168da63afda0549c997a77e99fcafaeb5043`.
- Current-base Rust/export repair isolated separately: draft `#144`, exact head `365091bc4391461b07023e1e54cefab1fce1bc24`.

PR #143 keeps `safe_to_publish=false` in every state, allows only a clean preview to set `safe_to_open_review_pr=true`, records the complete two-sided path union, and preserves conflict paths separately. Its focused semantic-guard, source-snapshot, and Linear bootstrap checks are green; unrelated current-base defects remain isolated in #144.

### Other suppressed duplicates

- `cliptown/cliptown-lib-core#1` and `zed-pkg/zed-lib-core#8` already landed the retained hardening intent.
- The retained Kubernetes relay-classifier fix is already represented by the current live PR; the stale overlapping PR is superseded.
- The Canonical marketing Nano ID finding is covered by the current Dependabot PR that updates the lockfile from 3.3.16 to 3.3.18 with green observed workflows. The older override patch was not replayed.
- No already-merged Wave 5–7 recovery file was duplicated.

## Recovered Zed histories

### Cursor

`zed-pkg/zed-cursor` now exists. The formerly abbreviated candidate resolves to full SHA:

`97ca863cec915382c2d2b75077f9e710f3a1668a`

It is the root commit and an ancestor of current `main@b6d5cc894f6ee1cb7812dec3748c76a580877529`. No additional recovery push is required.

### Windsurf

`zed-pkg/zed-windsurf` now exists. The canonical root is:

`4fae5d127cb1403a5274721da5682edd3cb38e3f`

It has no parents and is an ancestor of current `main@efaeae67976fd2d40e796cefb69c65b17c29b362`. No additional recovery push is required.

An earlier historical Windsurf state, `3d14d85716ef2a84bd08893a97940ff810c0fb44`, remains checksum-only evidence:

- source ZIP SHA-256: `93d07e7b0e7c87358875ea09b6a6a372c1e64def4a49a7ecc795f6edaba32ac7`
- Git bundle SHA-256: `1ea7dba864b984872b52468aecf90a6893e10e9128d3ff7110b2c822e4a53739`

The object is not present in the live repository, and an exact filename/time-window Library sweep did not recover the archive bytes. It is therefore **not** represented as landed, mergeable, or reconstructable in this pass. The later canonical root is the live authority.

## Linear reconciliation performed

Current immutable evidence required these tracker corrections:

- `DEN-986`: changed from Done back to In Progress because executable exchange/session/submission routes and the synthetic email-to-submission E2E are absent.
- `DEN-389`: changed from Done back to In Progress because its merged evidence records 0/11 gates approved, no selected commit/digest, every activation field `TODO`, and capture suspended.
- `DEN-661`: changed from Done back to In Progress. Historical PR #19 did merge normally, but current-main lifecycle and three-OS recertification remain open.
- `DEN-3399`: attached 3FA desktop PR #27 and recorded the intentional production-recipient/rekey blocker.
- `DEN-3394`: attached MCP PR #24 and classified the exact-head Actions result as billing-blocked before execution.

`DEN-2775` remains In Progress for direct-controller integration and the broader affected-repository build sweep. `DEN-2797` remains In Progress for continuous scheduled recovery, GitHub Projects v2 reconciliation, and every remaining row to receive durable landing or an explicit inaccessible/superseded disposition.

## Remaining blockers and non-claims

1. No complete raw transcript export was available; the pass is bounded by retained 40-day recovery carriers, cross-chat context, Library artifacts, and live connector evidence.
2. GitHub Projects v2 synchronization is not exposed by the current write surface and is not claimed.
3. Several organizations remain unable to start GitHub Actions jobs because of billing/spend-limit or runner-allocation state. A zero-step result is not test evidence.
4. 3FA production release remains fail closed until independent production recipient ownership and rekey evidence exist.
5. Messaging Intel participant routes and email-to-submission E2E are not implemented or certified.
6. Sonus current-main shutdown/retention lifecycle is not three-OS release-certified.
7. The historical Windsurf `3d14d857...` bytes were not recovered and must not be synthesized from the later live root.
8. New draft PRs are review artifacts only; none was merged, deployed, or promoted in this pass.

## Credential boundary

The credentials pasted into chat were not used or persisted. Existing authenticated GitHub and Linear app connections performed the writes. Because the plaintext values were disclosed in a conversation, they should be revoked and replaced through the existing external-secret/GitHub-App contract; no replacement value belongs in this ledger.
