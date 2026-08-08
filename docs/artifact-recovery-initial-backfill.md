# DEN-2797 initial artifact-recovery backfill

Generated from public-safe, bounded accessible-library batches on 2026-08-07 and 2026-08-08 and refreshed against current GitHub repository, branch, commit, and pull-request evidence.

## Result

| Metric | Count |
|---|---:|
| Ledger rows | 20 |
| Verified complete | 12 |
| Actionable | 8 |
| Missing repositories | 8 |
| Existing-repository artifacts already recovered, superseded, or on verified draft PRs | 8 |
| Blocked/ambiguous | 0 |

The checked-in deterministic generator is not a transcript export. It contains stable source file IDs, intended owner/repository identities, artifact digests and locators, current bounded GitHub evidence, and classification inputs. It contains no prompt body, hidden reasoning, email address, token, private key, or credential assignment.

## Duplicate prevention

The refreshed evidence scan found remote delivery for all five existing-repository artifacts:

- `canonical-cloud/canonical-api-server.rs` — merged PR #10 and later mainline corrections;
- `canonical-cloud/canonical-infra` — merged PR #4; PR #5 closed as superseded;
- `canonical-cloud/canonical-lib` — merged PR #1;
- `canonical-cloud/canonical-flutter` — open draft PR #1 at a later canonical-interface-aligned head;
- `ORESoftware/slack-ores-integrations` — merged PR #1 and later compatible hardening.

Those entries are complete for artifact-recovery purposes and are not sent to the local CLI worker. An open draft can remain review-blocked while still proving that the artifact reached a named remote branch and PR.

## Second bounded wave

The next older Library slice added three tangible artifacts after fresh remote verification:

- `DEN-602-github-admin-browser-hardening.patch` is already represented by merged `ORESoftware/ai-agent-coordinator.rs#56`, merge `8fa726f3d531d5ced99bfe3f67b6091f564e7d95`;
- `DEN-99-dependency-resolution.patch` is recovered to green draft `zed-pkg/zed-api-server.rs#16` at `b80f728dfed8f6cb005846015300a3ee19e01678`;
- `DEN-569-composition-model.patch` is recovered to green draft `fiducia-cloud/fiducia-brain.rs#26` at `c90cf14db6609ab550444af64200e22c8ee19327`, without modifying the merged DEN-1516 Rust model.

All three rows have complete repository, commit, branch, and PR evidence and therefore do not enter the local CLI queue. The two drafts still require normal product-owner review before merge.

## Recovery handoff

Only the eight genuinely missing repositories emit deterministic rows for local Codex task `019fd526-f34d-7f72-94fa-2da6185f2d74`. Every row requires a fresh GitHub read, secret scan, exact path staging, non-force push, draft PR, and post-write evidence read. A later batch must use the persisted cursor and revisit any source whose observation digest changes.

See `docs/nightly-artifact-recovery.md` for the complete contract and evidence inventory.
