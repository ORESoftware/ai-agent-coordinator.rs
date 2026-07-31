#!/usr/bin/env python3
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from render_infra import render_all  # noqa: E402
from validate_infra import (  # noqa: E402
    blueprint_root,
    load_documents,
    validate_documents,
)


class InfraBlueprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = blueprint_root()
        cls.documents = load_documents(cls.root)

    def documents_copy(self):
        return copy.deepcopy(self.documents)

    def assert_invalid(self, documents, message: str) -> None:
        with self.assertRaisesRegex(ValueError, message):
            validate_documents(documents)

    def test_checked_in_blueprint_is_valid_but_not_promotion_ready(self) -> None:
        report = validate_documents(self.documents_copy())
        self.assertTrue(report["valid"])
        self.assertEqual(report["package"], "memebank/mb-infra")
        self.assertEqual(report["application_count"], 14)
        self.assertEqual(report["worker_profile_count"], 3)
        self.assertEqual(report["promotion_ready_environment_count"], 0)
        self.assertTrue(report["environments"]["dev"]["renderable"])
        self.assertFalse(report["environments"]["prod"]["deployment_enabled"])

    def test_rejects_superseded_repository_name(self) -> None:
        documents = self.documents_copy()
        documents["fleet"]["package"] = "memebank/memebank-infra"
        self.assert_invalid(documents, "fleet.package must equal memebank/mb-infra")

    def test_rejects_duplicate_sync_wave(self) -> None:
        documents = self.documents_copy()
        documents["fleet"]["applications"][1]["sync_wave"] = 0
        self.assert_invalid(documents, "duplicate sync wave")

    def test_rejects_dependency_that_is_not_in_an_earlier_wave(self) -> None:
        documents = self.documents_copy()
        documents["fleet"]["applications"][1]["depends_on"] = ["smoke-tests"]
        self.assert_invalid(documents, "must have an earlier sync wave")

    def test_rejects_mutable_enabled_production_revision(self) -> None:
        documents = self.documents_copy()
        prod = documents["environments"]["prod"]
        prod["deployment_enabled"] = True
        prod["source_revision"] = "main"
        self.assert_invalid(documents, "source_revision must be a 40-character SHA")

    def test_rejects_plaintext_secret_field(self) -> None:
        documents = self.documents_copy()
        documents["environments"]["dev"]["password"] = "not-a-real-secret"
        self.assert_invalid(documents, "plaintext secret-bearing field is forbidden")

    def test_rejects_cloud_secret_on_local_worker(self) -> None:
        documents = self.documents_copy()
        profile = documents["worker_profiles"]["profiles"]["enrichment-local-cpu"]
        profile["secret_refs"] = ["external-secrets/cloud-provider"]
        self.assert_invalid(documents, "local inference profile must not carry secrets")

    def test_rejects_model_without_checksum(self) -> None:
        documents = self.documents_copy()
        model = documents["bundle"]["models"]["synthetic_fixture_ocr"]
        del model["checksum"]
        self.assert_invalid(documents, "checksum must be a non-empty string")

    def test_rejects_privileged_worker(self) -> None:
        documents = self.documents_copy()
        security = documents["worker_profiles"]["profiles"]["enrichment-local-cpu"]["security_context"]
        security["privileged"] = True
        self.assert_invalid(documents, "privileged must be false")

    def test_rejects_production_auto_sync(self) -> None:
        documents = self.documents_copy()
        documents["environments"]["prod"]["auto_sync"] = True
        self.assert_invalid(documents, "prod.auto_sync must remain false")

    def test_renderer_is_deterministic_and_keeps_prod_manual(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_index = render_all(self.root, Path(first))
            second_index = render_all(self.root, Path(second))
            self.assertEqual(first_index, second_index)
            prod_root = Path(first) / "bootstrap/argocd/prod/root-application.json"
            self.assertNotIn('"automated"', prod_root.read_text(encoding="utf-8"))
            dev_root = Path(first) / "bootstrap/argocd/dev/root-application.json"
            self.assertIn('"automated"', dev_root.read_text(encoding="utf-8"))
            self.assertTrue((Path(first) / "render-index.json").is_file())


if __name__ == "__main__":
    unittest.main()
