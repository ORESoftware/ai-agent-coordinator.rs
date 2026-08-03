#!/usr/bin/env python3
"""Regression tests for the MemeBank OCR/vision candidate inventory."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

VALIDATOR_PATH = Path(__file__).with_name("validate_memebank_vision_candidates.py")
SPEC = importlib.util.spec_from_file_location(
    "validate_memebank_vision_candidates",
    VALIDATOR_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPOSITORY_ROOT / MODULE.CATALOG_PATH


class VisionCandidateInventoryTests(unittest.TestCase):
    def catalog(self) -> dict[str, object]:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    def candidate(
        self,
        document: dict[str, object],
        candidate_id: str,
    ) -> dict[str, object]:
        candidates = document["candidates"]
        assert isinstance(candidates, list)
        for raw_candidate in candidates:
            assert isinstance(raw_candidate, dict)
            if raw_candidate["candidate_id"] == candidate_id:
                return raw_candidate
        raise AssertionError(f"missing fixture candidate {candidate_id}")

    def test_canonical_inventory_passes(self) -> None:
        summary = MODULE.validate_inventory(self.catalog())
        self.assertEqual(summary["candidate_count"], 29)
        self.assertEqual(
            summary["track_counts"],
            {"cloud": 9, "go": 5, "rust": 6, "typescript": 9},
        )
        self.assertEqual(
            summary["selection_required"],
            ["typescript-paddleocr-onnx-wrapper"],
        )
        self.assertEqual(summary["production_dependencies"], [])

    def test_duplicate_json_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="memebank-vision-candidates-"
        ) as temporary:
            path = Path(temporary) / "catalog.json"
            path.write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MODULE.InventoryError, "duplicate JSON key"):
                MODULE.load_json(path)

    def test_unknown_root_key_is_rejected(self) -> None:
        document = self.catalog()
        document["surprise"] = True
        with self.assertRaisesRegex(MODULE.InventoryError, "catalog keys changed"):
            MODULE.validate_inventory(document)

    def test_missing_candidate_is_rejected(self) -> None:
        document = self.catalog()
        candidates = document["candidates"]
        assert isinstance(candidates, list)
        candidates.pop()
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "candidate order or inventory changed",
        ):
            MODULE.validate_inventory(document)

    def test_unknown_candidate_is_rejected(self) -> None:
        document = self.catalog()
        candidate = self.candidate(document, "tesseract-js")
        candidate["candidate_id"] = "invented-wrapper"
        with self.assertRaisesRegex(MODULE.InventoryError, "unexpected candidate"):
            MODULE.validate_inventory(document)

    def test_duplicate_candidate_id_is_rejected(self) -> None:
        document = self.catalog()
        candidates = document["candidates"]
        assert isinstance(candidates, list)
        candidates.append(copy.deepcopy(candidates[0]))
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "candidate_id values must be unique",
        ):
            MODULE.validate_inventory(document)

    def test_candidate_cannot_change_track(self) -> None:
        document = self.catalog()
        self.candidate(document, "ort")["track"] = "go"
        with self.assertRaisesRegex(MODULE.InventoryError, "moved from rust to go"):
            MODULE.validate_inventory(document)

    def test_selection_required_candidate_cannot_fake_package_name(self) -> None:
        document = self.catalog()
        candidate = self.candidate(
            document,
            "typescript-paddleocr-onnx-wrapper",
        )
        identity = candidate["identity"]
        assert isinstance(identity, dict)
        identity["name"] = "generic-paddle-wrapper"
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "must not name a package",
        ):
            MODULE.validate_inventory(document)

    def test_exact_identity_requires_name(self) -> None:
        document = self.catalog()
        candidate = self.candidate(document, "gocv")
        identity = candidate["identity"]
        assert isinstance(identity, dict)
        identity["name"] = None
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "exact identity requires a name",
        ):
            MODULE.validate_inventory(document)

    def test_pilot_requires_pins_license_and_decision(self) -> None:
        document = self.catalog()
        self.candidate(document, "ocrs")["disposition"] = "pilot"
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "requires pinned_version, license, and decision_record",
        ):
            MODULE.validate_inventory(document)

    def test_evidenced_adoption_can_be_recorded(self) -> None:
        document = self.catalog()
        candidate = self.candidate(document, "tesseract-js")
        candidate["disposition"] = "adopt"
        candidate["production_dependency"] = True
        candidate["pinned_version"] = "7.0.0"
        candidate["license"] = "Apache-2.0"
        candidate["decision_record"] = "DEN-1011 benchmark decision 2026-08-03"
        summary = MODULE.validate_inventory(document)
        self.assertEqual(summary["production_dependencies"], ["tesseract-js"])

    def test_production_dependency_requires_adopt(self) -> None:
        document = self.catalog()
        self.candidate(document, "gocv")["production_dependency"] = True
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "production dependency requires adopt",
        ):
            MODULE.validate_inventory(document)

    def test_defer_requires_decision_record(self) -> None:
        document = self.catalog()
        self.candidate(document, "tfgo")["disposition"] = "defer"
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "defer requires a decision_record",
        ):
            MODULE.validate_inventory(document)

    def test_forbidden_biometric_capability_is_rejected(self) -> None:
        document = self.catalog()
        candidate = self.candidate(document, "aws-rekognition")
        capabilities = candidate["benchmark_capabilities"]
        assert isinstance(capabilities, list)
        capabilities.append("face-recognition")
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "unsupported capabilities|forbidden biometric capability",
        ):
            MODULE.validate_inventory(document)

    def test_duplicate_capability_is_rejected(self) -> None:
        document = self.catalog()
        candidate = self.candidate(document, "opencv-js")
        capabilities = candidate["benchmark_capabilities"]
        assert isinstance(capabilities, list)
        capabilities.append(capabilities[0])
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "must not contain duplicates",
        ):
            MODULE.validate_inventory(document)

    def test_cloud_candidate_cannot_claim_local_target(self) -> None:
        document = self.catalog()
        self.candidate(document, "aws-textract")["deployment_targets"] = [
            "cloud-api",
            "local-only",
        ]
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "cloud target must be exactly cloud-api",
        ):
            MODULE.validate_inventory(document)

    def test_model_name_remains_runtime_configuration(self) -> None:
        document = self.catalog()
        self.candidate(document, "openai-image-input")[
            "model_or_api_binding"
        ] = "pinned-api-version"
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "model name must remain configuration",
        ):
            MODULE.validate_inventory(document)

    def test_vlm_candidate_cannot_invent_embedding_endpoint(self) -> None:
        document = self.catalog()
        candidate = self.candidate(document, "gemini-image-understanding")
        capabilities = candidate["benchmark_capabilities"]
        assert isinstance(capabilities, list)
        capabilities.append("native-visual-embeddings")
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "must not invent a native embedding endpoint",
        ):
            MODULE.validate_inventory(document)

    def test_rekognition_cannot_replace_textract_ocr(self) -> None:
        document = self.catalog()
        candidate = self.candidate(document, "aws-rekognition")
        capabilities = candidate["benchmark_capabilities"]
        assert isinstance(capabilities, list)
        capabilities.append("ocr")
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "must not replace the Textract OCR adapter",
        ):
            MODULE.validate_inventory(document)

    def test_general_vision_cannot_replace_document_layout_api(self) -> None:
        document = self.catalog()
        candidate = self.candidate(document, "azure-ai-vision")
        capabilities = candidate["benchmark_capabilities"]
        assert isinstance(capabilities, list)
        capabilities.append("layout")
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "must not replace Document Intelligence",
        ):
            MODULE.validate_inventory(document)

    def test_evidence_requires_https(self) -> None:
        document = self.catalog()
        self.candidate(document, "burn")["evidence_urls"] = [
            "http://example.invalid/burn"
        ]
        with self.assertRaisesRegex(
            MODULE.InventoryError,
            "evidence URLs must be non-empty HTTPS URLs",
        ):
            MODULE.validate_inventory(document)


if __name__ == "__main__":
    unittest.main(verbosity=2)
