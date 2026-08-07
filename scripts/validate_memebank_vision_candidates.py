#!/usr/bin/env python3
"""Validate the MemeBank OCR/vision candidate inventory.

This validator performs no network calls and reads no credentials. It enforces
candidate identity, provider separation, disposition evidence, and safety
guardrails before the inventory can be used by the DEN-1011 benchmark or the
DEN-1018 adapter implementation.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import re
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(
    "repository-blueprints/memebank/memebank-e2e/benchmarks/vision/"
    "candidates/candidate-inventory.json"
)

EXPECTED_ORDER = (
    "tensorflow-js-core",
    "tensorflow-js-mobilenet",
    "tensorflow-js-coco-ssd",
    "tesseract-js",
    "paddleocr-js",
    "typescript-paddleocr-onnx-wrapper",
    "onnxruntime-web",
    "onnxruntime-node",
    "opencv-js",
    "gocv",
    "gosseract",
    "tfgo",
    "graft-tensorflow-go",
    "onnxruntime-go",
    "candle",
    "burn",
    "oar-ocr",
    "ocrs",
    "ort",
    "opencv-rust",
    "google-cloud-vision",
    "google-document-ai",
    "aws-rekognition",
    "aws-textract",
    "azure-ai-vision",
    "azure-document-intelligence",
    "openai-image-input",
    "gemini-image-understanding",
    "anthropic-claude-vision",
)

EXPECTED_TRACKS = {
    candidate_id: (
        "typescript"
        if index < 9
        else "go"
        if index < 14
        else "rust"
        if index < 20
        else "cloud"
    )
    for index, candidate_id in enumerate(EXPECTED_ORDER)
}

ROOT_KEYS = {
    "schema_version",
    "catalog_id",
    "generated_on",
    "tracking_issues",
    "policy",
    "candidates",
}
POLICY_KEYS = {
    "required_tracks",
    "allowed_dispositions",
    "exact_identity_required_for",
    "production_dependency_requires_disposition",
    "forbidden_capabilities",
    "model_names_are_configuration_for",
}
CANDIDATE_KEYS = {
    "candidate_id",
    "display_name",
    "track",
    "kind",
    "identity",
    "benchmark_capabilities",
    "deployment_targets",
    "model_or_api_binding",
    "disposition",
    "production_dependency",
    "pinned_version",
    "license",
    "decision_record",
    "tracking_issues",
    "evidence_urls",
    "notes",
}
IDENTITY_KEYS = {
    "status",
    "ecosystem",
    "name",
    "upstream",
    "version_policy",
}

ALLOWED_TRACKS = {"typescript", "go", "rust", "cloud"}
ALLOWED_KINDS = {
    "framework",
    "model-package",
    "local-library",
    "local-runtime",
    "upstream-sdk",
    "selection-required",
    "cloud-api",
}
ALLOWED_IDENTITY_STATUSES = {"exact", "selection-required"}
ALLOWED_BINDINGS = {
    "pinned-model-artifacts",
    "pinned-package-only",
    "pinned-api-version",
    "runtime-configured-model",
    "selection-required",
}
ALLOWED_CAPABILITIES = {
    "preprocess",
    "image-classification",
    "scene-labeling",
    "object-detection",
    "regions",
    "ocr",
    "orientation",
    "handwriting",
    "layout",
    "tables",
    "formulas",
    "seals",
    "captioning",
    "visual-question-answering",
    "moderation",
    "native-visual-embeddings",
    "text-embeddings",
    "segmentation",
    "batch",
}
ALLOWED_TARGETS = {
    "browser",
    "node",
    "react-native",
    "wasm",
    "webgl",
    "webgpu",
    "local-only",
    "go-worker",
    "rust-worker",
    "linux-server",
    "macos-desktop",
    "windows-desktop",
    "arm64",
    "gpu",
    "cloud-api",
}
EXPECTED_MODEL_CONFIGURED = {
    "openai-image-input",
    "gemini-image-understanding",
    "anthropic-claude-vision",
}
EXPECTED_SELECTION_REQUIRED = {"typescript-paddleocr-onnx-wrapper"}
CRITICAL_PROVIDER_PAIRS = (
    {"google-cloud-vision", "google-document-ai"},
    {"aws-rekognition", "aws-textract"},
    {"azure-ai-vision", "azure-document-intelligence"},
)


class InventoryError(ValueError):
    """Raised when the inventory violates a checked contract."""


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=reject_duplicate_keys)
    except OSError as error:
        raise InventoryError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise InventoryError(f"invalid JSON in {path}: {error}") from error


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InventoryError(f"{label} must be an object")
    return value


def require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise InventoryError(f"{label} keys changed: missing={missing}, extra={extra}")


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InventoryError(f"{label} must be a non-empty string")
    return value.strip()


def require_nullable_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return require_string(value, label)


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise InventoryError(f"{label} must be a boolean")
    return value


def unique_strings(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list):
        raise InventoryError(f"{label} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or (nonempty and not item.strip()):
            raise InventoryError(f"{label}[{index}] must be a string")
        result.append(item.strip())
    if len(result) != len(set(result)):
        raise InventoryError(f"{label} must not contain duplicates")
    return result


def validate_policy(document: Any) -> dict[str, Any]:
    policy = require_object(document, "policy")
    require_exact_keys(policy, POLICY_KEYS, "policy")

    if unique_strings(policy["required_tracks"], "policy.required_tracks") != [
        "typescript",
        "go",
        "rust",
        "cloud",
    ]:
        raise InventoryError("required track order changed")

    dispositions = unique_strings(
        policy["allowed_dispositions"], "policy.allowed_dispositions"
    )
    if dispositions != ["benchmark", "pilot", "adopt", "defer", "reject"]:
        raise InventoryError("allowed dispositions changed")

    if unique_strings(
        policy["exact_identity_required_for"],
        "policy.exact_identity_required_for",
    ) != ["pilot", "adopt"]:
        raise InventoryError("exact-identity transition policy changed")

    if policy["production_dependency_requires_disposition"] != "adopt":
        raise InventoryError("production dependency policy must require adopt")

    forbidden = unique_strings(
        policy["forbidden_capabilities"], "policy.forbidden_capabilities"
    )
    if set(forbidden) != {"face-recognition", "biometric-identification"}:
        raise InventoryError("biometric capability guardrail changed")

    configured = unique_strings(
        policy["model_names_are_configuration_for"],
        "policy.model_names_are_configuration_for",
    )
    if set(configured) != EXPECTED_MODEL_CONFIGURED:
        raise InventoryError("runtime-configured model set changed")

    return policy


def validate_identity(
    candidate_id: str,
    document: Any,
    binding: str,
) -> dict[str, Any]:
    identity = require_object(document, f"{candidate_id}.identity")
    require_exact_keys(identity, IDENTITY_KEYS, f"{candidate_id}.identity")

    status = require_string(identity["status"], f"{candidate_id}.identity.status")
    if status not in ALLOWED_IDENTITY_STATUSES:
        raise InventoryError(f"unsupported identity status for {candidate_id}: {status}")

    ecosystem = require_string(
        identity["ecosystem"], f"{candidate_id}.identity.ecosystem"
    )
    name = require_nullable_string(identity["name"], f"{candidate_id}.identity.name")
    upstream = require_string(
        identity["upstream"], f"{candidate_id}.identity.upstream"
    )
    if not upstream.startswith("https://"):
        raise InventoryError(f"{candidate_id} upstream must use https")
    require_string(
        identity["version_policy"], f"{candidate_id}.identity.version_policy"
    )

    if status == "exact" and name is None:
        raise InventoryError(f"{candidate_id} exact identity requires a name")
    if status == "selection-required":
        if name is not None:
            raise InventoryError(
                f"{candidate_id} selection-required identity must not name a package"
            )
        if binding != "selection-required":
            raise InventoryError(
                f"{candidate_id} selection-required identity needs matching binding"
            )
    elif binding == "selection-required":
        raise InventoryError(
            f"{candidate_id} exact identity cannot use selection-required binding"
        )

    if not ecosystem:
        raise InventoryError(f"{candidate_id} ecosystem is empty")
    return identity


def validate_candidate(
    raw: Any,
    index: int,
    policy: dict[str, Any],
) -> dict[str, Any]:
    candidate = require_object(raw, f"candidates[{index}]")
    candidate_id = require_string(
        candidate.get("candidate_id"), f"candidates[{index}].candidate_id"
    )
    require_exact_keys(candidate, CANDIDATE_KEYS, candidate_id)

    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", candidate_id):
        raise InventoryError(f"unsupported candidate_id {candidate_id!r}")
    if candidate_id not in EXPECTED_TRACKS:
        raise InventoryError(f"unexpected candidate {candidate_id}")

    require_string(candidate["display_name"], f"{candidate_id}.display_name")

    track = require_string(candidate["track"], f"{candidate_id}.track")
    if track not in ALLOWED_TRACKS:
        raise InventoryError(f"unsupported track for {candidate_id}: {track}")
    if track != EXPECTED_TRACKS[candidate_id]:
        raise InventoryError(
            f"{candidate_id} moved from {EXPECTED_TRACKS[candidate_id]} to {track}"
        )

    kind = require_string(candidate["kind"], f"{candidate_id}.kind")
    if kind not in ALLOWED_KINDS:
        raise InventoryError(f"unsupported kind for {candidate_id}: {kind}")
    if track == "cloud" and kind != "cloud-api":
        raise InventoryError(f"{candidate_id} cloud candidates must be cloud-api")

    binding = require_string(
        candidate["model_or_api_binding"], f"{candidate_id}.model_or_api_binding"
    )
    if binding not in ALLOWED_BINDINGS:
        raise InventoryError(f"unsupported binding for {candidate_id}: {binding}")

    identity = validate_identity(candidate_id, candidate["identity"], binding)

    capabilities = unique_strings(
        candidate["benchmark_capabilities"],
        f"{candidate_id}.benchmark_capabilities",
    )
    if not capabilities:
        raise InventoryError(f"{candidate_id} must benchmark at least one capability")
    unknown_capabilities = sorted(set(capabilities) - ALLOWED_CAPABILITIES)
    if unknown_capabilities:
        raise InventoryError(
            f"{candidate_id} has unsupported capabilities {unknown_capabilities}"
        )
    forbidden = set(policy["forbidden_capabilities"])
    if forbidden.intersection(capabilities):
        raise InventoryError(f"{candidate_id} includes forbidden biometric capability")

    targets = unique_strings(
        candidate["deployment_targets"], f"{candidate_id}.deployment_targets"
    )
    if not targets:
        raise InventoryError(f"{candidate_id} must declare deployment targets")
    unknown_targets = sorted(set(targets) - ALLOWED_TARGETS)
    if unknown_targets:
        raise InventoryError(
            f"{candidate_id} has unsupported deployment targets {unknown_targets}"
        )
    if track == "cloud" and targets != ["cloud-api"]:
        raise InventoryError(f"{candidate_id} cloud target must be exactly cloud-api")
    if track != "cloud" and "cloud-api" in targets:
        raise InventoryError(f"{candidate_id} local candidate cannot target cloud-api")

    disposition = require_string(
        candidate["disposition"], f"{candidate_id}.disposition"
    )
    if disposition not in policy["allowed_dispositions"]:
        raise InventoryError(f"unsupported disposition for {candidate_id}: {disposition}")

    production_dependency = require_bool(
        candidate["production_dependency"],
        f"{candidate_id}.production_dependency",
    )
    pinned_version = require_nullable_string(
        candidate["pinned_version"], f"{candidate_id}.pinned_version"
    )
    license_value = require_nullable_string(
        candidate["license"], f"{candidate_id}.license"
    )
    decision_record = require_nullable_string(
        candidate["decision_record"], f"{candidate_id}.decision_record"
    )

    if disposition in policy["exact_identity_required_for"]:
        if identity["status"] != "exact":
            raise InventoryError(
                f"{candidate_id} {disposition} requires an exact identity"
            )
        if not pinned_version or not license_value or not decision_record:
            raise InventoryError(
                f"{candidate_id} {disposition} requires pinned_version, license, and decision_record"
            )
    if disposition in {"defer", "reject"} and not decision_record:
        raise InventoryError(
            f"{candidate_id} {disposition} requires a decision_record"
        )
    if production_dependency and disposition != policy[
        "production_dependency_requires_disposition"
    ]:
        raise InventoryError(
            f"{candidate_id} production dependency requires adopt disposition"
        )
    if disposition != "adopt" and production_dependency:
        raise InventoryError(f"{candidate_id} non-adopted dependency cannot be production")
    if disposition == "benchmark" and production_dependency:
        raise InventoryError(f"{candidate_id} benchmark cannot be a production dependency")

    issues = unique_strings(
        candidate["tracking_issues"], f"{candidate_id}.tracking_issues"
    )
    if not set(issues).issubset({"DEN-1011", "DEN-1018"}) or "DEN-1011" not in issues:
        raise InventoryError(
            f"{candidate_id} must be benchmark-tracked by DEN-1011"
        )

    evidence = unique_strings(
        candidate["evidence_urls"], f"{candidate_id}.evidence_urls"
    )
    if not evidence or any(not url.startswith("https://") for url in evidence):
        raise InventoryError(f"{candidate_id} evidence URLs must be non-empty HTTPS URLs")

    notes = unique_strings(candidate["notes"], f"{candidate_id}.notes")
    if not notes:
        raise InventoryError(f"{candidate_id} must record at least one caveat")

    if candidate_id in EXPECTED_MODEL_CONFIGURED:
        if binding != "runtime-configured-model":
            raise InventoryError(f"{candidate_id} model name must remain configuration")
        if "native-visual-embeddings" in capabilities or "text-embeddings" in capabilities:
            raise InventoryError(
                f"{candidate_id} must not invent a native embedding endpoint"
            )
    elif binding == "runtime-configured-model":
        raise InventoryError(
            f"{candidate_id} is not approved for runtime-configured model binding"
        )

    if candidate_id in EXPECTED_SELECTION_REQUIRED:
        if identity["status"] != "selection-required":
            raise InventoryError(f"{candidate_id} must remain selection-required")
    elif identity["status"] == "selection-required":
        raise InventoryError(
            f"{candidate_id} unexpectedly lacks an exact package/API identity"
        )

    return candidate


def validate_inventory(document: Any) -> dict[str, Any]:
    root = require_object(document, "catalog")
    require_exact_keys(root, ROOT_KEYS, "catalog")

    if root["schema_version"] != 1:
        raise InventoryError("schema_version must equal 1")
    if root["catalog_id"] != "memebank-vision-candidates":
        raise InventoryError("catalog_id changed")
    generated_on = require_string(root["generated_on"], "generated_on")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", generated_on):
        raise InventoryError("generated_on must use YYYY-MM-DD")

    if unique_strings(root["tracking_issues"], "tracking_issues") != [
        "DEN-1011",
        "DEN-1018",
    ]:
        raise InventoryError("catalog tracking issues changed")

    policy = validate_policy(root["policy"])
    raw_candidates = root["candidates"]
    if not isinstance(raw_candidates, list):
        raise InventoryError("candidates must be an array")

    candidates = [
        validate_candidate(raw_candidate, index, policy)
        for index, raw_candidate in enumerate(raw_candidates)
    ]
    candidate_ids = [candidate["candidate_id"] for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise InventoryError("candidate_id values must be unique")
    if candidate_ids != list(EXPECTED_ORDER):
        missing = sorted(set(EXPECTED_ORDER) - set(candidate_ids))
        extra = sorted(set(candidate_ids) - set(EXPECTED_ORDER))
        raise InventoryError(
            f"candidate order or inventory changed: missing={missing}, extra={extra}"
        )

    tracks = Counter(candidate["track"] for candidate in candidates)
    if set(tracks) != set(policy["required_tracks"]):
        raise InventoryError(f"required track coverage changed: {dict(tracks)}")

    candidate_map = {candidate["candidate_id"]: candidate for candidate in candidates}
    for pair in CRITICAL_PROVIDER_PAIRS:
        if not pair.issubset(candidate_map):
            raise InventoryError(f"critical provider separation missing: {sorted(pair)}")

    if "ocr" in candidate_map["aws-rekognition"]["benchmark_capabilities"]:
        raise InventoryError("AWS Rekognition must not replace the Textract OCR adapter")
    if "ocr" not in candidate_map["aws-textract"]["benchmark_capabilities"]:
        raise InventoryError("AWS Textract must remain the AWS OCR candidate")
    if "layout" in candidate_map["azure-ai-vision"]["benchmark_capabilities"]:
        raise InventoryError(
            "Azure AI Vision must not replace Document Intelligence layout evaluation"
        )
    if "layout" not in candidate_map["azure-document-intelligence"][
        "benchmark_capabilities"
    ]:
        raise InventoryError("Azure Document Intelligence must evaluate layout")
    if "layout" in candidate_map["google-cloud-vision"]["benchmark_capabilities"]:
        raise InventoryError(
            "Cloud Vision must not replace Document AI layout evaluation"
        )
    if "layout" not in candidate_map["google-document-ai"][
        "benchmark_capabilities"
    ]:
        raise InventoryError("Google Document AI must evaluate layout")

    return {
        "catalog_id": root["catalog_id"],
        "candidate_count": len(candidates),
        "track_counts": dict(sorted(tracks.items())),
        "selection_required": sorted(
            candidate["candidate_id"]
            for candidate in candidates
            if candidate["identity"]["status"] == "selection-required"
        ),
        "production_dependencies": sorted(
            candidate["candidate_id"]
            for candidate in candidates
            if candidate["production_dependency"]
        ),
    }


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="override candidate inventory path",
    )
    args = parser.parse_args(argv)

    root = repository_root()
    path = args.catalog.resolve() if args.catalog else root / CATALOG_PATH
    summary = validate_inventory(load_json(path))
    print(
        "PASS "
        f"catalog={summary['catalog_id']} "
        f"candidates={summary['candidate_count']} "
        f"tracks={summary['track_counts']} "
        f"selection_required={summary['selection_required']} "
        f"production_dependencies={summary['production_dependencies']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
