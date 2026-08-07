#!/usr/bin/env python3
"""Safely enqueue one idempotent artifact-recovery coordinator run."""

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
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ENDPOINT_ENV = "AI_AGENT_COORDINATOR_URL"
DEFAULT_TOKEN_ENV = "AI_AGENT_COORDINATOR_API_TOKEN"
DEFAULT_TIMEZONE = "America/Chicago"
DEFAULT_LOCAL_TIME = "02:00"
DEFAULT_RECOVERY_MINUTES = 60
DEFAULT_CLI_TASK_ID = "019fd526-f34d-7f72-94fa-2da6185f2d74"
MAX_RESPONSE_BYTES = 1_048_576
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
MANUAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
TIME_RE = re.compile(r"^(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d)$")
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")


class SchedulerError(RuntimeError):
    """A safe, bounded scheduler or enqueue failure."""


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


def validate_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise SchedulerError(f"unknown timezone: {value}") from exc


def parse_local_time(value: str) -> tuple[int, int]:
    match = TIME_RE.fullmatch(value)
    if not match:
        raise SchedulerError("--local-time must use HH:MM")
    return int(match.group("hour")), int(match.group("minute"))


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
    zone = validate_timezone(timezone_name)
    hour, minute = parse_local_time(local_time)
    local_now = now.astimezone(zone)
    scheduled_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    scheduled_key = f"artifact-recovery:scheduled:{scheduled_local.date().isoformat()}"

    if force:
        identifier = validate_manual_id(manual_id)
        return ScheduleDecision(
            due=True,
            kind="manual",
            local_time=local_now,
            scheduled_for=scheduled_local,
            run_key=f"artifact-recovery:manual:{scheduled_local.date().isoformat()}:{identifier}",
            scheduled_run_key=scheduled_key,
            recovered=False,
            manual_id=identifier,
        )
    if manual_id is not None:
        raise SchedulerError("--manual-id is valid only with --force")

    delta_minutes = int(
        (local_now.replace(second=0, microsecond=0) - scheduled_local).total_seconds() // 60
    )
    return ScheduleDecision(
        due=delta_minutes in {0, recovery_minutes},
        kind="scheduled",
        local_time=local_now,
        scheduled_for=scheduled_local,
        run_key=scheduled_key,
        scheduled_run_key=scheduled_key,
        recovered=delta_minutes == recovery_minutes,
        manual_id=None,
    )


def validate_env_name(value: str) -> str:
    if not ENV_NAME_RE.fullmatch(value):
        raise SchedulerError("environment variable names must use A-Z, 0-9, and _")
    return value


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


def validate_task_id(value: str) -> str:
    if not TASK_ID_RE.fullmatch(value):
        raise SchedulerError("CLI task ID has an invalid shape")
    return value


def build_payload(
    decision: ScheduleDecision,
    *,
    timezone_name: str,
    local_time: str,
    recovery_minutes: int,
    cli_task_id: str,
) -> dict[str, Any]:
    return {
        "org": "ORESoftware",
        "repo": "ai-agent-coordinator.rs",
        "task_type": "artifact_recovery",
        "priority": 80,
        "max_attempts": 3,
        "budget_usd": 12.0,
        "payload": {
            "schema_version": "artifact_recovery_job.v1",
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
            "tracking": {
                "canonical_issue": "DEN-2797",
                "prompt_intake_foundation": "DEN-834",
                "repository_creation_issue": "DEN-319",
                "credential_rotation_issue": "DEN-1230",
                "local_cli_task_id": cli_task_id,
            },
            "source_contract": {
                "scan_all_accessible_authorized_chatgpt_threads": True,
                "scan_authorized_claude_session_exports": True,
                "include_newly_changed_sources": True,
                "exclude_hidden_reasoning": True,
                "exclude_secret_values": True,
                "persist_prompt_bodies": False,
                "bounded_batch_size": 50,
                "resume_from_durable_cursor": True,
            },
            "ledger_contract": {
                "schema_version": "artifact_recovery_ledger.v1",
                "key_fields": ["origin_source", "origin_id", "owner", "repository"],
                "verify_remote_before_action": True,
                "reuse_existing_repository_branch_and_pull_request": True,
                "default_new_repository_visibility": "private",
                "retry_transient_blockers_on_later_runs": True,
            },
            "detection_contract": {
                "states": [
                    "repository_missing",
                    "artifact_only",
                    "repository_has_no_remote",
                    "changes_uncommitted",
                    "commits_unpushed",
                    "branch_not_created",
                    "branch_not_published",
                    "branch_without_pull_request",
                    "claimed_repository_unverified",
                    "claimed_commit_unverified",
                    "claimed_branch_unverified",
                    "claimed_pull_request_unverified",
                ],
                "tangible_artifact_kinds": ["code", "documentation"],
                "ordinary_conversation_creates_no_repository": True,
            },
            "delivery_contract": {
                "github_app_first": True,
                "repository_creation_fallback": "emit_cli_recovery_item",
                "open_draft_pull_request": True,
                "commit_only_intended_paths": True,
                "scan_intended_content_for_secrets": True,
                "record_repository_branch_commit_and_pr_evidence": True,
                "update_linear_project_documentation": True,
                "update_github_project": True,
            },
            "allowed_actions": [
                "read_authorized_source_metadata",
                "read_current_github_evidence",
                "create_or_reuse_feature_branch",
                "commit_bounded_intended_scope",
                "push_without_force",
                "open_or_reuse_draft_pull_request",
                "amend_canonical_linear_issue",
                "synchronize_mapped_github_project",
                "emit_cli_recovery_item",
            ],
            "forbidden_actions": [
                "reuse_chat_pasted_credentials",
                "store_pat_or_token",
                "force_push",
                "broadly_stage_mixed_worktree",
                "direct_default_branch_write",
                "bypass_protection",
                "auto_merge",
                "claim_delivery_without_remote_evidence",
            ],
        },
    }


def redacted_plan(endpoint: str, decision: ScheduleDecision, payload: dict[str, Any]) -> dict[str, Any]:
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
            "User-Agent": "ai-agent-coordinator-artifact-recovery/1",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint")
    parser.add_argument("--endpoint-env", default=DEFAULT_ENDPOINT_ENV)
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    parser.add_argument("--timezone", default=os.getenv("ARTIFACT_RECOVERY_TIMEZONE", DEFAULT_TIMEZONE))
    parser.add_argument("--local-time", default=os.getenv("ARTIFACT_RECOVERY_LOCAL_TIME", DEFAULT_LOCAL_TIME))
    parser.add_argument(
        "--recovery-minutes",
        type=int,
        default=int(os.getenv("ARTIFACT_RECOVERY_RECOVERY_MINUTES", str(DEFAULT_RECOVERY_MINUTES))),
    )
    parser.add_argument("--cli-task-id", default=os.getenv("ARTIFACT_RECOVERY_CLI_TASK_ID", DEFAULT_CLI_TASK_ID))
    parser.add_argument("--now")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--manual-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        endpoint_env = validate_env_name(args.endpoint_env)
        token_env = validate_env_name(args.token_env)
        endpoint = validate_endpoint(args.endpoint or os.getenv(endpoint_env, ""))
        if not 0 < args.timeout_seconds <= 60:
            raise SchedulerError("--timeout-seconds must be greater than 0 and at most 60")
        task_id = validate_task_id(args.cli_task_id)
        decision = schedule_decision(
            parse_instant(args.now),
            timezone_name=args.timezone,
            local_time=args.local_time,
            recovery_minutes=args.recovery_minutes,
            force=args.force,
            manual_id=args.manual_id,
        )
        if not decision.due:
            print(json.dumps({"status": "not_due", "schedule": decision.as_dict()}, indent=2, sort_keys=True))
            return 0
        payload = build_payload(
            decision,
            timezone_name=args.timezone,
            local_time=args.local_time,
            recovery_minutes=args.recovery_minutes,
            cli_task_id=task_id,
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
