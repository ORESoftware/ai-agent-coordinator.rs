# flags2env runtime contract

The coordinator uses [`ORESoftware/flags-2-env`](https://github.com/ORESoftware/flags-2-env) to translate reviewed, non-secret CLI flags into the environment variables already consumed by the Rust process.

The pinned CI source revision is:

```text
0c7c1ab91fa55aadab4be9a61c3b5dd3b963201d
```

## Safety boundary

The root `.cli-flags.toml` is the only supported CLI-to-environment mapping. It intentionally excludes all credentials and secret material, including:

- `COORDINATOR_API_TOKEN`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_REPOSITORY_ADMIN_TOKEN`
- `MISTRAL_API_KEY`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`

Those values remain environment/secret-manager only. Do not add command-line aliases for them: process arguments may be exposed through shell history, process listings, crash reports, support bundles, or orchestration metadata.

## Local use

Build or install the pinned flags2env binary, then run the wrapper:

```bash
export FLAGS2ENV_BIN=/absolute/path/to/flags2env
bash scripts/with-flags2env.sh \
  --config=/etc/ai-agent-coordinator/coordinator.yaml \
  --json-logs \
  --log-filter=ai_agent_coordinator=debug,tower_http=info \
  -- \
  ./target/release/ai-agent-coordinator
```

The wrapper evaluates only shell-quoted exports emitted by flags2env and then replaces itself with the requested command.

Repository administration remains disabled by default. A reviewed dry run and a short-lived GitHub App installation token are still required before enabling it:

```bash
bash scripts/with-flags2env.sh \
  --github-repository-admin-enabled \
  --github-repository-admin-allowed-orgs=declarative-migrations \
  -- \
  ./target/release/ai-agent-coordinator
```

`GITHUB_REPOSITORY_ADMIN_TOKEN` must be supplied separately through the secret manager or environment. It is never accepted as a flag.

## CI contract

CI checks out flags2env at the exact revision above into `tools/flags-2-env`, builds the native CLI, audits `.cli-flags.toml`, and runs an isolated export smoke test. The smoke test proves:

- representative string, boolean, and default values map to the expected environment keys;
- repository administration remains disabled unless explicitly requested;
- the wrapper rejects unknown flags;
- secrets are not part of the generated CLI contract.

When adding a non-secret runtime setting, update `.cli-flags.toml`, the export smoke test, and this document in the same pull request. When adding a secret setting, update only secret-manager/environment documentation and keep it in `[env].ignore`.
