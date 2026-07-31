from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "enqueue_weekly_job_digest.py"
SPEC = importlib.util.spec_from_file_location("weekly_job_digest", MODULE_PATH)
assert SPEC and SPEC.loader
weekly = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = weekly
SPEC.loader.exec_module(weekly)


class WeeklyJobDigestTests(unittest.TestCase):
    def test_dst_winter_and_summer_gate_at_nine_eastern(self) -> None:
        winter = weekly.schedule_decision(
            datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
        )
        summer = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 30, tzinfo=timezone.utc)
        )
        self.assertTrue(winter.due)
        self.assertTrue(summer.due)
        self.assertEqual(winter.local_time.hour, 9)
        self.assertEqual(summer.local_time.hour, 9)
        self.assertNotEqual(winter.local_time.utcoffset(), summer.local_time.utcoffset())

    def test_non_due_hour_is_noop(self) -> None:
        decision = weekly.schedule_decision(
            datetime(2026, 7, 27, 12, 30, tzinfo=timezone.utc)
        )
        self.assertFalse(decision.due)

    def test_run_key_is_iso_week_and_force_keeps_same_identity(self) -> None:
        instant = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        ordinary = weekly.schedule_decision(instant)
        forced = weekly.schedule_decision(instant, force=True)
        self.assertFalse(ordinary.due)
        self.assertTrue(forced.due)
        self.assertEqual(ordinary.run_key, forced.run_key)
        self.assertEqual(ordinary.run_key, "job-agent:platform-sre-cto:2026-W31")

    def test_payload_is_discovery_only_and_routes_submission(self) -> None:
        decision = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 5, tzinfo=timezone.utc)
        )
        payload = weekly.build_payload(decision.run_key, decision.local_time)
        inner = payload["payload"]
        self.assertEqual(payload["task_type"], "job_opportunity_digest")
        self.assertEqual(inner["linear"]["canonical_issue"], "DEN-826")
        self.assertEqual(inner["linear"]["browser_application_issue"], "DEN-256")
        self.assertIn("submit_application", inner["forbidden_actions"])
        self.assertIn("send_email", inner["forbidden_actions"])
        self.assertNotIn("submit_application", inner["allowed_actions"])
        self.assertTrue(inner["safety"]["separate_explicit_action_required_for_every_send"])
        self.assertEqual(
            inner["candidate_profile"]["compensation_target_usd"], 175000
        )
        self.assertEqual(
            inner["volume_boundary"]["requested_range"],
            {"minimum": 150, "maximum": 350},
        )
        self.assertFalse(inner["volume_boundary"]["automatic_submission"])
        self.assertTrue(
            inner["volume_boundary"][
                "quality_deduplication_and_confirmation_take_precedence"
            ]
        )

    def test_endpoint_rejects_credentials_and_remote_http(self) -> None:
        with self.assertRaises(weekly.JobDigestError):
            weekly.validate_endpoint("https://user:pass@example.com")
        with self.assertRaises(weekly.JobDigestError):
            weekly.validate_endpoint("http://example.com")
        self.assertEqual(
            weekly.validate_endpoint("http://127.0.0.1:8080/"),
            "http://127.0.0.1:8080",
        )

    def test_dry_run_redacts_bearer(self) -> None:
        decision = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 5, tzinfo=timezone.utc)
        )
        payload = weekly.build_payload(decision.run_key, decision.local_time)
        plan = weekly.redacted_plan(
            "https://coordinator.example.com", decision.run_key, payload
        )
        serialized = json.dumps(plan)
        self.assertIn("[REDACTED]", serialized)
        self.assertNotIn("secret-value", serialized)

    def test_main_dry_run_never_reads_token(self) -> None:
        with patch.dict(os.environ, {"AI_AGENT_COORDINATOR_API_TOKEN": "secret-value"}):
            with patch.object(weekly, "enqueue") as enqueue:
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    rc = weekly.main(
                        [
                            "--endpoint",
                            "https://coordinator.example.com",
                            "--now",
                            "2026-07-27T13:05:00Z",
                            "--dry-run",
                        ]
                    )
        self.assertEqual(rc, 0)
        enqueue.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("[REDACTED]", output)
        self.assertNotIn("secret-value", output)

    def test_enqueue_sends_idempotent_bearer_request_and_returns_safe_summary(self) -> None:
        class FakeResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, amount: int) -> bytes:
                return json.dumps(
                    {
                        "job": {
                            "id": "job-123",
                            "task_type": "job_opportunity_digest",
                            "status": "queued",
                        }
                    }
                ).encode("utf-8")

        class FakeOpener:
            request = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        decision = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 5, tzinfo=timezone.utc)
        )
        payload = weekly.build_payload(decision.run_key, decision.local_time)
        fake = FakeOpener()
        with patch.object(weekly, "build_opener", return_value=fake):
            result = weekly.enqueue(
                "https://coordinator.example.com",
                "secret-value",
                decision.run_key,
                payload,
                12.0,
            )

        self.assertEqual(result["job_id"], "job-123")
        self.assertEqual(result["run_key"], decision.run_key)
        self.assertNotIn("secret-value", json.dumps(result))
        self.assertEqual(fake.request.get_header("Idempotency-key"), decision.run_key)
        self.assertEqual(
            fake.request.get_header("Authorization"), "Bearer secret-value"
        )
        self.assertEqual(fake.timeout, 12.0)

    def test_enqueue_rejects_empty_token_before_network(self) -> None:
        decision = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 5, tzinfo=timezone.utc)
        )
        payload = weekly.build_payload(decision.run_key, decision.local_time)
        with patch.object(weekly, "build_opener") as opener:
            with self.assertRaises(weekly.JobDigestError):
                weekly.enqueue(
                    "https://coordinator.example.com",
                    "",
                    decision.run_key,
                    payload,
                    10.0,
                )
        opener.assert_not_called()

    def test_naive_now_is_rejected(self) -> None:
        with self.assertRaises(weekly.JobDigestError):
            weekly.parse_instant("2026-07-27T09:00:00")


if __name__ == "__main__":
    unittest.main()
