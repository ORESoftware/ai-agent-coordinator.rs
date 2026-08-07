# DEN-2797 initial artifact-recovery backfill

Generated from the first public-safe, bounded accessible-library batch on 2026-08-07.

## Result

| Metric | Count |
|---|---:|
| Ledger rows | 17 |
| Verified complete | 4 |
| Actionable | 13 |
| Missing repositories | 8 |
| Existing-repository local artifacts | 5 |
| Blocked/ambiguous | 0 |

The checked-in deterministic generator is not a transcript export. It contains stable source file IDs, intended owner/repository identities, artifact digests and locators, current bounded GitHub evidence, and classification inputs. It contains no prompt body, hidden reasoning, email address, token, private key, or credential assignment.

## Recovery handoff

The 13 unresolved entries emit deterministic rows for local Codex task `019fd526-f34d-7f72-94fa-2da6185f2d74`. Every row requires a fresh GitHub read, secret scan, exact path staging, non-force push, draft PR, and post-write evidence read. A later batch must use the persisted cursor and revisit any source whose observation digest changes.

See `docs/nightly-artifact-recovery.md` for the complete contract and evidence inventory.
