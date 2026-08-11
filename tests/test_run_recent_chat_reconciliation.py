from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import run_recent_chat_reconciliation as recent  # noqa: E402


def completion_job(
    *,
    complete: int = 0,
    already_landed: int = 0,
    in_review: int = 0,
    blocked: int = 0,
    deferred: int = 0,
) -> dict:
    actionable = complete + already_landed + in_review + blocked + deferred
    return {
        "result": {
            "summary": {
                "prompts_scanned": actionable + 2,
                "actionable_items": actionable,
                "dispositions": {
                    "complete": complete,
                    "already_landed": already_landed,
                    "in_review": in_review,
                    "blocked_with_owner": blocked,
                    "deferred_with_owner": deferred,
                },
            }
        }
    }


class RecentChatReconciliationTests(unittest.TestCase):
    def test_defaults_match_canonical_lima_task(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            args = recent.build_parser().parse_args([])
        self.assertEqual(args.timezone, "America/Lima")
        self.assertEqual(args.local_time, "00:30")
        self.assertEqual(args.window_hours, 96)
        self.assertEqual(args.overlap_hours, 6)
        self.assertEqual(args.run_key_prefix, "recent-chat-reconciliation")

    def test_primary_and_recovery_share_namespaced_daily_key(self) -> None:
        primary = recent.generic.robust_schedule_decision(
            datetime(2026, 8, 10, 5, 30, tzinfo=timezone.utc),
            timezone_name="America/Lima",
            local_time="00:30",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
        )
        recovery = recent.generic.robust_schedule_decision(
            datetime(2026, 8, 10, 7, 7, tzinfo=timezone.utc),
            timezone_name="America/Lima",
            local_time="00:30",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
        )
        primary = recent.namespace_decision(primary, "recent-chat-reconciliation")
        recovery = recent.namespace_decision(recovery, "recent-chat-reconciliation")
        self.assertEqual(primary.run_key, recovery.run_key)
        self.assertEqual(
            primary.run_key, "recent-chat-reconciliation:scheduled:2026-08-10"
        )
        self.assertTrue(recovery.recovered)

    def test_manual_run_is_namespaced_and_separate(self) -> None:
        decision = recent.generic.robust_schedule_decision(
            datetime(2026, 8, 10, 16, 0, tzinfo=timezone.utc),
            timezone_name="America/Lima",
            local_time="00:30",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
            force=True,
            manual_id="canary-1",
        )
        decision = recent.namespace_decision(decision, "recent-chat-reconciliation")
        self.assertEqual(
            decision.run_key,
            "recent-chat-reconciliation:manual:2026-08-10:canary-1",
        )
        self.assertEqual(
            decision.scheduled_run_key,
            "recent-chat-reconciliation:scheduled:2026-08-10",
        )

    def test_payload_requires_96_hour_chat_scope_and_all_work_complete(self) -> None:
        decision = recent.generic.robust_schedule_decision(
            datetime(2026, 8, 10, 5, 30, tzinfo=timezone.utc),
            timezone_name="America/Lima",
            local_time="00:30",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
        )
        decision = recent.namespace_decision(decision, "recent-chat-reconciliation")
        payload = recent.build_recent_payload(
            decision,
            timezone_name="America/Lima",
            local_time="00:30",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
            window_hours=96,
            overlap_hours=6,
            cli_task_id=recent.scheduler.DEFAULT_CLI_TASK_ID,
            job_name=recent.DEFAULT_JOB_NAME,
        )
        contract = payload["payload"]
        self.assertEqual(contract["source_contract"]["rolling_window_hours"], 96)
        self.assertEqual(contract["source_contract"]["primary_source"], "chatgpt_threads")
        self.assertTrue(contract["job_contract"]["require_duplicate_free_mutations"])
        self.assertTrue(contract["job_contract"]["require_all_work_complete"])

    def test_only_finished_dispositions_allow_success(self) -> None:
        summary = recent._completion_summary(
            completion_job(complete=2, already_landed=1)
        )
        self.assertTrue(summary["all_work_complete"])
        self.assertEqual(summary["unfinished_items"], 0)
        self.assertEqual(recent._completion_exit_code(summary), 0)

    def test_in_review_blocked_or_deferred_work_fails_visibly(self) -> None:
        summary = recent._completion_summary(
            completion_job(complete=1, in_review=1, blocked=1, deferred=1)
        )
        self.assertFalse(summary["all_work_complete"])
        self.assertEqual(summary["unfinished_items"], 3)
        self.assertEqual(recent._completion_exit_code(summary), 3)

    def test_dry_run_is_credential_free(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            rc = recent.main(
                [
                    "--endpoint",
                    "https://coordinator.example.invalid",
                    "--now",
                    "2026-08-10T05:30:00Z",
                    "--dry-run",
                ]
            )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
