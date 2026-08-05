#!/usr/bin/env python3
"""Compose a deterministic, read-only daily portfolio briefing.

The tool consumes normalized lane envelopes. It does not fetch from or mutate
GitHub, Linear, email, job boards, browsers, or any delivery channel.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

INPUT_SCHEMA = "portfolio_briefing_input.v1"
STATE_SCHEMA = "portfolio_briefing_state.v1"
PLAN_SCHEMA = "portfolio_briefing_plan.v1"
MAX_ITEMS = 12
MAX_LANE_ITEMS = 100
MAX_TOTAL_ITEMS = 400
MAX_SOURCES = 8
MAX_DELIVERIES = 90
IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")
RUN_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,239}$")
ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
TIME_RE = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|bearer|password|secret)"
    r"\s*[:=]\s*[^\s,;]+"
)
TOKEN_SHAPE_RE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"
)

LANE_SPECS: dict[str, dict[str, str | None]] = {
    "must_act_today": {"label": "Must act today", "source_issue": None},
    "github_linear": {"label": "GitHub and Linear", "source_issue": None},
    "engineering_research": {"label": "Engineering research", "source_issue": "DEN-828"},
    "ai_technology": {"label": "AI and technology", "source_issue": None},
    "career": {"label": "Career", "source_issue": "DEN-826"},
    "inbox_relationships": {"label": "Inbox and relationships", "source_issue": "DEN-830"},
    "business_growth": {"label": "Business growth", "source_issue": None},
    "prompt_coverage": {"label": "Prompt coverage", "source_issue": "DEN-834"},
}
LANE_STATUSES = {"ok", "degraded", "unavailable"}
DISPOSITIONS = {"do_today", "monitor", "ignore"}
SOURCE_STATUSES = {"confirmed", "inference", "unverified"}
CONFIDENCE_SCORES = {"low": 1, "medium": 3, "high": 5}
MATERIAL_KEYS = {
    "status",
    "urgency",
    "evidence",
    "deadline",
    "owner",
    "recommended_action",
    "coverage_state",
}
RANK_KEYS = {
    "deadline_risk",
    "blocking_impact",
    "project_priority",
    "expected_value",
    "reversibility",
}
ALLOWED_LANE_KEYS = {"status", "source_issue", "generated_at", "error_summary", "items"}
ALLOWED_ITEM_KEYS = {
    "identity",
    "title",
    "what_changed",
    "why_it_matters",
    "confidence",
    "source_status",
    "relevant_date",
    "next_action",
    "sources",
    "disposition",
    "rank",
    "material",
}
ALLOWED_SOURCE_KEYS = {"label", "url"}

RANK_WEIGHTS = {
    "deadline_risk": 100,
    "blocking_impact": 80,
    "project_priority": 60,
    "expected_value": 40,
    "confidence": 20,
    "irreversibility": 10,
}
DISPOSITION_ORDER = {"do_today": 0, "monitor": 1, "ignore": 2}


class BriefingError(RuntimeError):
    """A bounded, user-facing validation or idempotency failure."""


@dataclass(frozen=True)
class RunContext:
    mode: str
    run_key: str
    scheduled_run_key: str
    scheduled_for: str
    generated_at: str
    timezone_name: str
    manual_id: str | None = None
    recovered: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "run_key": self.run_key,
            "scheduled_run_key": self.scheduled_run_key,
            "scheduled_for": self.scheduled_for,
            "generated_at": self.generated_at,
            "timezone": self.timezone_name,
            "manual_id": self.manual_id,
            "recovered": self.recovered,
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: Any) -> str:
    payload = value if isinstance(value, bytes) else _canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _parse_json(path: Path, label: str) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BriefingError(f"cannot read {label}: {path}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BriefingError(f"{label} is not valid JSON: {path}: {exc}") from exc


def _write_text(path: Path | None, text: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path | None, value: Any) -> None:
    if path is None:
        return
    _write_text(path, json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BriefingError(f"{label} must be a JSON object")
    return value


def _require_exact_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise BriefingError(f"{label} contains unknown fields: {', '.join(unknown)}")


def _redact(text: str) -> str:
    redacted = SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return TOKEN_SHAPE_RE.sub("[REDACTED]", redacted)


def _bounded_text(
    value: Any,
    label: str,
    *,
    minimum: int = 1,
    maximum: int,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise BriefingError(f"{label} must be a string")
    normalized = " ".join(value.split())
    if not normalized and not allow_empty:
        raise BriefingError(f"{label} must not be empty")
    if len(normalized) < minimum and not allow_empty:
        raise BriefingError(f"{label} is too short")
    if len(normalized) > maximum:
        raise BriefingError(f"{label} exceeds {maximum} characters")
    return _redact(normalized)


def _validate_identifier(value: Any, label: str, pattern: re.Pattern[str] = IDENTITY_RE) -> str:
    text = _bounded_text(value, label, maximum=240)
    if not pattern.fullmatch(text):
        raise BriefingError(f"{label} contains unsupported characters")
    return text


def _parse_instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise BriefingError(f"{label} must be an ISO-8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BriefingError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BriefingError(f"{label} must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def _normalize_instant(value: Any, label: str) -> str:
    return _parse_instant(value, label).isoformat().replace("+00:00", "Z")


def _parse_relevant_date(value: Any, label: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value:
        raise BriefingError(f"{label} must be an ISO date or timestamp")
    try:
        if "T" not in value:
            parsed_date = date.fromisoformat(value)
            parsed = datetime(
                parsed_date.year,
                parsed_date.month,
                parsed_date.day,
                tzinfo=timezone.utc,
            )
            return parsed_date.isoformat(), parsed
        parsed = _parse_instant(value, label)
        return parsed.isoformat().replace("+00:00", "Z"), parsed
    except ValueError as exc:
        raise BriefingError(f"{label} must be an ISO date or timestamp") from exc


def _sanitize_url(value: Any, label: str) -> str:
    raw = _bounded_text(value, label, maximum=2048)
    parsed = urlsplit(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise BriefingError(f"{label} must use HTTPS or loopback HTTP")
    if parsed.username or parsed.password:
        raise BriefingError(f"{label} must not contain credentials")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not loopback:
        raise BriefingError(f"{label} must use HTTPS unless it is loopback")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "scheduled_baseline": {
            "run_key": None,
            "items": {},
            "lanes": {},
        },
        "deliveries": {},
    }


def validate_state(value: Any) -> dict[str, Any]:
    state = copy.deepcopy(_require_object(value, "briefing state"))
    _require_exact_keys(
        state,
        {"schema_version", "scheduled_baseline", "deliveries"},
        "briefing state",
    )
    if state.get("schema_version") != STATE_SCHEMA:
        raise BriefingError(f"briefing state schema_version must be {STATE_SCHEMA}")

    baseline = _require_object(state.get("scheduled_baseline"), "scheduled_baseline")
    _require_exact_keys(baseline, {"run_key", "items", "lanes"}, "scheduled_baseline")
    run_key = baseline.get("run_key")
    if run_key is not None:
        _validate_identifier(run_key, "scheduled_baseline.run_key", RUN_KEY_RE)
    for field in ("items", "lanes"):
        values = _require_object(baseline.get(field), f"scheduled_baseline.{field}")
        for key, fingerprint in values.items():
            _validate_identifier(key, f"scheduled_baseline.{field} identity")
            if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
                raise BriefingError(
                    f"scheduled_baseline.{field}[{key!r}] must be a SHA-256 fingerprint"
                )

    deliveries = _require_object(state.get("deliveries"), "deliveries")
    if len(deliveries) > MAX_DELIVERIES:
        raise BriefingError(f"deliveries exceeds the retained limit of {MAX_DELIVERIES}")
    for run_key, record in deliveries.items():
        _validate_identifier(run_key, "delivery run key", RUN_KEY_RE)
        record_obj = _require_object(record, f"deliveries[{run_key!r}]")
        _require_exact_keys(
            record_obj,
            {"source_digest", "delivery_digest", "delivered_at", "mode"},
            f"deliveries[{run_key!r}]",
        )
        for digest_field in ("source_digest", "delivery_digest"):
            digest = record_obj.get(digest_field)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise BriefingError(
                    f"deliveries[{run_key!r}].{digest_field} must be SHA-256"
                )
        _normalize_instant(record_obj.get("delivered_at"), f"deliveries[{run_key!r}].delivered_at")
        if record_obj.get("mode") not in {"scheduled", "manual"}:
            raise BriefingError(f"deliveries[{run_key!r}].mode is invalid")
    return state


def _material_value(value: Any, label: str) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise BriefingError(f"{label} must be finite")
        return value
    if isinstance(value, str):
        return _bounded_text(value, label, maximum=600, allow_empty=True)
    if isinstance(value, list):
        if len(value) > 20:
            raise BriefingError(f"{label} exceeds 20 list items")
        return [_material_value(item, f"{label}[]") for item in value]
    if isinstance(value, dict):
        if len(value) > 20:
            raise BriefingError(f"{label} exceeds 20 object fields")
        normalized: dict[str, Any] = {}
        for key, nested in value.items():
            if not isinstance(key, str) or not key or len(key) > 80:
                raise BriefingError(f"{label} has an invalid nested field")
            normalized[key] = _material_value(nested, f"{label}.{key}")
        return normalized
    raise BriefingError(f"{label} contains an unsupported JSON value")


def _normalize_material(value: Any, label: str) -> dict[str, Any]:
    material = _require_object(value, label)
    _require_exact_keys(material, MATERIAL_KEYS, label)
    return {key: _material_value(material.get(key), f"{label}.{key}") for key in sorted(MATERIAL_KEYS)}


def _normalize_rank(value: Any, label: str) -> dict[str, int]:
    rank = _require_object(value, label)
    _require_exact_keys(rank, RANK_KEYS, label)
    normalized: dict[str, int] = {}
    for key in sorted(RANK_KEYS):
        score = rank.get(key)
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 5:
            raise BriefingError(f"{label}.{key} must be an integer from 0 to 5")
        normalized[key] = score
    return normalized


def _normalize_sources(value: Any, label: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise BriefingError(f"{label} must be a list")
    if len(value) > MAX_SOURCES:
        raise BriefingError(f"{label} exceeds {MAX_SOURCES} sources")
    sources: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw_source in enumerate(value):
        source = _require_object(raw_source, f"{label}[{index}]")
        _require_exact_keys(source, ALLOWED_SOURCE_KEYS, f"{label}[{index}]")
        normalized = {
            "label": _bounded_text(source.get("label"), f"{label}[{index}].label", maximum=120),
            "url": _sanitize_url(source.get("url"), f"{label}[{index}].url"),
        }
        key = (normalized["label"], normalized["url"])
        if key not in seen:
            seen.add(key)
            sources.append(normalized)
    return sources


def _ranking(rank: Mapping[str, int], confidence: str) -> tuple[int, dict[str, int]]:
    components = {
        "deadline_risk": rank["deadline_risk"] * RANK_WEIGHTS["deadline_risk"],
        "blocking_impact": rank["blocking_impact"] * RANK_WEIGHTS["blocking_impact"],
        "project_priority": rank["project_priority"] * RANK_WEIGHTS["project_priority"],
        "expected_value": rank["expected_value"] * RANK_WEIGHTS["expected_value"],
        "confidence": CONFIDENCE_SCORES[confidence] * RANK_WEIGHTS["confidence"],
        "irreversibility": (5 - rank["reversibility"]) * RANK_WEIGHTS["irreversibility"],
    }
    return sum(components.values()), components


def _normalize_item(raw: Any, lane_name: str, index: int) -> dict[str, Any]:
    label = f"lanes.{lane_name}.items[{index}]"
    item = _require_object(raw, label)
    _require_exact_keys(item, ALLOWED_ITEM_KEYS, label)

    identity = _validate_identifier(item.get("identity"), f"{label}.identity")
    confidence = item.get("confidence")
    if confidence not in CONFIDENCE_SCORES:
        raise BriefingError(f"{label}.confidence must be low, medium, or high")
    source_status = item.get("source_status")
    if source_status not in SOURCE_STATUSES:
        raise BriefingError(
            f"{label}.source_status must be confirmed, inference, or unverified"
        )
    disposition = item.get("disposition")
    if disposition not in DISPOSITIONS:
        raise BriefingError(f"{label}.disposition is invalid")

    relevant_date, relevant_sort = _parse_relevant_date(
        item.get("relevant_date"), f"{label}.relevant_date"
    )
    material = _normalize_material(item.get("material"), f"{label}.material")
    rank = _normalize_rank(item.get("rank"), f"{label}.rank")
    score, components = _ranking(rank, confidence)
    fingerprint = _sha256(
        {
            "material": material,
            "disposition": disposition,
            "source_status": source_status,
            "confidence": confidence,
        }
    )
    normalized = {
        "identity": identity,
        "lanes": [lane_name],
        "title": _bounded_text(item.get("title"), f"{label}.title", maximum=180),
        "what_changed": _bounded_text(
            item.get("what_changed"), f"{label}.what_changed", maximum=600
        ),
        "why_it_matters": _bounded_text(
            item.get("why_it_matters"), f"{label}.why_it_matters", maximum=600
        ),
        "confidence": confidence,
        "source_status": source_status,
        "relevant_date": relevant_date,
        "_relevant_sort": relevant_sort,
        "next_action": _bounded_text(
            item.get("next_action"), f"{label}.next_action", maximum=500
        ),
        "sources": _normalize_sources(item.get("sources"), f"{label}.sources"),
        "disposition": disposition,
        "rank": rank,
        "ranking": {
            "score": score,
            "components": components,
            "formula_version": "portfolio_rank.v1",
        },
        "material": material,
        "material_fingerprint": fingerprint,
    }
    return normalized


def _lane_fingerprint(status: str, error_summary: str | None) -> str:
    return _sha256({"status": status, "error_summary": error_summary})


def normalize_input(value: Any) -> dict[str, Any]:
    root = _require_object(value, "briefing input")
    _require_exact_keys(root, {"schema_version", "generated_at", "lanes"}, "briefing input")
    if root.get("schema_version") != INPUT_SCHEMA:
        raise BriefingError(f"briefing input schema_version must be {INPUT_SCHEMA}")
    generated_at = _normalize_instant(root.get("generated_at"), "briefing input.generated_at")
    lanes = _require_object(root.get("lanes"), "briefing input.lanes")
    expected = set(LANE_SPECS)
    actual = set(lanes)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown: {', '.join(unknown)}")
        raise BriefingError(f"briefing input must contain exactly eight lanes ({'; '.join(details)})")

    normalized_lanes: dict[str, Any] = {}
    total_items = 0
    for lane_name, spec in LANE_SPECS.items():
        lane = _require_object(lanes[lane_name], f"lanes.{lane_name}")
        _require_exact_keys(lane, ALLOWED_LANE_KEYS, f"lanes.{lane_name}")
        status = lane.get("status")
        if status not in LANE_STATUSES:
            raise BriefingError(f"lanes.{lane_name}.status is invalid")
        source_issue = lane.get("source_issue")
        expected_issue = spec["source_issue"]
        if source_issue is not None:
            if not isinstance(source_issue, str) or not ISSUE_RE.fullmatch(source_issue):
                raise BriefingError(f"lanes.{lane_name}.source_issue is invalid")
        if expected_issue is not None and source_issue != expected_issue:
            raise BriefingError(
                f"lanes.{lane_name}.source_issue must be {expected_issue}"
            )
        lane_generated_at = _normalize_instant(
            lane.get("generated_at"), f"lanes.{lane_name}.generated_at"
        )
        error_summary_raw = lane.get("error_summary")
        error_summary = None
        if error_summary_raw is not None:
            error_summary = _bounded_text(
                error_summary_raw,
                f"lanes.{lane_name}.error_summary",
                maximum=240,
            )
        if status == "ok" and error_summary is not None:
            raise BriefingError(f"lanes.{lane_name} cannot have error_summary when status is ok")
        if status != "ok" and error_summary is None:
            raise BriefingError(
                f"lanes.{lane_name} requires a bounded error_summary when degraded or unavailable"
            )
        raw_items = lane.get("items")
        if not isinstance(raw_items, list):
            raise BriefingError(f"lanes.{lane_name}.items must be a list")
        if len(raw_items) > MAX_LANE_ITEMS:
            raise BriefingError(
                f"lanes.{lane_name}.items exceeds {MAX_LANE_ITEMS} items"
            )
        if status == "unavailable" and raw_items:
            raise BriefingError(f"lanes.{lane_name}.items must be empty when unavailable")
        items = [_normalize_item(item, lane_name, i) for i, item in enumerate(raw_items)]
        total_items += len(items)
        normalized_lanes[lane_name] = {
            "label": spec["label"],
            "status": status,
            "source_issue": source_issue,
            "generated_at": lane_generated_at,
            "error_summary": error_summary,
            "fingerprint": _lane_fingerprint(status, error_summary),
            "items": items,
        }

    if total_items > MAX_TOTAL_ITEMS:
        raise BriefingError(f"briefing input exceeds {MAX_TOTAL_ITEMS} total items")
    return {
        "schema_version": INPUT_SCHEMA,
        "generated_at": generated_at,
        "lanes": normalized_lanes,
    }


def _merge_duplicate_identity(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    if existing["material_fingerprint"] != incoming["material_fingerprint"]:
        raise BriefingError(
            f"identity {existing['identity']!r} has conflicting material facts across lanes"
        )
    for field in (
        "title",
        "what_changed",
        "why_it_matters",
        "confidence",
        "source_status",
        "relevant_date",
        "next_action",
        "disposition",
    ):
        if existing[field] != incoming[field]:
            raise BriefingError(
                f"identity {existing['identity']!r} has conflicting {field} across lanes"
            )
    existing["lanes"] = sorted(set(existing["lanes"] + incoming["lanes"]))
    source_keys = {(source["label"], source["url"]) for source in existing["sources"]}
    for source in incoming["sources"]:
        key = (source["label"], source["url"])
        if key not in source_keys:
            if len(existing["sources"]) >= MAX_SOURCES:
                break
            existing["sources"].append(source)
            source_keys.add(key)
    if incoming["ranking"]["score"] > existing["ranking"]["score"]:
        existing["rank"] = incoming["rank"]
        existing["ranking"] = incoming["ranking"]


def _sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        DISPOSITION_ORDER[item["disposition"]],
        -item["ranking"]["score"],
        item["_relevant_sort"],
        item["identity"],
    )


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in item.items()
        if not key.startswith("_") and key != "material"
    }


def _lane_notice(lane_name: str, lane: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity": f"lane:{lane_name}:{lane['status']}",
        "lane": lane_name,
        "label": lane["label"],
        "status": lane["status"],
        "source_issue": lane["source_issue"],
        "generated_at": lane["generated_at"],
        "error_summary": lane["error_summary"],
        "fingerprint": lane["fingerprint"],
    }


def _render_sources(sources: Sequence[Mapping[str, str]]) -> str:
    if not sources:
        return "No external link supplied"
    return " · ".join(f"[{source['label']}]({source['url']})" for source in sources)


def _render_item(item: Mapping[str, Any]) -> list[str]:
    status_label = {
        "confirmed": "confirmed fact",
        "inference": "inference",
        "unverified": "unverified",
    }[item["source_status"]]
    lines = [
        f"### {item['title']}",
        "",
        f"**Changed:** {item['what_changed']}",
        "",
        f"**Why it matters:** {item['why_it_matters']}",
        "",
        (
            f"**Evidence:** {status_label}; confidence {item['confidence']}; "
            f"relevant date {item['relevant_date']}; rank {item['ranking']['score']}."
        ),
        "",
        f"**Next action:** {item['next_action']}",
        "",
        f"**Sources:** {_render_sources(item['sources'])}",
        "",
    ]
    return lines


def render_markdown(plan: Mapping[str, Any]) -> str:
    run = plan["run"]
    lines = [
        f"# Daily portfolio briefing — {run['scheduled_for'][:10]}",
        "",
        (
            f"_Run `{run['run_key']}` · {run['mode']} · "
            f"scheduled {run['scheduled_for']} · generated {run['generated_at']}_"
        ),
        "",
    ]
    if plan["status"] == "duplicate_delivery":
        lines += [
            "This run key has already been delivered with the same digest. No duplicate delivery is permitted.",
            "",
        ]
        return "\n".join(lines).rstrip() + "\n"

    lines += ["## Today's three priorities", ""]
    if plan["priorities"]:
        for index, item in enumerate(plan["priorities"], start=1):
            lines.append(
                f"{index}. **{item['title']}** — {item['next_action']} "
                f"({item['source_status']}, confidence {item['confidence']}, "
                f"{item['relevant_date']})"
            )
    else:
        lines.append("No new or materially changed priority item was supplied.")
    lines.append("")

    if plan["lane_failures"]:
        lines += ["## Source health", ""]
        for failure in plan["lane_failures"]:
            issue = f" ({failure['source_issue']})" if failure["source_issue"] else ""
            lines.append(
                f"- **{failure['label']}**{issue}: {failure['status']} — "
                f"{failure['error_summary']} (source timestamp {failure['generated_at']})"
            )
        lines.append("")

    buckets = {
        "do_today": "Do today",
        "monitor": "Monitor",
        "ignore": "Ignore",
    }
    for disposition, heading in buckets.items():
        lines += [f"## {heading}", ""]
        bucket_items = [
            item for item in plan["items"] if item["disposition"] == disposition
        ]
        if not bucket_items:
            lines.append("No new or materially changed item.")
            lines.append("")
            continue
        for item in bucket_items:
            lines.extend(_render_item(item))

    lines += ["## Do today / Monitor / Ignore", ""]
    for disposition, heading in buckets.items():
        count = sum(1 for item in plan["items"] if item["disposition"] == disposition)
        lines.append(f"- **{heading}:** {count} new or materially changed item(s).")
    lines.append("")
    return "\n".join(lines)


def _source_digest_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    # Source idempotency is based on the complete normalized material state, not
    # on the delta selected against the previous baseline. A retry after a
    # successful scheduled commit therefore produces the same source digest.
    return {
        "schema_version": plan["schema_version"],
        "run": {
            key: value
            for key, value in plan["run"].items()
            if key != "generated_at"
        },
        "next_scheduled_baseline": plan["next_scheduled_baseline"],
    }


def _delivery_digest_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    # Delivery integrity is separate from source idempotency. This digest covers
    # the exact bounded content that a caller is expected to deliver.
    return {
        "schema_version": plan["schema_version"],
        "source_digest": plan["source_digest"],
        "status": plan["status"],
        "items": plan["items"],
        "priorities": [item["identity"] for item in plan["priorities"]],
        "lane_failures": plan["lane_failures"],
        "counts": plan["counts"],
    }


def compose_briefing(
    input_value: Any,
    state_value: Any,
    run: RunContext,
) -> dict[str, Any]:
    normalized = normalize_input(input_value)
    state = validate_state(state_value)
    baseline = state["scheduled_baseline"]
    previous_items: dict[str, str] = baseline["items"]
    previous_lanes: dict[str, str] = baseline["lanes"]

    all_items: dict[str, dict[str, Any]] = {}
    lane_failures: list[dict[str, Any]] = []
    lane_fingerprints: dict[str, str] = {}
    for lane_name, lane in normalized["lanes"].items():
        lane_fingerprints[lane_name] = lane["fingerprint"]
        if (
            lane["status"] != "ok"
            and previous_lanes.get(lane_name) != lane["fingerprint"]
        ):
            lane_failures.append(_lane_notice(lane_name, lane))
        for item in lane["items"]:
            existing = all_items.get(item["identity"])
            if existing is None:
                all_items[item["identity"]] = item
            else:
                _merge_duplicate_identity(existing, item)

    changed = [
        item
        for item in all_items.values()
        if previous_items.get(item["identity"]) != item["material_fingerprint"]
    ]
    changed.sort(key=_sort_key)
    selected = changed[:MAX_ITEMS]
    public_items = [_public_item(item) for item in selected]
    priorities = [
        item for item in public_items if item["disposition"] != "ignore"
    ][:3]
    lane_failures.sort(key=lambda item: item["lane"])

    item_fingerprints = {
        identity: item["material_fingerprint"]
        for identity, item in sorted(all_items.items())
    }
    next_baseline = {
        "run_key": run.scheduled_run_key,
        "items": item_fingerprints,
        "lanes": dict(sorted(lane_fingerprints.items())),
    }
    counts = {
        "input_items": sum(len(lane["items"]) for lane in normalized["lanes"].values()),
        "unique_items": len(all_items),
        "new_or_changed": len(changed),
        "selected": len(selected),
        "omitted_due_to_limit": max(0, len(changed) - MAX_ITEMS),
        "unchanged_suppressed": max(0, len(all_items) - len(changed)),
        "lane_failures_changed": len(lane_failures),
    }
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA,
        "status": "ready" if public_items or lane_failures else "no_changes",
        "run": run.as_dict(),
        "source_generated_at": normalized["generated_at"],
        "counts": counts,
        "items": public_items,
        "priorities": priorities,
        "lane_failures": lane_failures,
        "next_scheduled_baseline": next_baseline,
    }
    source_digest = _sha256(_source_digest_payload(plan))
    plan["source_digest"] = source_digest
    plan["delivery_digest"] = _sha256(_delivery_digest_payload(plan))

    existing_delivery = state["deliveries"].get(run.run_key)
    if existing_delivery is not None:
        if existing_delivery["source_digest"] != source_digest:
            raise BriefingError(
                f"run key {run.run_key!r} was already delivered with different material source state"
            )
        plan["status"] = "duplicate_delivery"
        plan["items"] = []
        plan["priorities"] = []
        plan["lane_failures"] = []
        plan["counts"] = {
            **counts,
            "selected": 0,
            "lane_failures_changed": 0,
        }
        plan["delivery_digest"] = existing_delivery["delivery_digest"]

    plan["markdown"] = render_markdown(plan)
    return plan


def validate_plan(value: Any) -> dict[str, Any]:
    plan = copy.deepcopy(_require_object(value, "briefing plan"))
    required = {
        "schema_version",
        "status",
        "run",
        "source_generated_at",
        "counts",
        "items",
        "priorities",
        "lane_failures",
        "next_scheduled_baseline",
        "source_digest",
        "delivery_digest",
        "markdown",
    }
    _require_exact_keys(plan, required, "briefing plan")
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise BriefingError(f"briefing plan schema_version must be {PLAN_SCHEMA}")
    if plan.get("status") not in {"ready", "no_changes", "duplicate_delivery"}:
        raise BriefingError("briefing plan status is invalid")
    for digest_field in ("source_digest", "delivery_digest"):
        digest = plan.get(digest_field)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise BriefingError(f"briefing plan {digest_field} must be SHA-256")
    expected_source = _sha256(_source_digest_payload(plan))
    if expected_source != plan["source_digest"]:
        raise BriefingError("briefing plan source_digest does not match its source state")
    if plan["status"] != "duplicate_delivery":
        expected_delivery = _sha256(_delivery_digest_payload(plan))
        if expected_delivery != plan["delivery_digest"]:
            raise BriefingError(
                "briefing plan delivery_digest does not match its bounded content"
            )
    return plan


def commit_delivery(
    state_value: Any,
    plan_value: Any,
    delivered_at: str,
) -> dict[str, Any]:
    state = validate_state(state_value)
    plan = validate_plan(plan_value)
    if plan["status"] == "duplicate_delivery":
        return state

    delivered_at_normalized = _normalize_instant(delivered_at, "delivered_at")
    run = _require_object(plan["run"], "briefing plan.run")
    run_key = _validate_identifier(run.get("run_key"), "briefing plan.run.run_key", RUN_KEY_RE)
    mode = run.get("mode")
    if mode not in {"scheduled", "manual"}:
        raise BriefingError("briefing plan run mode is invalid")

    existing = state["deliveries"].get(run_key)
    if existing is not None:
        if (
            existing["source_digest"] != plan["source_digest"]
            or existing["delivery_digest"] != plan["delivery_digest"]
        ):
            raise BriefingError(
                f"run key {run_key!r} was already committed with different source or delivery content"
            )
        return state

    next_state = copy.deepcopy(state)
    next_state["deliveries"][run_key] = {
        "source_digest": plan["source_digest"],
        "delivery_digest": plan["delivery_digest"],
        "delivered_at": delivered_at_normalized,
        "mode": mode,
    }
    if mode == "scheduled":
        next_state["scheduled_baseline"] = copy.deepcopy(plan["next_scheduled_baseline"])

    if len(next_state["deliveries"]) > MAX_DELIVERIES:
        ordered = sorted(
            next_state["deliveries"].items(),
            key=lambda pair: _parse_instant(pair[1]["delivered_at"], "delivery.delivered_at"),
        )
        next_state["deliveries"] = dict(ordered[-MAX_DELIVERIES:])
    return validate_state(next_state)


def _parse_schedule_time(value: str) -> tuple[int, int]:
    match = TIME_RE.fullmatch(value)
    if not match:
        raise BriefingError("schedule time must use HH:MM")
    return int(match.group("hour")), int(match.group("minute"))


def build_run_context(
    *,
    mode: str,
    run_key: str,
    scheduled_run_key: str,
    scheduled_for: str,
    generated_at: str,
    timezone_name: str,
    manual_id: str | None,
    recovered: bool,
) -> RunContext:
    if mode not in {"scheduled", "manual"}:
        raise BriefingError("run mode must be scheduled or manual")
    normalized_run_key = _validate_identifier(run_key, "run_key", RUN_KEY_RE)
    normalized_scheduled_key = _validate_identifier(
        scheduled_run_key, "scheduled_run_key", RUN_KEY_RE
    )
    normalized_scheduled_for = _normalize_instant(scheduled_for, "scheduled_for")
    normalized_generated_at = _normalize_instant(generated_at, "generated_at")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise BriefingError(f"unknown timezone: {timezone_name}") from exc
    if mode == "manual":
        if manual_id is None:
            raise BriefingError("manual runs require --manual-id")
        manual_id = _validate_identifier(manual_id, "manual_id")
        if normalized_run_key == normalized_scheduled_key:
            raise BriefingError("manual run_key must differ from scheduled_run_key")
    elif manual_id is not None:
        raise BriefingError("scheduled runs must not include manual_id")
    return RunContext(
        mode=mode,
        run_key=normalized_run_key,
        scheduled_run_key=normalized_scheduled_key,
        scheduled_for=normalized_scheduled_for,
        generated_at=normalized_generated_at,
        timezone_name=timezone_name,
        manual_id=manual_id,
        recovered=recovered,
    )


def _plan_command(args: argparse.Namespace) -> int:
    input_value = _parse_json(args.input, "briefing input")
    state_value = _parse_json(args.state, "briefing state") if args.state else empty_state()
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat()
    run = build_run_context(
        mode=args.run_mode,
        run_key=args.run_key,
        scheduled_run_key=args.scheduled_run_key,
        scheduled_for=args.scheduled_for,
        generated_at=generated_at,
        timezone_name=args.timezone,
        manual_id=args.manual_id,
        recovered=args.recovered,
    )
    plan = compose_briefing(input_value, state_value, run)
    _write_json(args.output_json, plan)
    _write_text(args.output_markdown, plan["markdown"])
    if args.output_json is None:
        print(json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def _commit_command(args: argparse.Namespace) -> int:
    state_value = _parse_json(args.state, "briefing state") if args.state else empty_state()
    plan_value = _parse_json(args.plan, "briefing plan")
    delivered_at = args.delivered_at or datetime.now(timezone.utc).isoformat()
    next_state = commit_delivery(state_value, plan_value, delivered_at)
    _write_json(args.output_state, next_state)
    if args.output_state is None:
        print(json.dumps(next_state, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="compose a mutation-free briefing plan")
    plan.add_argument("--input", type=Path, required=True)
    plan.add_argument("--state", type=Path)
    plan.add_argument("--output-json", type=Path)
    plan.add_argument("--output-markdown", type=Path)
    plan.add_argument("--run-mode", choices=("scheduled", "manual"), required=True)
    plan.add_argument("--run-key", required=True)
    plan.add_argument("--scheduled-run-key", required=True)
    plan.add_argument("--scheduled-for", required=True)
    plan.add_argument("--generated-at")
    plan.add_argument("--timezone", default="America/Chicago")
    plan.add_argument("--manual-id")
    plan.add_argument("--recovered", action="store_true")
    plan.set_defaults(handler=_plan_command)

    commit = subparsers.add_parser(
        "commit",
        help="record a separately confirmed delivery and update scheduled baseline state",
    )
    commit.add_argument("--plan", type=Path, required=True)
    commit.add_argument("--state", type=Path)
    commit.add_argument("--output-state", type=Path)
    commit.add_argument("--delivered-at")
    commit.set_defaults(handler=_commit_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except BriefingError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
