# MemeBank source-v2 validation contract

**Tracking:** DEN-1005, DEN-1043, DEN-319  
**Scope:** `repository-fleets/memebank-source-v2.json`, its chunked payload, and deterministic reconstruction of the thirteen canonical MemeBank repositories.

The source-v2 carrier is transport material for an authorized publisher. It is not evidence that `github.com/memebank` contains the repositories, that a branch was pushed, or that organization governance was applied. Remote creation evidence must still be recorded against the live organization.

## Trust boundaries

The validator treats the manifest, chunk files, encoded archive, archive metadata, tar member names, file modes, source roots, commit metadata, repository trees, expected commits, and monorepo gitlinks as mutually checked inputs. A successful check requires all layers to agree:

1. the manifest names exactly thirteen repositories in canonical order;
2. the fleet remains private, targets `main`, and forbids `memebank-infra`;
3. every chunk stays inside the checkout, is a regular non-symlink file, and matches its byte count and SHA-256 digest;
4. the joined payload is strict base64 and reconstructs the exact approved tarball digest;
5. archive members stay under `memebank-source-v2/`, cannot traverse, cannot contain `.git`, cannot collide case-insensitively, and cannot be links or special files;
6. member and aggregate extraction sizes remain bounded;
7. extraction produces exactly the approved repository roots and source-file count;
8. each reconstructed Git tree, root commit, and tracked-entry count matches the manifest;
9. every monorepo gitlink pins the exact approved child repository head.

No validation step reads credentials or performs network I/O.

## Permanent regression suite

`repository-fleets/test_validate_memebank_source_v2.py` adds synthetic attack and drift cases around the complete reconstruction check. It covers:

- organization, visibility, branch, source-root, repository-order, repository-record, tracked-entry, and gitlink drift;
- absolute paths, parent traversal, backslashes, empty components, NUL bytes, foreign roots, and embedded `.git` metadata;
- symlinks and special archive members;
- exact duplicate and case-colliding archive paths;
- per-member and aggregate extraction limits;
- preservation of the exact thirteen repository roots and executable-mode semantics;
- chunk path escape, missing chunks, symlinked chunks, digest mismatch, invalid base64, and final archive digest mismatch.

The suite deliberately uses only Python's standard library. It can run in a clean checkout without network access:

```bash
python3 -m py_compile \
  repository-fleets/validate_memebank_source_v2.py \
  repository-fleets/test_validate_memebank_source_v2.py
python3 -m unittest -v repository-fleets/test_validate_memebank_source_v2.py
python3 repository-fleets/validate_memebank_source_v2.py
```

## CI ordering

The `memebank-source-v2` workflow runs the checks in this order:

1. audit stored chunk sizes and checksums;
2. retain the exact carrier as a one-day diagnostic artifact;
3. compile the validator and regression suite;
4. run synthetic attack-regression tests;
5. reconstruct the archive and all thirteen deterministic Git histories;
6. prove the quarantined v1 carrier remains unusable.

The synthetic suite does not replace full reconstruction. Full reconstruction does not replace negative testing. Both are required so a malformed carrier cannot pass merely because its happy path was regenerated consistently.

## Publication boundary

An authorized publisher must still verify the live remote identity, visibility, repository ID, default branch, pushed commit, baseline PR, merged head, checks, branch/ruleset state, and monorepo gitlinks. A passing source-v2 workflow proves only that the reviewed source carrier is internally coherent and reproducible.
