"""Fail-closed source-coverage receipts for nightly artifact recovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .common import (
    RecoveryError,
    expect_bool,
    expect_list,
    expect_object,
    expect_string,
    parse_timestamp,
    sha256_value,
    validate_public_safety,
)

COVERAGE_SCHEMA = "artifact_recovery_source_coverage.v1"
SUPPORTED_SOURCES = (
    "chatgpt",
    "claude",
    "linear",
    "github",
    "local_repo",
    "file_library",
    "conversation",
)
SUPPORTED_STATES = (
    "complete",
    "partial",
    "unavailable",
    "unauthorized",
    "stale",
    "not_configured",
    "excluded",
)
ERROR_CLASSES = (
    "authorization",
    "availability",
    "clock_skew",
    "pagination",
    "rate_limited",
    "stale_watermark",
    "validation",
)
SHA256_LENGTH = 64
MAX_SOURCES = 32
MAX_AGE_SECONDS = 31 * 24 * 60 * 60
MAX_CLOCK_SKEW_SECONDS = 60 * 60


def _strict_object(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    obj = expect_object(value, field)
    extra = set(obj) - allowed
    if extra:
        raise RecoveryError(f"{field} has unsupported keys: {sorted(extra)}")
    return obj


def _expect_int(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecoveryError(f"{field} must be an integer")
    if value < minimum:
        raise RecoveryError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise RecoveryError(f"{field} must be at most {maximum}")
    return value


def _parse_datetime(value: Any, field: str) -> tuple[str, datetime]:
    normalized = parse_timestamp(value, field)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return normalized, parsed.astimezone(timezone.utc)


def _expect_sha256(value: Any, field: str) -> str:
    digest = expect_string(value, field, SHA256_LENGTH)
    if len(digest) != SHA256_LENGTH or any(char not in "0123456789abcdef" for char in digest):
        raise RecoveryError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _optional_sha256(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _expect_sha256(value, field)


def _normalize_sources(value: Any, field: str) -> list[str]:
    raw_sources = expect_list(value, field, MAX_SOURCES)
    sources: list[str] = []
    for index, source_value in enumerate(raw_sources):
        source = expect_string(source_value, f"{field}[{index}]", 32)
        if source not in SUPPORTED_SOURCES:
            raise RecoveryError(f"{field}[{index}] is unsupported")
        sources.append(source)
    if len(sources) != len(set(sources)):
        raise RecoveryError(f"{field} contains duplicate sources")
    return sorted(sources)


def _normalize_policy(value: Any) -> dict[str, Any]:
    policy = _strict_object(
        value,
        "policy",
        {"required_sources", "optional_sources", "max_clock_skew_seconds"},
    )
    required_sources = _normalize_sources(policy.get("required_sources"), "policy.required_sources")
    optional_sources = _normalize_sources(policy.get("optional_sources", []), "policy.optional_sources")
    overlap = set(required_sources) & set(optional_sources)
    if overlap:
        raise RecoveryError(f"policy source sets overlap: {sorted(overlap)}")
    if not required_sources:
        raise RecoveryError("policy.required_sources must not be empty")
    max_clock_skew_seconds = _expect_int(
        policy.get("max_clock_skew_seconds", 300),
        "policy.max_clock_skew_seconds",
        maximum=MAX_CLOCK_SKEW_SECONDS,
    )
    return {
        "required_sources": required_sources,
        "optional_sources": optional_sources,
        "max_clock_skew_seconds": max_clock_skew_seconds,
    }


def _normalize_receipt(
    value: Any,
    index: int,
    *,
    generated_at: datetime,
    max_clock_skew_seconds: int,
) -> dict[str, Any]:
    field = f"receipts[{index}]"
    receipt = _strict_object(
        value,
        field,
        {
            "source",
            "source_identity_sha256",
            "capability_sha256",
            "window",
            "captured_at",
            "freshness",
            "pagination",
            "reported_state",
            "state",
            "error_class",
            "retryable",
        },
    )
    source = expect_string(receipt.get("source"), f"{field}.source", 32)
    if source not in SUPPORTED_SOURCES:
        raise RecoveryError(f"{field}.source is unsupported")
    source_identity_sha256 = _expect_sha256(
        receipt.get("source_identity_sha256"),
        f"{field}.source_identity_sha256",
    )
    capability_sha256 = _expect_sha256(
        receipt.get("capability_sha256"),
        f"{field}.capability_sha256",
    )

    window = _strict_object(receipt.get("window"), f"{field}.window", {"start", "end"})
    window_start, window_start_dt = _parse_datetime(window.get("start"), f"{field}.window.start")
    window_end, window_end_dt = _parse_datetime(window.get("end"), f"{field}.window.end")
    if window_start_dt > window_end_dt:
        raise RecoveryError(f"{field}.window.start must not be after window.end")

    captured_at, captured_at_dt = _parse_datetime(receipt.get("captured_at"), f"{field}.captured_at")
    skew = timedelta(seconds=max_clock_skew_seconds)
    if captured_at_dt > generated_at + skew:
        raise RecoveryError(f"{field}.captured_at is beyond the allowed clock skew")
    if window_end_dt > captured_at_dt + skew:
        raise RecoveryError(f"{field}.window.end is after captured_at")

    freshness = _strict_object(
        receipt.get("freshness"),
        f"{field}.freshness",
        {"watermark_at", "max_age_seconds", "age_seconds"},
    )
    watermark_at, watermark_at_dt = _parse_datetime(
        freshness.get("watermark_at"),
        f"{field}.freshness.watermark_at",
    )
    if watermark_at_dt > captured_at_dt + skew:
        raise RecoveryError(f"{field}.freshness.watermark_at is after captured_at")
    max_age_seconds = _expect_int(
        freshness.get("max_age_seconds"),
        f"{field}.freshness.max_age_seconds",
        minimum=1,
        maximum=MAX_AGE_SECONDS,
    )
    age_seconds = int((generated_at - watermark_at_dt).total_seconds())
    if age_seconds < -max_clock_skew_seconds:
        raise RecoveryError(f"{field}.freshness.watermark_at is beyond the allowed clock skew")
    age_seconds = max(0, age_seconds)
    provided_age_seconds = freshness.get("age_seconds")
    if provided_age_seconds is not None and _expect_int(
        provided_age_seconds, f"{field}.freshness.age_seconds"
    ) != age_seconds:
        raise RecoveryError(f"{field}.freshness.age_seconds does not match the watermark")

    pagination = _strict_object(
        receipt.get("pagination"),
        f"{field}.pagination",
        {
            "started",
            "complete",
            "last_page_complete",
            "pages_read",
            "items_read",
            "terminal_cursor_sha256",
        },
    )
    pagination_started = expect_bool(pagination.get("started"), f"{field}.pagination.started")
    pagination_complete = expect_bool(pagination.get("complete"), f"{field}.pagination.complete")
    last_page_complete = expect_bool(
        pagination.get("last_page_complete"),
        f"{field}.pagination.last_page_complete",
    )
    pages_read = _expect_int(pagination.get("pages_read"), f"{field}.pagination.pages_read")
    items_read = _expect_int(pagination.get("items_read"), f"{field}.pagination.items_read")
    terminal_cursor_sha256 = _optional_sha256(
        pagination.get("terminal_cursor_sha256"),
        f"{field}.pagination.terminal_cursor_sha256",
    )
    if not pagination_started and (pages_read != 0 or items_read != 0):
        raise RecoveryError(f"{field}.pagination cannot report reads before it starts")
    if pagination_complete and not pagination_started:
        raise RecoveryError(f"{field}.pagination cannot complete before it starts")
    if pagination_complete and pages_read < 1:
        raise RecoveryError(f"{field}.pagination.complete requires at least one page")
    if items_read > 0 and pages_read < 1:
        raise RecoveryError(f"{field}.pagination.items_read requires at least one page")

    reported_state = expect_string(receipt.get("reported_state"), f"{field}.reported_state", 32)
    if reported_state not in SUPPORTED_STATES:
        raise RecoveryError(f"{field}.reported_state is unsupported")
    error_class_value = receipt.get("error_class")
    if error_class_value is None:
        error_class = None
    else:
        error_class = expect_string(error_class_value, f"{field}.error_class", 32)
        if error_class not in ERROR_CLASSES:
            raise RecoveryError(f"{field}.error_class is unsupported")
    retryable = expect_bool(receipt.get("retryable", False), f"{field}.retryable")

    state = reported_state
    derived_error_class = error_class
    if reported_state == "complete":
        if not pagination_started or not pagination_complete or not last_page_complete:
            state = "partial"
            derived_error_class = "pagination"
        elif age_seconds > max_age_seconds:
            state = "stale"
            derived_error_class = "stale_watermark"
    if state == "unauthorized" and derived_error_class not in {None, "authorization"}:
        raise RecoveryError(f"{field}.error_class conflicts with unauthorized state")
    if state == "unauthorized":
        derived_error_class = "authorization"
    if state == "unavailable" and derived_error_class is None:
        derived_error_class = "availability"
    if state == "partial" and derived_error_class is None:
        derived_error_class = "pagination"
    if state == "stale":
        derived_error_class = "stale_watermark"
    if state == "complete" and derived_error_class is not None:
        raise RecoveryError(f"{field}.error_class must be null for complete state")
    if state in {"not_configured", "excluded"} and (
        pagination_started or pages_read or items_read or derived_error_class is not None
    ):
        raise RecoveryError(f"{field} cannot collect or fail when state is {state}")

    provided_state = receipt.get("state")
    if provided_state is not None and provided_state != state:
        raise RecoveryError(f"{field}.state does not match the derived state {state}")

    normalized = {
        "source": source,
        "source_identity_sha256": source_identity_sha256,
        "capability_sha256": capability_sha256,
        "window": {"start": window_start, "end": window_end},
        "captured_at": captured_at,
        "freshness": {
            "watermark_at": watermark_at,
            "max_age_seconds": max_age_seconds,
            "age_seconds": age_seconds,
        },
        "pagination": {
            "started": pagination_started,
            "complete": pagination_complete,
            "last_page_complete": last_page_complete,
            "pages_read": pages_read,
            "items_read": items_read,
            "terminal_cursor_sha256": terminal_cursor_sha256,
        },
        "reported_state": reported_state,
        "state": state,
        "error_class": derived_error_class,
        "retryable": retryable,
    }
    validate_public_safety(normalized)
    return normalized


def build_source_coverage_report(
    value: Mapping[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate raw receipts, derive effective states, and return a canonical report."""

    root = _strict_object(
        value,
        "$",
        {"schema_version", "generated_at", "policy", "receipts", "summary", "report_sha256"},
    )
    if root.get("schema_version") != COVERAGE_SCHEMA:
        raise RecoveryError(f"schema_version must be {COVERAGE_SCHEMA}")
    generated_at, generated_at_dt = _parse_datetime(root.get("generated_at"), "generated_at")
    if now is not None:
        _, now_dt = _parse_datetime(now, "--now")
        if generated_at_dt > now_dt + timedelta(seconds=MAX_CLOCK_SKEW_SECONDS):
            raise RecoveryError("generated_at is beyond the allowed clock skew")

    policy = _normalize_policy(root.get("policy"))
    raw_receipts = expect_list(root.get("receipts"), "receipts", MAX_SOURCES)
    receipts = [
        _normalize_receipt(
            raw_receipt,
            index,
            generated_at=generated_at_dt,
            max_clock_skew_seconds=policy["max_clock_skew_seconds"],
        )
        for index, raw_receipt in enumerate(raw_receipts)
    ]
    source_names = [receipt["source"] for receipt in receipts]
    if len(source_names) != len(set(source_names)):
        raise RecoveryError("receipts contain duplicate sources")

    expected_sources = set(policy["required_sources"]) | set(policy["optional_sources"])
    actual_sources = set(source_names)
    missing = sorted(expected_sources - actual_sources)
    unexpected = sorted(actual_sources - expected_sources)
    if missing:
        raise RecoveryError(f"receipts are missing policy sources: {missing}")
    if unexpected:
        raise RecoveryError(f"receipts contain sources outside policy: {unexpected}")

    receipts_by_source = {receipt["source"]: receipt for receipt in receipts}
    blocked_states = {"unauthorized", "not_configured", "excluded"}
    partial_states = {"partial", "unavailable", "stale"}
    blocked_sources = sorted(
        source
        for source in policy["required_sources"]
        if receipts_by_source[source]["state"] in blocked_states
    )
    partial_sources = sorted(
        source
        for source in policy["required_sources"]
        if receipts_by_source[source]["state"] in partial_states
    )
    complete_sources = sorted(
        source for source, receipt in receipts_by_source.items() if receipt["state"] == "complete"
    )
    if blocked_sources:
        status = "blocked"
    elif partial_sources:
        status = "partial"
    else:
        status = "complete"
    summary = {
        "status": status,
        "complete": status == "complete",
        "required_sources": len(policy["required_sources"]),
        "optional_sources": len(policy["optional_sources"]),
        "complete_sources": complete_sources,
        "partial_sources": partial_sources,
        "blocked_sources": blocked_sources,
        "source_states": {
            source: receipts_by_source[source]["state"] for source in sorted(receipts_by_source)
        },
    }

    report_without_digest = {
        "schema_version": COVERAGE_SCHEMA,
        "generated_at": generated_at,
        "policy": policy,
        "receipts": sorted(receipts, key=lambda receipt: receipt["source"]),
        "summary": summary,
    }
    digest = sha256_value(report_without_digest)
    report = {**report_without_digest, "report_sha256": digest}

    if "summary" in root and root["summary"] != summary:
        raise RecoveryError("summary does not match normalized receipts")
    if "report_sha256" in root and root["report_sha256"] != digest:
        raise RecoveryError("report_sha256 does not match canonical report")
    validate_public_safety(report)
    return report


def build_example_source_coverage(*, now: str) -> dict[str, Any]:
    """Build a deterministic synthetic fixture used only by the contract CI lane."""

    generated_at, generated_at_dt = _parse_datetime(now, "--now")
    window_start = generated_at_dt - timedelta(hours=1)
    required_sources = ["chatgpt", "claude", "linear", "github", "local_repo"]
    receipts: list[dict[str, Any]] = []
    for source in required_sources:
        receipts.append(
            {
                "source": source,
                "source_identity_sha256": sha256_value(
                    {"fixture": "source-identity", "source": source}
                ),
                "capability_sha256": sha256_value(
                    {"fixture": "capability", "source": source, "access": "read-only"}
                ),
                "window": {
                    "start": window_start.isoformat().replace("+00:00", "Z"),
                    "end": generated_at,
                },
                "captured_at": generated_at,
                "freshness": {
                    "watermark_at": generated_at,
                    "max_age_seconds": 7200,
                },
                "pagination": {
                    "started": True,
                    "complete": True,
                    "last_page_complete": True,
                    "pages_read": 1,
                    "items_read": 0,
                    "terminal_cursor_sha256": None,
                },
                "reported_state": "complete",
                "error_class": None,
                "retryable": False,
            }
        )
    return build_source_coverage_report(
        {
            "schema_version": COVERAGE_SCHEMA,
            "generated_at": generated_at,
            "policy": {
                "required_sources": required_sources,
                "optional_sources": [],
                "max_clock_skew_seconds": 300,
            },
            "receipts": receipts,
        },
        now=generated_at,
    )
