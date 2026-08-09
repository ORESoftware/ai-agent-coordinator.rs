from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "validate_artifact_recovery_wave5_sealed_archives.py"
SPEC = importlib.util.spec_from_file_location("wave5_sealed", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class SealedArchiveRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = module.load()

    def test_canonical_fixture_is_valid(self) -> None:
        module.validate(copy.deepcopy(self.fixture))

    def test_force_push_is_rejected(self) -> None:
        value = copy.deepcopy(self.fixture)
        value["policy"]["force_push"] = True
        with self.assertRaises(ValueError):
            module.validate(value)

    def test_binary_payload_claim_is_rejected(self) -> None:
        value = copy.deepcopy(self.fixture)
        value["policy"]["binary_payload_committed"] = True
        with self.assertRaises(ValueError):
            module.validate(value)

    def test_checksum_drift_is_rejected(self) -> None:
        value = copy.deepcopy(self.fixture)
        value["archives"][0]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            module.validate(value)

    def test_repository_count_drift_is_rejected(self) -> None:
        value = copy.deepcopy(self.fixture)
        value["archives"][1]["expected_repository_histories"] = 31
        with self.assertRaises(ValueError):
            module.validate(value)

    def test_missing_canonical_evidence_is_rejected(self) -> None:
        value = copy.deepcopy(self.fixture)
        value["archives"][2]["canonical_heads"] = []
        with self.assertRaises(ValueError):
            module.validate(value)

    def test_excluded_target_is_rejected(self) -> None:
        value = copy.deepcopy(self.fixture)
        value["archives"][0]["canonical_heads"][0]["repository"] = "dancing-dragons/example"
        with self.assertRaises(ValueError):
            module.validate(value)

    def test_credential_shaped_value_is_rejected(self) -> None:
        value = copy.deepcopy(self.fixture)
        value["limitations"].append("ghp_" + "A" * 20)
        with self.assertRaises(ValueError):
            module.validate(value)


if __name__ == "__main__":
    unittest.main()
