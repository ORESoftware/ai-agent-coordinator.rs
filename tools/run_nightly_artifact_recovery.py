#!/usr/bin/env python3
"""Enqueue and verify one fail-closed nightly artifact-recovery run.

This is the production schedule wrapper for DEN-2797. It deliberately treats
"job accepted" as an intermediate state: a scheduled invocation succeeds only
when the coordinator returns a terminal, coverage-complete run receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

import enqueue_artifact_recovery as scheduler

DEFAULT_LOCAL_TIME = "02:17"
DEFAULT_RECOVERY_AFTER_MINUTES = 60
DEFAULT_MAX_LATENESS_MINUTES = 240
DEFAULT_WINDOW_HOURS = 96
DEFAULT_OVERLAP_HOURS = 6
DEFAULT_POLL_INTERVAL_SECONDS = 15.0
DEFAULT_TERMINAL_TIMEOUT_SECONDS = 1_800.0
MAX_JOB_RESPONSE_BYTES = 1_048_576
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class NightlyRunError(RuntimeError):
    """A bounded, public-safe activation or verification failure."""


@dataclass(frozen=True)
class RunValidation:
    run_key: str
    status: str
    prompts_scanned: int
    actionable_items: int
    dispositioned_items: int
    source_coverage_status: str
    report_sha256: str
    manifest_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_key": self.run_key,
            "status": self.status,
            "prompts_scanned": self.prompts_scanned,
            "actionable_items": self.actionable_items,
            "dispositioned_items": self.dispositioned_items,
            "source_coverage_status": self.source_coverage_status,
            "report_sha256": self.report_sha256,
            "manifest_sha256": self.manifest_sha256,
        }


def _bounded_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NightlyRunError(f"{field} must be a non-negative integer")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise NightlyRunError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NightlyRunError(f"{field} must be a JSON object")
    return value


def require_enabled(env_name: str, environ: Mapping[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    if env.get(env_name, "").strip().lower() != "true":
        raise NightlyRunError(
            f"scheduled enqueue is disabled: {env_name} must be exactly true"
        )


def robust_schedule_decision(
    now: datetime,
    *,
    timezone_name: str,
    local_time: str,
    recovery_after_minutes: int,
    max_lateness_minutes: int,
    force: bool = False,
    manual_id: str | None = None,
) -> scheduler.ScheduleDecision:
    """Return one daily idempotent decision across a bounded delayed-run window."""

    if force:
        return scheduler.schedule_decision(
            now,
            timezone_name=timezone_name,
            local_time=local_time,
            recovery_minutes=recovery_after_minutes,
            force=True,
            manual_id=manual_id,
        )
    if manual_id is not None:
        raise NightlyRunError("--manual-id is valid only with --force")
    if not 1 <= recovery_after_minutes <= 720:
        raise NightlyRunError("recovery-after-minutes must be between 1 and 720")
    if not recovery_after_minutes <= max_lateness_minutes <= 720:
        raise NightlyRunError(
            "max-lateness-minutes must be between recovery-after-minutes and 720"
        )

    zone = scheduler.validate_timezone(timezone_name)
    hour, minute = scheduler.parse_local_time(local_time)
    local_now = now.astimezone(zone)
    scheduled_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    lateness_minutes = int(
        (local_now.replace(second=0, microsecond=0) - scheduled_local).total_seconds()
        // 60
    )
    scheduled_key = f"artifact-recovery:scheduled:{scheduled_local.date().isoformat()}"
    return scheduler.ScheduleDecision(
        due=0 <= lateness_minutes <= max_lateness_minutes,
        kind="scheduled",
        local_time=local_now,
        scheduled_for=scheduled_local,
        run_key=scheduled_key,
        scheduled_run_key=scheduled_key,
        recovered=lateness_minutes >= recovery_after_minutes,
        manual_id=None,
    )


def build_hardened_payload(
    decision: scheduler.ScheduleDecision,
    *,
    timezone_name: str,
    local_time: str,
    recovery_after_minutes: int,
    max_lateness_minutes: int,
    window_hours: int,
    overlap_hours: int,
    cli_task_id: str,
) -> dict[str, Any]:
    if not 72 <= window_hours <= 168:
        raise NightlyRunError("window-hours must be between 72 and 168")
    if not 0 <= overlap_hours < window_hours:
        raise NightlyRunError("overlap-hours must be non-negative and smaller than window-hours")

    payload = scheduler.build_payload(
        decision,
        timezone_name=timezone_name,
        local_time=local_time,
        recovery_minutes=recovery_after_minutes,
        cli_task_id=cli_task_id,
    )
    contract = _object(payload.get("payload"), "payload")
    source = _object(contract.get("source_contract"), "payload.source_contract")
    source.update(
        {
            "rolling_window_hours": window_hours,
            "overlap_hours": overlap_hours,
            "window_selection": "threads_created_or_updated_since_cutoff",
            "revisit_unresolved_items_outside_window": True,
            "require_full_pagination": True,
            "require_fresh_source_coverage_receipt": True,
            "source_coverage_schema_version": "artifact_recovery_source_coverage.v1",
            "required_source_policy": "every configured source must emit one fresh complete receipt",
            "chatgpt_scope": "all accessible authorized threads and chats in the rolling window",
        }
    )
    contract["schedule_contract"] = {
        "schema_version": "artifact_recovery_schedule.v2",
        "local_time": local_time,
        "timezone": timezone_name,
        "recovery_after_minutes": recovery_after_minutes,
        "max_lateness_minutes": max_lateness_minutes,
        "same_daily_idempotency_key_for_primary_and_watchdog": True,
        "silent_skip_forbidden": True,
    }
    contract["completion_contract"] = {
        "schema_version": "artifact_recovery_completion.v1",
        "terminal_job_status": "succeeded",
        "required_source_coverage_status": "complete",
        "require_every_actionable_item_dispositioned": True,
        "allowed_dispositions": [
            "complete",
            "already_landed",
            "in_review",
            "blocked_with_owner",
            "deferred_with_owner",
        ],
        "require_zero_unclassified_items": True,
        "require_zero_unowned_items": True,
        "require_zero_missing_evidence_items": True,
        "require_run_ledger": True,
        "require_artifact_manifest": True,
        "require_reconciliation_report": True,
        "work_complete_requires_resolvable_remote_evidence": True,
        "blocked_or_deferred_requires_owner_blocker_and_next_action": True,
        "email_delivery_required_when_recipient_configured": True,
        "email_recipient_environment_variable": "ARTIFACT_RECOVERY_REPORT_TO",
    }
    return payload


def extract_job(enqueue_result: Mapping[str, Any]) -> Mapping[str, Any]:
    response = _object(enqueue_result.get("response"), "enqueue response")
    job = _object(response.get("job"), "enqueue response.job")
    job_id = job.get("id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise NightlyRunError("enqueue response did not contain a job ID")
    return job


def validate_completion_job(job: Mapping[str, Any], expected_run_key: str) -> RunValidation:
    if job.get("status") != "succeeded":
        raise NightlyRunError("artifact-recovery job is not succeeded")
    result = _object(job.get("result"), "job.result")
    if result.get("schema_version") != "artifact_recovery_completion.v1":
        raise NightlyRunError("job.result has an unsupported completion schema")
    if result.get("run_key") != expected_run_key:
        raise NightlyRunError("job.result run_key does not match the scheduled run")
    if result.get("status") != "complete":
        raise NightlyRunError("job.result did not claim complete")

    coverage = _object(result.get("source_coverage"), "job.result.source_coverage")
    if coverage.get("schema_version") != "artifact_recovery_source_coverage.v1":
        raise NightlyRunError("source coverage has an unsupported schema")
    coverage_summary = _object(coverage.get("summary"), "source coverage summary")
    if coverage_summary.get("status") != "complete":
        raise NightlyRunError("required source coverage is not complete")
    _sha256(coverage.get("report_sha256"), "source coverage report_sha256")

    summary = _object(result.get("summary"), "job.result.summary")
    prompts_scanned = _bounded_int(summary.get("prompts_scanned"), "prompts_scanned")
    actionable_items = _bounded_int(summary.get("actionable_items"), "actionable_items")
    dispositions = _object(summary.get("dispositions"), "summary.dispositions")
    allowed = (
        "complete",
        "already_landed",
        "in_review",
        "blocked_with_owner",
        "deferred_with_owner",
    )
    dispositioned = sum(
        _bounded_int(dispositions.get(name, 0), f"dispositions.{name}")
        for name in allowed
    )
    if dispositioned != actionable_items:
        raise NightlyRunError("not every actionable item has an allowed disposition")
    for field in ("unclassified_items", "unowned_items", "missing_evidence_items"):
        if _bounded_int(summary.get(field), field) != 0:
            raise NightlyRunError(f"{field} must be zero")

    manifest = _object(result.get("artifact_manifest"), "job.result.artifact_manifest")
    if manifest.get("schema_version") != "artifact_recovery_manifest.v1":
        raise NightlyRunError("artifact manifest has an unsupported schema")
    manifest_sha256 = _sha256(manifest.get("manifest_sha256"), "manifest_sha256")
    artifacts = _object(manifest.get("artifacts"), "artifact_manifest.artifacts")
    for required in ("run_ledger", "source_coverage", "reconciliation_report"):
        artifact = _object(
            artifacts.get(required), f"artifact_manifest.artifacts.{required}"
        )
        _sha256(
            artifact.get("sha256"),
            f"artifact_manifest.artifacts.{required}.sha256",
        )
        locator = artifact.get("locator")
        if not isinstance(locator, str) or not locator.strip() or len(locator) > 512:
            raise NightlyRunError(
                f"artifact_manifest.artifacts.{required}.locator is invalid"
            )

    report_sha256 = _sha256(result.get("report_sha256"), "job.result.report_sha256")
    return RunValidation(
        run_key=expected_run_key,
        status="complete",
        prompts_scanned=prompts_scanned,
        actionable_items=actionable_items,
        dispositioned_items=dispositioned,
        source_coverage_status="complete",
        report_sha256=report_sha256,
        manifest_sha256=manifest_sha256,
    )


def _read_bounded(response: HTTPResponse) -> bytes:
    body = response.read(MAX_JOB_RESPONSE_BYTES + 1)
    if len(body) > MAX_JOB_RESPONSE_BYTES:
        raise NightlyRunError("coordinator job response exceeded the size limit")
    return body


def fetch_job(
    endpoint: str, token: str, job_id: str, timeout_seconds: float
) -> Mapping[str, Any]:
    if not token:
        raise NightlyRunError("coordinator token is empty")
    request = Request(
        f"{endpoint}/v1/jobs/{job_id}",
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "ai-agent-coordinator-artifact-recovery-verifier/1",
        },
    )
    opener = build_opener(scheduler.NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            body = _read_bounded(response)
            status = response.status
    except HTTPError as exc:
        exc.read(4096)
        raise NightlyRunError(
            f"coordinator job read returned HTTP {exc.code}"
        ) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise NightlyRunError("coordinator job read failed") from exc
    if status != 200:
        raise NightlyRunError(f"coordinator job read returned HTTP {status}")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise NightlyRunError("coordinator job response was not valid JSON") from exc
    return _object(
        parsed.get("job") if isinstance(parsed, dict) else None,
        "job response.job",
    )


def poll_terminal_job(
    fetch: Callable[[], Mapping[str, Any]],
    *,
    expected_run_key: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Mapping[str, Any], RunValidation]:
    if not 1 <= timeout_seconds <= 7_200:
        raise NightlyRunError("terminal-timeout-seconds must be between 1 and 7200")
    if not 0.1 <= poll_interval_seconds <= 300:
        raise NightlyRunError("poll-interval-seconds must be between 0.1 and 300")
    deadline = monotonic() + timeout_seconds
    last_status = "unknown"
    while True:
        job = fetch()
        status = job.get("status")
        if not isinstance(status, str):
            raise NightlyRunError("job status is missing or invalid")
        last_status = status
        if status in TERMINAL_STATUSES:
            if status != "succeeded":
                error_class = "present" if job.get("last_error") else "absent"
                raise NightlyRunError(
                    f"artifact-recovery job ended as {status}; "
                    f"bounded error record {error_class}"
                )
            return job, validate_completion_job(job, expected_run_key)
        if status not in {"queued", "running"}:
            raise NightlyRunError(f"unsupported job status: {status}")
        observed = monotonic()
        if observed >= deadline:
            raise NightlyRunError(
                "artifact-recovery job did not become terminal; "
                f"last status {last_status}"
            )
        sleep(min(poll_interval_seconds, max(0.0, deadline - observed)))


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def write_receipt(path: str | None, receipt: Mapping[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(receipt)
    payload["receipt_sha256"] = _canonical_digest(payload)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint")
    parser.add_argument("--endpoint-env", default=scheduler.DEFAULT_ENDPOINT_ENV)
    parser.add_argument("--token-env", default=scheduler.DEFAULT_TOKEN_ENV)
    parser.add_argument("--enabled-env", default="ARTIFACT_RECOVERY_ENQUEUE_ENABLED")
    parser.add_argument(
        "--timezone",
        default=os.getenv("ARTIFACT_RECOVERY_TIMEZONE", scheduler.DEFAULT_TIMEZONE),
    )
    parser.add_argument(
        "--local-time",
        default=os.getenv("ARTIFACT_RECOVERY_LOCAL_TIME", DEFAULT_LOCAL_TIME),
    )
    parser.add_argument(
        "--recovery-after-minutes",
        type=int,
        default=int(
            os.getenv(
                "ARTIFACT_RECOVERY_RECOVERY_MINUTES",
                str(DEFAULT_RECOVERY_AFTER_MINUTES),
            )
        ),
    )
    parser.add_argument(
        "--max-lateness-minutes",
        type=int,
        default=int(
            os.getenv(
                "ARTIFACT_RECOVERY_MAX_LATENESS_MINUTES",
                str(DEFAULT_MAX_LATENESS_MINUTES),
            )
        ),
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=int(
            os.getenv("ARTIFACT_RECOVERY_WINDOW_HOURS", str(DEFAULT_WINDOW_HOURS))
        ),
    )
    parser.add_argument(
        "--overlap-hours",
        type=int,
        default=int(
            os.getenv("ARTIFACT_RECOVERY_OVERLAP_HOURS", str(DEFAULT_OVERLAP_HOURS))
        ),
    )
    parser.add_argument(
        "--cli-task-id",
        default=os.getenv(
            "ARTIFACT_RECOVERY_CLI_TASK_ID", scheduler.DEFAULT_CLI_TASK_ID
        ),
    )
    parser.add_argument("--now")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--manual-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--request-timeout-seconds", type=float, default=15.0)
    parser.add_argument(
        "--terminal-timeout-seconds",
        type=float,
        default=DEFAULT_TERMINAL_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    parser.add_argument("--evidence-output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipt: dict[str, Any] = {
        "schema_version": "artifact_recovery_schedule_receipt.v1",
        "status": "failed",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        endpoint_env = scheduler.validate_env_name(args.endpoint_env)
        token_env = scheduler.validate_env_name(args.token_env)
        enabled_env = scheduler.validate_env_name(args.enabled_env)
        endpoint = scheduler.validate_endpoint(
            args.endpoint or os.getenv(endpoint_env, "")
        )
        now = scheduler.parse_instant(args.now)
        decision = robust_schedule_decision(
            now,
            timezone_name=args.timezone,
            local_time=args.local_time,
            recovery_after_minutes=args.recovery_after_minutes,
            max_lateness_minutes=args.max_lateness_minutes,
            force=args.force,
            manual_id=args.manual_id,
        )
        receipt.update(
            {
                "run_key": decision.run_key,
                "scheduled_for": decision.scheduled_for.isoformat(),
                "observed_local_time": decision.local_time.isoformat(),
                "recovered": decision.recovered,
            }
        )
        if not decision.due:
            raise NightlyRunError(
                "scheduled invocation fell outside the bounded lateness window"
            )
        task_id = scheduler.validate_task_id(args.cli_task_id)
        payload = build_hardened_payload(
            decision,
            timezone_name=args.timezone,
            local_time=args.local_time,
            recovery_after_minutes=args.recovery_after_minutes,
            max_lateness_minutes=args.max_lateness_minutes,
            window_hours=args.window_hours,
            overlap_hours=args.overlap_hours,
            cli_task_id=task_id,
        )
        if args.dry_run:
            plan = scheduler.redacted_plan(endpoint, decision, payload)
            plan["status"] = "dry_run_hardened"
            plan["completion_verification"] = (
                "terminal receipt required in live mode"
            )
            receipt.update(
                {"status": "dry_run", "plan_sha256": _canonical_digest(plan)}
            )
            write_receipt(args.evidence_output, receipt)
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0

        require_enabled(enabled_env)
        token = os.getenv(token_env, "")
        enqueue_result = scheduler.enqueue(
            endpoint,
            token,
            decision,
            payload,
            args.request_timeout_seconds,
        )
        initial_job = extract_job(enqueue_result)
        job_id = str(initial_job["id"])
        receipt["job_id"] = job_id
        job, validation = poll_terminal_job(
            lambda: fetch_job(
                endpoint, token, job_id, args.request_timeout_seconds
            ),
            expected_run_key=decision.run_key,
            timeout_seconds=args.terminal_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        receipt.update(
            {
                "status": "complete",
                "job_status": job.get("status"),
                "attempts": job.get("attempts"),
                "updated_at": job.get("updated_at"),
                "validation": validation.as_dict(),
            }
        )
        write_receipt(args.evidence_output, receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0
    except (NightlyRunError, scheduler.SchedulerError) as exc:
        receipt["error_class"] = exc.__class__.__name__
        receipt["error"] = str(exc)
        write_receipt(args.evidence_output, receipt)
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
