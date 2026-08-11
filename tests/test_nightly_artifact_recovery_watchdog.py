from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import nightly_artifact_recovery_watchdog as watchdog  # noqa: E402


def canonical_digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def policy() -> watchdog.WatchdogPolicy:
    return watchdog.WatchdogPolicy(
        repository="ORESoftware/ai-agent-coordinator.rs",
        workflow="nightly-artifact-recovery.yml",
        timezone_name="America/New_York",
        local_time="00:37",
        max_dispatch_delay_minutes=240,
        settlement_minutes=310,
        execution_job="enqueue-and-verify",
        artifact_prefix="artifact-recovery-terminal-",
        expected_branch="main",
    )


def run_fixture() -> dict:
    return {
        "id": 1001,
        "event": "schedule",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_sha": "a" * 40,
        "run_started_at": "2026-08-10T05:07:00Z",
        "run_attempt": 1,
        "html_url": "https://github.example.invalid/runs/1001",
    }


def jobs_fixture(conclusion: str = "success") -> list[dict]:
    return [
        {"name": "validate", "status": "completed", "conclusion": "success"},
        {
            "name": "enqueue-and-verify",
            "status": "completed",
            "conclusion": conclusion,
        },
    ]


def artifacts_fixture(expired: bool = False) -> list[dict]:
    return [
        {
            "id": 5001,
            "name": "artifact-recovery-terminal-1001-1",
            "expired": expired,
            "size_in_bytes": 2048,
            "digest": "sha256:" + "b" * 64,
        }
    ]


def terminal_receipt() -> dict:
    receipt = {
        "schema_version": "artifact_recovery_schedule_receipt.v1",
        "status": "complete",
        "recorded_at": "2026-08-10T05:20:00+00:00",
        "run_key": "artifact-recovery:scheduled:2026-08-10",
        "scheduled_for": "2026-08-10T00:37:00-04:00",
        "observed_local_time": "2026-08-10T01:07:00-04:00",
        "recovered": False,
        "job_id": "job-1",
        "job_status": "succeeded",
        "attempts": 1,
        "updated_at": "2026-08-10T05:19:00Z",
        "validation": {
            "run_key": "artifact-recovery:scheduled:2026-08-10",
            "status": "complete",
            "prompts_scanned": 42,
            "actionable_items": 7,
            "dispositioned_items": 7,
            "source_coverage_status": "complete",
            "report_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
        },
    }
    receipt["receipt_sha256"] = canonical_digest(receipt)
    return receipt


class NightlyArtifactRecoveryWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 10, 10, 7, tzinfo=timezone.utc)
        self.policy = policy()
        self.night = watchdog.logical_night(self.now, self.policy)

    def test_logical_night_uses_today_after_settlement(self) -> None:
        self.assertEqual(self.night.logical_date.isoformat(), "2026-08-10")
        self.assertEqual(
            self.night.expected_run_key, "artifact-recovery:scheduled:2026-08-10"
        )

    def test_logical_night_uses_yesterday_before_settlement(self) -> None:
        night = watchdog.logical_night(
            datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc), self.policy
        )
        self.assertEqual(night.logical_date.isoformat(), "2026-08-09")

    def test_green_workflow_requires_execution_job_and_receipt_artifact(self) -> None:
        result = watchdog.evaluate_attempt(
            run_fixture(),
            jobs_fixture(),
            artifacts_fixture(),
            policy=self.policy,
            night=self.night,
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["dispatch_delay_minutes"], 30)

    def test_skipped_execution_job_cannot_hide_behind_green_validation(self) -> None:
        jobs = jobs_fixture("skipped")
        result = watchdog.evaluate_attempt(
            run_fixture(), jobs, artifacts_fixture(), policy=self.policy, night=self.night
        )
        self.assertEqual(result["status"], "failure")
        self.assertTrue(any("enqueue-and-verify" in reason for reason in result["reasons"]))

    def test_missing_or_expired_terminal_artifact_fails(self) -> None:
        missing = watchdog.evaluate_attempt(
            run_fixture(), jobs_fixture(), [], policy=self.policy, night=self.night
        )
        expired = watchdog.evaluate_attempt(
            run_fixture(),
            jobs_fixture(),
            artifacts_fixture(True),
            policy=self.policy,
            night=self.night,
        )
        self.assertEqual(missing["status"], "failure")
        self.assertEqual(expired["status"], "failure")

    def test_report_passes_when_any_redundant_attempt_is_fully_valid(self) -> None:
        failed_run = copy.deepcopy(run_fixture())
        failed_run["id"] = 1000
        failed_run["run_started_at"] = "2026-08-10T04:37:00Z"
        failed_run["conclusion"] = "failure"
        snapshot = watchdog.SnapshotClient(
            {
                "workflow_runs": [failed_run, run_fixture()],
                "jobs_by_run": {
                    "1000": jobs_fixture("failure"),
                    "1001": jobs_fixture(),
                },
                "artifacts_by_run": {
                    "1000": [],
                    "1001": artifacts_fixture(),
                },
            }
        )
        report = watchdog.build_report(
            snapshot, policy=self.policy, night=self.night, now=self.now
        )
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["selected_attempt"]["run_id"], 1001)
        self.assertEqual(len(report["attempts"]), 2)

    def test_no_post_schedule_run_fails_closed(self) -> None:
        snapshot = watchdog.SnapshotClient(
            {"workflow_runs": [], "jobs_by_run": {}, "artifacts_by_run": {}}
        )
        report = watchdog.build_report(
            snapshot, policy=self.policy, night=self.night, now=self.now
        )
        self.assertEqual(report["status"], "failure")
        self.assertTrue(any("no scheduled workflow run" in reason for reason in report["reasons"]))

    def test_terminal_receipt_digest_and_accounting_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            path.write_text(json.dumps(terminal_receipt()))
            result = watchdog.validate_terminal_receipt(
                path, "artifact-recovery:scheduled:2026-08-10"
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["actionable_items"], 7)

            bad = terminal_receipt()
            bad["validation"]["dispositioned_items"] = 6
            bad["receipt_sha256"] = canonical_digest(
                {key: value for key, value in bad.items() if key != "receipt_sha256"}
            )
            path.write_text(json.dumps(bad))
            with self.assertRaises(watchdog.WatchdogError):
                watchdog.validate_terminal_receipt(
                    path, "artifact-recovery:scheduled:2026-08-10"
                )

    def test_tampered_terminal_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            receipt = terminal_receipt()
            receipt["status"] = "failed"
            path.write_text(json.dumps(receipt))
            with self.assertRaises(watchdog.WatchdogError):
                watchdog.validate_terminal_receipt(
                    path, "artifact-recovery:scheduled:2026-08-10"
                )

    def test_fixture_cli_writes_machine_and_human_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_json = root / "report.json"
            report_md = root / "report.md"
            outputs = root / "outputs.txt"
            snapshot = root / "snapshot.json"
            receipt = root / "receipt.json"
            emitted = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "nightly_artifact_recovery_watchdog.py"),
                    "emit-fixtures",
                    "--snapshot-output",
                    str(snapshot),
                    "--receipt-output",
                    str(receipt),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(emitted.returncode, 0, emitted.stderr)
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "nightly_artifact_recovery_watchdog.py"),
                    "locate",
                    "--snapshot",
                    str(snapshot),
                    "--now",
                    "2026-08-10T10:07:00Z",
                    "--output-json",
                    str(report_json),
                    "--output-markdown",
                    str(report_md),
                    "--github-output",
                    str(outputs),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(report_json.read_text())["status"], "success")
            self.assertIn("Outcome: SUCCESS", report_md.read_text())
            self.assertIn("run_id=1001", outputs.read_text())
            combined = report_json.read_text() + report_md.read_text()
            self.assertNotIn("ghp_", combined)
            self.assertNotIn("lin_api_", combined)

    def test_receipt_fixture_cli_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "receipt-validation.json"
            snapshot = root / "snapshot.json"
            receipt = root / "receipt.json"
            emitted = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "nightly_artifact_recovery_watchdog.py"),
                    "emit-fixtures",
                    "--snapshot-output",
                    str(snapshot),
                    "--receipt-output",
                    str(receipt),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(emitted.returncode, 0, emitted.stderr)
            result = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "nightly_artifact_recovery_watchdog.py"),
                    "validate-receipt",
                    "--receipt",
                    str(receipt),
                    "--expected-run-key",
                    "artifact-recovery:scheduled:2026-08-10",
                    "--output-json",
                    str(output),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())["status"], "success")


if __name__ == "__main__":
    unittest.main()
