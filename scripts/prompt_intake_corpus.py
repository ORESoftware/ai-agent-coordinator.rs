#!/usr/bin/env python3
"""Validate and exercise the public-safe ChatGPT prompt-intake acceptance corpus.

The corpus intentionally stores synthetic source fingerprints and canonical Linear
issue dispositions only. It never stores prompt bodies, chat identifiers, user
identifiers, credentials, or model output.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = 1
MAX_CORPUS_BYTES = 256 * 1024
MAX_STRING_CHARS = 160
MAX_TELEMETRY_LABELS = 2
MAX_TELEMETRY_SAMPLES = 32

TOP_LEVEL_KEYS = {
    "schema_version",
    "corpus_id",
    "snapshot_at",
    "cutoff_at",
    "source_kind",
    "retention_policy",
    "expected",
    "telemetry_samples_ms",
    "daily_briefing",
    "relation_cases",
    "items",
}
RETENTION_KEYS = {"source_metadata_days", "receipt_days"}
EXPECTED_KEYS = {
    "items",
    "live",
    "reference_only",
    "planned_creates",
    "status_counts",
    "decision_counts",
    "classification_counts",
    "exclusion_counts",
}
DAILY_BRIEFING_KEYS = {"do_today", "monitor", "ignore"}
ITEM_KEYS = {
    "fixture_id",
    "source",
    "canonical_issue",
    "project_key",
    "priority",
    "status",
    "classification",
    "decision",
    "exclusion_reason",
    "duplicate_of",
    "receipt",
}
SOURCE_KEYS = {"kind", "observed_at", "source_fingerprint", "retained_until"}
RECEIPT_KEYS = {
    "kind",
    "operation_id",
    "recorded_at",
    "result_fingerprint",
    "retained_until",
}
DUPLICATE_RELATION_KEYS = {"case_id", "kind", "source_issue", "target_issue"}
REFINEMENT_RELATION_KEYS = {
    "case_id",
    "kind",
    "canonical_issue",
    "source_fingerprints",
    "expected_decision",
    "material_fields",
}

PRIORITIES = {"urgent", "high", "medium", "low"}
STATUSES = {"in_progress", "todo", "backlog", "done", "duplicate"}
CLASSIFICATIONS = {
    "repository_work",
    "recurring_automation",
    "operational_program",
    "product_work",
}
DECISIONS = {"amend_existing", "no_op_reference", "create_new"}
EXCLUSION_REASONS = {None, "already_completed", "duplicate_reference"}
MATERIAL_FIELDS = {"acceptance_criteria", "next_steps", "test_evidence", "scope"}

LIVE_STATUSES = {"in_progress", "todo", "backlog"}
REFERENCE_STATUSES = {"done", "duplicate"}

ISSUE_RE = re.compile(r"DEN-[1-9][0-9]*\Z")
FIXTURE_RE = re.compile(r"chat-[0-9]{3}\Z")
CASE_RE = re.compile(r"(?:duplicate|refinement)-[0-9]{3}\Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
PROJECT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/ -]{0,79}\Z")
CORPUS_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,79}\Z")
TOKEN_RE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b")
BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:api[_ -]?key|password|private[_ -]?key|secret|token)\b\s*[:=]\s*\S+"
)
FORBIDDEN_RECORD_KEYS = {
    "prompt",
    "prompt_text",
    "prompt_body",
    "text",
    "body",
    "message",
    "message_id",
    "thread_id",
    "user",
    "user_id",
    "account_id",
    "email",
    "token",
    "secret",
    "password",
    "private_key",
    "authorization",
    "cookie",
    "transcript",
    "model_response",
    "hidden_reasoning",
    "chain_of_thought",
}

METRIC_LABEL_ALLOWLIST: dict[str, set[str]] = {
    "outcome": {"complete", "partial", "failed"},
    "decision": DECISIONS,
    "reason": {"already_completed", "duplicate_reference", "none"},
    "result": {"prevented", "not_needed", "repaired"},
}
METRIC_NAMES = {
    "prompt_intake_scanned_total",
    "prompt_intake_reference_only_total",
    "prompt_intake_evidence_failures_total",
    "prompt_intake_issue_updates_total",
    "prompt_intake_issue_creates_total",
    "prompt_intake_duplicates_prevented_total",
    "prompt_intake_ambiguities_total",
    "prompt_intake_race_repairs_total",
}


class CorpusError(ValueError):
    """Raised when corpus data violates the public acceptance contract."""


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CorpusError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    payload = path.read_bytes()
    if len(payload) > MAX_CORPUS_BYTES:
        raise CorpusError(f"corpus exceeds {MAX_CORPUS_BYTES} bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorpusError("corpus must be UTF-8") from exc
    try:
        value = json.loads(text, object_pairs_hook=_reject_duplicate_object_pairs)
    except json.JSONDecodeError as exc:
        raise CorpusError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise CorpusError("corpus root must be an object")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def corpus_digest(corpus: Mapping[str, Any]) -> str:
    return sha256_text(canonical_json(corpus))


def parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise CorpusError(f"{field} must be a non-empty timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CorpusError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CorpusError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _expect_exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise CorpusError(f"{field} keys mismatch: missing={missing}, extra={extra}")


def _expect_int(value: Any, field: str, minimum: int = 0, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorpusError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise CorpusError(f"{field} must be between {minimum} and {maximum}")
    return value


def _expect_short_string(value: Any, field: str, maximum: int = MAX_STRING_CHARS) -> str:
    if not isinstance(value, str) or not value:
        raise CorpusError(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise CorpusError(f"{field} exceeds {maximum} characters")
    if any(ord(character) < 0x20 for character in value):
        raise CorpusError(f"{field} contains a control character")
    return value


def _walk(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _walk(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _walk(nested, f"{path}[{index}]")


def validate_public_safety(corpus: Mapping[str, Any]) -> None:
    for path, value in _walk(corpus):
        if isinstance(value, dict):
            for key in value:
                if key.lower() in FORBIDDEN_RECORD_KEYS:
                    raise CorpusError(f"forbidden sensitive field at {path}.{key}")
        elif isinstance(value, str):
            if len(value) > MAX_STRING_CHARS and not HEX64_RE.fullmatch(value):
                raise CorpusError(f"unbounded string at {path}")
            if TOKEN_RE.search(value):
                raise CorpusError(f"credential-shaped value at {path}")
            if BEARER_RE.search(value):
                raise CorpusError(f"bearer credential at {path}")
            if PRIVATE_KEY_RE.search(value):
                raise CorpusError(f"private key material at {path}")
            if SECRET_ASSIGNMENT_RE.search(value):
                raise CorpusError(f"secret assignment at {path}")
            if EMAIL_RE.search(value):
                raise CorpusError(f"email-shaped personal data at {path}")


def _normalize_counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def derive_expected(corpus: Mapping[str, Any]) -> dict[str, Any]:
    items = corpus["items"]
    status_counts = _normalize_counts(item["status"] for item in items)
    decision_counts = _normalize_counts(item["decision"] for item in items)
    classification_counts = _normalize_counts(item["classification"] for item in items)
    exclusion_counts = _normalize_counts(
        item["exclusion_reason"] for item in items if item["exclusion_reason"] is not None
    )
    live = sum(item["status"] in LIVE_STATUSES for item in items)
    reference_only = sum(item["status"] in REFERENCE_STATUSES for item in items)
    return {
        "items": len(items),
        "live": live,
        "reference_only": reference_only,
        "planned_creates": decision_counts.get("create_new", 0),
        "status_counts": status_counts,
        "decision_counts": decision_counts,
        "classification_counts": classification_counts,
        "exclusion_counts": exclusion_counts,
    }


def _validate_count_map(value: Any, field: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise CorpusError(f"{field} must be an object")
    result: dict[str, int] = {}
    for key, count in value.items():
        _expect_short_string(key, f"{field} key", 64)
        result[key] = _expect_int(count, f"{field}.{key}")
    if list(value) != sorted(value):
        raise CorpusError(f"{field} keys must be sorted")
    return result


def _validate_item(
    item: Any,
    *,
    cutoff: datetime,
    source_days: int,
    receipt_days: int,
    allow_post_cutoff: bool = False,
) -> None:
    if not isinstance(item, dict):
        raise CorpusError("every corpus item must be an object")
    _expect_exact_keys(item, ITEM_KEYS, "item")

    fixture_id = _expect_short_string(item["fixture_id"], "item.fixture_id", 16)
    if not FIXTURE_RE.fullmatch(fixture_id):
        raise CorpusError("item.fixture_id must match chat-NNN")
    issue = _expect_short_string(item["canonical_issue"], "item.canonical_issue", 20)
    if not ISSUE_RE.fullmatch(issue):
        raise CorpusError("item.canonical_issue must match DEN-N")
    project = _expect_short_string(item["project_key"], "item.project_key", 80)
    if not PROJECT_RE.fullmatch(project):
        raise CorpusError("item.project_key has an invalid shape")

    priority = item["priority"]
    status = item["status"]
    classification = item["classification"]
    decision = item["decision"]
    exclusion = item["exclusion_reason"]
    duplicate_of = item["duplicate_of"]
    if priority not in PRIORITIES:
        raise CorpusError(f"unsupported priority for {fixture_id}")
    if status not in STATUSES:
        raise CorpusError(f"unsupported status for {fixture_id}")
    if classification not in CLASSIFICATIONS:
        raise CorpusError(f"unsupported classification for {fixture_id}")
    if decision not in DECISIONS:
        raise CorpusError(f"unsupported decision for {fixture_id}")
    if exclusion not in EXCLUSION_REASONS:
        raise CorpusError(f"unsupported exclusion reason for {fixture_id}")

    if status in LIVE_STATUSES:
        if decision != "amend_existing" or exclusion is not None or duplicate_of is not None:
            raise CorpusError(f"live item {fixture_id} must amend one existing issue")
    elif status == "done":
        if decision != "no_op_reference" or exclusion != "already_completed":
            raise CorpusError(f"done item {fixture_id} must be an already-completed reference")
        if duplicate_of is not None:
            raise CorpusError(f"done item {fixture_id} cannot have duplicate_of")
    else:
        if decision != "no_op_reference" or exclusion != "duplicate_reference":
            raise CorpusError(f"duplicate item {fixture_id} must be a duplicate reference")
        if not isinstance(duplicate_of, str) or not ISSUE_RE.fullmatch(duplicate_of):
            raise CorpusError(f"duplicate item {fixture_id} requires a canonical target")
        if duplicate_of == issue:
            raise CorpusError(f"duplicate item {fixture_id} cannot target itself")

    source = item["source"]
    if not isinstance(source, dict):
        raise CorpusError(f"source for {fixture_id} must be an object")
    _expect_exact_keys(source, SOURCE_KEYS, f"source for {fixture_id}")
    if source["kind"] != "synthetic_redacted":
        raise CorpusError(f"source kind for {fixture_id} must be synthetic_redacted")
    source_fingerprint = _expect_short_string(
        source["source_fingerprint"], f"source fingerprint for {fixture_id}", 64
    )
    if not HEX64_RE.fullmatch(source_fingerprint):
        raise CorpusError(f"source fingerprint for {fixture_id} must be lowercase SHA-256")
    observed_at = parse_timestamp(source["observed_at"], f"source observed_at for {fixture_id}")
    retained_until = parse_timestamp(
        source["retained_until"], f"source retained_until for {fixture_id}"
    )
    if not allow_post_cutoff and observed_at > cutoff:
        raise CorpusError(f"source for {fixture_id} falls after the corpus cutoff")
    if allow_post_cutoff and observed_at <= cutoff:
        raise CorpusError(f"post-cutoff source for {fixture_id} must be after the cutoff")
    retention_seconds = (retained_until - observed_at).total_seconds()
    if retention_seconds != source_days * 86_400:
        raise CorpusError(f"source retention for {fixture_id} does not match policy")

    receipt = item["receipt"]
    if not isinstance(receipt, dict):
        raise CorpusError(f"receipt for {fixture_id} must be an object")
    _expect_exact_keys(receipt, RECEIPT_KEYS, f"receipt for {fixture_id}")
    if receipt["kind"] != "acceptance_oracle":
        raise CorpusError(f"receipt kind for {fixture_id} must be acceptance_oracle")
    for field in ("operation_id", "result_fingerprint"):
        fingerprint = _expect_short_string(receipt[field], f"receipt {field} for {fixture_id}", 64)
        if not HEX64_RE.fullmatch(fingerprint):
            raise CorpusError(f"receipt {field} for {fixture_id} must be lowercase SHA-256")
    recorded_at = parse_timestamp(receipt["recorded_at"], f"receipt recorded_at for {fixture_id}")
    receipt_retained_until = parse_timestamp(
        receipt["retained_until"], f"receipt retained_until for {fixture_id}"
    )
    if recorded_at < observed_at:
        raise CorpusError(f"receipt for {fixture_id} predates its synthetic source")
    if (receipt_retained_until - recorded_at).total_seconds() != receipt_days * 86_400:
        raise CorpusError(f"receipt retention for {fixture_id} does not match policy")


def _validate_relations(corpus: Mapping[str, Any], issue_to_item: Mapping[str, Mapping[str, Any]]) -> None:
    cases = corpus["relation_cases"]
    if not isinstance(cases, list):
        raise CorpusError("relation_cases must be an array")
    seen_case_ids: set[str] = set()
    duplicate_pairs: set[tuple[str, str]] = set()
    refinement_count = 0
    for case in cases:
        if not isinstance(case, dict):
            raise CorpusError("every relation case must be an object")
        case_id = _expect_short_string(case.get("case_id"), "relation case_id", 24)
        if not CASE_RE.fullmatch(case_id):
            raise CorpusError(f"invalid relation case id: {case_id}")
        if case_id in seen_case_ids:
            raise CorpusError(f"duplicate relation case id: {case_id}")
        seen_case_ids.add(case_id)
        kind = case.get("kind")
        if kind == "duplicate_issue":
            _expect_exact_keys(case, DUPLICATE_RELATION_KEYS, case_id)
            source = case["source_issue"]
            target = case["target_issue"]
            if source not in issue_to_item or target not in issue_to_item:
                raise CorpusError(f"duplicate relation {case_id} references an unknown issue")
            if issue_to_item[source]["duplicate_of"] != target:
                raise CorpusError(f"duplicate relation {case_id} disagrees with its item")
            duplicate_pairs.add((source, target))
        elif kind == "material_refinement":
            _expect_exact_keys(case, REFINEMENT_RELATION_KEYS, case_id)
            refinement_count += 1
            issue = case["canonical_issue"]
            if issue not in issue_to_item:
                raise CorpusError(f"refinement relation {case_id} references an unknown issue")
            if case["expected_decision"] != "amend_existing":
                raise CorpusError(f"refinement relation {case_id} must amend existing work")
            fingerprints = case["source_fingerprints"]
            if not isinstance(fingerprints, list) or len(fingerprints) < 2:
                raise CorpusError(f"refinement relation {case_id} requires at least two fingerprints")
            if len(set(fingerprints)) != len(fingerprints):
                raise CorpusError(f"refinement relation {case_id} has repeated fingerprints")
            if any(not isinstance(item, str) or not HEX64_RE.fullmatch(item) for item in fingerprints):
                raise CorpusError(f"refinement relation {case_id} has an invalid fingerprint")
            material_fields = case["material_fields"]
            if not isinstance(material_fields, list) or not material_fields:
                raise CorpusError(f"refinement relation {case_id} requires material fields")
            if len(set(material_fields)) != len(material_fields):
                raise CorpusError(f"refinement relation {case_id} repeats material fields")
            if not set(material_fields) <= MATERIAL_FIELDS:
                raise CorpusError(f"refinement relation {case_id} has an unsupported material field")
        else:
            raise CorpusError(f"unsupported relation kind in {case_id}")
    expected_duplicate_pairs = {
        (item["canonical_issue"], item["duplicate_of"])
        for item in corpus["items"]
        if item["status"] == "duplicate"
    }
    if duplicate_pairs != expected_duplicate_pairs:
        raise CorpusError("duplicate relation cases must exactly cover duplicate items")
    if refinement_count < 1:
        raise CorpusError("at least one material refinement case is required")


def _validate_daily_briefing(corpus: Mapping[str, Any], issues: set[str]) -> None:
    briefing = corpus["daily_briefing"]
    if not isinstance(briefing, dict):
        raise CorpusError("daily_briefing must be an object")
    _expect_exact_keys(briefing, DAILY_BRIEFING_KEYS, "daily_briefing")
    buckets: dict[str, list[str]] = {}
    for name in ("do_today", "monitor", "ignore"):
        values = briefing[name]
        if not isinstance(values, list):
            raise CorpusError(f"daily_briefing.{name} must be an array")
        if any(not isinstance(value, str) or not ISSUE_RE.fullmatch(value) for value in values):
            raise CorpusError(f"daily_briefing.{name} has an invalid issue")
        if len(values) != len(set(values)):
            raise CorpusError(f"daily_briefing.{name} contains duplicates")
        buckets[name] = values
    if set().union(*map(set, buckets.values())) != issues:
        raise CorpusError("daily briefing buckets must cover every canonical issue")
    if any(
        set(buckets[left]) & set(buckets[right])
        for left, right in (
            ("do_today", "monitor"),
            ("do_today", "ignore"),
            ("monitor", "ignore"),
        )
    ):
        raise CorpusError("daily briefing buckets must be disjoint")
    item_by_issue = {item["canonical_issue"]: item for item in corpus["items"]}
    if any(
        item_by_issue[issue]["status"] not in REFERENCE_STATUSES
        for issue in buckets["ignore"]
    ):
        raise CorpusError("daily briefing ignore bucket may contain only reference-only items")
    if any(
        item_by_issue[issue]["status"] in REFERENCE_STATUSES
        for issue in buckets["do_today"] + buckets["monitor"]
    ):
        raise CorpusError("live daily briefing buckets may not contain reference-only items")


def validate_corpus(corpus: Mapping[str, Any]) -> None:
    if not isinstance(corpus, dict):
        raise CorpusError("corpus must be an object")
    _expect_exact_keys(corpus, TOP_LEVEL_KEYS, "corpus")
    if corpus["schema_version"] != SCHEMA_VERSION:
        raise CorpusError(f"schema_version must be {SCHEMA_VERSION}")
    corpus_id = _expect_short_string(corpus["corpus_id"], "corpus_id", 80)
    if not CORPUS_ID_RE.fullmatch(corpus_id):
        raise CorpusError("corpus_id has an invalid shape")
    if corpus["source_kind"] != "synthetic_redacted_acceptance":
        raise CorpusError("source_kind must be synthetic_redacted_acceptance")
    snapshot_at = parse_timestamp(corpus["snapshot_at"], "snapshot_at")
    cutoff_at = parse_timestamp(corpus["cutoff_at"], "cutoff_at")
    if snapshot_at < cutoff_at:
        raise CorpusError("snapshot_at cannot predate cutoff_at")

    retention = corpus["retention_policy"]
    if not isinstance(retention, dict):
        raise CorpusError("retention_policy must be an object")
    _expect_exact_keys(retention, RETENTION_KEYS, "retention_policy")
    source_days = _expect_int(
        retention["source_metadata_days"], "source_metadata_days", 1, 365
    )
    receipt_days = _expect_int(
        retention["receipt_days"], "receipt_days", source_days, 3_650
    )

    items = corpus["items"]
    if not isinstance(items, list) or not items:
        raise CorpusError("items must be a non-empty array")
    fixture_ids: set[str] = set()
    issues: set[str] = set()
    source_fingerprints: set[str] = set()
    operation_ids: set[str] = set()
    issue_to_item: dict[str, Mapping[str, Any]] = {}
    for item in items:
        _validate_item(
            item,
            cutoff=cutoff_at,
            source_days=source_days,
            receipt_days=receipt_days,
        )
        fixture_id = item["fixture_id"]
        issue = item["canonical_issue"]
        source_fingerprint = item["source"]["source_fingerprint"]
        operation_id = item["receipt"]["operation_id"]
        for value, seen, label in (
            (fixture_id, fixture_ids, "fixture id"),
            (issue, issues, "canonical issue"),
            (source_fingerprint, source_fingerprints, "source fingerprint"),
            (operation_id, operation_ids, "operation id"),
        ):
            if value in seen:
                raise CorpusError(f"duplicate {label}: {value}")
            seen.add(value)
        issue_to_item[issue] = item

    for item in items:
        target = item["duplicate_of"]
        if target is not None and target not in issues:
            raise CorpusError(f"duplicate target is absent from corpus: {target}")

    expected = corpus["expected"]
    if not isinstance(expected, dict):
        raise CorpusError("expected must be an object")
    _expect_exact_keys(expected, EXPECTED_KEYS, "expected")
    for scalar in ("items", "live", "reference_only", "planned_creates"):
        _expect_int(expected[scalar], f"expected.{scalar}")
    for field in (
        "status_counts",
        "decision_counts",
        "classification_counts",
        "exclusion_counts",
    ):
        _validate_count_map(expected[field], f"expected.{field}")
    derived = derive_expected(corpus)
    if expected != derived:
        raise CorpusError(f"expected counts drifted: expected={expected}, derived={derived}")
    if expected["planned_creates"] != 0:
        raise CorpusError("the audited corpus must produce zero create operations")

    samples = corpus["telemetry_samples_ms"]
    if not isinstance(samples, list) or not samples:
        raise CorpusError("telemetry_samples_ms must be a non-empty array")
    if len(samples) > MAX_TELEMETRY_SAMPLES:
        raise CorpusError("too many telemetry latency samples")
    for index, value in enumerate(samples):
        _expect_int(value, f"telemetry_samples_ms[{index}]", 0, 60_000)

    _validate_relations(corpus, issue_to_item)
    _validate_daily_briefing(corpus, issues)
    validate_public_safety(corpus)


def _percentile(samples: Sequence[int], percentile: float) -> int:
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def build_telemetry(corpus: Mapping[str, Any]) -> dict[str, Any]:
    expected = derive_expected(corpus)
    samples = list(corpus["telemetry_samples_ms"])
    duplicate_count = expected["status_counts"].get("duplicate", 0)
    metrics = [
        {
            "name": "prompt_intake_scanned_total",
            "value": expected["items"],
            "labels": {"outcome": "complete"},
        },
        {
            "name": "prompt_intake_reference_only_total",
            "value": expected["reference_only"],
            "labels": {"reason": "none"},
        },
        {
            "name": "prompt_intake_evidence_failures_total",
            "value": 0,
            "labels": {"outcome": "complete"},
        },
        {
            "name": "prompt_intake_issue_updates_total",
            "value": expected["decision_counts"].get("amend_existing", 0),
            "labels": {"decision": "amend_existing"},
        },
        {
            "name": "prompt_intake_issue_creates_total",
            "value": expected["planned_creates"],
            "labels": {"decision": "create_new"},
        },
        {
            "name": "prompt_intake_duplicates_prevented_total",
            "value": duplicate_count,
            "labels": {"result": "prevented"},
        },
        {
            "name": "prompt_intake_ambiguities_total",
            "value": 0,
            "labels": {"result": "not_needed"},
        },
        {
            "name": "prompt_intake_race_repairs_total",
            "value": 0,
            "labels": {"result": "not_needed"},
        },
    ]
    telemetry = {
        "schema_version": 1,
        "metrics": metrics,
        "scan_latency_ms": {
            "samples": len(samples),
            "minimum": min(samples),
            "p50": _percentile(samples, 0.50),
            "p95": _percentile(samples, 0.95),
            "maximum": max(samples),
        },
    }
    validate_bounded_telemetry(telemetry)
    return telemetry


def validate_bounded_telemetry(telemetry: Mapping[str, Any]) -> None:
    if set(telemetry) != {"schema_version", "metrics", "scan_latency_ms"}:
        raise CorpusError("telemetry has unexpected fields")
    if telemetry["schema_version"] != 1:
        raise CorpusError("telemetry schema_version must be 1")
    metrics = telemetry["metrics"]
    if not isinstance(metrics, list) or len(metrics) != len(METRIC_NAMES):
        raise CorpusError("telemetry must contain the exact bounded metric set")
    seen: set[str] = set()
    for metric in metrics:
        if not isinstance(metric, dict) or set(metric) != {"name", "value", "labels"}:
            raise CorpusError("telemetry metric shape is invalid")
        name = metric["name"]
        if name not in METRIC_NAMES or name in seen:
            raise CorpusError(f"unsupported or repeated telemetry metric: {name}")
        seen.add(name)
        _expect_int(metric["value"], f"telemetry metric {name}")
        labels = metric["labels"]
        if not isinstance(labels, dict) or len(labels) > MAX_TELEMETRY_LABELS:
            raise CorpusError(f"telemetry labels for {name} are unbounded")
        for key, value in labels.items():
            if key not in METRIC_LABEL_ALLOWLIST or value not in METRIC_LABEL_ALLOWLIST[key]:
                raise CorpusError(f"telemetry label {key}={value} is not allowlisted")
    if seen != METRIC_NAMES:
        raise CorpusError("telemetry metric set is incomplete")
    latency = telemetry["scan_latency_ms"]
    if not isinstance(latency, dict) or set(latency) != {
        "samples",
        "minimum",
        "p50",
        "p95",
        "maximum",
    }:
        raise CorpusError("scan latency summary shape is invalid")
    values = [
        _expect_int(latency[key], f"scan_latency_ms.{key}", 0, 60_000)
        for key in ("minimum", "p50", "p95", "maximum")
    ]
    _expect_int(
        latency["samples"],
        "scan_latency_ms.samples",
        1,
        MAX_TELEMETRY_SAMPLES,
    )
    if values != sorted(values):
        raise CorpusError("scan latency summary must be monotonic")
    validate_public_safety(telemetry)


def summarize_corpus(corpus: Mapping[str, Any]) -> dict[str, Any]:
    validate_corpus(corpus)
    return {
        "schema_version": 1,
        "status": "pass",
        "corpus_id": corpus["corpus_id"],
        "corpus_digest": corpus_digest(corpus),
        "snapshot_at": corpus["snapshot_at"],
        "counts": derive_expected(corpus),
        "telemetry": build_telemetry(corpus),
        "daily_briefing": copy.deepcopy(corpus["daily_briefing"]),
    }


def simulate_post_cutoff(corpus: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    validate_corpus(corpus)
    retention = corpus["retention_policy"]
    cutoff = parse_timestamp(corpus["cutoff_at"], "cutoff_at")
    _validate_item(
        item,
        cutoff=cutoff,
        source_days=retention["source_metadata_days"],
        receipt_days=retention["receipt_days"],
        allow_post_cutoff=True,
    )
    existing_ids = {entry["fixture_id"] for entry in corpus["items"]}
    existing_issues = {entry["canonical_issue"] for entry in corpus["items"]}
    existing_sources = {
        entry["source"]["source_fingerprint"] for entry in corpus["items"]
    }
    if item["fixture_id"] in existing_ids:
        raise CorpusError("post-cutoff fixture id already exists")
    if item["canonical_issue"] in existing_issues:
        raise CorpusError("post-cutoff acceptance case must use a distinct canonical issue")
    if item["source"]["source_fingerprint"] in existing_sources:
        raise CorpusError("post-cutoff source fingerprint already exists")
    if item["decision"] != "amend_existing" or item["status"] not in LIVE_STATUSES:
        raise CorpusError("post-cutoff acceptance case must amend one existing live issue")
    delta = {
        "items": 1,
        "live": 1,
        "reference_only": 0,
        "planned_creates": 0,
        "status_counts": {item["status"]: 1},
        "decision_counts": {"amend_existing": 1},
        "classification_counts": {item["classification"]: 1},
        "exclusion_counts": {},
    }
    return {
        "schema_version": 1,
        "base_corpus_digest": corpus_digest(corpus),
        "existing_items_unchanged": True,
        "post_cutoff_decision": {
            "fixture_id": item["fixture_id"],
            "canonical_issue": item["canonical_issue"],
            "decision": item["decision"],
            "status": item["status"],
        },
        "delta": delta,
    }


def purge_retained_metadata(corpus: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    validate_corpus(corpus)
    if now.tzinfo is None:
        raise CorpusError("purge time must include a timezone")
    now = now.astimezone(timezone.utc)
    source_purged = 0
    receipt_purged = 0
    items: list[dict[str, Any]] = []
    for item in corpus["items"]:
        source_expired = now >= parse_timestamp(
            item["source"]["retained_until"], "source retained_until"
        )
        receipt_expired = now >= parse_timestamp(
            item["receipt"]["retained_until"], "receipt retained_until"
        )
        if source_expired:
            source_purged += 1
            source_state: dict[str, Any] = {"state": "purged"}
        else:
            source_state = {
                "state": "retained",
                "observed_at": item["source"]["observed_at"],
                "source_fingerprint": item["source"]["source_fingerprint"],
                "retained_until": item["source"]["retained_until"],
            }
        if receipt_expired:
            receipt_purged += 1
            receipt_state: dict[str, Any] = {
                "state": "expired_tombstone",
                "receipt_digest": sha256_text(canonical_json(item["receipt"])),
            }
        else:
            receipt_state = {"state": "retained", **copy.deepcopy(item["receipt"])}
        items.append(
            {
                "fixture_id": item["fixture_id"],
                "canonical_issue": item["canonical_issue"],
                "source": source_state,
                "receipt": receipt_state,
            }
        )
    result = {
        "schema_version": 1,
        "corpus_id": corpus["corpus_id"],
        "purged_at": now.isoformat().replace("+00:00", "Z"),
        "counts": {
            "items": len(items),
            "source_metadata_purged": source_purged,
            "receipts_purged": receipt_purged,
        },
        "items": items,
    }
    validate_public_safety(result)
    return result


def _write_json(value: Any, output: Path | None) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if output is None:
        sys.stdout.write(text)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "summarize"):
        command = subparsers.add_parser(name)
        command.add_argument("corpus", type=Path)
        if name == "summarize":
            command.add_argument("--output", type=Path)
    purge = subparsers.add_parser("purge")
    purge.add_argument("corpus", type=Path)
    purge.add_argument("--now", required=True)
    purge.add_argument("--output", type=Path)
    simulate = subparsers.add_parser("simulate-post-cutoff")
    simulate.add_argument("corpus", type=Path)
    simulate.add_argument("item", type=Path)
    simulate.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        corpus = load_json(args.corpus)
        if args.command == "validate":
            validate_corpus(corpus)
            _write_json(
                {
                    "status": "pass",
                    "corpus_id": corpus["corpus_id"],
                    "corpus_digest": corpus_digest(corpus),
                    "counts": derive_expected(corpus),
                },
                None,
            )
        elif args.command == "summarize":
            _write_json(summarize_corpus(corpus), args.output)
        elif args.command == "purge":
            now = parse_timestamp(args.now, "--now")
            _write_json(purge_retained_metadata(corpus, now), args.output)
        elif args.command == "simulate-post-cutoff":
            item = load_json(args.item)
            _write_json(simulate_post_cutoff(corpus, item), args.output)
        else:  # pragma: no cover
            raise AssertionError(f"unhandled command: {args.command}")
    except (CorpusError, OSError) as exc:
        print(f"prompt-intake corpus validation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
