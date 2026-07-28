# Non-secret CLI flags

The coordinator keeps credentials environment-only while allowing operators and
agents to express ordinary runtime settings as command-line flags through
[`ORESoftware/flags-2-env`](https://github.com/ORESoftware/flags-2-env).

## Usage

Provide an existing `flags2env` binary through `FLAGS2ENV_BIN`, install it on
`PATH`, or place a source checkout under `vendor/flags-2-env`,
`tools/flags-2-env`, or a sibling `flags-2-env` directory.

```bash
bash scripts/with-flags audit

bash scripts/with-flags \
  --config=coordinator.yaml \
  --json-logs=true \
  --repository-admin-enabled=false \
  --rust-log=ai_agent_coordinator=info,tower_http=info \
  -- cargo run --locked --release --
```

The wrapper exports only values declared by `.cli-flags.toml`, rejects unknown
options and parse errors, and then replaces itself with the requested command.
The Rust CLI continues reading `COORDINATOR_CONFIG` and
`COORDINATOR_JSON_LOGS` through Clap's environment support.

## Credential boundary

These values are deliberately excluded from the command-line contract:

- `COORDINATOR_API_TOKEN`
- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_REPOSITORY_ADMIN_TOKEN`
- `MISTRAL_API_KEY`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`

Supply them through the runtime secret manager or environment injection. Do not
place them in shell history, process arguments, repository files, workflow
arguments, or Linear issues.

CI checks out a reviewed `flags-2-env` commit, builds it from source, audits the
configuration, validates representative mappings, and proves a credential-like
command-line option is rejected without echoing its value.
