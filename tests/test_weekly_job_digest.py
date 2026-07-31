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

TEST_PROFILE = {
    "target_roles": ["Synthetic Platform Engineer"],
    "skills": ["Synthetic distributed systems"],
    "work_authorization": {"review_required": True},
    "preferred_arrangement": ["Synthetic remote"],
    "compensation": {"review_required": True},
    "test_marker": "private-profile-marker",
}


class WeeklyJobDigestTests(unittest.TestCase):
    def test_dst_winter_and_summer_gate_at_0917_eastern(self) -> None:
        winter = weekly.schedule_decision(
            datetime(2026, 1, 5, 14, 17, tzinfo=timezone.utc)
        )
        summer = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 17, tzinfo=timezone.utc)
        )
        self.assertTrue(winter.due)
        self.assertTrue(summer.due)
        self.assertEqual((winter.local_time.hour, winter.local_time.minute), (9, 17))
        self.assertEqual((summer.local_time.hour, summer.local_time.minute), (9, 17))
        self.assertNotEqual(winter.local_time.utcoffset(), summer.local_time.utcoffset())

    def test_non_due_hour_or_minute_is_noop(self) -> None:
        wrong_hour = weekly.schedule_decision(
            datetime(2026, 7, 27, 12, 17, tzinfo=timezone.utc)
        )
        wrong_minute = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 30, tzinfo=timezone.utc)
        )
        self.assertFalse(wrong_hour.due)
        self.assertFalse(wrong_minute.due)

    def test_run_key_is_iso_week_and_force_keeps_same_identity(self) -> None:
        instant = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
        ordinary = weekly.schedule_decision(instant)
        forced = weekly.schedule_decision(instant, force=True)
        self.assertFalse(ordinary.due)
        self.assertTrue(forced.due)
        self.assertEqual(ordinary.run_key, forced.run_key)
        self.assertEqual(ordinary.run_key, "job-agent:platform-sre-cto:2026-W31")

    def test_profile_is_loaded_from_json_and_sensitive_fields_are_rejected(self) -> None:
        profile = weekly.load_candidate_profile(json.dumps(TEST_PROFILE))
        self.assertEqual(profile, TEST_PROFILE)

        with self.assertRaises(weekly.JobDigestError):
            weekly.load_candidate_profile(
                json.dumps({"target_roles": ["Role"], "password": "forbidden"})
            )
        with self.assertRaises(weekly.JobDigestError):
            weekly.load_candidate_profile(json.dumps({"skills": ["missing roles"]}))
        with self.assertRaises(weekly.JobDigestError):
            weekly.load_candidate_profile("not-json")

    def test_payload_is_discovery_only_and_routes_submission(self) -> None:
        decision = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 17, tzinfo=timezone.utc)
        )
        payload = weekly.build_payload(
            decision.run_key,
            decision.local_time,
            TEST_PROFILE,
        )
        inner = payload["payload"]
        self.assertEqual(payload["task_type"], "job_opportunity_digest")
        self.assertEqual(inner["linear"]["canonical_issue"], "DEN-826")
        self.assertEqual(inner["linear"]["browser_application_issue"], "DEN-256")
        self.assertEqual(inner["candidate_profile"], TEST_PROFILE)
        self.assertIn("submit_application", inner["forbidden_actions"])
        self.assertIn("send_email", inner["forbidden_actions"])
        self.assertNotIn("submit_application", inner["allowed_actions"])
        self.assertTrue(inner["safety"]["separate_explicit_action_required_for_every_send"])
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

    def test_dry_run_redacts_candidate_profile_and_bearer(self) -> None:
        decision = weekly.schedule_decision(
            datetime(2026, 7, 27, 13, 17, tzinfo=timezone.utc)
        )
        payload = weekly.build_payload(
            decision.run_key,
            decision.local_time,
            TEST_PROFILE,
        )
        plan = weekly.redacted_plan(
            "https://coordinator.example.com", decision.run_key, payload
        )
        serialized = json.dumps(plan)
        self.assertIn("[REDACTED]", serialized)
        self.assertNotIn("private-profile-marker", serialized)
        self.assertEqual(
            plan["request"]["payload"]["candidate_profile"],
            {"redacted": True},
        )

    def test_main_dry_run_never_prints_token_or_candidate_profile(self) -> None:
        environment = {
            "AI_AGENT_COORDINATOR_API_TOKEN": "secret-value",
            "AI_AGENT_JOB_PROFILE_JSON": json.dumps(TEST_PROFILE),
        }
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(weekly, "enqueue") as enqueue:
                with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                    rc = weekly.main(
                        [
                            "--endpoint",
                            "https://coordinator.example.com",
                            "--now",
                            "2026-07-27T13:17:00Z",
                            "--dry-run",
                        ]
                    )
        self.assertEqual(rc, 0)
        enqueue.assert_not_called()
        output = stdout.getvalue()
        self.assertIn("[REDACTED]", output)
        self.assertNotIn("secret-value", output)
        self.assertNotIn("private-profile-marker", output)

    def test_not_due_does_not_require_candidate_profile(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = weekly.main(
                    [
                        "--endpoint",
                        "https://coordinator.example.com",
                        "--now",
                        "2026-07-27T13:30:00Z",
                        "--dry-run",
                    ]
                )
        self.assertEqual(rc, 0)
        self.assertIn('"status": "not_due"', stdout.getvalue())

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
            datetime(2026, 7, 27, 13, 17, tzinfo=timezone.utc)
        )
        payload = weekly.build_payload(
            decision.run_key,
            decision.local_time,
            TEST_PROFILE,
        )
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
            datetime(2026, 7, 27, 13, 17, tzinfo=timezone.utc)
        )
        payload = weekly.build_payload(
            decision.run_key,
            decision.local_time,
            TEST_PROFILE,
        )
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
            weekly.parse_instant("2026-07-27T09:17:00")


if __name__ == "__main__":
    unittest.main()
