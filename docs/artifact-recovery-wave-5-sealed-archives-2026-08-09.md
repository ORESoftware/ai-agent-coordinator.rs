# Wave 5 sealed-archive reconciliation

This supplement closes the remaining opaque-archive gap in the June 30 through August 9, 2026 ChatGPT/library reconciliation. It extends the source-first Wave 5 ledger in this branch; it does not replace or rewrite that work.

## Result

Three high-value historical source bundles are now anchored in GitHub by immutable byte lengths, SHA-256 digests, persistent Library identifiers, current canonical repository heads, and explicit dispositions:

| Archive | Bytes | SHA-256 | Histories | Disposition |
|---|---:|---|---:|---|
| `hypesiege-streempilot-bootstrap.tar.gz` | 2,025,578 | `0b38d197161b91db0a2a3d08def94d1e965fa8d48bb6aa998ba9614b667728e0` | 32 | Historical snapshot; do not replay over newer live repositories |
| `four-org-rust-bootstrap-2026-08-04.zip` | 2,195,312 | `a715a4919e3a7d9e7eb5b8b7878e2d9485e4228504e57fe7a6d703a166cb1d37` | 32 | Canonical organization repositories now exist and have advanced |
| `mcp-rust-libs-scaffold.zip` | 120,181 | `a88df67f4c0ffc6cf0ee74f7cd2eec46004e2d8021bfbef6051cd1af7143ca3b` | 1 | Superseded by the current `ORESoftware/mcp-rust-libs` repository |

## Semantic reconciliation

The Hypesiege/StreemPilot archive contains complete independent Git histories from July 31. Current product repositories contain newer commits and fleet-ownership changes. At least one sealed commit, `hypesiege-monorepo@ad06355d…`, is not an ancestor exposed by the live repository. The safe result is therefore to preserve the archive and checksum while leaving current history authoritative. Importing the archive onto `main` would not be a merge; it would be destructive replacement.

The four-organization archive was produced while the organizations were unreachable from the earlier execution environment. The canonical short-name repositories now exist in `apostille-me`, `embedded-alerts`, `evento-globolo`, and `hacker-house-medellin`, with later delivery-state commits. The archive remains useful recovery evidence, but its code must be compared file-by-file before any future selective salvage.

The MCP scaffold is likewise historical. `ORESoftware/mcp-rust-libs` now contains later merged protocol, runtime, configuration, API-documentation, and certification changes, so the scaffold cannot be promoted wholesale without regression.

## Credential and transport boundary

Static scans found no credential-shaped values, private keys, or credential-bearing Git remotes in these three selected archives. A different MCP test archive contains an intentional rejected credential-in-URL fixture and is deliberately not represented as a publishable payload here.

The current GitHub connector can commit source and metadata but cannot create organization repositories or upload opaque release assets. The execution container cannot reach GitHub transport endpoints. Therefore the archive bytes remain in the persistent Library while this repository stores their immutable identity and disposition. This is an explicit limitation, not a false claim that the bytes were uploaded to GitHub.

## Validation

```console
python3 scripts/validate_artifact_recovery_wave5_sealed_archives.py
python3 -m unittest -v tests/test_artifact_recovery_wave5_sealed_archives.py
```

The tests fail closed on checksum or byte-count drift, force-push policy changes, binary-upload claims, excluded targets, missing canonical evidence, repository-count drift, and credential-shaped values.

## Policy

- no force push or branch-history rewrite;
- no automatic merge of product PRs;
- no replay of stale archives over newer repositories;
- semantic union only, through ordinary forward commits and reviewable PRs;
- no credentials persisted in source, reports, remotes, or comments;
- `dancing-dragons` remains excluded; and
- checksum evidence is not product acceptance.
