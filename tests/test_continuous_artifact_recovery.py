from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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
        first = continuous.schedule_decision(datetime(2026, 9, 2, 17, 1, 1, tzinfo=timezone.utc))
        second = continuous.schedule_decision(datetime(2026, 9, 2, 17, 2, 59, tzinfo=timezone.utc))
        adjacent = continuous.schedule_decision(datetime(2026, 9, 2, 17, 3, 0, tzinfo=timezone.utc))
        self.assertEqual(first.run_key, second.run_key)
        self.assertNotEqual(second.run_key, adjacent.run_key)

    def test_payload_has_exact_window_policy_and_max_three(self) -> None:
        decision = continuous.schedule_decision(datetime(2026, 9, 2, 17, 3, tzinfo=timezone.utc))
        payload = continuous.build_payload(decision)
        contract = payload["payload"]
        self.assertEqual(contract["source_contract"]["rolling_window_hours"], 1200)
        self.assertEqual(contract["source_contract"]["worker_concurrency_limit"], 3)
        self.assertTrue(contract["ledger_contract"]["skip_archived_cancelled_duplicate_superseded_or_outmoded"])
        self.assertEqual(contract["tracking"]["policy_path"], "AGENTS.md")
        self.assertIn("revoke_or_rotate_credentials", contract["forbidden_actions"])

    def test_four_workers_are_rejected(self) -> None:
        decision = continuous.schedule_decision(datetime.now(timezone.utc))
        with self.assertRaisesRegex(continuous.ContinuousRecoveryError, "between 1 and 3"):
            continuous.build_payload(decision, max_workers=4)


class PoolTests(unittest.TestCase):
    def test_pool_count_is_bounded(self) -> None:
        self.assertEqual(pool.worker_count(None), 3)
        self.assertEqual(pool.worker_count("1"), 1)
        with self.assertRaisesRegex(pool.PoolError, "between 1 and 3"):
            pool.worker_count("4")


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
                self.assertTrue((Path(directory) / ".continuous-artifact-recovery.lock").exists())


if __name__ == "__main__":
    unittest.main()
