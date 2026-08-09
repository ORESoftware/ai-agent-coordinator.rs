from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import enqueue_artifact_recovery as scheduler  # noqa: E402


class EnqueueArtifactRecoveryTests(unittest.TestCase):
    def test_exact_central_schedule_and_recovery_invocation(self) -> None:
        scheduled = scheduler.schedule_decision(
            datetime(2026, 8, 7, 7, 0, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:00",
            recovery_minutes=60,
        )
        self.assertTrue(scheduled.due)
        self.assertFalse(scheduled.recovered)
        self.assertEqual(scheduled.run_key, "artifact-recovery:scheduled:2026-08-07")

        recovered = scheduler.schedule_decision(
            datetime(2026, 8, 7, 8, 0, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:00",
            recovery_minutes=60,
        )
        self.assertTrue(recovered.due)
        self.assertTrue(recovered.recovered)
        self.assertEqual(recovered.run_key, scheduled.run_key)

    def test_non_due_hour_is_no_op(self) -> None:
        decision = scheduler.schedule_decision(
            datetime(2026, 8, 7, 7, 30, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:00",
            recovery_minutes=60,
        )
        self.assertFalse(decision.due)

    def test_manual_run_has_separate_idempotency_key(self) -> None:
        decision = scheduler.schedule_decision(
            datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:00",
            force=True,
            manual_id="backfill-a",
        )
        self.assertEqual(decision.kind, "manual")
        self.assertEqual(decision.run_key, "artifact-recovery:manual:2026-08-07:backfill-a")
        self.assertEqual(decision.scheduled_run_key, "artifact-recovery:scheduled:2026-08-07")

    def test_payload_preserves_delivery_and_credential_boundaries(self) -> None:
        decision = scheduler.schedule_decision(
            datetime(2026, 8, 7, 7, 0, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:00",
        )
        payload = scheduler.build_payload(
            decision,
            timezone_name="America/Chicago",
            local_time="02:00",
            recovery_minutes=60,
            cli_task_id=scheduler.DEFAULT_CLI_TASK_ID,
        )
        self.assertEqual(payload["task_type"], "artifact_recovery")
        contract = payload["payload"]
        self.assertEqual(contract["tracking"]["canonical_issue"], "DEN-2797")
        self.assertEqual(contract["tracking"]["local_cli_task_id"], scheduler.DEFAULT_CLI_TASK_ID)
        self.assertEqual(contract["ledger_contract"]["default_new_repository_visibility"], "private")
        self.assertTrue(contract["delivery_contract"]["open_draft_pull_request"])
        self.assertEqual(contract["delivery_contract"]["repository_creation_fallback"], "emit_cli_recovery_item")
        self.assertIn("reuse_chat_pasted_credentials", contract["forbidden_actions"])
        self.assertIn("auto_merge", contract["forbidden_actions"])
        self.assertNotIn("merge_pull_request", contract["allowed_actions"])

    def test_redacted_plan_does_not_expose_token(self) -> None:
        decision = scheduler.schedule_decision(
            datetime(2026, 8, 7, 7, 0, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:00",
        )
        payload = scheduler.build_payload(
            decision,
            timezone_name="America/Chicago",
            local_time="02:00",
            recovery_minutes=60,
            cli_task_id=scheduler.DEFAULT_CLI_TASK_ID,
        )
        plan = scheduler.redacted_plan("https://coordinator.example.invalid", decision, payload)
        raw = json.dumps(plan)
        self.assertEqual(plan["authorization"], "Bearer [REDACTED]")
        self.assertNotIn("ghp_", raw)
        self.assertNotIn("sk-", raw)

    def test_endpoint_rejects_credentials_query_and_insecure_remote_http(self) -> None:
        for value in (
            "https://user:pass@example.com",
            "https://example.com?token=value",
            "http://example.com",
        ):
            with self.subTest(value=value):
                with self.assertRaises(scheduler.SchedulerError):
                    scheduler.validate_endpoint(value)
        self.assertEqual(scheduler.validate_endpoint("http://127.0.0.1:8080/"), "http://127.0.0.1:8080")


if __name__ == "__main__":
    unittest.main()
