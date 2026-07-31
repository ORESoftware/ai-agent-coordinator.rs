#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).with_name("validate_contracts.py")
SPEC = importlib.util.spec_from_file_location("validate_contracts", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ROOT = Path(__file__).resolve().parents[1]


class ContractValidationTests(unittest.TestCase):
    def copy_blueprint(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        destination = Path(directory.name) / "mb-interfaces"
        shutil.copytree(ROOT, destination)
        return destination

    def test_blueprint_validates_and_report_is_deterministic(self) -> None:
        first = MODULE.validate_root(ROOT)
        second = MODULE.validate_root(ROOT)
        self.assertEqual(first, second)
        self.assertEqual(first["package"], "memebank/mb-interfaces")
        self.assertEqual(first["validated_fixture_count"], 7)
        self.assertEqual(
            first["operations"],
            [
                "GET /v1/assets/{asset_id}",
                "GET /v1/sync/events",
                "POST /v1/clipboard/manifests",
                "POST /v1/imports",
                "POST /v1/search",
            ],
        )

    def test_missing_local_reference_is_rejected(self) -> None:
        root = self.copy_blueprint()
        path = root / "schemas" / "v1" / "search.schema.json"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace(
                "common.schema.json#/$defs/Uuid",
                "missing.schema.json#/$defs/Uuid",
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MODULE.ContractValidationError, "missing file"):
            MODULE.validate_root(root)

    def test_removing_a_required_v1_field_is_rejected(self) -> None:
        root = self.copy_blueprint()
        path = root / "schemas" / "v1" / "catalog.schema.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["$defs"]["Asset"]["required"].remove("asset_id")
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ContractValidationError, "removed required fields"):
            MODULE.validate_root(root)

    def test_removing_a_public_operation_is_rejected(self) -> None:
        root = self.copy_blueprint()
        path = root / "openapi" / "v1" / "openapi.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        del document["paths"]["/v1/search"]
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ContractValidationError, "missing operation"):
            MODULE.validate_root(root)

    def test_secret_bearing_fixture_field_is_rejected(self) -> None:
        root = self.copy_blueprint()
        path = root / "fixtures" / "v1" / "asset.valid.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["access_token"] = "fixture-values-must-never-contain-tokens"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.ContractValidationError, "forbidden fixture fields"):
            MODULE.validate_root(root)


if __name__ == "__main__":
    unittest.main()
