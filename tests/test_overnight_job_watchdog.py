from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import overnight_job_watchdog as watchdog  # noqa: E402

CATALOG = ROOT / "config" / "overnight-jobs.json"
ENTRY = watchdog.select_entry(
    watchdog.load_catalog(str(CATALOG)), "recent-chat-reconciliation"
)


def successful_attempt(run_id: int = 42, attempt: int = 1):
    run = {
        "id": run_id,
        "run_attempt": attempt,
        "event": "schedule",
        "created_at": "2026-08-10T05:34:00Z",
        "run_started_at": "2026-08-10T05:34:00Z",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "html_url": "https://example.invalid/run/42",
    }
    jobs = [
        {
            "name": "enqueue-and-verify",
            "status": "completed",
            "conclusion": "success",
        }
    ]
    artifacts = [
        {
            "id": 101,
            "name": f"recent-chat-reconciliation-terminal-{run_id}-{attempt}",
            "expired": False,
            "size_in_bytes": 1024,
        },
        {
            "id": 102,
            "name": f"recent-chat-reconciliation-report-{run_id}-{attempt}",
            "expired": False,
            "size_in_bytes": 2048,
        },
    ]
    return run, jobs, artifacts


class OvernightWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "snapshot.json"
        self.receipt = self.root / "terminal.json"
        self.report = self.root / "report"
        rc = watchdog.main(
            [
                "emit-fixtures",
                "--catalog",
                str(CATALOG),
                "--snapshot-output",
                str(self.snapshot),
                "--terminal-receipt-output",
                str(self.receipt),
                "--report-dir",
                str(self.report),
            ]
        )
        self.assertEqual(rc, 0)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def validate(self):
        return watchdog.validate_evidence(
            entry=ENTRY,
            terminal_receipt=self.receipt,
            report_dir=self.report,
            expected_run_key="recent-chat-reconciliation:scheduled:2026-08-10",
            run_id=42,
            run_attempt=1,
            terminal_artifact_name="recent-chat-reconciliation-terminal-42-1",
            report_artifact_name="recent-chat-reconciliation-report-42-1",
        )

    def rewrite_receipt(self, mutate) -> None:
        data = json.loads(self.receipt.read_text())
        mutate(data)
        data.pop("receipt_sha256", None)
        data["receipt_sha256"] = watchdog._canonical_digest(data)
        self.receipt.write_text(json.dumps(data))

    def rewrite_ledger(self, mutate) -> None:
        path = self.report / "run-ledger.json"
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data))

    def test_success_requires_run_job_and_exact_artifacts(self) -> None:
        run, jobs, artifacts = successful_attempt()
        night = watchdog.logical_night(
            datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            ENTRY,
            "2026-08-10",
        )
        result = watchdog.evaluate_attempt(
            run, entry=ENTRY, night=night, jobs=jobs, artifacts=artifacts
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["dispatch_delay_minutes"], 4)

    def test_false_green_missing_live_job_fails(self) -> None:
        run, _, artifacts = successful_attempt()
        night = watchdog.logical_night(
            datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            ENTRY,
            "2026-08-10",
        )
        result = watchdog.evaluate_attempt(
            run,
            entry=ENTRY,
            night=night,
            jobs=[
                {"name": "validate", "status": "completed", "conclusion": "success"}
            ],
            artifacts=artifacts,
        )
        self.assertEqual(result["status"], "failure")
        self.assertTrue(any("required job" in item for item in result["reasons"]))

    def test_delayed_recovery_inside_window_can_satisfy_job(self) -> None:
        run, jobs, artifacts = successful_attempt()
        run["created_at"] = run["run_started_at"] = "2026-08-10T07:07:00Z"
        night = watchdog.logical_night(
            datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            ENTRY,
            "2026-08-10",
        )
        result = watchdog.evaluate_attempt(
            run, entry=ENTRY, night=night, jobs=jobs, artifacts=artifacts
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["dispatch_delay_minutes"], 97)

    def test_run_outside_delivery_window_is_not_selected(self) -> None:
        snapshot = json.loads(self.snapshot.read_text())
        snapshot["workflow_runs"][0]["created_at"] = "2026-08-10T10:31:00Z"
        snapshot["workflow_runs"][0]["run_started_at"] = "2026-08-10T10:31:00Z"
        client = watchdog.SnapshotClient(snapshot)
        night = watchdog.logical_night(
            datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            ENTRY,
            "2026-08-10",
        )
        report = watchdog.build_report(
            client,
            entry=ENTRY,
            night=night,
            now=datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(report["status"], "failure")
        self.assertIn("no scheduled workflow run", report["reasons"][0])

    def test_valid_downloaded_evidence_passes(self) -> None:
        result = self.validate()
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["all_work_complete"])
        self.assertEqual(result["unfinished_items"], 0)

    def test_terminal_receipt_digest_tampering_fails(self) -> None:
        data = json.loads(self.receipt.read_text())
        data["completion_summary"]["unfinished_items"] = 1
        self.receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(watchdog.WatchdogError, "digest mismatch"):
            self.validate()

    def test_wrong_run_key_fails(self) -> None:
        self.rewrite_receipt(lambda data: data.__setitem__("run_key", "wrong:key"))
        with self.assertRaisesRegex(watchdog.WatchdogError, "run key mismatch"):
            self.validate()

    def test_all_work_complete_false_fails(self) -> None:
        self.rewrite_receipt(
            lambda data: data["completion_summary"].__setitem__(
                "all_work_complete", False
            )
        )
        with self.assertRaisesRegex(watchdog.WatchdogError, "all_work_complete"):
            self.validate()

    def test_in_review_work_fails_even_when_dispositioned(self) -> None:
        def mutate(data):
            dispositions = data["completion_summary"]["dispositions"]
            dispositions["complete"] = 1
            dispositions["in_review"] = 1
            data["completion_summary"]["unfinished_items"] = 1
            data["completion_summary"]["all_work_complete"] = False

        self.rewrite_receipt(mutate)
        with self.assertRaises(watchdog.WatchdogError):
            self.validate()

    def test_ledger_execution_identity_mismatch_fails(self) -> None:
        self.rewrite_ledger(lambda data: data.__setitem__("run_attempt", 2))
        with self.assertRaisesRegex(watchdog.WatchdogError, "execution identity"):
            self.validate()

    def test_ledger_manifest_mismatch_fails(self) -> None:
        self.rewrite_ledger(lambda data: data.__setitem__("artifact_manifest", []))
        with self.assertRaisesRegex(watchdog.WatchdogError, "artifact manifest"):
            self.validate()

    def test_before_settlement_defaults_to_previous_logical_date(self) -> None:
        night = watchdog.logical_night(
            datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc), ENTRY
        )
        self.assertEqual(night.logical_date.isoformat(), "2026-08-09")

    def test_duplicate_exact_artifact_is_rejected(self) -> None:
        run, jobs, artifacts = successful_attempt()
        artifacts.append(copy.deepcopy(artifacts[0]))
        night = watchdog.logical_night(
            datetime(2026, 8, 10, 13, 30, tzinfo=timezone.utc),
            ENTRY,
            "2026-08-10",
        )
        with self.assertRaisesRegex(watchdog.WatchdogError, "duplicate artifact"):
            watchdog.evaluate_attempt(
                run, entry=ENTRY, night=night, jobs=jobs, artifacts=artifacts
            )


if __name__ == "__main__":
    unittest.main()
