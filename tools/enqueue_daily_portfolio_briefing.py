#!/usr/bin/env python3
"""Safely enqueue one idempotent daily portfolio-briefing coordinator job."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.client import HTTPResponse
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ENDPOINT_ENV = "AI_AGENT_COORDINATOR_URL"
DEFAULT_TOKEN_ENV = "AI_AGENT_COORDINATOR_API_TOKEN"
DEFAULT_TIMEZONE = "America/Chicago"
DEFAULT_LOCAL_TIME = "08:00"
DEFAULT_RECOVERY_MINUTES = 60
MAX_RESPONSE_BYTES = 1_048_576
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MANUAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
TIME_RE = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")

LANES = (
    {"lane": "must_act_today", "source_issue": None},
    {"lane": "github_linear", "source_issue": None},
    {"lane": "engineering_research", "source_issue": "DEN-828"},
    {"lane": "ai_technology", "source_issue": None},
    {"lane": "career", "source_issue": "DEN-826"},
    {"lane": "inbox_relationships", "source_issue": "DEN-830"},
    {"lane": "business_growth", "source_issue": None},
    {"lane": "prompt_coverage", "source_issue": "DEN-834"},
)


class SchedulerError(RuntimeError):
    """A safe, bounded scheduler failure."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: HTTPResponse,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class ScheduleDecision:
    due: bool
    kind: str
    local_time: datetime
    scheduled_for: datetime
    run_key: str
    scheduled_run_key: str
    recovered: bool
    manual_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "due": self.due,
            "kind": self.kind,
            "local_time": self.local_time.isoformat(),
            "scheduled_for": self.scheduled_for.isoformat(),
            "run_key": self.run_key,
            "scheduled_run_key": self.scheduled_run_key,
            "recovered": self.recovered,
            "manual_id": self.manual_id,
        }


def parse_instant(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SchedulerError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise SchedulerError("--now must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def parse_local_time(value: str) -> tuple[int, int]:
    match = TIME_RE.fullmatch(value)
    if not match:
        raise SchedulerError("--local-time must use HH:MM")
    return int(match.group("hour")), int(match.group("minute"))


def validate_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise SchedulerError(f"unknown timezone: {value}") from exc


def validate_manual_id(value: str | None) -> str:
    if value is None or not MANUAL_ID_RE.fullmatch(value):
        raise SchedulerError(
            "manual runs require --manual-id using 1-80 letters, digits, '.', '_', '+', or '-'"
        )
    return value


def schedule_decision(
    now: datetime,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
    local_time: str = DEFAULT_LOCAL_TIME,
    recovery_minutes: int = DEFAULT_RECOVERY_MINUTES,
    force: bool = False,
    manual_id: str | None = None,
) -> ScheduleDecision:
    if now.tzinfo is None:
        raise SchedulerError("schedule time must be timezone-aware")
    if not 1 <= recovery_minutes <= 180:
        raise SchedulerError("--recovery-minutes must be between 1 and 180")
    timezone_value = validate_timezone(timezone_name)
    hour, minute = parse_local_time(local_time)
    local_now = now.astimezone(timezone_value)
    scheduled_local = local_now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    scheduled_key = f"daily-portfolio:scheduled:{scheduled_local.date().isoformat()}"

    if force:
        normalized_manual_id = validate_manual_id(manual_id)
        manual_key = (
            f"daily-portfolio:manual:{scheduled_local.date().isoformat()}:"
            f"{normalized_manual_id}"
        )
        return ScheduleDecision(
            due=True,
            kind="manual",
            local_time=local_now,
            scheduled_for=scheduled_local,
            run_key=manual_key,
            scheduled_run_key=scheduled_key,
            recovered=False,
            manual_id=normalized_manual_id,
        )
    if manual_id is not None:
        raise SchedulerError("--manual-id is valid only with --force")

    delta = local_now.replace(second=0, microsecond=0) - scheduled_local
    minutes = int(delta.total_seconds() // 60)
    due = minutes in {0, recovery_minutes}
    return ScheduleDecision(
        due=due,
        kind="scheduled",
        local_time=local_now,
        scheduled_for=scheduled_local,
        run_key=scheduled_key,
        scheduled_run_key=scheduled_key,
        recovered=minutes == recovery_minutes,
        manual_id=None,
    )


def validate_env_name(name: str) -> str:
    if not ENV_NAME_RE.fullmatch(name):
        raise SchedulerError("environment variable names must use A-Z, 0-9, and _")
    return name


def validate_endpoint(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise SchedulerError("coordinator endpoint is required")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"}:
        raise SchedulerError("coordinator endpoint must use HTTPS or loopback HTTP")
    if parsed.username or parsed.password:
        raise SchedulerError("coordinator endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise SchedulerError("coordinator endpoint must not contain query or fragment")
    if not parsed.hostname:
        raise SchedulerError("coordinator endpoint must include a hostname")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not loopback:
        raise SchedulerError("non-loopback coordinator endpoints must use HTTPS")
    return value.rstrip("/")


def build_payload(
    decision: ScheduleDecision,
    *,
    timezone_name: str,
    local_time: str,
    recovery_minutes: int,
) -> dict[str, Any]:
    return {
        "org": "ORESoftware",
        "repo": "ai-agent-coordinator.rs",
        "task_type": "daily_portfolio_briefing",
        "priority": 60,
        "max_attempts": 3,
        "budget_usd": 4.0,
        "payload": {
            "schema_version": "daily_portfolio_briefing_job.v1",
            "run": {
                "mode": decision.kind,
                "run_key": decision.run_key,
                "scheduled_run_key": decision.scheduled_run_key,
                "scheduled_for": decision.scheduled_for.isoformat(),
                "observed_local_time": decision.local_time.isoformat(),
                "timezone": timezone_name,
                "local_time": local_time,
                "recovered": decision.recovered,
                "recovery_minutes": recovery_minutes,
                "manual_id": decision.manual_id,
            },
            "linear": {
                "canonical_issue": "DEN-824",
                "source_issues": ["DEN-826", "DEN-828", "DEN-830", "DEN-834"],
            },
            "source_lanes": [
                {
                    **lane,
                    "normalized_envelope_required": True,
                    "failure_isolated": True,
                }
                for lane in LANES
            ],
            "composition_contract": {
                "maximum_substantive_items": 12,
                "top_priorities": 3,
                "sections": ["do_today", "monitor", "ignore"],
                "stable_identity_required": True,
                "material_change_deduplication_required": True,
                "facts_inferences_and_unverified_must_be_distinct": True,
                "exact_dates_required": True,
                "source_links_required": True,
                "markdown_and_json_required": True,
            },
            "ranking_contract": {
                "formula_version": "portfolio_rank.v1",
                "dimensions": [
                    "deadline_risk",
                    "blocking_impact",
                    "project_priority",
                    "expected_value",
                    "confidence",
                    "reversibility",
                ],
                "deterministic_tie_breaking_required": True,
            },
            "state_contract": {
                "previous_scheduled_baseline_required": True,
                "delivery_idempotency_key": decision.run_key,
                "manual_run_must_not_advance_scheduled_baseline": True,
                "commit_state_only_after_confirmed_delivery": True,
            },
            "allowed_actions": [
                "read_normalized_child_outputs",
                "compose_briefing_plan",
                "read_previous_briefing_state",
                "prepare_delivery_payload",
                "record_confirmed_delivery_state",
            ],
            "forbidden_actions": [
                "create_or_update_linear_issue",
                "merge_pull_request",
                "deploy_service",
                "send_email_without_delivery_authorization",
                "submit_job_application",
                "reply_to_message",
                "create_repository",
                "include_secret_or_hidden_reasoning",
            ],
            "safety": {
                "read_only_source_collection": True,
                "external_writes_disabled": True,
                "separate_delivery_confirmation_required": True,
                "lane_failure_isolation_required": True,
                "bounded_error_summaries_required": True,
            },
        },
    }


def redacted_plan(
    endpoint: str,
    decision: ScheduleDecision,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "status": "dry_run",
        "endpoint": f"{endpoint}/v1/jobs",
        "idempotency_key": decision.run_key,
        "authorization": "Bearer [REDACTED]",
        "schedule": decision.as_dict(),
        "request": payload,
    }


def _read_bounded(response: HTTPResponse) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise SchedulerError("coordinator response exceeded the size limit")
    return body


def enqueue(
    endpoint: str,
    token: str,
    decision: ScheduleDecision,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    if not token:
        raise SchedulerError("configured coordinator token environment variable is empty")
    request = Request(
        f"{endpoint}/v1/jobs",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": decision.run_key,
            "User-Agent": "ai-agent-coordinator-daily-portfolio-briefing/1",
        },
    )
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = _read_bounded(response)
            status = response.status
    except HTTPError as exc:
        exc.read(4096)
        raise SchedulerError(f"coordinator returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise SchedulerError("coordinator request failed") from exc

    if not 200 <= status < 300:
        raise SchedulerError(f"coordinator returned HTTP {status}")
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise SchedulerError("coordinator response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise SchedulerError("coordinator response must be a JSON object")
    return {
        "status": "enqueued",
        "idempotency_key": decision.run_key,
        "coordinator_status": status,
        "response": parsed,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint")
    parser.add_argument("--endpoint-env", default=DEFAULT_ENDPOINT_ENV)
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--timezone", default=os.getenv("DAILY_BRIEFING_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--local-time", default=os.getenv("DAILY_BRIEFING_LOCAL_TIME", DEFAULT_LOCAL_TIME))
    parser.add_argument(
        "--recovery-minutes",
        type=int,
        default=int(
            os.getenv(
                "DAILY_BRIEFING_RECOVERY_MINUTES",
                str(DEFAULT_RECOVERY_MINUTES),
            )
        ),
    )
    parser.add_argument("--now")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--manual-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        endpoint_env = validate_env_name(args.endpoint_env)
        token_env = validate_env_name(args.token_env)
        endpoint = validate_endpoint(args.endpoint or os.getenv(endpoint_env, ""))
        if not 0 < args.timeout_seconds <= 60:
            raise SchedulerError("--timeout-seconds must be greater than 0 and at most 60")
        decision = schedule_decision(
            parse_instant(args.now),
            timezone_name=args.timezone,
            local_time=args.local_time,
            recovery_minutes=args.recovery_minutes,
            force=args.force,
            manual_id=args.manual_id,
        )
        if not decision.due:
            print(
                json.dumps(
                    {
                        "status": "not_due",
                        "schedule": decision.as_dict(),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        payload = build_payload(
            decision,
            timezone_name=args.timezone,
            local_time=args.local_time,
            recovery_minutes=args.recovery_minutes,
        )
        if args.dry_run:
            result = redacted_plan(endpoint, decision, payload)
        else:
            result = enqueue(
                endpoint,
                os.getenv(token_env, ""),
                decision,
                payload,
                args.timeout_seconds,
            )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except SchedulerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
