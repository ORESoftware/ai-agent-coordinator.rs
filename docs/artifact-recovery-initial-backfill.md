# DEN-2797 initial artifact-recovery backfill

Generated from the first public-safe, bounded accessible-library batch on 2026-08-07 and refreshed against current GitHub repository, branch, commit, and pull-request evidence.

## Result

| Metric | Count |
|---|---:|
| Ledger rows | 17 |
| Verified complete | 9 |
| Actionable | 8 |
| Missing repositories | 8 |
| Existing-repository artifacts already recovered or superseded | 5 |
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

## Recovery handoff

Only the eight genuinely missing repositories emit deterministic rows for local Codex task `019fd526-f34d-7f72-94fa-2da6185f2d74`. Every row requires a fresh GitHub read, secret scan, exact path staging, non-force push, draft PR, and post-write evidence read. A later batch must use the persisted cursor and revisit any source whose observation digest changes.

See `docs/nightly-artifact-recovery.md` for the complete contract and evidence inventory.
