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

import run_nightly_artifact_recovery as nightly  # noqa: E402


def digest(char: str) -> str:
    return char * 64


def complete_job(run_key: str) -> dict:
    return {
        "id": "job-1",
        "status": "succeeded",
        "attempts": 1,
        "updated_at": "2026-08-10T08:31:00Z",
        "result": {
            "schema_version": "artifact_recovery_completion.v1",
            "run_key": run_key,
            "status": "complete",
            "source_coverage": {
                "schema_version": "artifact_recovery_source_coverage.v1",
                "summary": {"status": "complete"},
                "report_sha256": digest("a"),
            },
            "summary": {
                "prompts_scanned": 20,
                "actionable_items": 5,
                "dispositions": {
                    "complete": 2,
                    "already_landed": 1,
                    "in_review": 1,
                    "blocked_with_owner": 1,
                    "deferred_with_owner": 0,
                },
                "unclassified_items": 0,
                "unowned_items": 0,
                "missing_evidence_items": 0,
            },
            "artifact_manifest": {
                "schema_version": "artifact_recovery_manifest.v1",
                "manifest_sha256": digest("b"),
                "artifacts": {
                    "run_ledger": {
                        "sha256": digest("c"),
                        "locator": "r2://private/run-ledger.json",
                    },
                    "source_coverage": {
                        "sha256": digest("d"),
                        "locator": "r2://private/source-coverage.json",
                    },
                    "reconciliation_report": {
                        "sha256": digest("e"),
                        "locator": "r2://private/report.json",
                    },
                },
            },
            "report_sha256": digest("f"),
        },
    }


class NightlyArtifactRecoveryTests(unittest.TestCase):
    def test_real_august_10_delay_is_recovered_not_silently_ignored(self) -> None:
        decision = nightly.robust_schedule_decision(
            datetime(2026, 8, 10, 8, 24, 56, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:17",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
        )
        self.assertTrue(decision.due)
        self.assertTrue(decision.recovered)
        self.assertEqual(
            decision.run_key, "artifact-recovery:scheduled:2026-08-10"
        )

    def test_outside_lateness_window_is_not_due(self) -> None:
        decision = nightly.robust_schedule_decision(
            datetime(2026, 8, 10, 12, 18, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:17",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
        )
        self.assertFalse(decision.due)

    def test_schedule_requires_explicit_activation(self) -> None:
        with self.assertRaises(nightly.NightlyRunError):
            nightly.require_enabled("ARTIFACT_RECOVERY_ENQUEUE_ENABLED", {})
        nightly.require_enabled(
            "ARTIFACT_RECOVERY_ENQUEUE_ENABLED",
            {"ARTIFACT_RECOVERY_ENQUEUE_ENABLED": "true"},
        )

    def test_payload_requires_96_hour_window_and_terminal_receipts(self) -> None:
        decision = nightly.robust_schedule_decision(
            datetime(2026, 8, 10, 7, 17, tzinfo=timezone.utc),
            timezone_name="America/Chicago",
            local_time="02:17",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
        )
        payload = nightly.build_hardened_payload(
            decision,
            timezone_name="America/Chicago",
            local_time="02:17",
            recovery_after_minutes=60,
            max_lateness_minutes=240,
            window_hours=96,
            overlap_hours=6,
            cli_task_id=nightly.scheduler.DEFAULT_CLI_TASK_ID,
        )
        contract = payload["payload"]
        self.assertEqual(contract["source_contract"]["rolling_window_hours"], 96)
        self.assertTrue(contract["source_contract"]["require_full_pagination"])
        self.assertTrue(contract["schedule_contract"]["silent_skip_forbidden"])
        self.assertEqual(
            contract["completion_contract"]["required_source_coverage_status"],
            "complete",
        )

    def test_completion_accepts_only_fully_dispositioned_work(self) -> None:
        run_key = "artifact-recovery:scheduled:2026-08-10"
        validation = nightly.validate_completion_job(complete_job(run_key), run_key)
        self.assertEqual(validation.actionable_items, 5)
        self.assertEqual(validation.dispositioned_items, 5)

        incomplete = complete_job(run_key)
        incomplete["result"]["summary"]["unclassified_items"] = 1
        with self.assertRaises(nightly.NightlyRunError):
            nightly.validate_completion_job(incomplete, run_key)

    def test_partial_source_coverage_blocks_success(self) -> None:
        run_key = "artifact-recovery:scheduled:2026-08-10"
        job = complete_job(run_key)
        job["result"]["source_coverage"]["summary"]["status"] = "partial"
        with self.assertRaises(nightly.NightlyRunError):
            nightly.validate_completion_job(job, run_key)

    def test_poll_waits_for_terminal_and_validates_receipt(self) -> None:
        run_key = "artifact-recovery:scheduled:2026-08-10"
        queue = iter(
            [{"status": "queued"}, {"status": "running"}, complete_job(run_key)]
        )
        clock = iter([0.0, 0.0, 1.0, 2.0])
        job, validation = nightly.poll_terminal_job(
            lambda: next(queue),
            expected_run_key=run_key,
            timeout_seconds=10,
            poll_interval_seconds=1,
            monotonic=lambda: next(clock),
            sleep=lambda _: None,
        )
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(validation.status, "complete")

    def test_main_dry_run_never_requires_activation_or_token(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            rc = nightly.main(
                [
                    "--endpoint",
                    "https://coordinator.example.invalid",
                    "--now",
                    "2026-08-10T07:17:00Z",
                    "--dry-run",
                ]
            )
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
