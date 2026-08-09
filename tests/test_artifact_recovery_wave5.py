from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_artifact_recovery_wave5 as wave5  # noqa: E402


class ArtifactRecoveryWave5Tests(unittest.TestCase):
    def fixture(self) -> dict:
        return wave5.build_fixture()

    def evidence(self) -> dict[str, dict]:
        return {item["id"]: item for item in self.fixture()["github_evidence"]}

    def test_payload_digest_and_window(self) -> None:
        raw = wave5.payload_bytes()
        self.assertEqual(wave5.PAYLOAD_SHA256, hashlib.sha256(raw).hexdigest())
        fixture = json.loads(raw)
        self.assertEqual(fixture["window"], {"start": "2026-06-29", "end": "2026-08-08"})
        self.assertEqual(fixture["excluded_targets"], ["dancing-dragons"])

    def test_safety_policy_is_fail_closed(self) -> None:
        policy = self.fixture()["policy"]
        for key in (
            "force_push",
            "automatic_merge",
            "production_mutation",
            "credential_values_stored",
            "binary_payloads_checked_in",
            "recovery_is_acceptance",
        ):
            self.assertIs(policy[key], False, key)

    def test_artifact_hashes_and_evidence_references(self) -> None:
        fixture = self.fixture()
        self.assertEqual(len(fixture["artifacts"]), 7)
        evidence_ids = {item["id"] for item in fixture["github_evidence"]}
        for artifact in fixture["artifacts"]:
            self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(artifact["bytes"], 0)
            self.assertLessEqual(set(artifact["evidence_ids"]), evidence_ids)

    def test_pr_state_matrix_and_exclusion(self) -> None:
        rows = self.fixture()["github_evidence"]
        states = [item["state"] for item in rows]
        self.assertEqual(states.count("open"), 14)
        self.assertEqual(states.count("closed"), 1)
        self.assertEqual(states.count("merged"), 1)
        self.assertFalse(any(item["repository"].lower().startswith("dancing-dragons/") for item in rows))

    def test_canonical_semantic_successor_is_green(self) -> None:
        rows = self.evidence()
        self.assertEqual(rows["canonical-pr19"]["state"], "closed")
        self.assertEqual(rows["canonical-pr20"]["state"], "open")
        self.assertEqual(rows["canonical-pr20"]["validation"]["status"], "green")
        self.assertEqual(
            {run["name"] for run in rows["canonical-pr20"]["validation"]["runs"]},
            {"ci", "declarative-postgres"},
        )

    def test_memebank_test_heads_pin_exact_product_heads(self) -> None:
        rows = self.evidence()
        self.assertEqual(rows["memebank-rest-test-pr2"]["certifies"], rows["memebank-rest-pr9"]["head"])
        self.assertEqual(rows["memebank-ocr-test-pr2"]["certifies"], rows["memebank-ocr-pr26"]["head"])
        self.assertEqual(rows["memebank-rest-test-pr2"]["validation"]["status"], "green")
        self.assertEqual(rows["memebank-ocr-test-pr2"]["validation"]["status"], "green")

    def test_go_failures_are_recorded_as_infrastructure_blocked(self) -> None:
        rows = self.evidence()
        for key in ("cm-pay-pr1", "cp-go-pr1"):
            validation = rows[key]["validation"]
            self.assertEqual(validation["status"], "infrastructure_blocked")
            self.assertIn("Actions spending limit", validation["reason"])

    def test_non_claims_preserve_failed_execution_reports(self) -> None:
        fixture = self.fixture()
        dispositions = {artifact["name"]: artifact["disposition"] for artifact in fixture["artifacts"]}
        self.assertEqual(dispositions["chatgpt-work-recovery-2026-08-08.zip"], "preserved_as_non_claim")
        self.assertEqual(dispositions["api_docs_rollout_execution_report.md"], "preserved_as_non_claim")
        self.assertEqual(dispositions["api_docs_rollout_status.json"], "preserved_as_non_claim")

    def test_cli_output_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "wave5.json"
            command = [
                sys.executable,
                str(SCRIPTS / "validate_artifact_recovery_wave5.py"),
                "--output",
                str(output),
            ]
            subprocess.run(command, check=True)
            first = output.read_bytes()
            self.assertEqual(first, wave5.payload_bytes())
            subprocess.run(command, check=True)
            self.assertEqual(first, output.read_bytes())


if __name__ == "__main__":
    unittest.main()
