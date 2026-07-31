# Linear MCP bootstrap for Claude Code and OpenCode

This repository includes a secret-free bootstrap for Linear's official hosted MCP server. It is intended to run on the developer's desktop machine, where Claude Code or OpenCode can open a browser and complete Linear's OAuth flow.

The default endpoint is the read-write hosted server:

```text
https://mcp.linear.app/mcp
```

Pass `--readonly` to use Linear's server-enforced read-only endpoint:

```text
https://mcp.linear.app/mcp/readonly
```

## Security boundary

`tools/linear_mcp_bootstrap.py` deliberately has no API-key or bearer-token option. It does not inspect the process environment for Linear credentials, write credentials to configuration, echo child-process output, or perform OAuth itself. Authentication remains in the desktop client and its protected credential store.

The bootstrap also:

- configures Claude Code at **user scope**, so the server is available across repositories without checking a project MCP file into source control;
- adds one remote MCP entry to the current OpenCode config while preserving unrelated settings and servers;
- creates a timestamped owner-readable backup before every OpenCode mutation;
- performs OpenCode writes atomically and refuses to modify a symlink;
- supports idempotent install, structural check, dry-run, read-only, and remove operations;
- validates the installed Claude endpoint without printing `claude mcp get` output;
- continues configuring one client when the other client is unavailable, then returns a nonzero status describing the unavailable client.

Do not paste a Linear API key into the command, a shell profile, a repository `.env` file, a Linear issue, or a CI secret for this workflow. The normal desktop path uses browser OAuth.

## Prerequisites

- Python 3.10 or newer.
- A current Claude Code installation for `--client claude`.
- A current OpenCode installation for `--client opencode`.
- A desktop browser session that can authenticate to the intended Linear workspace.

The bootstrap can create OpenCode's config before the `opencode` executable is installed because it only edits the documented JSON structure. The subsequent OAuth command requires the OpenCode CLI.

## Configure Claude Code

From a checkout of this repository on the desktop machine:

```bash
python3 tools/linear_mcp_bootstrap.py --client claude
```

The command creates or repairs this logical configuration:

```bash
claude mcp add \
  --transport http \
  --scope user \
  linear-server \
  https://mcp.linear.app/mcp
```

Then start Claude Code, run:

```text
/mcp
```

Select `linear-server` and complete the browser login. The bootstrap does not attempt this interactive step.

Verify the structural configuration without changing it:

```bash
python3 tools/linear_mcp_bootstrap.py --client claude --check
```

For a first read-only verification:

```bash
python3 tools/linear_mcp_bootstrap.py \
  --client claude \
  --readonly
```

After authenticating, use a bounded read-only prompt such as:

```text
Use Linear to list the five most recently updated issues in the Denman workspace. Do not create or update anything.
```

Switching from read-only to read-write is an explicit second operation:

```bash
python3 tools/linear_mcp_bootstrap.py --client claude
```

Claude Code will see an endpoint change and may require authentication again.

## Configure OpenCode

The current OpenCode configuration is normally located at:

```text
~/.config/opencode/opencode.json
```

Configure the hosted server:

```bash
python3 tools/linear_mcp_bootstrap.py --client opencode
```

The tool adds this entry while retaining unrelated JSON:

```json
{
  "mcp": {
    "linear-server": {
      "type": "remote",
      "url": "https://mcp.linear.app/mcp"
    }
  }
}
```

Then start authentication:

```bash
opencode mcp auth linear-server
```

OpenCode opens a browser and stores its OAuth state outside the repository config. Verify server and authentication status with:

```bash
opencode mcp list
```

Use an explicit config path when OpenCode is installed elsewhere or when testing an isolated file:

```bash
python3 tools/linear_mcp_bootstrap.py \
  --client opencode \
  --opencode-config "$HOME/.config/opencode/opencode.json"
```

`OPENCODE_CONFIG` and `XDG_CONFIG_HOME` are also respected.

## Configure both clients

`all` is the default client selection:

```bash
python3 tools/linear_mcp_bootstrap.py
```

Each client is handled independently. For example, if Claude Code is absent but the OpenCode config is writable, OpenCode is still configured and the process exits nonzero with a Claude-specific error.

## Preview, verify, and remove

Preview structural changes without touching either client:

```bash
python3 tools/linear_mcp_bootstrap.py --dry-run
```

Verify the desired installed state:

```bash
python3 tools/linear_mcp_bootstrap.py --check
```

Remove only the named Linear entry:

```bash
python3 tools/linear_mcp_bootstrap.py --remove
```

Verify that it is absent:

```bash
python3 tools/linear_mcp_bootstrap.py --remove --check
```

Use a different entry name when maintaining separate workspace authentication contexts:

```bash
python3 tools/linear_mcp_bootstrap.py \
  --client claude \
  --server-name linear-denman
```

Names are restricted to ASCII letters, digits, hyphens, and underscores so they are safe for both client CLIs.

## Credential revocation and recovery

To revoke only the locally stored OAuth session while retaining the server definition:

```bash
claude mcp logout linear-server
opencode mcp logout linear-server
```

Then rerun `/mcp` in Claude Code or `opencode mcp auth linear-server` to authenticate again.

To remove the server definition and preserve all unrelated client configuration:

```bash
python3 tools/linear_mcp_bootstrap.py --remove
```

OpenCode mutations retain the immediately previous config in a sibling file named like:

```text
opencode.json.bak.20260730T170000Z
```

If the config is malformed or is a symlink, the bootstrap exits without changing it. Repair or replace the file manually, then rerun `--check` before installation.

## CI and tests

CI never contacts Linear and never requires a credential. It uses a fake Claude CLI and temporary OpenCode configs to cover:

- install, repair, idempotency, dry-run, check, and removal;
- read-write and server-enforced read-only endpoints;
- preservation of unrelated OpenCode settings and MCP entries;
- current and legacy nested OpenCode MCP maps;
- atomic writes, collision-safe backups, malformed JSON, and symlink refusal;
- exact Claude Code user-scope command construction;
- independent handling when one client is unavailable;
- redaction when credential-looking environment variables are present.

Run the suite locally:

```bash
python3 -m unittest discover \
  -s tests \
  -p 'test_linear_mcp_bootstrap.py' \
  -v
```
