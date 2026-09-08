from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMAL_PATH = ROOT / "formal" / "continuous_recovery_supervisor.py"
POOL_PATH = ROOT / "tools" / "run_continuous_artifact_recovery_pool.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


formal = load_module("continuous_recovery_supervisor_formal", FORMAL_PATH)
pool = load_module("continuous_recovery_pool_under_test", POOL_PATH)


class ProductionInputTests(unittest.TestCase):
    def test_integer_inputs_are_canonical_unsigned_decimal(self) -> None:
        self.assertEqual(pool.worker_count(None), 3)
        self.assertEqual(pool.worker_count("1"), 1)
        self.assertEqual(pool.worker_count("2"), 2)
        self.assertEqual(pool.worker_count("3"), 3)

        for invalid in ("", "0", "4", "+1", " 1", "1 ", "1_0", "3.0", "three"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(pool.PoolError):
                    pool.worker_count(invalid)

    def test_worker_id_prefix_is_bounded_ascii_and_log_safe(self) -> None:
        for valid in (None, "continuous-recovery", "worker_1", "a.b-c"):
            with self.subTest(valid=valid):
                self.assertTrue(pool.worker_id_prefix(valid))

        for invalid in (
            "",
            " leading",
            "trailing ",
            ".hidden",
            "slash/value",
            "line\nbreak",
            "snowman-\u2603",
            "a" * 97,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(pool.PoolError):
                    pool.worker_id_prefix(invalid)


class BoundedModelTests(unittest.TestCase):
    def test_exact_bounded_state_space_and_witnesses(self) -> None:
        receipt = formal.run_verification()
        exploration = receipt["exploration"]
        self.assertEqual(
            [item["workers"] for item in exploration],
            [1, 2, 3],
        )
        self.assertEqual(
            [item["states"] for item in exploration],
            [80, 834, 4644],
        )
        self.assertEqual(
            [item["transitions"] for item in exploration],
            [101, 1416, 9382],
        )
        for item in exploration:
            self.assertTrue(item["clean_shutdown_witness"])
            self.assertTrue(item["budget_exhaustion_witness"])
            self.assertTrue(item["kill_timeout_witness"])

    def test_receipt_is_deterministic_and_source_bound(self) -> None:
        first = formal.run_verification()
        second = formal.run_verification()
        self.assertEqual(first, second)
        self.assertEqual(
            first["schema_version"],
            "ores.formal.continuous-recovery-supervisor.v1",
        )
        self.assertEqual(len(first["source"]["model_sha256"]), 64)
        self.assertEqual(len(first["source"]["production_sha256"]), 64)
        self.assertGreater(first["refinement"]["restart_budget_vectors"], 0)

        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(
                json.dumps(first, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            parsed = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed, first)

    def test_tla_peer_spec_names_every_critical_transition(self) -> None:
        text = (
            ROOT / "formal" / "ContinuousRecoverySupervisor.tla"
        ).read_text(encoding="utf-8")
        for required in (
            "Crash(i)",
            "StableExit(i)",
            "BackoffElapsed(i)",
            "WindowElapsed(i)",
            "StopRequested",
            "ChildExited(i)",
            "TerminateGraceExpired",
            "KillGraceExpired",
            "NoRespawnAfterStop",
            "Safety",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
