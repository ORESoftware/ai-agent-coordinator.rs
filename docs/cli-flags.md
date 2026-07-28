# Non-secret CLI flags

The coordinator keeps credentials environment-only while allowing operators and agents to express ordinary runtime settings as command-line flags through [`ORESoftware/flags-2-env`](https://github.com/ORESoftware/flags-2-env).

The root `.cli-flags.toml` is the single reviewed CLI-to-environment contract. Unknown options and invalid values fail closed.

## Usage

Provide an existing `flags2env` binary through `FLAGS2ENV_BIN`, install it on `PATH`, or place a source checkout under `vendor/flags-2-env`, `tools/flags-2-env`, or a sibling `flags-2-env` directory.

```bash
bash scripts/with-flags audit

bash scripts/with-flags \
  --config=coordinator.yaml \
  --json-logs=true \
  --repository-admin-enabled=false \
  --rust-log=ai_agent_coordinator=info,tower_http=info \
  -- cargo run --locked --release --
```

The longer repository-administration aliases from the GitHub adapter are also supported:

```bash
bash scripts/with-flags \
  --github-repository-admin-enabled=false \
  --github-repository-admin-allowed-orgs=fiducia-cloud,sonus-auris \
  --log-filter=warn \
  -- ./target/release/ai-agent-coordinator
```

The wrapper exports only values declared by `.cli-flags.toml`, rejects unknown options and parse errors, and then replaces itself with the requested command. The Rust CLI continues reading `COORDINATOR_CONFIG` and `COORDINATOR_JSON_LOGS` through Clap's environment support.

## Defaults

When no reviewed flags are supplied, the contract exports fail-closed operational defaults:

- `COORDINATOR_CONFIG=coordinator.yaml`
- `COORDINATOR_JSON_LOGS=false`
- `GITHUB_REPOSITORY_ADMIN_ENABLED=false`
- `GITHUB_API_BASE_URL=https://api.github.com`
- `GITHUB_API_VERSION=2022-11-28`
- `GITHUB_API_USER_AGENT=ai-agent-coordinator`
- `RUST_LOG=ai_agent_coordinator=info,tower_http=info`

Repository administration therefore remains disabled until it is explicitly enabled after a reviewed dry run and an exact organization allowlist is supplied.

## Credential boundary

These values are deliberately excluded from the command-line contract:

- `COORDINATOR_API_TOKEN`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_REPOSITORY_ADMIN_TOKEN`
- `MISTRAL_API_KEY`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`

Supply them through the runtime secret manager or environment injection. Do not place them in shell history, process arguments, repository files, workflow arguments, logs, or Linear issues.

The wrapper explicitly rejects credential-like options before invoking `flags2env`, and CI proves rejected values are not echoed. CI also checks out a reviewed `flags-2-env` commit, builds it from source, audits the configuration, validates explicit mappings and defaults, exercises both alias families, proves unknown options fail closed, and proves the credential variables remain unset in the launched process.
