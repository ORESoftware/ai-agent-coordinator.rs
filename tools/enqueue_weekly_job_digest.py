#!/usr/bin/env python3
"""Enqueue the weekly Platform/SRE/CTO opportunity digest safely.

The scheduler is intentionally discovery-only. It creates one idempotent
coordinator job per ISO week and explicitly forbids application submission or
outbound messaging. Browser applications remain routed through DEN-256.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

TIME_ZONE_NAME = "America/New_York"
TIME_ZONE = ZoneInfo(TIME_ZONE_NAME)
TARGET_WEEKDAY = 0  # Monday
TARGET_HOUR = 9
DEFAULT_TOKEN_ENV = "AI_AGENT_COORDINATOR_API_TOKEN"
DEFAULT_ENDPOINT_ENV = "AI_AGENT_COORDINATOR_URL"
MAX_RESPONSE_BYTES = 1_048_576
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class JobDigestError(RuntimeError):
    """A safe, user-facing failure."""


class NoRedirect(HTTPRedirectHandler):
    """Prevent bearer credentials from following redirects."""

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
    local_time: datetime
    run_key: str


def parse_instant(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise JobDigestError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise JobDigestError("--now must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def schedule_decision(now: datetime, force: bool = False) -> ScheduleDecision:
    if now.tzinfo is None:
        raise JobDigestError("schedule time must be timezone-aware")
    local_time = now.astimezone(TIME_ZONE)
    iso_year, iso_week, _ = local_time.isocalendar()
    run_key = f"job-agent:platform-sre-cto:{iso_year}-W{iso_week:02d}"
    due = force or (
        local_time.weekday() == TARGET_WEEKDAY and local_time.hour == TARGET_HOUR
    )
    return ScheduleDecision(due=due, local_time=local_time, run_key=run_key)


def validate_env_name(name: str) -> str:
    if not ENV_NAME_RE.fullmatch(name):
        raise JobDigestError("environment variable names must use A-Z, 0-9, and _")
    return name


def validate_endpoint(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise JobDigestError("coordinator endpoint is required")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"}:
        raise JobDigestError("coordinator endpoint must use HTTPS or loopback HTTP")
    if parsed.username or parsed.password:
        raise JobDigestError("coordinator endpoint must not contain credentials")
    if parsed.query or parsed.fragment:
        raise JobDigestError("coordinator endpoint must not contain query or fragment")
    if not parsed.hostname:
        raise JobDigestError("coordinator endpoint must include a hostname")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not loopback:
        raise JobDigestError("non-loopback coordinator endpoints must use HTTPS")
    return value.rstrip("/")


def build_payload(run_key: str, local_time: datetime) -> dict[str, Any]:
    return {
        "org": "ORESoftware",
        "repo": "ai-agent-coordinator.rs",
        "task_type": "job_opportunity_digest",
        "priority": 40,
        "max_attempts": 3,
        "budget_usd": 3.0,
        "payload": {
            "schema_version": "job_opportunity_digest.v1",
            "run_key": run_key,
            "scheduled_timezone": TIME_ZONE_NAME,
            "scheduled_local_time": local_time.isoformat(),
            "linear": {
                "canonical_issue": "DEN-826",
                "browser_application_issue": "DEN-256",
                "browser_pilot_issue": "DEN-395",
            },
            "candidate_profile": {
                "target_roles": [
                    "Platform Engineer",
                    "Site Reliability Engineer",
                    "Cloud Infrastructure Engineer",
                    "Developer Platform Engineer",
                    "CTO",
                ],
                "skills": [
                    "SDKs",
                    "developer tooling",
                    "observability",
                    "Node.js",
                    "TypeScript",
                    "Next.js",
                    "Python",
                    "Rust",
                    "Go",
                    "PostgreSQL",
                    "Docker",
                    "Kubernetes",
                    "AWS",
                    "GCP",
                ],
                "work_authorization": {
                    "country": "United States",
                    "citizen": True,
                    "sponsorship_required": False,
                },
                "preferred_arrangement": ["United States remote", "full-time"],
                "compensation_target_usd": 175000,
            },
            "volume_boundary": {
                "requested_range": {"minimum": 150, "maximum": 350},
                "automatic_submission": False,
                "quality_deduplication_and_confirmation_take_precedence": True,
                "scale_only_after_verified_batches": True,
            },
            "required_output": {
                "ranked_shortlist": True,
                "fit_reasons": True,
                "uncertainties": True,
                "deduplicated_application_queue": True,
                "counts": [
                    "discovered",
                    "shortlisted",
                    "queued",
                    "submitted",
                    "confirmed",
                    "replied",
                    "waiting",
                    "interview",
                    "blocked",
                    "skipped",
                    "closed",
                ],
            },
            "allowed_actions": [
                "discover_current_roles",
                "normalize_role_metadata",
                "score_evidence_backed_fit",
                "deduplicate_requisitions",
                "read_approved_application_status",
                "prepare_review_queue",
                "draft_grounded_follow_up",
            ],
            "forbidden_actions": [
                "submit_application",
                "send_email",
                "reply_to_message",
                "forward_message",
                "enter_sensitive_or_protected_fields",
                "solve_captcha",
                "perform_mfa",
                "accept_legal_attestation",
                "fabricate_candidate_fact",
            ],
            "safety": {
                "read_only_discovery": True,
                "separate_explicit_action_required_for_every_send": True,
                "browser_submission_must_route_through": "DEN-256",
                "final_submit_confirmation_required": True,
                "exclude_low_fit_duplicate_stale_or_suspicious_roles": True,
            },
        },
    }


def redacted_plan(endpoint: str, run_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "dry_run",
        "endpoint": f"{endpoint}/v1/jobs",
        "idempotency_key": run_key,
        "authorization": "Bearer [REDACTED]",
        "request": payload,
    }


def _read_bounded(response: HTTPResponse) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise JobDigestError("coordinator response exceeded the size limit")
    return body


def enqueue(
    endpoint: str,
    token: str,
    run_key: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    if not token:
        raise JobDigestError("configured coordinator token environment variable is empty")
    request = Request(
        f"{endpoint}/v1/jobs",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Idempotency-Key": run_key,
            "User-Agent": "ai-agent-coordinator-weekly-job-digest/1",
        },
    )
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = _read_bounded(response)
            status = response.status
    except HTTPError as exc:
        exc.read(4096)
        raise JobDigestError(f"coordinator returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise JobDigestError(f"coordinator request failed: {exc}") from exc

    if status != 202:
        raise JobDigestError(f"coordinator returned unexpected HTTP {status}")
    try:
        decoded = json.loads(body)
        job = decoded["job"]
        job_id = job["id"]
        task_type = job["task_type"]
        job_status = job["status"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise JobDigestError("coordinator returned an invalid job response") from exc
    if task_type != payload["task_type"]:
        raise JobDigestError("coordinator response task type did not match request")
    return {
        "status": "enqueued",
        "run_key": run_key,
        "job_id": job_id,
        "job_status": job_status,
        "task_type": task_type,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Safely enqueue the weekly Platform/SRE/CTO discovery digest."
    )
    result.add_argument(
        "--endpoint",
        default=os.environ.get(DEFAULT_ENDPOINT_ENV, ""),
        help=f"Coordinator base URL (default: ${DEFAULT_ENDPOINT_ENV}).",
    )
    result.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help="Name of the environment variable containing the bearer token.",
    )
    result.add_argument(
        "--now",
        help="ISO-8601 time override for deterministic validation.",
    )
    result.add_argument(
        "--force",
        action="store_true",
        help="Bypass the Monday 09:00 America/New_York scheduler gate.",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the redacted request without making a network call.",
    )
    result.add_argument(
        "--timeout-seconds",
        type=float,
        default=15.0,
        help="Network timeout, from 1 through 30 seconds.",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if not 1.0 <= args.timeout_seconds <= 30.0:
            raise JobDigestError("--timeout-seconds must be between 1 and 30")
        endpoint = validate_endpoint(args.endpoint)
        token_env = validate_env_name(args.token_env)
        decision = schedule_decision(parse_instant(args.now), force=args.force)
        payload = build_payload(decision.run_key, decision.local_time)
        if not decision.due:
            print(
                json.dumps(
                    {
                        "status": "not_due",
                        "run_key": decision.run_key,
                        "local_time": decision.local_time.isoformat(),
                        "required_local_time": "Monday 09:00 America/New_York",
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.dry_run:
            result = redacted_plan(endpoint, decision.run_key, payload)
        else:
            token = os.environ.get(token_env, "")
            result = enqueue(
                endpoint,
                token,
                decision.run_key,
                payload,
                args.timeout_seconds,
            )
        print(json.dumps(result, sort_keys=True))
        return 0
    except JobDigestError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
