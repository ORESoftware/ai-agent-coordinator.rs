from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

if not (TOOLS / "enqueue_artifact_recovery.py").exists():
    raise RuntimeError("tests must run from the repository root")

import run_continuous_artifact_recovery as continuous
import run_continuous_artifact_recovery_pool as pool
import run_continuous_artifact_recovery_worker as worker


class SchedulerTests(unittest.TestCase):
    def test_same_bucket_is_idempotent_and_adjacent_bucket_changes(self) -> None:
        first = continuous.schedule_decision(
            datetime(2026, 9, 2, 17, 1, 1, tzinfo=timezone.utc)
        )
        second = continuous.schedule_decision(
            datetime(2026, 9, 2, 17, 2, 59, tzinfo=timezone.utc)
        )
        adjacent = continuous.schedule_decision(
            datetime(2026, 9, 2, 17, 3, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(first.run_key, second.run_key)
        self.assertNotEqual(second.run_key, adjacent.run_key)

    def test_payload_has_exact_window_policy_and_max_three(self) -> None:
        decision = continuous.schedule_decision(
            datetime(2026, 9, 2, 17, 3, tzinfo=timezone.utc)
        )
        payload = continuous.build_payload(decision)
        contract = payload["payload"]
        self.assertEqual(
            contract["source_contract"]["rolling_window_hours"],
            1200,
        )
        self.assertEqual(
            contract["source_contract"]["worker_concurrency_limit"],
            3,
        )
        self.assertTrue(
            contract["ledger_contract"][
                "skip_archived_cancelled_duplicate_superseded_or_outmoded"
            ]
        )
        self.assertEqual(contract["tracking"]["policy_path"], "AGENTS.md")
        self.assertIn(
            "revoke_or_rotate_credentials",
            contract["forbidden_actions"],
        )

    def test_four_workers_are_rejected(self) -> None:
        decision = continuous.schedule_decision(datetime.now(timezone.utc))
        with self.assertRaisesRegex(
            continuous.ContinuousRecoveryError,
            "between 1 and 3",
        ):
            continuous.build_payload(decision, max_workers=4)

    def test_malformed_environment_integer_fails_closed(self) -> None:
        old = dict(os.environ)
        try:
            os.environ["CONTINUOUS_RECOVERY_INTERVAL_SECONDS"] = "three"
            with self.assertRaisesRegex(
                continuous.ContinuousRecoveryError,
                "must be an integer",
            ):
                continuous.build_parser()
        finally:
            os.environ.clear()
            os.environ.update(old)


class PoolTests(unittest.TestCase):
    def test_pool_count_is_bounded(self) -> None:
        self.assertEqual(pool.worker_count(None), 3)
        self.assertEqual(pool.worker_count("1"), 1)
        with self.assertRaisesRegex(pool.PoolError, "between 1 and 3"):
            pool.worker_count("4")
        with self.assertRaisesRegex(pool.PoolError, "must be an integer"):
            pool.worker_count("three")

    def test_restart_budget_stops_crash_thrash(self) -> None:
        budget = pool.RestartBudget(
            max_restarts=2,
            window_seconds=60,
            max_backoff_seconds=30,
        )
        allowed, delay, attempts = budget.record(0, 10.0)
        self.assertTrue(allowed)
        self.assertEqual((delay, attempts), (1.0, 1))

        allowed, delay, attempts = budget.record(0, 11.0)
        self.assertTrue(allowed)
        self.assertEqual((delay, attempts), (2.0, 2))

        allowed, delay, attempts = budget.record(0, 12.0)
        self.assertFalse(allowed)
        self.assertEqual((delay, attempts), (4.0, 3))

        budget.reset(0)
        allowed, delay, attempts = budget.record(0, 13.0)
        self.assertTrue(allowed)
        self.assertEqual((delay, attempts), (1.0, 1))


class WorkerTests(unittest.TestCase):
    def test_window_configuration_is_bounded(self) -> None:
        old = dict(os.environ)
        try:
            os.environ["ARTIFACT_RECOVERY_WINDOW_HOURS"] = "1200"
            os.environ["ARTIFACT_RECOVERY_OVERLAP_HOURS"] = "6"
            self.assertEqual(worker.configured_window(), (1200, 6))
            os.environ["ARTIFACT_RECOVERY_WINDOW_HOURS"] = "1201"
            with self.assertRaises(worker.WorkerError):
                worker.configured_window()
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_state_fence_serializes_the_shared_state_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with worker.state_fence(Path(directory), 1):
                self.assertTrue(
                    (
                        Path(directory)
                        / ".continuous-artifact-recovery.lock"
                    ).exists()
                )

    def test_fence_no_longer_wraps_the_complete_job(self) -> None:
        self.assertIs(
            worker.FencedRecoveryEngine.run,
            worker.engine_runtime.RecoveryEngine.run,
        )
        self.assertIn(
            "state_transaction",
            worker.FencedRecoveryEngine.__dict__,
        )

    def test_cursor_merge_is_monotonic(self) -> None:
        source = "github"
        current = {
            "schema_version": "artifact_recovery_cursors.v1",
            "sources": {
                source: {
                    "high_water_mark": "2026-09-02T18:00:00Z",
                    "coverage_state": "complete",
                    "receipt_sha256": "prior",
                }
            },
        }
        older = SimpleNamespace(
            spec=SimpleNamespace(source=source),
            high_water_mark="2026-09-02T17:00:00Z",
            receipt={"state": "complete"},
        )
        merged = worker.engine_runtime._merge_cursor_state(
            current,
            [older],
            updated_at=datetime(2026, 9, 2, 19, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            merged["sources"][source]["high_water_mark"],
            "2026-09-02T18:00:00Z",
        )

        newer = SimpleNamespace(
            spec=SimpleNamespace(source=source),
            high_water_mark="2026-09-02T20:00:00Z",
            receipt={"state": "complete"},
        )
        merged = worker.engine_runtime._merge_cursor_state(
            merged,
            [newer],
            updated_at=datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            merged["sources"][source]["high_water_mark"],
            "2026-09-02T20:00:00Z",
        )


if __name__ == "__main__":
    unittest.main()
