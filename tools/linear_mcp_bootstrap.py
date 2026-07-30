#!/usr/bin/env python3
"""Configure Linear's hosted OAuth MCP server for Claude Code and OpenCode.

The utility is intentionally secret-free: it never accepts, reads, or prints a
Linear API key or OAuth token. Authentication remains an interactive browser
flow owned by the selected desktop client.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence

READ_WRITE_URL = "https://mcp.linear.app/mcp"
READ_ONLY_URL = "https://mcp.linear.app/mcp/readonly"
DEFAULT_SERVER_NAME = "linear-server"
MAX_CAPTURE_BYTES = 64 * 1024
SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class BootstrapError(RuntimeError):
    """Expected operator-facing failure."""


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _emit(message: str) -> None:
    print(message, flush=True)


def _resolve_executable(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.parent != Path(".") or os.sep in value:
        return (
            str(candidate)
            if candidate.is_file() and os.access(candidate, os.X_OK)
            else None
        )
    return shutil.which(value)


def _run_quiet(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a command without echoing its arguments or child output.

    Client output can contain local paths or authentication metadata. The
    caller receives bounded text for structural validation, but the content is
    never copied into normal stdout or stderr.
    """

    try:
        completed = subprocess.run(
            list(command),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError("An MCP client command could not be completed safely.") from exc

    # Bound pathological or accidentally sensitive output in memory.
    completed.stdout = completed.stdout[:MAX_CAPTURE_BYTES]
    completed.stderr = completed.stderr[:MAX_CAPTURE_BYTES]
    return completed


def _claude_status(claude: str, name: str, endpoint: str) -> tuple[bool, bool]:
    result = _run_quiet([claude, "mcp", "get", name])
    if result.returncode != 0:
        return False, False
    combined = f"{result.stdout}\n{result.stderr}"
    return True, endpoint in combined


def _claude_remove(claude: str, name: str) -> bool:
    result = _run_quiet([claude, "mcp", "remove", name, "--scope", "user"])
    if result.returncode == 0:
        return True
    # Older clients do not accept --scope on remove.
    return _run_quiet([claude, "mcp", "remove", name]).returncode == 0


def configure_claude(
    *,
    claude_bin: str,
    name: str,
    endpoint: str,
    check: bool,
    dry_run: bool,
    remove: bool,
) -> None:
    claude = _resolve_executable(claude_bin)
    if claude is None:
        raise BootstrapError(
            "Claude Code CLI was not found. Install it on the desktop machine "
            "and rerun this command."
        )

    exists, matches = _claude_status(claude, name, endpoint)

    if remove:
        if not exists:
            _emit(f"Claude Code: {name} is already absent.")
            return
        if check:
            raise BootstrapError(f"Claude Code still has the {name} MCP entry.")
        if dry_run:
            _emit(f"Claude Code: would remove the user-scoped {name} server.")
            return
        if not _claude_remove(claude, name):
            raise BootstrapError(
                "Claude Code could not remove the Linear MCP entry. Run "
                f"`claude mcp remove {name}` on the desktop and retry."
            )
        _emit(f"Claude Code: removed {name}.")
        return

    if exists and matches:
        _emit(f"Claude Code: {name} already targets the expected hosted endpoint.")
        return

    if check:
        if exists:
            raise BootstrapError(
                f"Claude Code has a {name} entry, but it does not target the "
                "expected hosted endpoint."
            )
        raise BootstrapError(f"Claude Code is missing the user-scoped {name} entry.")

    if dry_run:
        action = "replace" if exists else "add"
        _emit(f"Claude Code: would {action} the user-scoped {name} server.")
        return

    if exists and not _claude_remove(claude, name):
        raise BootstrapError(
            "Claude Code has a conflicting Linear MCP entry and it could not "
            "be replaced safely."
        )

    # Keep this argument order aligned with the documented Claude Code CLI.
    added = _run_quiet(
        [
            claude,
            "mcp",
            "add",
            "--transport",
            "http",
            name,
            "--scope",
            "user",
            endpoint,
        ]
    )
    if added.returncode != 0:
        raise BootstrapError(
            "Claude Code rejected the hosted Linear MCP configuration. Upgrade "
            "Claude Code and rerun this command."
        )

    present, correct = _claude_status(claude, name, endpoint)
    if not (present and correct):
        raise BootstrapError(
            "Claude Code did not report the expected Linear MCP entry after installation."
        )

    _emit(f"Claude Code: configured user-scoped {name}.")
    _emit(
        "Claude Code: open a session, run `/mcp`, select the Linear server, "
        "and complete the browser OAuth flow."
    )


def default_opencode_config() -> Path:
    if override := os.environ.get("OPENCODE_CONFIG"):
        return Path(override).expanduser()
    xdg_raw = os.environ.get("XDG_CONFIG_HOME")
    xdg = Path(xdg_raw).expanduser() if xdg_raw else Path.home() / ".config"
    return xdg / "opencode" / "opencode.json"


def _assert_regular_config_path(path: Path) -> None:
    if path.is_symlink():
        raise BootstrapError("Refusing to modify an OpenCode config symlink.")
    if path.exists() and not path.is_file():
        raise BootstrapError("OpenCode config path is not a regular file.")


def _load_json_object(path: Path) -> dict[str, Any]:
    _assert_regular_config_path(path)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise BootstrapError("OpenCode config must be UTF-8 text.") from exc
    except json.JSONDecodeError as exc:
        raise BootstrapError(
            f"OpenCode config is not valid JSON at line {exc.lineno}, column {exc.colno}."
        ) from exc
    if not isinstance(value, dict):
        raise BootstrapError("OpenCode config must contain one top-level JSON object.")
    return value


def _next_backup_path(path: Path) -> Path:
    base = path.with_name(f"{path.name}.bak.{_utc_stamp()}")
    candidate = base
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{base.name}.{suffix}")
        suffix += 1
    return candidate


def _chmod_owner_only(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows and some network filesystems do not expose POSIX modes.
        pass


def _atomic_write_json(path: Path, value: dict[str, Any]) -> Path | None:
    _assert_regular_config_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        backup = _next_backup_path(path)
        shutil.copy2(path, backup)
        _chmod_owner_only(backup)

    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _chmod_owner_only(path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return backup


def _opencode_servers(config: dict[str, Any], *, create: bool) -> dict[str, Any] | None:
    current = config.get("mcp")
    if current is None:
        if not create:
            return None
        current = {}
        config["mcp"] = current
    if not isinstance(current, dict):
        raise BootstrapError("OpenCode config field `mcp` must be a JSON object.")

    # Current OpenCode uses a direct map under `mcp`. A short-lived older
    # configuration shape nested that map under `mcp.servers`; preserve and
    # update it in place when detected rather than corrupting the file.
    legacy_servers = current.get("servers")
    if isinstance(legacy_servers, dict):
        return legacy_servers
    return current


def configure_opencode(
    *,
    config_path: Path,
    name: str,
    endpoint: str,
    check: bool,
    dry_run: bool,
    remove: bool,
) -> None:
    path = config_path.expanduser()
    config = _load_json_object(path)
    servers = _opencode_servers(config, create=not remove)
    existing = None if servers is None else servers.get(name)
    expected = {"type": "remote", "url": endpoint}

    if remove:
        if existing is None:
            _emit(f"OpenCode: {name} is already absent.")
            return
        if check:
            raise BootstrapError(f"OpenCode still has the {name} MCP entry.")
        if dry_run:
            _emit(f"OpenCode: would remove {name} from {path}.")
            return
        assert servers is not None
        del servers[name]
        backup = _atomic_write_json(path, config)
        _emit(f"OpenCode: removed {name} from {path}.")
        if backup is not None:
            _emit(f"OpenCode: preserved the previous config in {backup}.")
        return

    if existing == expected:
        _emit(f"OpenCode: {name} already targets the expected hosted endpoint.")
        return

    if check:
        if existing is None:
            raise BootstrapError(f"OpenCode is missing the {name} MCP entry.")
        raise BootstrapError(
            f"OpenCode has a {name} entry, but it does not match the hosted OAuth configuration."
        )

    if dry_run:
        action = "replace" if existing is not None else "add"
        _emit(f"OpenCode: would {action} {name} in {path}.")
        return

    assert servers is not None
    servers[name] = expected
    backup = _atomic_write_json(path, config)
    _emit(f"OpenCode: configured {name} in {path}.")
    if backup is not None:
        _emit(f"OpenCode: preserved the previous config in {backup}.")
    _emit(f"OpenCode: run `opencode mcp auth {name}` and complete browser OAuth.")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Configure Linear's hosted OAuth MCP server without creating or exposing an API key."
        )
    )
    parser.add_argument(
        "--client",
        choices=("all", "claude", "opencode"),
        default="all",
        help="client to configure (default: all)",
    )
    parser.add_argument(
        "--server-name",
        default=DEFAULT_SERVER_NAME,
        help=f"MCP entry name (default: {DEFAULT_SERVER_NAME})",
    )
    parser.add_argument(
        "--readonly",
        action="store_true",
        help="use Linear's server-enforced read-only endpoint",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the requested final state without changing anything",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="describe structural changes without changing anything",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="remove only this MCP entry and preserve unrelated configuration",
    )
    parser.add_argument(
        "--claude-bin",
        default=os.environ.get("CLAUDE_BIN", "claude"),
        help="Claude Code executable (default: CLAUDE_BIN or claude)",
    )
    parser.add_argument(
        "--opencode-config",
        type=Path,
        default=None,
        help="OpenCode config path (default: OPENCODE_CONFIG or XDG config path)",
    )
    args = parser.parse_args(argv)
    if args.check and args.dry_run:
        parser.error("--check and --dry-run are mutually exclusive")
    if not SERVER_NAME_PATTERN.fullmatch(args.server_name):
        parser.error(
            "--server-name may contain only ASCII letters, digits, hyphens, and underscores"
        )
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    endpoint = READ_ONLY_URL if args.readonly else READ_WRITE_URL
    errors: list[str] = []

    if args.client in ("all", "claude"):
        try:
            configure_claude(
                claude_bin=args.claude_bin,
                name=args.server_name,
                endpoint=endpoint,
                check=args.check,
                dry_run=args.dry_run,
                remove=args.remove,
            )
        except BootstrapError as exc:
            errors.append(f"Claude Code: {exc}")

    if args.client in ("all", "opencode"):
        try:
            configure_opencode(
                config_path=args.opencode_config or default_opencode_config(),
                name=args.server_name,
                endpoint=endpoint,
                check=args.check,
                dry_run=args.dry_run,
                remove=args.remove,
            )
        except BootstrapError as exc:
            errors.append(f"OpenCode: {exc}")

    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
