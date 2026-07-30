from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "linear_mcp_bootstrap.py"
READ_WRITE_URL = "https://mcp.linear.app/mcp"
READ_ONLY_URL = "https://mcp.linear.app/mcp/readonly"
SECRET_SENTINEL = "linear-secret-must-never-appear"


class LinearMcpBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = self.root / "opencode.json"
        self.claude_state = self.root / "claude-state.json"
        self.fake_claude = self.root / "claude"
        self.fake_claude.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json
                import os
                from pathlib import Path
                import sys

                state_path = Path(os.environ["FAKE_CLAUDE_STATE"])
                args = sys.argv[1:]
                state = {}
                if state_path.exists():
                    state = json.loads(state_path.read_text(encoding="utf-8"))

                if len(args) >= 3 and args[:2] == ["mcp", "get"]:
                    name = args[2]
                    entry = state.get(name)
                    if entry is None:
                        raise SystemExit(1)
                    print(json.dumps(entry))
                    raise SystemExit(0)

                if len(args) >= 3 and args[:2] == ["mcp", "remove"]:
                    name = args[2]
                    state.pop(name, None)
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    raise SystemExit(0)

                if len(args) >= 3 and args[:2] == ["mcp", "add"]:
                    name = None
                    endpoint = None
                    for index, value in enumerate(args):
                        if value == "--transport":
                            continue
                        if index > 0 and args[index - 1] == "--transport":
                            continue
                        if value == "--scope":
                            continue
                        if index > 0 and args[index - 1] == "--scope":
                            continue
                        if value.startswith("https://"):
                            endpoint = value
                        elif value not in {"mcp", "add"} and name is None:
                            name = value
                    if not name or not endpoint:
                        raise SystemExit(64)
                    state[name] = {"url": endpoint, "args": args}
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    raise SystemExit(0)

                raise SystemExit(64)
                """
            ),
            encoding="utf-8",
        )
        self.fake_claude.chmod(
            self.fake_claude.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_tool(self, *arguments: str, extra_env: dict[str, str] | None = None):
        env = os.environ.copy()
        env.update(
            {
                "FAKE_CLAUDE_STATE": str(self.claude_state),
                "LINEAR_API_KEY": SECRET_SENTINEL,
                "LINEAR_API_TOKEN": SECRET_SENTINEL,
            }
        )
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=15,
        )
        self.assertNotIn(SECRET_SENTINEL, result.stdout)
        self.assertNotIn(SECRET_SENTINEL, result.stderr)
        return result

    def test_opencode_add_preserves_unrelated_config_and_is_idempotent(self) -> None:
        original = {
            "$schema": "https://opencode.ai/config.json",
            "theme": "system",
            "mcp": {"other": {"type": "remote", "url": "https://example.test/mcp"}},
        }
        self.config.write_text(json.dumps(original), encoding="utf-8")

        first = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        configured = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(configured["theme"], "system")
        self.assertEqual(configured["mcp"]["other"], original["mcp"]["other"])
        self.assertEqual(
            configured["mcp"]["linear-server"],
            {"type": "remote", "url": READ_WRITE_URL},
        )
        backups = list(self.root.glob("opencode.json.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), original)

        second = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already targets", second.stdout)
        self.assertEqual(len(list(self.root.glob("opencode.json.bak.*"))), 1)

    def test_opencode_legacy_nested_shape_is_preserved(self) -> None:
        self.config.write_text(
            json.dumps(
                {
                    "mcp": {
                        "servers": {
                            "other": {
                                "type": "remote",
                                "url": "https://example.test/mcp",
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        result = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
            "--readonly",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        configured = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(
            configured["mcp"]["servers"]["linear-server"]["url"],
            READ_ONLY_URL,
        )
        self.assertIn("other", configured["mcp"]["servers"])

    def test_opencode_dry_run_and_check_do_not_mutate(self) -> None:
        result = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
            "--dry-run",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.config.exists())

        check = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
            "--check",
        )
        self.assertEqual(check.returncode, 2)
        self.assertFalse(self.config.exists())

    def test_opencode_remove_preserves_other_server(self) -> None:
        self.config.write_text(
            json.dumps(
                {
                    "mcp": {
                        "linear-server": {"type": "remote", "url": READ_WRITE_URL},
                        "other": {"type": "remote", "url": "https://example.test/mcp"},
                    }
                }
            ),
            encoding="utf-8",
        )
        result = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
            "--remove",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        configured = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertNotIn("linear-server", configured["mcp"])
        self.assertIn("other", configured["mcp"])

        check = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
            "--remove",
            "--check",
        )
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_invalid_json_and_symlink_fail_closed(self) -> None:
        self.config.write_text("{not-json", encoding="utf-8")
        invalid = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertIn("not valid JSON", invalid.stderr)
        self.assertEqual(self.config.read_text(encoding="utf-8"), "{not-json")

        target = self.root / "real.json"
        target.write_text("{}", encoding="utf-8")
        link = self.root / "linked.json"
        link.symlink_to(target)
        linked = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(link),
        )
        self.assertEqual(linked.returncode, 2)
        self.assertIn("symlink", linked.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")

    def test_backups_are_collision_safe(self) -> None:
        self.config.write_text("{}", encoding="utf-8")
        first = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_tool(
            "--client",
            "opencode",
            "--opencode-config",
            str(self.config),
            "--readonly",
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(list(self.root.glob("opencode.json.bak.*"))), 2)

    def test_claude_add_check_readonly_and_remove(self) -> None:
        add = self.run_tool(
            "--client",
            "claude",
            "--claude-bin",
            str(self.fake_claude),
        )
        self.assertEqual(add.returncode, 0, add.stderr)
        state = json.loads(self.claude_state.read_text(encoding="utf-8"))
        entry = state["linear-server"]
        self.assertEqual(entry["url"], READ_WRITE_URL)
        self.assertEqual(
            entry["args"],
            [
                "mcp",
                "add",
                "--transport",
                "http",
                "linear-server",
                "--scope",
                "user",
                READ_WRITE_URL,
            ],
        )

        check = self.run_tool(
            "--client",
            "claude",
            "--claude-bin",
            str(self.fake_claude),
            "--check",
        )
        self.assertEqual(check.returncode, 0, check.stderr)

        replace_readonly = self.run_tool(
            "--client",
            "claude",
            "--claude-bin",
            str(self.fake_claude),
            "--readonly",
        )
        self.assertEqual(replace_readonly.returncode, 0, replace_readonly.stderr)
        state = json.loads(self.claude_state.read_text(encoding="utf-8"))
        self.assertEqual(state["linear-server"]["url"], READ_ONLY_URL)

        dry_remove = self.run_tool(
            "--client",
            "claude",
            "--claude-bin",
            str(self.fake_claude),
            "--remove",
            "--dry-run",
        )
        self.assertEqual(dry_remove.returncode, 0, dry_remove.stderr)
        self.assertTrue(self.claude_state.exists())

        remove = self.run_tool(
            "--client",
            "claude",
            "--claude-bin",
            str(self.fake_claude),
            "--remove",
        )
        self.assertEqual(remove.returncode, 0, remove.stderr)
        self.assertEqual(json.loads(self.claude_state.read_text(encoding="utf-8")), {})

    def test_missing_claude_does_not_block_opencode_in_all_mode(self) -> None:
        result = self.run_tool(
            "--client",
            "all",
            "--claude-bin",
            str(self.root / "missing-claude"),
            "--opencode-config",
            str(self.config),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Claude Code CLI was not found", result.stderr)
        configured = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(configured["mcp"]["linear-server"]["url"], READ_WRITE_URL)

    def test_server_name_rejects_terminal_or_shell_injection(self) -> None:
        result = self.run_tool(
            "--client",
            "opencode",
            "--server-name",
            "linear;echo-owned",
            "--opencode-config",
            str(self.config),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.config.exists())

    def test_source_has_no_secret_input_surface(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("LINEAR_API_KEY", source)
        self.assertNotIn("LINEAR_API_TOKEN", source)
        self.assertNotIn("Authorization: Bearer", source)


if __name__ == "__main__":
    unittest.main()
