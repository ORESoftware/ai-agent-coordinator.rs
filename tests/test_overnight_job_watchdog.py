from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import overnight_job_watchdog as watchdog  # noqa: E402

ENTRY = {
    "id": "recent-chat-reconciliation",
    "workflow": "last-30-hours-introspection.yml",
    "branch": "main",
    "timezone": "America/Lima",
    "local_time": "00:30",
    "max_dispatch_delay_minutes": 240,
    "required_job_names": ["enqueue-and-verify"],
    "required_artifact_prefixes": [
        "recent-chat-reconciliation-terminal-",
        "recent-chat-reconciliation-report-",
    ],
}


class OvernightWatchdogTests(unittest.TestCase):
    def test_success_requires_run_job_and_both_artifacts(self) -> None:
        result = watchdog.evaluate_job(
            ENTRY,
            now=datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            target_date=None,
            list_runs=lambda _: [
                {
                    "id": 42,
                    "created_at": "2026-08-10T05:34:00Z",
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "main",
                    "html_url": "https://example.invalid/run/42",
                }
            ],
            list_jobs=lambda _: [{"name": "enqueue-and-verify", "conclusion": "success"}],
            list_artifacts=lambda _: [
                {"name": "recent-chat-reconciliation-terminal-42-1", "expired": False},
                {"name": "recent-chat-reconciliation-report-42-1", "expired": False},
            ],
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["dispatch_delay_minutes"], 4)

    def test_false_green_missing_live_job_fails(self) -> None:
        result = watchdog.evaluate_job(
            ENTRY,
            now=datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            target_date=None,
            list_runs=lambda _: [
                {
                    "id": 43,
                    "created_at": "2026-08-10T05:30:00Z",
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "main",
                }
            ],
            list_jobs=lambda _: [{"name": "validate", "conclusion": "success"}],
            list_artifacts=lambda _: [],
        )
        self.assertEqual(result["status"], "failure")
        self.assertIn("required job", result["errors"][0])

    def test_delayed_recovery_run_inside_window_can_satisfy_job(self) -> None:
        result = watchdog.evaluate_job(
            ENTRY,
            now=datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            target_date=None,
            list_runs=lambda _: [
                {
                    "id": 44,
                    "created_at": "2026-08-10T07:07:00Z",
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "main",
                }
            ],
            list_jobs=lambda _: [{"name": "enqueue-and-verify", "conclusion": "success"}],
            list_artifacts=lambda _: [
                {"name": "recent-chat-reconciliation-terminal-44-1", "expired": False},
                {"name": "recent-chat-reconciliation-report-44-1", "expired": False},
            ],
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["dispatch_delay_minutes"], 97)

    def test_run_outside_delivery_window_is_missing(self) -> None:
        result = watchdog.evaluate_job(
            ENTRY,
            now=datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            target_date=None,
            list_runs=lambda _: [
                {
                    "id": 45,
                    "created_at": "2026-08-10T10:31:00Z",
                    "status": "completed",
                    "conclusion": "success",
                    "head_branch": "main",
                }
            ],
            list_jobs=lambda _: [],
            list_artifacts=lambda _: [],
        )
        self.assertEqual(result["status"], "failure")
        self.assertIn("no schedule-event run", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
