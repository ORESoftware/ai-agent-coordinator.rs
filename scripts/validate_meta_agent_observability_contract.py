#!/usr/bin/env python3
"""Dependency-free validator for the Meta Agent observable event contract.

This validator supplements the committed JSON Schema with policy checks that JSON
Schema alone cannot express portably in every client: byte ceilings, duplicate
keys, hidden-reasoning and credential leakage, transport privilege boundaries,
and idempotent replay behavior.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = ROOT / "contracts" / "meta-agent-observability" / "v1"
POLICY_PATH = CONTRACT_ROOT / "event-policy.json"
SCHEMA_PATH = CONTRACT_ROOT / "event-envelope.schema.json"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"

TOP_LEVEL_FIELDS = {
    "schema_version",
    "event_id",
    "idempotency_key",
    "occurred_at",
    "source",
    "correlation",
    "kind",
    "payload_classification",
    "redaction_state",
    "evidence_references",
    "delivery",
    "payload",
}
SOURCE_FIELDS = {"agent_id", "provider", "model", "instance_id", "metadata"}
CORRELATION_FIELDS = {
    "correlation_id",
    "causation_id",
    "parent_event_id",
    "server_id",
    "session_id",
    "run_id",
    "goal_id",
    "task_id",
}
DELIVERY_FIELDS = {"transport", "delivery_id", "attempt", "ack_requested", "sequence"}
EVIDENCE_FIELDS = {"kind", "reference", "sha256", "observed_at"}
EVIDENCE_KINDS = {"artifact", "commit", "document", "log", "metric", "test"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)authorization\s*:\s*bearer\s+\S+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class ContractViolation(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidationResult:
    normalized: dict[str, Any]
    digest: str


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractViolation("duplicate-json-key", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    except ContractViolation:
        raise
    except (OSError, json.JSONDecodeError) as error:
        raise ContractViolation("invalid-json", f"{path}: {error}") from error


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ContractViolation(code, message)


def require_exact_fields(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    require(isinstance(value, dict), "invalid-type", f"{path} must be an object")
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    require(not missing, "missing-field", f"{path} is missing fields: {missing}")
    require(not unknown, "unknown-field", f"{path} has unknown fields: {unknown}")
    return value


def require_subset_fields(
    value: Any, required: set[str], allowed: set[str], path: str
) -> dict[str, Any]:
    require(isinstance(value, dict), "invalid-type", f"{path} must be an object")
    actual = set(value)
    missing = sorted(required - actual)
    unknown = sorted(actual - allowed)
    require(not missing, "missing-field", f"{path} is missing fields: {missing}")
    require(not unknown, "unknown-field", f"{path} has unknown fields: {unknown}")
    return value


def validate_uuid(value: Any, path: str) -> str:
    require(isinstance(value, str), "invalid-uuid", f"{path} must be a UUID string")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ContractViolation("invalid-uuid", f"{path} must be a UUID") from error
    require(str(parsed) == value.lower(), "invalid-uuid", f"{path} must be canonical UUID text")
    return value


def validate_timestamp(value: Any, path: str) -> str:
    require(isinstance(value, str), "invalid-timestamp", f"{path} must be a timestamp")
    require(value.endswith("Z"), "invalid-timestamp", f"{path} must use UTC Z notation")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ContractViolation("invalid-timestamp", f"{path} is not RFC3339") from error
    require(parsed.tzinfo is not None, "invalid-timestamp", f"{path} must be timezone-aware")
    return value


def validate_identifier(value: Any, path: str, maximum: int = 255) -> str:
    require(isinstance(value, str), "invalid-identifier", f"{path} must be a string")
    encoded = value.encode("utf-8")
    require(0 < len(encoded) <= maximum, "invalid-identifier", f"{path} is out of bounds")
    require(bool(IDENTIFIER_RE.fullmatch(value)), "invalid-identifier", f"{path} has unsafe characters")
    return value


def walk(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + (str(index),))


def validate_privacy(value: Any, policy: dict[str, Any]) -> None:
    forbidden = {name.lower() for name in policy["forbidden_field_names"]}
    secret_fragments = tuple(fragment.lower() for fragment in policy["secret_field_fragments"])
    for path, child in walk(value):
        if path:
            leaf = path[-1].lower()
            if leaf in forbidden:
                raise ContractViolation(
                    "forbidden-field", f"hidden/private reasoning field is forbidden: {'.'.join(path)}"
                )
            if any(fragment in leaf for fragment in secret_fragments):
                raise ContractViolation(
                    "secret-shaped-field", f"credential-bearing field is forbidden: {'.'.join(path)}"
                )
        if isinstance(child, str):
            for pattern in SECRET_VALUE_PATTERNS:
                if pattern.search(child):
                    raise ContractViolation(
                        "secret-shaped-value", f"credential-shaped value at {'.'.join(path) or '$'}"
                    )


def validate_policy(policy: Any) -> dict[str, Any]:
    require(isinstance(policy, dict), "invalid-policy", "event policy must be an object")
    required = {
        "schema_version",
        "protocol_version",
        "max_event_bytes",
        "max_payload_bytes",
        "max_evidence_references",
        "max_metadata_entries",
        "reliable_transports",
        "best_effort_transports",
        "payload_classifications",
        "redaction_states",
        "event_kinds",
        "udp_allowed_event_kinds",
        "forbidden_field_names",
        "secret_field_fragments",
    }
    require(set(policy) == required, "invalid-policy", "event policy keys drifted")
    require(policy["schema_version"] == 1, "invalid-policy", "policy schema version must be 1")
    require(policy["protocol_version"] == "1.0", "invalid-policy", "protocol version must be 1.0")
    for key in (
        "max_event_bytes",
        "max_payload_bytes",
        "max_evidence_references",
        "max_metadata_entries",
    ):
        require(isinstance(policy[key], int) and policy[key] > 0, "invalid-policy", f"{key} must be positive")
    require(
        policy["max_payload_bytes"] < policy["max_event_bytes"],
        "invalid-policy",
        "payload ceiling must be smaller than event ceiling",
    )
    lists = (
        "reliable_transports",
        "best_effort_transports",
        "payload_classifications",
        "redaction_states",
        "event_kinds",
        "udp_allowed_event_kinds",
        "forbidden_field_names",
        "secret_field_fragments",
    )
    for key in lists:
        value = policy[key]
        require(isinstance(value, list) and value, "invalid-policy", f"{key} must be a nonempty list")
        require(all(isinstance(item, str) and item for item in value), "invalid-policy", f"{key} entries must be strings")
        require(len(value) == len(set(value)), "invalid-policy", f"{key} must not contain duplicates")
    transports = set(policy["reliable_transports"]) | set(policy["best_effort_transports"])
    require(transports == {"http", "websocket", "tcp", "udp"}, "invalid-policy", "transport set drifted")
    require(
        set(policy["udp_allowed_event_kinds"]) <= set(policy["event_kinds"]),
        "invalid-policy",
        "UDP kinds must be a subset of event kinds",
    )
    return policy


def validate_schema(schema: Any, policy: dict[str, Any]) -> dict[str, Any]:
    require(isinstance(schema, dict), "invalid-schema", "JSON Schema must be an object")
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "invalid-schema", "schema draft drifted")
    require(schema.get("type") == "object", "invalid-schema", "event schema must describe an object")
    require(schema.get("additionalProperties") is False, "invalid-schema", "top-level unknown fields must fail")
    require(set(schema.get("required", [])) == TOP_LEVEL_FIELDS, "invalid-schema", "required fields drifted")
    require(set(schema.get("properties", {})) == TOP_LEVEL_FIELDS, "invalid-schema", "schema properties drifted")
    require(
        schema["properties"]["schema_version"].get("const") == policy["protocol_version"],
        "invalid-schema",
        "schema and policy protocol versions differ",
    )
    return schema


def validate_event(event: Any, policy: dict[str, Any]) -> ValidationResult:
    require_exact_fields(event, TOP_LEVEL_FIELDS, "$event")
    event_size = len(canonical_bytes(event))
    require(event_size <= policy["max_event_bytes"], "event-too-large", "event exceeds byte ceiling")
    require(event["schema_version"] == policy["protocol_version"], "unsupported-version", "unsupported schema version")
    event_id = validate_uuid(event["event_id"], "event_id")
    idempotency_key = event["idempotency_key"]
    require(isinstance(idempotency_key, str), "invalid-idempotency-key", "idempotency key must be a string")
    require(0 < len(idempotency_key.encode("utf-8")) <= 128, "invalid-idempotency-key", "idempotency key is out of bounds")
    require(bool(IDEMPOTENCY_RE.fullmatch(idempotency_key)), "invalid-idempotency-key", "idempotency key has unsafe characters")
    validate_timestamp(event["occurred_at"], "occurred_at")

    source = require_subset_fields(event["source"], {"agent_id", "provider", "model", "metadata"}, SOURCE_FIELDS, "source")
    for key in ("agent_id", "provider", "model"):
        validate_identifier(source[key], f"source.{key}")
    if "instance_id" in source:
        validate_identifier(source["instance_id"], "source.instance_id")
    metadata = source["metadata"]
    require(isinstance(metadata, dict), "invalid-metadata", "source.metadata must be an object")
    require(len(metadata) <= policy["max_metadata_entries"], "invalid-metadata", "source.metadata has too many entries")
    for key, value in metadata.items():
        validate_identifier(key, f"source.metadata.{key}")
        require(isinstance(value, str) and len(value.encode("utf-8")) <= 512, "invalid-metadata", f"source.metadata.{key} is invalid")

    correlation = require_subset_fields(event["correlation"], {"correlation_id"}, CORRELATION_FIELDS, "correlation")
    validate_uuid(correlation["correlation_id"], "correlation.correlation_id")
    for key in ("causation_id", "parent_event_id"):
        if key in correlation:
            validate_uuid(correlation[key], f"correlation.{key}")
            require(correlation[key] != event_id, "self-causation", f"correlation.{key} must not equal event_id")
    for key in ("server_id", "session_id", "run_id", "goal_id", "task_id"):
        if key in correlation:
            validate_identifier(correlation[key], f"correlation.{key}")

    kind = event["kind"]
    require(kind in policy["event_kinds"], "unknown-event-kind", f"unsupported event kind: {kind}")
    require(event["payload_classification"] in policy["payload_classifications"], "invalid-classification", "payload classification is invalid")
    require(event["redaction_state"] in policy["redaction_states"], "invalid-redaction-state", "redaction state is invalid")

    evidence = event["evidence_references"]
    require(isinstance(evidence, list), "invalid-evidence", "evidence_references must be an array")
    require(len(evidence) <= policy["max_evidence_references"], "invalid-evidence", "too many evidence references")
    for index, reference in enumerate(evidence):
        reference = require_subset_fields(reference, {"kind", "reference", "sha256"}, EVIDENCE_FIELDS, f"evidence_references[{index}]")
        require(reference["kind"] in EVIDENCE_KINDS, "invalid-evidence", "unknown evidence kind")
        require(isinstance(reference["reference"], str) and 0 < len(reference["reference"].encode("utf-8")) <= 2048, "invalid-evidence", "evidence reference is invalid")
        require(isinstance(reference["sha256"], str) and bool(SHA256_RE.fullmatch(reference["sha256"])), "invalid-evidence", "evidence sha256 is invalid")
        if "observed_at" in reference:
            validate_timestamp(reference["observed_at"], f"evidence_references[{index}].observed_at")

    delivery = require_subset_fields(event["delivery"], {"transport", "delivery_id", "attempt", "ack_requested"}, DELIVERY_FIELDS, "delivery")
    transport = delivery["transport"]
    transports = policy["reliable_transports"] + policy["best_effort_transports"]
    require(transport in transports, "invalid-transport", "delivery transport is invalid")
    validate_identifier(delivery["delivery_id"], "delivery.delivery_id")
    require(isinstance(delivery["attempt"], int) and not isinstance(delivery["attempt"], bool) and 1 <= delivery["attempt"] <= 16, "invalid-delivery", "delivery attempt is invalid")
    require(isinstance(delivery["ack_requested"], bool), "invalid-delivery", "ack_requested must be boolean")
    if "sequence" in delivery:
        require(isinstance(delivery["sequence"], int) and not isinstance(delivery["sequence"], bool) and delivery["sequence"] >= 0, "invalid-delivery", "sequence is invalid")
    if transport == "udp":
        require(kind in policy["udp_allowed_event_kinds"], "udp-event-kind-forbidden", f"UDP cannot carry {kind}")
        require(delivery["ack_requested"] is False, "udp-ack-forbidden", "UDP cannot request acknowledgements")
        require("sequence" not in delivery, "udp-sequence-forbidden", "UDP cannot claim reliable sequence delivery")
        require(delivery["attempt"] == 1, "udp-retry-forbidden", "UDP delivery attempt must remain 1")

    payload = event["payload"]
    require(isinstance(payload, dict), "invalid-payload", "payload must be an object")
    require(len(canonical_bytes(payload)) <= policy["max_payload_bytes"], "payload-too-large", "payload exceeds byte ceiling")
    validate_privacy(event, policy)

    normalized = copy.deepcopy(event)
    normalized["source"]["metadata"] = dict(sorted(normalized["source"]["metadata"].items()))
    normalized["evidence_references"] = sorted(
        normalized["evidence_references"],
        key=lambda item: (item["kind"], item["reference"], item["sha256"]),
    )
    digest = hashlib.sha256(canonical_bytes(normalized)).hexdigest()
    return ValidationResult(normalized=normalized, digest=digest)


def apply_idempotently(
    event: dict[str, Any],
    policy: dict[str, Any],
    seen: dict[str, str],
) -> str:
    result = validate_event(event, policy)
    key = event["idempotency_key"]
    existing = seen.get(key)
    if existing is None:
        seen[key] = result.digest
        return "applied"
    if existing == result.digest:
        return "duplicate"
    raise ContractViolation(
        "idempotency-conflict",
        f"idempotency key {key!r} was reused for a different normalized event",
    )


def validate_fixtures(policy: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    require(set(manifest) == {"schema_version", "valid", "invalid"}, "invalid-manifest", "fixture manifest keys drifted")
    require(manifest["schema_version"] == 1, "invalid-manifest", "fixture manifest version must be 1")
    valid_paths = manifest["valid"]
    invalid_entries = manifest["invalid"]
    require(isinstance(valid_paths, list) and valid_paths, "invalid-manifest", "valid fixture list is empty")
    require(isinstance(invalid_entries, list) and invalid_entries, "invalid-manifest", "invalid fixture list is empty")
    all_paths = list(valid_paths) + [entry.get("path") for entry in invalid_entries if isinstance(entry, dict)]
    require(len(all_paths) == len(set(all_paths)), "invalid-manifest", "fixture paths must be unique")

    valid_results: list[dict[str, str]] = []
    for relative in valid_paths:
        require(isinstance(relative, str) and relative.startswith("valid/"), "invalid-manifest", "valid fixture path is unsafe")
        event = load_json(FIXTURE_ROOT / relative)
        result = validate_event(event, policy)
        valid_results.append({"path": relative, "digest": result.digest})

    invalid_results: list[dict[str, str]] = []
    for entry in invalid_entries:
        require(isinstance(entry, dict) and set(entry) == {"path", "error"}, "invalid-manifest", "invalid fixture entry drifted")
        relative = entry["path"]
        expected_error = entry["error"]
        require(isinstance(relative, str) and relative.startswith("invalid/"), "invalid-manifest", "invalid fixture path is unsafe")
        require(isinstance(expected_error, str) and expected_error, "invalid-manifest", "invalid fixture error is missing")
        try:
            validate_event(load_json(FIXTURE_ROOT / relative), policy)
        except ContractViolation as error:
            require(error.code == expected_error, "unexpected-fixture-error", f"{relative}: expected {expected_error}, got {error.code}")
            invalid_results.append({"path": relative, "error": error.code})
        else:
            raise ContractViolation("invalid-fixture-accepted", f"invalid fixture was accepted: {relative}")

    return {"valid": valid_results, "invalid": invalid_results}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_report() -> dict[str, Any]:
    policy = validate_policy(load_json(POLICY_PATH))
    schema = validate_schema(load_json(SCHEMA_PATH), policy)
    manifest = load_json(MANIFEST_PATH)
    fixture_results = validate_fixtures(policy, manifest)

    first_event = load_json(FIXTURE_ROOT / manifest["valid"][0])
    seen: dict[str, str] = {}
    first = apply_idempotently(first_event, policy, seen)
    second = apply_idempotently(copy.deepcopy(first_event), policy, seen)
    require((first, second) == ("applied", "duplicate"), "idempotency-regression", "identical replay must be a duplicate")
    conflicting = copy.deepcopy(first_event)
    conflicting["event_id"] = "a61a852d-dc86-4e8e-a986-215a81960793"
    try:
        apply_idempotently(conflicting, policy, seen)
    except ContractViolation as error:
        require(error.code == "idempotency-conflict", "idempotency-regression", "conflicting replay must fail")
    else:
        raise ContractViolation("idempotency-regression", "conflicting replay was accepted")

    return {
        "schema_version": 1,
        "protocol_version": policy["protocol_version"],
        "event_kind_count": len(policy["event_kinds"]),
        "udp_event_kind_count": len(policy["udp_allowed_event_kinds"]),
        "valid_fixture_count": len(fixture_results["valid"]),
        "invalid_fixture_count": len(fixture_results["invalid"]),
        "policy_sha256": file_sha256(POLICY_PATH),
        "schema_sha256": file_sha256(SCHEMA_PATH),
        "manifest_sha256": file_sha256(MANIFEST_PATH),
        "fixtures": fixture_results,
        "idempotency": {
            "first_delivery": first,
            "identical_replay": second,
            "conflicting_replay": "rejected",
        },
        "top_level_additional_properties": schema["additionalProperties"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report()
    except ContractViolation as error:
        print(f"meta-agent-observability-contract status=failed code={error.code} reason={error}", file=sys.stderr)
        return 1
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
