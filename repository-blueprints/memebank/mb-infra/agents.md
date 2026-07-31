# MemeBank infrastructure agent instructions

Load every ancestor lowercase `agents.md` from filesystem root to `$PWD` in root-to-leaf order before editing this tree.

## Merge policy

- avoid git rebase in favor of git merge.
- Never force-push, rewrite shared history, bypass required checks, or resolve a conflict by selecting one side wholesale.
- Resolve conflicts semantically. Preserve compatible dependency edges, sync waves, environment policy, worker isolation, artifact provenance, secret references, and operational documentation from both branches.
- After every merge, scan the full worktree outside `.git` for conflict markers and run `make agent-check`.

## Canonical ownership

1. The repository identity is exactly `memebank/mb-infra`; `memebank-infra` is forbidden.
2. Service repositories own source, tests, image builds, SBOMs, and provenance. This repository owns deployed immutable versions, environment policy, Argo topology, secret references, network policy, model locks, and operational promotion.
3. `control-plane/fleet.json` owns child Application names, dependency edges, sync waves, namespaces, and component paths.
4. `environments/*.json` owns cluster bindings and environment-specific eligibility. Keep the three files structurally aligned.
5. `artifact-locks/bundle.json` owns one tested compatibility set. Never edit a digest without updating its provenance and compatibility evidence.
6. Generated `.artifacts/` output is disposable review evidence and must not be hand-edited or committed.

## Security and secrets

- Never commit passwords, OAuth tokens, API keys, private keys, refresh tokens, presigned URLs, signed cookies, or raw secret values.
- Commit only references to External Secrets or another explicitly approved secret manager.
- Local CPU/GPU inference profiles receive no cloud-provider credentials and no arbitrary internet egress.
- Cloud dispatch requires a dedicated service account, scoped secret references, and an exact endpoint allowlist.
- Preserve non-root execution, read-only root filesystems, dropped capabilities, RuntimeDefault seccomp, disabled privilege escalation, explicit requests/limits, and probes.
- Do not enable host networking, host PID/IPC, privileged containers, hostPath, broad service-account token mounts, or wildcard egress.

## Promotion

- Staging and production use immutable Git revisions, image digests, chart versions, and model checksums. Never introduce `latest`, branch refs, floating ranges, or mutable model aliases.
- Production auto-sync remains disabled. Production changes are reviewed Git promotions or reverts.
- A compatibility set includes the interface schema, service images, native runtime, model artifact, processor/tokenizer/dictionary revision, and index/embedding contract.
- A syntactically valid placeholder is not a real artifact. Keep placeholder entries `promotable: false` and document the blocker.

## Validation

Run before review:

```bash
make agent-check
make report
```

Record the exact source commit, rendered-plan digest, CI run, known placeholders, and missing cluster evidence in the pull request and DEN-1021.
