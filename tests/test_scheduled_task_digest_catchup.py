from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "tools" / "scheduled_task_digest.py"
SPEC = importlib.util.spec_from_file_location("scheduled_task_digest_catchup", MODULE_PATH)
assert SPEC and SPEC.loader
digest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = digest
SPEC.loader.exec_module(digest)


class ScheduledTaskDigestCatchupTests(unittest.TestCase):
    def decision(self, now: str, *extra: str) -> dict[str, object]:
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            rc = digest.main(["decision", "--now", now, *extra])
        self.assertEqual(rc, 0)
        return json.loads(output.getvalue())

    def test_delayed_schedule_recovers_on_the_same_central_date(self) -> None:
        result = self.decision(
            "2026-09-02T17:06:00Z",
            "--same-day-catchup",
        )
        self.assertTrue(result["due"])
        self.assertTrue(result["catchup"])
        self.assertEqual(result["logical_date"], "2026-09-02")

    def test_early_dst_alternative_remains_not_due(self) -> None:
        result = self.decision(
            "2026-01-12T12:07:00Z",
            "--same-day-catchup",
        )
        self.assertFalse(result["due"])
        self.assertFalse(result["catchup"])
        self.assertEqual(result["local_time"], "2026-01-12T06:07:00-06:00")

    def test_force_is_distinct_from_catchup_and_writes_github_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            github_output = Path(directory) / "github-output"
            result = self.decision(
                "2026-01-12T12:07:00Z",
                "--same-day-catchup",
                "--force",
                "--github-output",
                str(github_output),
            )
            self.assertTrue(result["due"])
            self.assertFalse(result["catchup"])
            values = github_output.read_text()
            self.assertIn("due=true", values)
            self.assertIn("catchup=false", values)

    def test_workflow_has_terminal_false_green_guard(self) -> None:
        workflow = (
            REPOSITORY_ROOT / ".github" / "workflows" / "scheduled-task-digest.yml"
        ).read_text()
        self.assertIn("name: schedule-outcome", workflow)
        self.assertIn("needs.digest.result", workflow)
        self.assertIn("--same-day-catchup", workflow)
        self.assertIn("Refuse false-green schedule completion", workflow)


if __name__ == "__main__":
    unittest.main()
