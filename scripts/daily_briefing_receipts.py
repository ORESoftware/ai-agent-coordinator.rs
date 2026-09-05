#!/usr/bin/env python3
"""Deterministic daily-briefing lane and delivery-state contracts."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

LANE_SCHEMA = "portfolio-briefing-lane-manifest/v1"
DELIVERY_SCHEMA = "portfolio-briefing-delivery-state/v1"
MAX_MANIFEST_BYTES = 512 * 1024
MAX_TEXT = 2_000
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,95}$")
READY_STATUSES = {"success", "no_results"}
TERMINAL_LANE_STATUSES = READY_STATUSES | {
    "failed",
    "blocked",
    "unavailable",
    "skipped",
}
DELIVERY_STATES = {
    "prepared",
    "claimed",
    "delivering",
    "delivered",
    "retryable",
    "terminal_failed",
    "reconciling",
}
ROOT_KEYS = {
    "schema_version",
    "briefing_id",
    "scheduled_window",
    "policy_sha256",
    "expected_lanes",
    "receipts",
    "safety",
}
WINDOW_KEYS = {"start", "end", "timezone"}
RECEIPT_KEYS = {
    "lane_id",
    "status",
    "observed_at",
    "producer_commit",
    "schema_version",
    "source_cursor_sha256",
    "result_sha256",
    "result_bytes",
    "item_count",
    "operation_id",
    "path_identity",
    "retained_until",
    "reason_code",
}
SAFETY_KEYS = {
    "contains_source_payloads",
    "contains_credentials",
    "delivery_authorized",
    "notes",
}
STATE_KEYS = {
    "schema_version",
    "briefing_id",
    "state",
    "content_sha256",
    "destination_id",
    "operation_id",
    "fencing_token",
    "attempt",
    "receipt_id",
    "remote_identity_sha256",
    "failure_class",
}
COMMAND_KEYS = {
    "kind",
    "operation_id",
    "fencing_token",
    "receipt_id",
    "remote_identity_sha256",
    "failure_class",
}


class ContractError(ValueError):
    """Raised when a briefing contract fails closed."""


@dataclass(frozen=True)
class LaneReport:
    valid: bool
    ready_for_composition: bool
    briefing_id: str | None
    manifest_sha256: str | None
    envelope_sha256: str | None
    expected_lane_count: int
    receipt_count: int
    blocking_lanes: tuple[str, ...]
    errors: tuple[str, ...]


def _pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: str | Path) -> dict[str, Any]:
    payload = Path(path).read_bytes()
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ContractError(f"manifest exceeds {MAX_MANIFEST_BYTES} bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("manifest must be UTF-8") from exc
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ContractError("manifest root must be an object")
    return value


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _exact_object(
    value: Any, expected: set[str], path: str, errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return None
    actual = set(value)
    if actual != expected:
        errors.append(
            f"{path} keys mismatch: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    return value


def _text(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int = 1,
    maximum: int = MAX_TEXT,
) -> str | None:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        errors.append(
            f"{path} must be a string of length {minimum}..{maximum}"
        )
        return None
    return value


def _timestamp(value: Any, path: str, errors: list[str]) -> datetime | None:
    text = _text(value, path, errors, 20, 64)
    if text is None:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path} must be ISO-8601")
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        errors.append(f"{path} must include a UTC offset")
        return None
    return result


def _sha(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 64, 64)
    if text is not None and not SHA256.fullmatch(text):
        errors.append(f"{path} must be lowercase SHA-256")
        return None
    return text


def _commit(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 40, 40)
    if text is not None and not COMMIT.fullmatch(text):
        errors.append(f"{path} must be a lowercase 40-character commit SHA")
        return None
    return text


def _non_negative(value: Any, path: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{path} must be a non-negative integer")
        return None
    return value


def _walk_public_safe(value: Any, path: str, errors: list[str]) -> None:
    forbidden_keys = {
        "raw_payload",
        "message_body",
        "email_body",
        "prompt_text",
        "access_token",
        "api_key",
        "authorization",
        "credential",
        "secret",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} contains a non-string key")
            elif key.casefold() in forbidden_keys:
                errors.append(f"{path}.{key} is prohibited")
            _walk_public_safe(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_public_safe(child, f"{path}[{index}]", errors)
    elif isinstance(value, str) and len(value) > MAX_TEXT:
        errors.append(f"{path} exceeds the text bound")


def validate_lane_manifest(value: Mapping[str, Any]) -> LaneReport:
    errors: list[str] = []
    _walk_public_safe(value, "$", errors)
    root = _exact_object(value, ROOT_KEYS, "$", errors) or {}
    if root.get("schema_version") != LANE_SCHEMA:
        errors.append(f"schema_version must be {LANE_SCHEMA}")

    briefing_id = _text(root.get("briefing_id"), "briefing_id", errors, 5, 96)
    if briefing_id is not None and not SLUG.fullmatch(briefing_id):
        errors.append("briefing_id has an invalid format")
    _sha(root.get("policy_sha256"), "policy_sha256", errors)

    window = _exact_object(
        root.get("scheduled_window"), WINDOW_KEYS, "scheduled_window", errors
    ) or {}
    start = _timestamp(window.get("start"), "scheduled_window.start", errors)
    end = _timestamp(window.get("end"), "scheduled_window.end", errors)
    _text(window.get("timezone"), "scheduled_window.timezone", errors, 3, 64)
    if start is not None and end is not None and start >= end:
        errors.append("scheduled_window.start must be before end")

    expected = root.get("expected_lanes")
    if not isinstance(expected, list) or not 1 <= len(expected) <= 64:
        errors.append("expected_lanes must contain 1..64 lane IDs")
        expected = []
    expected_ids: list[str] = []
    for index, raw in enumerate(expected):
        lane_id = _text(raw, f"expected_lanes[{index}]", errors, 3, 64)
        if lane_id is not None:
            if not SLUG.fullmatch(lane_id):
                errors.append(f"expected_lanes[{index}] has an invalid format")
            expected_ids.append(lane_id)
    if len(expected_ids) != len(set(expected_ids)):
        errors.append("expected_lanes must not contain duplicates")

    receipts = root.get("receipts")
    if not isinstance(receipts, list) or len(receipts) > 64:
        errors.append("receipts must be an array of at most 64 items")
        receipts = []

    observed_ids: list[str] = []
    normalized: list[dict[str, Any]] = []
    blocking: list[str] = []
    operations: set[str] = set()
    path_identities: set[str] = set()
    for index, raw in enumerate(receipts):
        path = f"receipts[{index}]"
        receipt = _exact_object(raw, RECEIPT_KEYS, path, errors) or {}
        lane_id = _text(receipt.get("lane_id"), f"{path}.lane_id", errors, 3, 64)
        if lane_id is not None:
            if not SLUG.fullmatch(lane_id):
                errors.append(f"{path}.lane_id has an invalid format")
            observed_ids.append(lane_id)
        status = _text(receipt.get("status"), f"{path}.status", errors, 3, 32)
        if status is not None and status not in TERMINAL_LANE_STATUSES:
            errors.append(f"{path}.status is unsupported")
        observed = _timestamp(
            receipt.get("observed_at"), f"{path}.observed_at", errors
        )
        retained = _timestamp(
            receipt.get("retained_until"), f"{path}.retained_until", errors
        )
        if observed is not None and retained is not None and retained <= observed:
            errors.append(f"{path}.retained_until must be after observed_at")
        if observed is not None and start is not None and observed < start:
            errors.append(f"{path}.observed_at precedes the scheduled window")
        if observed is not None and end is not None and observed >= end:
            errors.append(f"{path}.observed_at must fall inside the scheduled window")
        _commit(receipt.get("producer_commit"), f"{path}.producer_commit", errors)
        _text(receipt.get("schema_version"), f"{path}.schema_version", errors, 3, 96)
        _sha(
            receipt.get("source_cursor_sha256"),
            f"{path}.source_cursor_sha256",
            errors,
        )
        operation_id = _sha(
            receipt.get("operation_id"), f"{path}.operation_id", errors
        )
        if operation_id is not None:
            if operation_id in operations:
                errors.append(f"duplicate operation_id at {path}")
            operations.add(operation_id)
        path_identity = _sha(
            receipt.get("path_identity"), f"{path}.path_identity", errors
        )
        if path_identity is not None:
            if path_identity in path_identities:
                errors.append(f"duplicate path_identity at {path}")
            path_identities.add(path_identity)

        result_sha = receipt.get("result_sha256")
        result_bytes = receipt.get("result_bytes")
        item_count = receipt.get("item_count")
        reason_code = receipt.get("reason_code")
        if status in READY_STATUSES:
            _sha(result_sha, f"{path}.result_sha256", errors)
            byte_count = _non_negative(result_bytes, f"{path}.result_bytes", errors)
            count = _non_negative(item_count, f"{path}.item_count", errors)
            if status == "no_results" and count not in {None, 0}:
                errors.append(f"{path}.item_count must be zero for no_results")
            if status == "success" and count == 0:
                errors.append(f"{path}.item_count must be positive for success")
            if byte_count == 0 and status == "success":
                errors.append(f"{path}.result_bytes must be positive for success")
            if reason_code is not None:
                errors.append(f"{path}.reason_code must be null for ready statuses")
        else:
            if result_sha is not None or result_bytes is not None or item_count is not None:
                errors.append(
                    f"{path} non-ready status must not claim result bytes, digest, or items"
                )
            _text(reason_code, f"{path}.reason_code", errors, 3, 96)
            if lane_id is not None:
                blocking.append(lane_id)

        if isinstance(raw, dict):
            normalized.append(copy.deepcopy(raw))

    if len(observed_ids) != len(set(observed_ids)):
        errors.append("receipts must not contain duplicate lane IDs")
    missing = sorted(set(expected_ids) - set(observed_ids))
    unexpected = sorted(set(observed_ids) - set(expected_ids))
    if missing:
        errors.append(f"missing lane receipts: {missing}")
        blocking.extend(missing)
    if unexpected:
        errors.append(f"unexpected lane receipts: {unexpected}")
    if len(receipts) != len(expected_ids):
        errors.append("receipt count must equal expected lane count")

    safety = _exact_object(root.get("safety"), SAFETY_KEYS, "safety", errors) or {}
    for key in (
        "contains_source_payloads",
        "contains_credentials",
        "delivery_authorized",
    ):
        if safety.get(key) is not False:
            errors.append(f"safety.{key} must be false")
    notes = safety.get("notes")
    if not isinstance(notes, list) or not notes:
        errors.append("safety.notes must be a non-empty array")
    else:
        for index, note in enumerate(notes):
            _text(note, f"safety.notes[{index}]", errors, 8, 500)

    normalized.sort(key=lambda item: str(item.get("lane_id", "")))
    envelope = {
        "schema_version": "portfolio_briefing_input.v1",
        "briefing_id": briefing_id,
        "scheduled_window": copy.deepcopy(root.get("scheduled_window")),
        "policy_sha256": root.get("policy_sha256"),
        "lane_receipts": normalized,
    }
    ready = (
        not errors
        and not blocking
        and set(observed_ids) == set(expected_ids)
        and all(
            isinstance(receipt, dict)
            and receipt.get("status") in READY_STATUSES
            for receipt in receipts
        )
    )
    try:
        manifest_digest = canonical_sha256(value)
        envelope_digest = canonical_sha256(envelope)
    except (TypeError, ValueError) as exc:
        errors.append(f"manifest cannot be canonically serialized: {exc}")
        manifest_digest = None
        envelope_digest = None
    return LaneReport(
        valid=not errors,
        ready_for_composition=ready,
        briefing_id=briefing_id,
        manifest_sha256=manifest_digest,
        envelope_sha256=envelope_digest,
        expected_lane_count=len(expected_ids),
        receipt_count=len(receipts),
        blocking_lanes=tuple(sorted(set(blocking))),
        errors=tuple(errors),
    )


def validate_delivery_state(value: Mapping[str, Any]) -> None:
    errors: list[str] = []
    state = _exact_object(value, STATE_KEYS, "delivery_state", errors) or {}
    if state.get("schema_version") != DELIVERY_SCHEMA:
        errors.append(f"delivery_state.schema_version must be {DELIVERY_SCHEMA}")
    briefing_id = _text(
        state.get("briefing_id"), "delivery_state.briefing_id", errors, 5, 96
    )
    if briefing_id is not None and not SLUG.fullmatch(briefing_id):
        errors.append("delivery_state.briefing_id has an invalid format")
    current = state.get("state")
    if current not in DELIVERY_STATES:
        errors.append("delivery_state.state is unsupported")
    _sha(state.get("content_sha256"), "delivery_state.content_sha256", errors)
    destination = _text(
        state.get("destination_id"), "delivery_state.destination_id", errors, 3, 96
    )
    if destination is not None and not SLUG.fullmatch(destination):
        errors.append("delivery_state.destination_id has an invalid format")
    _sha(state.get("operation_id"), "delivery_state.operation_id", errors)
    token = _non_negative(
        state.get("fencing_token"), "delivery_state.fencing_token", errors
    )
    attempt = _non_negative(state.get("attempt"), "delivery_state.attempt", errors)
    if token == 0:
        errors.append("delivery_state.fencing_token must be positive")
    if attempt == 0:
        errors.append("delivery_state.attempt must be positive")

    receipt = state.get("receipt_id")
    remote = state.get("remote_identity_sha256")
    failure = state.get("failure_class")
    if current == "delivered":
        _sha(receipt, "delivery_state.receipt_id", errors)
        _sha(remote, "delivery_state.remote_identity_sha256", errors)
        if failure is not None:
            errors.append("delivered state must not contain failure_class")
    elif current in {"retryable", "terminal_failed", "reconciling"}:
        _text(failure, "delivery_state.failure_class", errors, 3, 96)
        if receipt is not None or remote is not None:
            errors.append(f"{current} state must not claim a delivery receipt")
    else:
        if receipt is not None or remote is not None or failure is not None:
            errors.append(
                f"{current} state must not contain receipt or failure fields"
            )
    if errors:
        raise ContractError("; ".join(errors))


def transition_delivery(
    current: Mapping[str, Any], command: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a validated successor without mutating ``current`` on any path."""

    validate_delivery_state(current)
    errors: list[str] = []
    cmd = _exact_object(command, COMMAND_KEYS, "command", errors) or {}
    kind = _text(cmd.get("kind"), "command.kind", errors, 3, 64)
    operation = _sha(cmd.get("operation_id"), "command.operation_id", errors)
    token = _non_negative(cmd.get("fencing_token"), "command.fencing_token", errors)
    if errors:
        raise ContractError("; ".join(errors))
    assert kind is not None and operation is not None and token is not None

    state = str(current["state"])
    current_token = int(current["fencing_token"])
    current_operation = str(current["operation_id"])
    candidate = copy.deepcopy(dict(current))

    def same_owner() -> None:
        if token != current_token or operation != current_operation:
            raise ContractError("command does not own the current fenced operation")

    if kind == "claim":
        if state not in {"prepared", "retryable"}:
            raise ContractError(f"claim is illegal from {state}")
        if token <= current_token:
            raise ContractError("claim requires a strictly newer fencing token")
        candidate.update(
            {
                "state": "claimed",
                "operation_id": operation,
                "fencing_token": token,
                "attempt": int(current["attempt"]) + (1 if state == "retryable" else 0),
                "receipt_id": None,
                "remote_identity_sha256": None,
                "failure_class": None,
            }
        )
    elif kind == "begin_delivery":
        if state != "claimed":
            raise ContractError(f"begin_delivery is illegal from {state}")
        same_owner()
        candidate["state"] = "delivering"
    elif kind == "accept_receipt":
        if state not in {"delivering", "reconciling"}:
            raise ContractError(f"accept_receipt is illegal from {state}")
        same_owner()
        receipt_errors: list[str] = []
        receipt = _sha(cmd.get("receipt_id"), "command.receipt_id", receipt_errors)
        remote = _sha(
            cmd.get("remote_identity_sha256"),
            "command.remote_identity_sha256",
            receipt_errors,
        )
        if receipt_errors:
            raise ContractError("; ".join(receipt_errors))
        candidate.update(
            {
                "state": "delivered",
                "receipt_id": receipt,
                "remote_identity_sha256": remote,
                "failure_class": None,
            }
        )
    elif kind == "record_retryable_failure":
        if state != "delivering":
            raise ContractError(f"record_retryable_failure is illegal from {state}")
        same_owner()
        failure_errors: list[str] = []
        failure = _text(
            cmd.get("failure_class"), "command.failure_class", failure_errors, 3, 96
        )
        if failure_errors:
            raise ContractError("; ".join(failure_errors))
        candidate.update(
            {
                "state": "retryable",
                "receipt_id": None,
                "remote_identity_sha256": None,
                "failure_class": failure,
            }
        )
    elif kind == "record_terminal_failure":
        if state not in {"claimed", "delivering", "reconciling"}:
            raise ContractError(f"record_terminal_failure is illegal from {state}")
        same_owner()
        failure_errors = []
        failure = _text(
            cmd.get("failure_class"), "command.failure_class", failure_errors, 3, 96
        )
        if failure_errors:
            raise ContractError("; ".join(failure_errors))
        candidate.update(
            {
                "state": "terminal_failed",
                "receipt_id": None,
                "remote_identity_sha256": None,
                "failure_class": failure,
            }
        )
    elif kind == "mark_ambiguous":
        if state != "delivering":
            raise ContractError(f"mark_ambiguous is illegal from {state}")
        same_owner()
        failure_errors = []
        failure = _text(
            cmd.get("failure_class"), "command.failure_class", failure_errors, 3, 96
        )
        if failure_errors:
            raise ContractError("; ".join(failure_errors))
        candidate.update(
            {
                "state": "reconciling",
                "receipt_id": None,
                "remote_identity_sha256": None,
                "failure_class": failure,
            }
        )
    elif kind == "reconcile_not_delivered":
        if state != "reconciling":
            raise ContractError(f"reconcile_not_delivered is illegal from {state}")
        same_owner()
        candidate.update(
            {
                "state": "retryable",
                "receipt_id": None,
                "remote_identity_sha256": None,
                "failure_class": "reconciled_not_delivered",
            }
        )
    else:
        raise ContractError(f"unsupported command kind: {kind}")

    validate_delivery_state(candidate)
    return candidate
