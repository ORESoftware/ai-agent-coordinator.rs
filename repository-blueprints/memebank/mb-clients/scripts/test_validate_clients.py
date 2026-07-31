#!/usr/bin/env python3
from __future__ import annotations

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from behavior import advance_cursor, build_plan, should_retry  # noqa: E402
from contract import validate_operations, validate_policy  # noqa: E402
from generate_clients import generate  # noqa: E402
from io_helpers import load_json  # noqa: E402
from validate_clients import root, validate  # noqa: E402


class ClientBlueprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = root()
        cls.operations = load_json(cls.base / "contract/operations.json")
        cls.policy = load_json(cls.base / "contract/client-policy.json")

    def test_checked_in_blueprint(self) -> None:
        report = validate(self.base)
        self.assertTrue(report["valid"])
        self.assertEqual(report["operation_count"], 5)
        self.assertEqual(report["language_count"], 3)
        self.assertEqual(report["scenario_counts"]["retry_scenarios"], 6)
        self.assertFalse(report["publication_ready"])

    def test_generator_is_deterministic(self) -> None:
        self.assertEqual(generate(self.base), generate(self.base))

    def test_duplicate_operation_is_rejected(self) -> None:
        document = copy.deepcopy(self.operations)
        document["operations"][1]["operation_id"] = document["operations"][0]["operation_id"]
        with self.assertRaisesRegex(ValueError, "duplicate operation_id"):
            validate_operations(document)

    def test_retryable_write_requires_idempotency(self) -> None:
        document = copy.deepcopy(self.operations)
        document["operations"][0]["idempotency_key"] = "forbidden"
        with self.assertRaisesRegex(ValueError, "retryable write must permit"):
            validate_operations(document)

    def test_auth_and_request_id_are_mandatory(self) -> None:
        document = copy.deepcopy(self.operations)
        document["operations"][0]["auth_required"] = False
        with self.assertRaisesRegex(ValueError, "auth_required must be true"):
            validate_operations(document)
        document = copy.deepcopy(self.operations)
        document["operations"][0]["request_id_required"] = False
        with self.assertRaisesRegex(ValueError, "request_id_required must be true"):
            validate_operations(document)

    def test_retry_budget_is_bounded(self) -> None:
        document = copy.deepcopy(self.policy)
        document["retry"]["max_attempts"] = 11
        with self.assertRaisesRegex(ValueError, "bounded at 10"):
            validate_policy(document)

    def test_sensitive_observability_fields_are_forbidden(self) -> None:
        document = copy.deepcopy(self.policy)
        document["observability"]["forbidden_attributes"].remove("ocr_text")
        with self.assertRaisesRegex(ValueError, "forbidden_attributes is incomplete"):
            validate_policy(document)

    def test_request_planning_enforces_idempotency_and_paths(self) -> None:
        operations = validate_operations(copy.deepcopy(self.operations))
        policy = validate_policy(copy.deepcopy(self.policy))
        create_import = next(item for item in operations if item["operation_id"] == "createImport")
        with self.assertRaisesRegex(ValueError, "requires Idempotency-Key"):
            build_plan(create_import, {}, {}, {}, policy)
        get_asset = next(item for item in operations if item["operation_id"] == "getAsset")
        with self.assertRaisesRegex(ValueError, "path parameters"):
            build_plan(get_asset, {"asset_id": "a", "extra": "b"}, {}, {}, policy)

    def test_retry_stops_after_budget_or_body_start(self) -> None:
        policy = validate_policy(copy.deepcopy(self.policy))
        self.assertTrue(should_retry("safe_read", 503, 1, False, policy))
        self.assertFalse(should_retry("safe_read", 503, 3, False, policy))
        self.assertFalse(should_retry("safe_read", 503, 1, True, policy))

    def test_cursor_never_moves_backwards(self) -> None:
        current = {"epoch": 4, "sequence": 88}
        through = {"epoch": 4, "sequence": 95}
        self.assertEqual(advance_cursor(current, through), through)
        with self.assertRaisesRegex(ValueError, "must not move backwards"):
            advance_cursor(through, current)

    def test_stale_generated_output_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "mb-clients"
            shutil.copytree(self.base, destination)
            path = destination / "clients/typescript/src/generated.ts"
            path.write_text(path.read_text().replace("contract-digest: sha256:", "contract-digest: sha256:stale-", 1))
            with self.assertRaisesRegex(ValueError, "generated output is stale"):
                validate(destination)


if __name__ == "__main__":
    unittest.main()
