#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "evaluate.py"
SPEC = importlib.util.spec_from_file_location("vision_evaluate", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ROOT = Path(__file__).resolve().parents[1]


class VisionBenchmarkTests(unittest.TestCase):
    def copy_root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        destination = Path(directory.name) / "vision"
        shutil.copytree(ROOT, destination)
        return destination

    def load_results(self, root: Path) -> dict[str, object]:
        path = root / "results" / "recorded-results.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def write_results(self, root: Path, document: dict[str, object]) -> None:
        (root / "results" / "recorded-results.json").write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def test_synthetic_matrix_is_deterministic_and_never_auto_promotes(self) -> None:
        first = MODULE.evaluate(ROOT)
        second = MODULE.evaluate(ROOT)
        self.assertEqual(first, second)
        self.assertTrue(first["lane_coverage"]["pass"])
        self.assertIsNone(first["decision"]["automatic_winner"])
        self.assertFalse(first["decision"]["promotion_ready"])

        reports = {
            report["candidate_id"]: report
            for report in first["candidate_reports"]
        }
        local = reports["fixture-local-rust"]
        remote = reports["fixture-remote-api"]
        self.assertTrue(local["hard_gate_pass"])
        self.assertFalse(remote["hard_gate_pass"])
        self.assertEqual(
            remote["metrics"]["captions"]["prompt_injection_resistance"],
            0.0,
        )
        self.assertEqual(local["metrics"]["retrieval"]["mrr"], 1.0)
        self.assertGreater(len(first["input_digests"]), 8)

    def test_tampered_asset_digest_is_rejected(self) -> None:
        root = self.copy_root()
        asset = root / "corpus" / "assets" / "stylized-english.svg"
        asset.write_text(asset.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.BenchmarkError, "asset digest mismatch"):
            MODULE.evaluate(root)

    def test_live_provider_recording_requires_explicit_opt_in(self) -> None:
        root = self.copy_root()
        results = self.load_results(root)
        run = results["runs"][1]
        run["recording_mode"] = "live"
        run["live_provider"] = True
        run["consent_record"] = "consent-fixture-2026-07-31"
        self.write_results(root, results)

        with self.assertRaisesRegex(MODULE.BenchmarkError, "--allow-live-provider"):
            MODULE.evaluate(root)

        report = MODULE.evaluate(
            root,
            allow_live_provider=True,
            max_live_cost_usd=0.01,
        )
        self.assertEqual(len(report["candidate_reports"]), 2)

    def test_live_provider_cost_ceiling_is_fail_closed(self) -> None:
        root = self.copy_root()
        results = self.load_results(root)
        run = results["runs"][1]
        run["recording_mode"] = "live"
        run["live_provider"] = True
        run["consent_record"] = "consent-fixture-2026-07-31"
        self.write_results(root, results)

        with self.assertRaisesRegex(MODULE.BenchmarkError, "live ceiling"):
            MODULE.evaluate(
                root,
                allow_live_provider=True,
                max_live_cost_usd=0.001,
            )

    def test_duplicate_json_keys_are_rejected(self) -> None:
        root = self.copy_root()
        policy = root / "policies" / "gates.json"
        policy.write_text(
            '{"schema_version":1,"schema_version":1}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MODULE.BenchmarkError, "duplicate JSON key"):
            MODULE.evaluate(root)

    def test_candidate_budget_is_enforced_even_for_recorded_results(self) -> None:
        root = self.copy_root()
        results = self.load_results(root)
        results["runs"][0]["observations"][0]["operation"]["cost_usd"] = 0.0001
        self.write_results(root, results)
        with self.assertRaisesRegex(MODULE.BenchmarkError, "candidate budget"):
            MODULE.evaluate(root)


if __name__ == "__main__":
    unittest.main()
