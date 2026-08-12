from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "scheduled_task_digest.py"
SPEC = importlib.util.spec_from_file_location("scheduled_task_digest", MODULE_PATH)
assert SPEC and SPEC.loader
digest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = digest
SPEC.loader.exec_module(digest)


class ScheduledTaskDigestTests(unittest.TestCase):
    def test_dst_safe_gate_accepts_summer_and_winter_0700_central(self) -> None:
        summer = digest.schedule_decision(
            datetime(2026, 8, 12, 12, 7, tzinfo=timezone.utc)
        )
        winter = digest.schedule_decision(
            datetime(2026, 1, 12, 13, 7, tzinfo=timezone.utc)
        )
        self.assertTrue(summer.due)
        self.assertTrue(winter.due)
        self.assertEqual(summer.local_time.hour, 7)
        self.assertEqual(winter.local_time.hour, 7)
        self.assertNotEqual(summer.local_time.utcoffset(), winter.local_time.utcoffset())
        self.assertEqual(summer.run_key, "scheduled-task-digest:2026-08-12")

    def test_dst_alternative_and_post_window_are_not_due(self) -> None:
        summer_alternative = digest.schedule_decision(
            datetime(2026, 8, 12, 13, 7, tzinfo=timezone.utc)
        )
        winter_alternative = digest.schedule_decision(
            datetime(2026, 1, 12, 12, 7, tzinfo=timezone.utc)
        )
        before = digest.schedule_decision(
            datetime(2026, 8, 12, 11, 59, tzinfo=timezone.utc)
        )
        self.assertFalse(summer_alternative.due)
        self.assertFalse(winter_alternative.due)
        self.assertFalse(before.due)

    def test_cron_parser_counts_hourly_window(self) -> None:
        start = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            digest.expected_occurrences(["0 * * * *"], start, end),
            24,
        )
        self.assertEqual(
            digest.expected_occurrences(["30 0 * * *"], start, end, timezone_name="America/Lima"),
            1,
        )

    def test_extract_crons_supports_quoted_and_unquoted_values(self) -> None:
        workflow = """
on:
  schedule:
    - cron: '7 12 * * *'
    - cron: "7 13 * * *"
    - cron: 30 0 * * * # recovery
"""
        self.assertEqual(
            digest.extract_crons(workflow),
            ["7 12 * * *", "7 13 * * *", "30 0 * * *"],
        )

    def test_invalid_cron_fails_closed(self) -> None:
        with self.assertRaises(digest.DigestError):
            digest.compile_cron("0 25 * * *")
        with self.assertRaises(digest.DigestError):
            digest.compile_cron("0 0 * *")

    def test_green_validate_plus_skipped_enqueue_is_false_green(self) -> None:
        run = {"status": "completed", "conclusion": "success"}
        jobs = [
            {"name": "validate", "conclusion": "success"},
            {"name": "enqueue", "conclusion": "skipped"},
        ]
        status, reason = digest.classify_run(run, jobs)
        self.assertEqual(status, "false_green")
        self.assertIn("substantive", reason)

    def test_timezone_guard_plus_skipped_delivery_is_not_due(self) -> None:
        run = {"status": "completed", "conclusion": "success"}
        jobs = [
            {"name": "timezone-guard", "conclusion": "success"},
            {"name": "deliver digest", "conclusion": "skipped"},
        ]
        status, _ = digest.classify_run(run, jobs)
        self.assertEqual(status, "not_due")

    def test_success_requires_substantive_workload(self) -> None:
        run = {"status": "completed", "conclusion": "success"}
        jobs = [
            {"name": "validate", "conclusion": "success"},
            {"name": "Discover, audit, harden, and test the clients fleet", "conclusion": "success"},
        ]
        status, _ = digest.classify_run(run, jobs)
        self.assertEqual(status, "success")

    def test_validation_only_green_is_unverified(self) -> None:
        run = {"status": "completed", "conclusion": "success"}
        jobs = [{"name": "validate", "conclusion": "success"}]
        status, _ = digest.classify_run(run, jobs)
        self.assertEqual(status, "unverified_success")

    def test_renderers_escape_html_and_keep_one_digest(self) -> None:
        fixture = {
            "schema_version": digest.DIGEST_SCHEMA,
            "generated_at": "2026-08-12T12:00:00Z",
            "window": {
                "start": "2026-08-11T12:00:00Z",
                "end": "2026-08-12T12:00:00Z",
                "hours": 24,
            },
            "recipient": "alexanded.d.mills@gmail.com",
            "summary": {
                "critical": 1,
                "attention": 0,
                "success": 1,
                "not_due": 0,
                "total": 2,
                "status_counts": {"failure": 1, "success": 1},
            },
            "coverage": {
                "complete": True,
                "repositories_scanned": 2,
                "scheduled_workflows": 2,
                "schedule_runs": 2,
                "errors": [],
            },
            "records": [
                {
                    "status": "failure",
                    "name": "<broken>",
                    "reason": "failed",
                    "repository": "org/repo",
                    "expected_occurrences": 1,
                    "observed_runs": 1,
                },
                {
                    "status": "success",
                    "name": "healthy",
                    "reason": "certified",
                    "repository": "org/repo2",
                    "expected_occurrences": 1,
                    "observed_runs": 1,
                },
            ],
        }
        fixture["digest_sha256"] = digest.digest_sha256(fixture)
        plain = digest.render_plain_text(fixture)
        rich = digest.render_html(fixture)
        self.assertEqual(plain.count("Scheduled-task digest"), 1)
        self.assertIn("[FAILED] <broken>", plain)
        self.assertIn("&lt;broken&gt;", rich)
        self.assertNotIn("<broken>", rich)

    def test_sendgrid_acceptance_returns_safe_receipt(self) -> None:
        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeResponse:
            status = 202
            headers = FakeHeaders({"X-Message-Id": "message-123"})

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeOpener:
            request = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        opener = FakeOpener()
        receipt = digest._send_sendgrid(
            api_key="secret-api-key",
            sender="sender@example.com",
            recipient="recipient@example.com",
            subject="subject",
            plain_text="plain",
            html_text="<p>html</p>",
            opener=opener,
        )
        serialized = json.dumps(receipt)
        self.assertNotIn("secret-api-key", serialized)
        self.assertEqual(receipt["message_id"], "message-123")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer secret-api-key")

    def test_delivery_without_provider_fails_closed(self) -> None:
        with self.assertRaises(digest.DigestError):
            digest.deliver_digest(
                recipient="recipient@example.com",
                subject="subject",
                plain_text="plain",
                html_text="<p>html</p>",
                environment={},
            )

    def test_config_binds_exact_requested_recipient(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "scheduled-task-digest.json"
        config = digest.load_config(config_path)
        self.assertEqual(config["recipient"], "alexanded.d.mills@gmail.com")
        self.assertIn("zed-pkg-test/.github", config["explicit_repositories"])

    def test_decision_command_writes_github_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with patch("sys.stdout", new_callable=io.StringIO):
                rc = digest.main(
                    [
                        "decision",
                        "--now",
                        "2026-08-12T12:07:00Z",
                        "--github-output",
                        str(output),
                    ]
                )
            self.assertEqual(rc, 0)
            values = output.read_text()
            self.assertIn("due=true", values)
            self.assertIn("logical_date=2026-08-12", values)
            self.assertIn(
                "artifact_name=scheduled-task-digest-receipt-2026-08-12",
                values,
            )

    def test_dry_run_receipt_contains_no_environment_secrets(self) -> None:
        decision = digest.schedule_decision(
            datetime(2026, 8, 12, 12, 7, tzinfo=timezone.utc)
        )
        fixture = {
            "schema_version": digest.DIGEST_SCHEMA,
            "digest_sha256": "a" * 64,
        }
        with tempfile.TemporaryDirectory() as directory:
            receipt = digest.write_receipt(
                Path(directory),
                decision=decision,
                digest=fixture,
                delivery={"provider": "stdout", "accepted": False, "dry_run": True},
                recipient="recipient@example.com",
                sent_at=datetime(2026, 8, 12, 12, 8, tzinfo=timezone.utc),
            )
            self.assertNotIn("token", json.dumps(receipt).lower())


if __name__ == "__main__":
    unittest.main()
