# ChatGPT work reconciliation snapshot — 2026-08-08

This directory preserves the credential-clean source artifact recovered from the 2026-08-08 cross-thread work-reconciliation session.

It is an **archival review snapshot**, not a replacement for the coordinator's canonical artifact-recovery implementation. The authoritative workflow remains `docs/nightly-artifact-recovery.md` and the implementation tracked by Linear issue `DEN-2797`.

The deterministic source archive is stored as ordered `payload.tar.gz.part-*` files, matching this repository's existing artifact-recovery convention. Run:

```bash
./restore.sh
```

The restore script reconstructs the archive, verifies SHA-256 `d018c6fb46ec272b57a7456d96535ed4a5e3c27b62ca4afa381429b133ce0468`, extracts the original Rust and shell sources, verifies their source hashes, and runs `bash -n` on the shell prototype.

## Safety and validation

- A concrete credential scan found no GitHub, Linear, Cloudflare, Slack, R2, or private-key value in the payload.
- Credential-prefix strings in the Rust source are assembled inside the detector and are not credential values.
- The reconstructed source hashes exactly match the current recovery artifact.
- The Rust prototype now includes focused unit tests, but Rust tooling was unavailable in the recovery container, so compilation was not executed and the source is not wired into the production crate.
- No force push, merge, deployment, Cloudflare, DNS, Worker, or R2 mutation is part of this snapshot.

Before promoting any behavior, reconcile it with the existing fail-closed queue, exact-path staging, GitHub App authorization, idempotency, and evidence contracts in `DEN-2797`.
