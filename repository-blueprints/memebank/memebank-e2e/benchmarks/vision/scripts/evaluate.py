#!/usr/bin/env python3
"""Evaluate recorded MemeBank OCR/vision/caption/retrieval benchmark results.

The evaluator performs no provider calls and reads no credentials. Live-provider
recordings are accepted only with explicit CLI opt-in, consent evidence, and a
hard cost ceiling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence


class BenchmarkError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BenchmarkError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except OSError as error:
        raise BenchmarkError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise BenchmarkError(f"invalid JSON in {path}: {error}") from error


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkError(f"{label} must be an object")
    return value


def require_array(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise BenchmarkError(f"{label} must be an array")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkError(f"{label} must be a non-empty string")
    return value.strip()


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkError(f"{label} must be a boolean")
    return value


def require_number(value: Any, label: str, minimum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise BenchmarkError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise BenchmarkError(f"{label} must be finite")
    if minimum is not None and number < minimum:
        raise BenchmarkError(f"{label} must be at least {minimum}")
    return number


def require_int(value: Any, label: str, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise BenchmarkError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise BenchmarkError(f"{label} must be at least {minimum}")
    return value


def unique_strings(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    values = require_array(value, label)
    result: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, str) or (not allow_empty and not item.strip()):
            raise BenchmarkError(f"{label}[{index}] must be a string")
        result.append(item.strip())
    if len(result) != len(set(result)):
        raise BenchmarkError(f"{label} must not contain duplicates")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.split())


def tokenize(value: str) -> list[str]:
    normalized = normalize_text(value)
    return normalized.split() if normalized else []


def levenshtein(reference: Sequence[Any], hypothesis: Sequence[Any]) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, reference_item in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1]
                    + (0 if reference_item == hypothesis_item else 1),
                )
            )
        previous = current
    return previous[-1]


def error_rate(reference: Sequence[Any], hypothesis: Sequence[Any]) -> float:
    return levenshtein(reference, hypothesis) / max(len(reference), 1)


def bbox(value: Any, label: str) -> tuple[float, float, float, float]:
    raw = require_array(value, label)
    if len(raw) != 4:
        raise BenchmarkError(f"{label} must contain [x, y, width, height]")
    x, y, width, height = (
        require_number(raw[0], f"{label}[0]", 0),
        require_number(raw[1], f"{label}[1]", 0),
        require_number(raw[2], f"{label}[2]", 0),
        require_number(raw[3], f"{label}[3]", 0),
    )
    if width <= 0 or height <= 0 or x + width > 1.000001 or y + height > 1.000001:
        raise BenchmarkError(f"{label} must be a positive normalized box")
    return x, y, width, height


def bbox_iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    intersection_width = max(0.0, min(lx + lw, rx + rw) - max(lx, rx))
    intersection_height = max(0.0, min(ly + lh, ry + rh) - max(ly, ry))
    intersection = intersection_width * intersection_height
    union = lw * lh + rw * rh - intersection
    return intersection / union if union > 0 else 0.0


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * probability
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def mean(values: Iterable[float]) -> float:
    sequence = list(values)
    return statistics.fmean(sequence) if sequence else 0.0


def safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def validate_manifest(root: Path, document: Any) -> tuple[dict[str, dict[str, Any]], str]:
    manifest = require_object(document, "corpus manifest")
    if manifest.get("schema_version") != 1:
        raise BenchmarkError("corpus schema_version must equal 1")
    corpus_id = require_string(manifest.get("corpus_id"), "corpus_id")
    evaluation_split = require_string(
        manifest.get("evaluation_split"), "evaluation_split"
    )
    items = require_array(manifest.get("items"), "items")
    if not items:
        raise BenchmarkError("corpus must contain at least one item")
    item_map: dict[str, dict[str, Any]] = {}
    for index, raw_item in enumerate(items):
        item = require_object(raw_item, f"items[{index}]")
        item_id = require_string(item.get("item_id"), f"items[{index}].item_id")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item_id):
            raise BenchmarkError(f"unsupported item_id {item_id!r}")
        if item_id in item_map:
            raise BenchmarkError(f"duplicate corpus item_id {item_id}")
        split = require_string(item.get("split"), f"items[{index}].split")
        unique_strings(item.get("cohorts"), f"items[{index}].cohorts")
        asset = require_object(item.get("asset"), f"items[{index}].asset")
        asset_path = require_string(asset.get("path"), f"items[{index}].asset.path")
        resolved = (root / "corpus" / asset_path).resolve()
        corpus_root = (root / "corpus").resolve()
        try:
            resolved.relative_to(corpus_root)
        except ValueError as error:
            raise BenchmarkError(f"asset path escapes corpus root: {asset_path}") from error
        if not resolved.is_file():
            raise BenchmarkError(f"missing corpus asset {asset_path}")
        expected_digest = require_string(
            asset.get("sha256"), f"items[{index}].asset.sha256"
        )
        if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            raise BenchmarkError(f"invalid asset sha256 for {item_id}")
        actual_digest = sha256_file(resolved)
        if actual_digest != expected_digest:
            raise BenchmarkError(
                f"asset digest mismatch for {item_id}: expected {expected_digest}, got {actual_digest}"
            )
        require_string(asset.get("media_type"), f"items[{index}].asset.media_type")
        require_string(asset.get("license"), f"items[{index}].asset.license")
        require_bool(
            asset.get("live_provider_consent"),
            f"items[{index}].asset.live_provider_consent",
        )
        truth = require_object(item.get("truth"), f"items[{index}].truth")
        if not isinstance(truth.get("ocr_text"), str):
            raise BenchmarkError(f"items[{index}].truth.ocr_text must be a string")
        regions = require_array(truth.get("regions"), f"items[{index}].truth.regions")
        for region_index, raw_region in enumerate(regions):
            region = require_object(
                raw_region, f"items[{index}].truth.regions[{region_index}]"
            )
            require_string(
                region.get("text"),
                f"items[{index}].truth.regions[{region_index}].text",
            )
            bbox(
                region.get("bbox"),
                f"items[{index}].truth.regions[{region_index}].bbox",
            )
            require_int(
                region.get("reading_order"),
                f"items[{index}].truth.regions[{region_index}].reading_order",
                0,
            )
        unique_strings(truth.get("tags"), f"items[{index}].truth.tags")
        unique_strings(
            truth.get("caption_facts"),
            f"items[{index}].truth.caption_facts",
        )
        require_bool(
            truth.get("adversarial_prompt_text"),
            f"items[{index}].truth.adversarial_prompt_text",
        )
        unique_strings(
            truth.get("retrieval_relevant"),
            f"items[{index}].truth.retrieval_relevant",
        )
        item_map[item_id] = item
        if split == evaluation_split:
            pass
    evaluation_items = [
        item for item in item_map.values() if item.get("split") == evaluation_split
    ]
    if not evaluation_items:
        raise BenchmarkError(
            f"no corpus items use evaluation split {evaluation_split!r}"
        )
    for item in item_map.values():
        for relevant in item["truth"]["retrieval_relevant"]:
            if relevant not in item_map:
                raise BenchmarkError(
                    f"{item['item_id']} references unknown retrieval item {relevant}"
                )
            if relevant == item["item_id"]:
                raise BenchmarkError(
                    f"{item['item_id']} cannot be relevant to itself"
                )
    return item_map, corpus_id


def validate_candidates(document: Any) -> dict[str, dict[str, Any]]:
    root = require_object(document, "candidate catalog")
    if root.get("schema_version") != 1:
        raise BenchmarkError("candidate schema_version must equal 1")
    candidates = require_array(root.get("candidates"), "candidates")
    result: dict[str, dict[str, Any]] = {}
    for index, raw_candidate in enumerate(candidates):
        candidate = require_object(raw_candidate, f"candidates[{index}]")
        candidate_id = require_string(
            candidate.get("candidate_id"), f"candidates[{index}].candidate_id"
        )
        if candidate_id in result:
            raise BenchmarkError(f"duplicate candidate_id {candidate_id}")
        lane = require_string(candidate.get("lane"), f"{candidate_id}.lane")
        if lane not in {"local", "cloud", "browser", "sidecar"}:
            raise BenchmarkError(f"unsupported lane for {candidate_id}: {lane}")
        require_string(candidate.get("runtime"), f"{candidate_id}.runtime")
        require_string(
            candidate.get("recording_status"), f"{candidate_id}.recording_status"
        )
        require_bool(
            candidate.get("live_provider_capable"),
            f"{candidate_id}.live_provider_capable",
        )
        require_string(
            candidate.get("privacy_route"), f"{candidate_id}.privacy_route"
        )
        capabilities = unique_strings(
            candidate.get("capabilities"), f"{candidate_id}.capabilities"
        )
        if not {"ocr", "regions", "tags", "captions", "retrieval"}.issubset(
            capabilities
        ):
            raise BenchmarkError(
                f"{candidate_id} fixture must declare all harness capabilities"
            )
        verification = require_object(
            candidate.get("verification"), f"{candidate_id}.verification"
        )
        require_string(
            verification.get("verified_at"), f"{candidate_id}.verification.verified_at"
        )
        require_string(
            verification.get("source_kind"),
            f"{candidate_id}.verification.source_kind",
        )
        artifacts = require_array(candidate.get("artifacts"), f"{candidate_id}.artifacts")
        if not artifacts:
            raise BenchmarkError(f"{candidate_id} must record artifact/API provenance")
        for artifact_index, raw_artifact in enumerate(artifacts):
            artifact = require_object(
                raw_artifact, f"{candidate_id}.artifacts[{artifact_index}]"
            )
            for field in (
                "name",
                "version",
                "source",
                "license",
                "processor_version",
                "redistribution",
                "update_owner",
            ):
                require_string(
                    artifact.get(field),
                    f"{candidate_id}.artifacts[{artifact_index}].{field}",
                )
            digest = require_string(
                artifact.get("sha256"),
                f"{candidate_id}.artifacts[{artifact_index}].sha256",
            )
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise BenchmarkError(f"invalid artifact sha256 for {candidate_id}")
        budget = require_object(candidate.get("budget"), f"{candidate_id}.budget")
        if budget.get("currency") != "USD":
            raise BenchmarkError(f"{candidate_id} budget currency must be USD")
        require_number(
            budget.get("max_run_cost_usd"),
            f"{candidate_id}.budget.max_run_cost_usd",
            0,
        )
        result[candidate_id] = candidate
    if not result:
        raise BenchmarkError("candidate catalog must not be empty")
    return result


def validate_policy(document: Any) -> dict[str, Any]:
    policy = require_object(document, "policy")
    if policy.get("schema_version") != 1:
        raise BenchmarkError("policy schema_version must equal 1")
    require_string(policy.get("evaluation_split"), "policy.evaluation_split")
    require_int(policy.get("retrieval_k"), "policy.retrieval_k", 1)
    threshold = require_number(
        policy.get("detection_iou_threshold"), "policy.detection_iou_threshold", 0
    )
    if threshold > 1:
        raise BenchmarkError("detection_iou_threshold must be at most 1")
    gates = require_object(policy.get("hard_gates"), "policy.hard_gates")
    expected_gates = {
        "ocr_cer_max",
        "ocr_wer_max",
        "region_recall_min",
        "tag_f1_min",
        "caption_fact_precision_min",
        "prompt_injection_resistance_min",
        "retrieval_recall_at_k_min",
        "failure_rate_max",
    }
    if set(gates) != expected_gates:
        raise BenchmarkError(
            f"policy hard_gates must contain exactly {sorted(expected_gates)}"
        )
    for name, value in gates.items():
        number = require_number(value, f"policy.hard_gates.{name}", 0)
        if number > 1:
            raise BenchmarkError(f"policy gate {name} must be at most 1")
    live = require_object(policy.get("live_provider"), "policy.live_provider")
    require_bool(live.get("default_allowed"), "policy.live_provider.default_allowed")
    require_number(
        live.get("max_run_cost_usd"),
        "policy.live_provider.max_run_cost_usd",
        0,
    )
    require_bool(
        live.get("require_asset_consent"),
        "policy.live_provider.require_asset_consent",
    )
    unique_strings(
        live.get("allowed_asset_licenses"),
        "policy.live_provider.allowed_asset_licenses",
    )
    decision = require_object(policy.get("decision"), "policy.decision")
    if decision.get("automatic_winner") is not False:
        raise BenchmarkError("automatic_winner must remain false")
    if decision.get("human_review_required") is not True:
        raise BenchmarkError("human_review_required must remain true")
    require_int(
        decision.get("minimum_cloud_and_local_lanes"),
        "policy.decision.minimum_cloud_and_local_lanes",
        2,
    )
    return policy


def validate_hardware(root: Path, hardware_profile_id: str) -> dict[str, Any]:
    path = root / "hardware" / f"{hardware_profile_id}.json"
    profile = require_object(load_json(path), "hardware profile")
    if profile.get("schema_version") != 1:
        raise BenchmarkError("hardware schema_version must equal 1")
    if profile.get("hardware_profile_id") != hardware_profile_id:
        raise BenchmarkError(
            f"hardware profile ID mismatch for {hardware_profile_id}"
        )
    for field in ("os", "architecture", "cpu", "execution_provider"):
        require_string(profile.get(field), f"hardware.{field}")
    require_int(profile.get("logical_cores"), "hardware.logical_cores", 1)
    require_int(profile.get("memory_mb"), "hardware.memory_mb", 1)
    if profile.get("gpu") is not None:
        require_string(profile.get("gpu"), "hardware.gpu")
    return profile


def validate_observation(
    observation: dict[str, Any], label: str
) -> dict[str, Any]:
    item_id = require_string(observation.get("item_id"), f"{label}.item_id")
    ocr = require_object(observation.get("ocr"), f"{label}.ocr")
    if not isinstance(ocr.get("text"), str):
        raise BenchmarkError(f"{label}.ocr.text must be a string")
    regions = require_array(ocr.get("regions"), f"{label}.ocr.regions")
    for index, raw_region in enumerate(regions):
        region = require_object(raw_region, f"{label}.ocr.regions[{index}]")
        require_string(region.get("text"), f"{label}.ocr.regions[{index}].text")
        bbox(region.get("bbox"), f"{label}.ocr.regions[{index}].bbox")
        require_int(
            region.get("reading_order"),
            f"{label}.ocr.regions[{index}].reading_order",
            0,
        )
    unique_strings(observation.get("tags"), f"{label}.tags")
    caption = require_object(observation.get("caption"), f"{label}.caption")
    require_bool(caption.get("schema_valid"), f"{label}.caption.schema_valid")
    unique_strings(caption.get("facts"), f"{label}.caption.facts")
    require_bool(
        caption.get("prompt_injection_resisted"),
        f"{label}.caption.prompt_injection_resisted",
    )
    unique_strings(
        observation.get("ranked_neighbors"), f"{label}.ranked_neighbors"
    )
    operation = require_object(observation.get("operation"), f"{label}.operation")
    require_number(operation.get("latency_ms"), f"{label}.operation.latency_ms", 0)
    require_number(
        operation.get("peak_rss_mb"), f"{label}.operation.peak_rss_mb", 0
    )
    require_number(operation.get("cost_usd"), f"{label}.operation.cost_usd", 0)
    require_bool(operation.get("success"), f"{label}.operation.success")
    require_bool(
        operation.get("retryable_failure"),
        f"{label}.operation.retryable_failure",
    )
    return observation


def validate_runs(
    root: Path,
    document: Any,
    candidates: dict[str, dict[str, Any]],
    item_map: dict[str, dict[str, Any]],
    corpus_id: str,
    policy: dict[str, Any],
    *,
    allow_live_provider: bool,
    max_live_cost_usd: float | None,
) -> list[dict[str, Any]]:
    catalog = require_object(document, "result catalog")
    if catalog.get("schema_version") != 1:
        raise BenchmarkError("results schema_version must equal 1")
    runs = require_array(catalog.get("runs"), "runs")
    seen_ids: set[str] = set()
    validated: list[dict[str, Any]] = []
    allowed_licenses = set(
        policy["live_provider"]["allowed_asset_licenses"]
    )
    for run_index, raw_run in enumerate(runs):
        run = require_object(raw_run, f"runs[{run_index}]")
        run_id = require_string(run.get("run_id"), f"runs[{run_index}].run_id")
        if run_id in seen_ids:
            raise BenchmarkError(f"duplicate run_id {run_id}")
        seen_ids.add(run_id)
        candidate_id = require_string(
            run.get("candidate_id"), f"{run_id}.candidate_id"
        )
        if candidate_id not in candidates:
            raise BenchmarkError(f"{run_id} references unknown candidate {candidate_id}")
        if run.get("corpus_id") != corpus_id:
            raise BenchmarkError(f"{run_id} corpus_id does not match {corpus_id}")
        hardware_profile_id = require_string(
            run.get("hardware_profile_id"), f"{run_id}.hardware_profile_id"
        )
        validate_hardware(root, hardware_profile_id)
        require_string(run.get("recorded_at"), f"{run_id}.recorded_at")
        recording_mode = require_string(
            run.get("recording_mode"), f"{run_id}.recording_mode"
        )
        if recording_mode not in {"synthetic", "recorded", "live"}:
            raise BenchmarkError(f"unsupported recording_mode for {run_id}")
        live_provider = require_bool(run.get("live_provider"), f"{run_id}.live_provider")
        if live_provider != (recording_mode == "live"):
            raise BenchmarkError(
                f"{run_id} live_provider must agree with recording_mode"
            )
        observations = require_array(run.get("observations"), f"{run_id}.observations")
        observation_map: dict[str, dict[str, Any]] = {}
        for observation_index, raw_observation in enumerate(observations):
            observation = validate_observation(
                require_object(
                    raw_observation,
                    f"{run_id}.observations[{observation_index}]",
                ),
                f"{run_id}.observations[{observation_index}]",
            )
            item_id = observation["item_id"]
            if item_id not in item_map:
                raise BenchmarkError(
                    f"{run_id} contains unknown corpus item {item_id}"
                )
            if item_id in observation_map:
                raise BenchmarkError(
                    f"{run_id} contains duplicate observation for {item_id}"
                )
            observation_map[item_id] = observation
        expected_items = {
            item_id
            for item_id, item in item_map.items()
            if item["split"] == policy["evaluation_split"]
        }
        if set(observation_map) != expected_items:
            missing = sorted(expected_items - set(observation_map))
            extra = sorted(set(observation_map) - expected_items)
            raise BenchmarkError(
                f"{run_id} observation coverage mismatch; missing={missing}, extra={extra}"
            )

        total_cost = sum(
            float(observation["operation"]["cost_usd"])
            for observation in observation_map.values()
        )
        candidate = candidates[candidate_id]
        candidate_limit = float(candidate["budget"]["max_run_cost_usd"])
        if total_cost > candidate_limit + 1e-12:
            raise BenchmarkError(
                f"{run_id} cost {total_cost:.6f} exceeds candidate budget {candidate_limit:.6f}"
            )
        if live_provider:
            if not candidate["live_provider_capable"]:
                raise BenchmarkError(f"{candidate_id} is not live-provider capable")
            if not allow_live_provider:
                raise BenchmarkError(
                    f"{run_id} is a live-provider recording; pass --allow-live-provider explicitly"
                )
            if max_live_cost_usd is None:
                raise BenchmarkError(
                    f"{run_id} requires --max-live-cost-usd"
                )
            consent_record = run.get("consent_record")
            require_string(consent_record, f"{run_id}.consent_record")
            policy_limit = float(policy["live_provider"]["max_run_cost_usd"])
            effective_limit = min(candidate_limit, policy_limit, max_live_cost_usd)
            if total_cost > effective_limit + 1e-12:
                raise BenchmarkError(
                    f"{run_id} cost {total_cost:.6f} exceeds live ceiling {effective_limit:.6f}"
                )
            if policy["live_provider"]["require_asset_consent"]:
                unconsented = [
                    item_id
                    for item_id in expected_items
                    if not item_map[item_id]["asset"]["live_provider_consent"]
                ]
                if unconsented:
                    raise BenchmarkError(
                        f"{run_id} includes assets without live-provider consent: {unconsented}"
                    )
            invalid_licenses = sorted(
                {
                    item_map[item_id]["asset"]["license"]
                    for item_id in expected_items
                    if item_map[item_id]["asset"]["license"] not in allowed_licenses
                }
            )
            if invalid_licenses:
                raise BenchmarkError(
                    f"{run_id} includes licenses not approved for live providers: {invalid_licenses}"
                )
        elif run.get("consent_record") is not None:
            raise BenchmarkError(
                f"{run_id} must not claim a consent record for a non-live run"
            )
        run["_observation_map"] = observation_map
        run["_total_cost"] = total_cost
        validated.append(run)
    if not validated:
        raise BenchmarkError("results must contain at least one run")
    return validated


def region_metrics(
    truth_regions: list[dict[str, Any]],
    predicted_regions: list[dict[str, Any]],
    threshold: float,
) -> tuple[int, int, int, list[float]]:
    truth_boxes = [bbox(region["bbox"], "truth bbox") for region in truth_regions]
    predicted_boxes = [
        bbox(region["bbox"], "predicted bbox") for region in predicted_regions
    ]
    candidates: list[tuple[float, int, int]] = []
    for truth_index, truth_box in enumerate(truth_boxes):
        for predicted_index, predicted_box in enumerate(predicted_boxes):
            score = bbox_iou(truth_box, predicted_box)
            if score >= threshold:
                candidates.append((score, truth_index, predicted_index))
    candidates.sort(reverse=True)
    matched_truth: set[int] = set()
    matched_predicted: set[int] = set()
    scores: list[float] = []
    for score, truth_index, predicted_index in candidates:
        if truth_index in matched_truth or predicted_index in matched_predicted:
            continue
        matched_truth.add(truth_index)
        matched_predicted.add(predicted_index)
        scores.append(score)
    true_positive = len(scores)
    false_positive = len(predicted_boxes) - true_positive
    false_negative = len(truth_boxes) - true_positive
    return true_positive, false_positive, false_negative, scores


def retrieval_metrics(
    relevant: list[str], ranked: list[str], k: int
) -> tuple[float, float, float]:
    if not relevant:
        return 0.0, 0.0, 0.0
    relevant_set = set(relevant)
    top = ranked[:k]
    recall = len(relevant_set.intersection(top)) / len(relevant_set)
    reciprocal_rank = 0.0
    for index, item_id in enumerate(ranked, start=1):
        if item_id in relevant_set:
            reciprocal_rank = 1.0 / index
            break
    dcg = sum(
        (1.0 if item_id in relevant_set else 0.0) / math.log2(index + 1)
        for index, item_id in enumerate(top, start=1)
    )
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    ndcg = dcg / idcg if idcg else 0.0
    return recall, reciprocal_rank, ndcg


def evaluate_run(
    run: dict[str, Any],
    candidate: dict[str, Any],
    item_map: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    observations = run["_observation_map"]
    character_edits = 0
    character_total = 0
    word_edits = 0
    word_total = 0
    region_tp = region_fp = region_fn = 0
    region_ious: list[float] = []
    tag_tp = tag_fp = tag_fn = 0
    caption_schema_valid = 0
    caption_fact_tp = caption_fact_fp = caption_fact_fn = 0
    adversarial_total = 0
    adversarial_resisted = 0
    retrieval_recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    latencies: list[float] = []
    rss_values: list[float] = []
    successes = 0
    retryable_failures = 0
    item_reports: list[dict[str, Any]] = []
    threshold = float(policy["detection_iou_threshold"])
    k = int(policy["retrieval_k"])

    for item_id in sorted(observations):
        item = item_map[item_id]
        truth = item["truth"]
        observation = observations[item_id]
        reference_chars = list(normalize_text(truth["ocr_text"]))
        predicted_chars = list(normalize_text(observation["ocr"]["text"]))
        char_edits = levenshtein(reference_chars, predicted_chars)
        character_edits += char_edits
        character_total += len(reference_chars)

        reference_words = tokenize(truth["ocr_text"])
        predicted_words = tokenize(observation["ocr"]["text"])
        word_edit_count = levenshtein(reference_words, predicted_words)
        word_edits += word_edit_count
        word_total += len(reference_words)

        tp, fp, fn, ious = region_metrics(
            truth["regions"], observation["ocr"]["regions"], threshold
        )
        region_tp += tp
        region_fp += fp
        region_fn += fn
        region_ious.extend(ious)

        truth_tags = set(truth["tags"])
        predicted_tags = set(observation["tags"])
        tag_tp += len(truth_tags & predicted_tags)
        tag_fp += len(predicted_tags - truth_tags)
        tag_fn += len(truth_tags - predicted_tags)

        caption = observation["caption"]
        caption_schema_valid += int(caption["schema_valid"])
        truth_facts = set(truth["caption_facts"])
        predicted_facts = set(caption["facts"])
        caption_fact_tp += len(truth_facts & predicted_facts)
        caption_fact_fp += len(predicted_facts - truth_facts)
        caption_fact_fn += len(truth_facts - predicted_facts)
        if truth["adversarial_prompt_text"]:
            adversarial_total += 1
            adversarial_resisted += int(caption["prompt_injection_resisted"])

        relevant = truth["retrieval_relevant"]
        if relevant:
            recall, reciprocal_rank, ndcg = retrieval_metrics(
                relevant, observation["ranked_neighbors"], k
            )
            retrieval_recalls.append(recall)
            reciprocal_ranks.append(reciprocal_rank)
            ndcgs.append(ndcg)

        operation = observation["operation"]
        latencies.append(float(operation["latency_ms"]))
        rss_values.append(float(operation["peak_rss_mb"]))
        successes += int(operation["success"])
        retryable_failures += int(
            not operation["success"] and operation["retryable_failure"]
        )
        item_reports.append(
            {
                "item_id": item_id,
                "cohorts": sorted(item["cohorts"]),
                "cer": round(error_rate(reference_chars, predicted_chars), 6),
                "wer": round(error_rate(reference_words, predicted_words), 6),
                "region_matches": tp,
                "region_truth": len(truth["regions"]),
                "region_predicted": len(observation["ocr"]["regions"]),
                "latency_ms": float(operation["latency_ms"]),
                "success": bool(operation["success"]),
            }
        )

    item_count = len(observations)
    tag_precision = safe_ratio(tag_tp, tag_tp + tag_fp)
    tag_recall = safe_ratio(tag_tp, tag_tp + tag_fn)
    tag_f1 = (
        2 * tag_precision * tag_recall / (tag_precision + tag_recall)
        if tag_precision + tag_recall
        else 0.0
    )
    fact_precision = safe_ratio(
        caption_fact_tp, caption_fact_tp + caption_fact_fp
    )
    fact_recall = safe_ratio(
        caption_fact_tp, caption_fact_tp + caption_fact_fn
    )
    total_latency_seconds = sum(latencies) / 1000.0
    metrics = {
        "ocr": {
            "cer": round(safe_ratio(character_edits, character_total), 6),
            "wer": round(safe_ratio(word_edits, word_total), 6),
            "region_precision": round(
                safe_ratio(region_tp, region_tp + region_fp), 6
            ),
            "region_recall": round(
                safe_ratio(region_tp, region_tp + region_fn), 6
            ),
            "mean_matched_iou": round(mean(region_ious), 6),
        },
        "tags": {
            "precision": round(tag_precision, 6),
            "recall": round(tag_recall, 6),
            "f1": round(tag_f1, 6),
        },
        "captions": {
            "schema_valid_rate": round(
                safe_ratio(caption_schema_valid, item_count), 6
            ),
            "fact_precision": round(fact_precision, 6),
            "fact_recall": round(fact_recall, 6),
            "prompt_injection_resistance": (
                round(
                    safe_ratio(adversarial_resisted, adversarial_total),
                    6,
                )
                if adversarial_total
                else 1.0
            ),
            "adversarial_fixture_count": adversarial_total,
        },
        "retrieval": {
            "query_count": len(retrieval_recalls),
            f"recall_at_{k}": round(mean(retrieval_recalls), 6),
            "mrr": round(mean(reciprocal_ranks), 6),
            f"ndcg_at_{k}": round(mean(ndcgs), 6),
        },
        "operations": {
            "item_count": item_count,
            "success_count": successes,
            "failure_rate": round(
                safe_ratio(item_count - successes, item_count), 6
            ),
            "retryable_failure_rate": round(
                safe_ratio(retryable_failures, item_count), 6
            ),
            "latency_p50_ms": round(percentile(latencies, 0.50), 3),
            "latency_p95_ms": round(percentile(latencies, 0.95), 3),
            "throughput_items_per_second_serial": round(
                safe_ratio(item_count, total_latency_seconds), 3,
            ),
            "peak_rss_mb": round(max(rss_values, default=0.0), 3),
            "total_cost_usd": round(float(run["_total_cost"]), 6),
            "cost_per_1000_assets_usd": round(
                safe_ratio(float(run["_total_cost"]) * 1000, item_count), 6
            ),
        },
    }
    gates = policy["hard_gates"]
    gate_results = {
        "ocr_cer": metrics["ocr"]["cer"] <= gates["ocr_cer_max"],
        "ocr_wer": metrics["ocr"]["wer"] <= gates["ocr_wer_max"],
        "region_recall": metrics["ocr"]["region_recall"]
        >= gates["region_recall_min"],
        "tag_f1": metrics["tags"]["f1"] >= gates["tag_f1_min"],
        "caption_fact_precision": metrics["captions"]["fact_precision"]
        >= gates["caption_fact_precision_min"],
        "prompt_injection_resistance": metrics["captions"][
            "prompt_injection_resistance"
        ]
        >= gates["prompt_injection_resistance_min"],
        "retrieval_recall": metrics["retrieval"][f"recall_at_{k}"]
        >= gates["retrieval_recall_at_k_min"],
        "failure_rate": metrics["operations"]["failure_rate"]
        <= gates["failure_rate_max"],
    }
    return {
        "candidate_id": run["candidate_id"],
        "candidate": {
            "display_name": candidate["display_name"],
            "lane": candidate["lane"],
            "runtime": candidate["runtime"],
            "recording_status": candidate["recording_status"],
            "privacy_route": candidate["privacy_route"],
            "capabilities": sorted(candidate["capabilities"]),
            "artifact_provenance": candidate["artifacts"],
        },
        "run": {
            "run_id": run["run_id"],
            "recorded_at": run["recorded_at"],
            "recording_mode": run["recording_mode"],
            "live_provider": run["live_provider"],
            "hardware_profile_id": run["hardware_profile_id"],
        },
        "metrics": metrics,
        "hard_gates": gate_results,
        "hard_gate_pass": all(gate_results.values()),
        "items": item_reports,
    }


def input_digests(root: Path) -> list[dict[str, Any]]:
    paths = [
        root / "corpus" / "manifest.json",
        root / "candidates" / "candidates.json",
        root / "results" / "recorded-results.json",
        root / "policies" / "gates.json",
    ]
    paths.extend(sorted((root / "corpus" / "assets").glob("*")))
    paths.extend(sorted((root / "hardware").glob("*.json")))
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(set(paths))
    ]


def evaluate(
    root: Path,
    *,
    allow_live_provider: bool = False,
    max_live_cost_usd: float | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    manifest_doc = load_json(root / "corpus" / "manifest.json")
    candidate_doc = load_json(root / "candidates" / "candidates.json")
    result_doc = load_json(root / "results" / "recorded-results.json")
    policy_doc = load_json(root / "policies" / "gates.json")
    item_map, corpus_id = validate_manifest(root, manifest_doc)
    candidates = validate_candidates(candidate_doc)
    policy = validate_policy(policy_doc)
    if policy["evaluation_split"] != manifest_doc["evaluation_split"]:
        raise BenchmarkError(
            "policy evaluation_split must match corpus evaluation_split"
        )
    runs = validate_runs(
        root,
        result_doc,
        candidates,
        item_map,
        corpus_id,
        policy,
        allow_live_provider=allow_live_provider,
        max_live_cost_usd=max_live_cost_usd,
    )
    reports = [
        evaluate_run(run, candidates[run["candidate_id"]], item_map, policy)
        for run in runs
    ]
    reports.sort(key=lambda report: (report["candidate_id"], report["run"]["run_id"]))
    lanes = {report["candidate"]["lane"] for report in reports}
    required_lane_count = int(
        policy["decision"]["minimum_cloud_and_local_lanes"]
    )
    lane_coverage_pass = {"local", "cloud"}.issubset(lanes) and len(lanes) >= required_lane_count
    return {
        "schema_version": 1,
        "benchmark": "memebank-vision",
        "corpus_id": corpus_id,
        "evaluation_split": policy["evaluation_split"],
        "policy": {
            "retrieval_k": policy["retrieval_k"],
            "detection_iou_threshold": policy["detection_iou_threshold"],
            "hard_gates": policy["hard_gates"],
        },
        "lane_coverage": {
            "observed_lanes": sorted(lanes),
            "required_local_and_cloud": True,
            "pass": lane_coverage_pass,
        },
        "candidate_reports": reports,
        "decision": {
            "automatic_winner": None,
            "human_review_required": True,
            "promotion_ready": False,
            "reason": "Synthetic fixtures validate the harness only. Production promotion requires real, consented corpus evidence, current dependency/provider verification, license review, model checksums, and operational measurements.",
        },
        "input_digests": input_digests(root),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="vision benchmark root",
    )
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--allow-live-provider",
        action="store_true",
        help="accept pre-recorded live-provider results after consent and budget checks",
    )
    parser.add_argument(
        "--max-live-cost-usd",
        type=float,
        help="additional hard ceiling for a live-provider recording",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.max_live_cost_usd is not None and args.max_live_cost_usd < 0:
        raise BenchmarkError("--max-live-cost-usd must be non-negative")
    report = evaluate(
        args.root,
        allow_live_provider=args.allow_live_provider,
        max_live_cost_usd=args.max_live_cost_usd,
    )
    serialized = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkError as error:
        print(f"benchmark validation failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
