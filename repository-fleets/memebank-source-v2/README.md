# MemeBank source-v2 carrier

This directory contains the repaired, deterministic, source-only carrier for the canonical MemeBank repository fleet.

## Why source-v2 exists

The original `repository-fleets/memebank-source.json` carrier is **not publishable**. Its base64 chunks were truncated during the original connector upload, so they do not match their recorded sizes or SHA-256 digests and cannot reconstruct the approved archive. The v1 files remain historical evidence only; no publisher may fall back to them.

Source-v2 was regenerated from the reviewed 13-repository bootstrap after:

- removing every `.git` directory and all repository-local history;
- adding an explicit `.gitmodules` file and exact child gitlinks to `memebank-monorepo`;
- scanning source filenames and contents for high-signal credentials, private keys, Google service-account material, and GitHub tokens;
- reconstructing all 13 repositories with a fixed author, timestamp, message, default branch, trees, root commits, and tracked-entry counts;
- splitting the archive into bounded 12,000-byte base64 chunks so connector transport cannot silently truncate them.

## Publication contract

A publisher must read `../memebank-source-v2.json` from a pinned commit and then:

1. Verify every chunk's exact byte count and SHA-256 digest.
2. Strictly base64-decode the concatenated payload.
3. Verify the archive's exact size and SHA-256 digest.
4. Reject absolute paths, traversal, case collisions, `.git` paths, links, devices, and oversized members.
5. Extract exactly the 13 approved repository roots and 170 regular source files.
6. Rebuild each repository with the fixed Git identity and timestamp and verify its exact tree, root commit, and tracked-entry count.
7. Publish child repositories before `memebank-monorepo`.
8. Materialize the monorepo's 11 mode-`160000` gitlinks exactly as recorded.
9. Create repositories as private with `main`; preserve the seven named legacy repositories.
10. Refuse to overwrite a nonempty remote unless its `main` is the exact approved root or a descendant under a separately reviewed update policy.
11. Verify every remote `main` SHA after publication.

`mb-infra` is the only canonical Kubernetes/Argo CD app-of-apps repository. The superseded name `memebank-infra` is forbidden.

Run the complete validator from repository root:

```bash
python3 repository-fleets/validate_memebank_source_v2.py
```
