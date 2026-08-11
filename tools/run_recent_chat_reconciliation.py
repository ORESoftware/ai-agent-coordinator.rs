#!/usr/bin/env python3
"""Run the dedicated recent-chat reconciliation with a namespaced run key."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import enqueue_artifact_recovery as scheduler
import run_nightly_artifact_recovery as generic

DEFAULT_TIMEZONE = "America/Lima"
DEFAULT_LOCAL_TIME = "00:30"
DEFAULT_RUN_KEY_PREFIX = "recent-chat-reconciliation"
DEFAULT_JOB_NAME = "Recent-96-hours ChatGPT introspection and reconciliation"
PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def validate_prefix(value: str) -> str:
    if not PREFIX_RE.fullmatch(value):
        raise generic.NightlyRunError(
            "run-key-prefix must be 1-80 lowercase letters, digits, or hyphens"
        )
    return value


def validate_job_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 160 or any(ord(char) < 32 for char in cleaned):
        raise generic.NightlyRunError("job-name must be bounded printable text")
    return cleaned


def namespace_decision(
    decision: scheduler.ScheduleDecision, prefix: str
) -> scheduler.ScheduleDecision:
    date = decision.scheduled_for.date().isoformat()
    scheduled_key = f"{prefix}:scheduled:{date}"
    run_key = scheduled_key
    if decision.kind == "manual":
        run_key = f"{prefix}:manual:{date}:{decision.manual_id}"
    return replace(
        decision,
        run_key=run_key,
        scheduled_run_key=scheduled_key,
    )


def build_recent_payload(
    decision: scheduler.ScheduleDecision,
    *,
    timezone_name: str,
    local_time: str,
    recovery_after_minutes: int,
    max_lateness_minutes: int,
    window_hours: int,
    overlap_hours: int,
    cli_task_id: str,
    job_name: str,
) -> dict[str, Any]:
    payload = generic.build_hardened_payload(
        decision,
        timezone_name=timezone_name,
        local_time=local_time,
        recovery_after_minutes=recovery_after_minutes,
        max_lateness_minutes=max_lateness_minutes,
        window_hours=window_hours,
        overlap_hours=overlap_hours,
        cli_task_id=cli_task_id,
    )
    contract = generic._object(payload.get("payload"), "payload")
    contract["job_contract"] = {
        "schema_version": "recent_chat_reconciliation.v1",
        "job_name": job_name,
        "canonical_issue": "DEN-2797",
        "runtime_blocker": "DEN-3474",
        "native_chatgpt_task_control_plane_is_separate": True,
        "require_every_accessible_authorized_chatgpt_thread": True,
        "require_durable_source_cursor": True,
        "require_duplicate_free_mutations": True,
    }
    source = generic._object(contract.get("source_contract"), "source_contract")
    source["primary_source"] = "chatgpt_threads"
    source["lookback_policy"] = "rolling_96_hours_plus_unresolved_backlog"
    return payload


def build_parser():
    parser = generic.build_parser()
    parser.description = __doc__
    parser.set_defaults(
        timezone=DEFAULT_TIMEZONE,
        local_time=DEFAULT_LOCAL_TIME,
        window_hours=96,
        overlap_hours=6,
    )
    parser.add_argument(
        "--run-key-prefix",
        default=os.getenv(
            "RECENT_CHAT_RECONCILIATION_RUN_KEY_PREFIX", DEFAULT_RUN_KEY_PREFIX
        ),
    )
    parser.add_argument(
        "--job-name",
        default=os.getenv("RECENT_CHAT_RECONCILIATION_JOB_NAME", DEFAULT_JOB_NAME),
    )
    return parser


def _completion_summary(job: Mapping[str, Any]) -> dict[str, Any]:
    result = generic._object(job.get("result"), "job.result")
    summary = generic._object(result.get("summary"), "job.result.summary")
    dispositions = generic._object(summary.get("dispositions"), "summary.dispositions")
    allowed = (
        "complete",
        "already_landed",
        "in_review",
        "blocked_with_owner",
        "deferred_with_owner",
    )
    counts = {
        name: generic._bounded_int(dispositions.get(name, 0), name) for name in allowed
    }
    unfinished = (
        counts["in_review"]
        + counts["blocked_with_owner"]
        + counts["deferred_with_owner"]
    )
    return {
        "prompts_scanned": generic._bounded_int(
            summary.get("prompts_scanned"), "prompts_scanned"
        ),
        "actionable_items": generic._bounded_int(
            summary.get("actionable_items"), "actionable_items"
        ),
        "dispositions": counts,
        "unfinished_items": unfinished,
        "all_work_complete": unfinished == 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipt: dict[str, Any] = {
        "schema_version": "recent_chat_reconciliation_receipt.v1",
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
        prefix = validate_prefix(args.run_key_prefix)
        job_name = validate_job_name(args.job_name)
        decision = generic.robust_schedule_decision(
            scheduler.parse_instant(args.now),
            timezone_name=args.timezone,
            local_time=args.local_time,
            recovery_after_minutes=args.recovery_after_minutes,
            max_lateness_minutes=args.max_lateness_minutes,
            force=args.force,
            manual_id=args.manual_id,
        )
        decision = namespace_decision(decision, prefix)
        receipt.update(
            {
                "run_key": decision.run_key,
                "scheduled_for": decision.scheduled_for.isoformat(),
                "observed_local_time": decision.local_time.isoformat(),
                "recovered": decision.recovered,
                "job_name": job_name,
            }
        )
        if not decision.due:
            raise generic.NightlyRunError(
                "scheduled invocation fell outside the bounded lateness window"
            )
        task_id = scheduler.validate_task_id(args.cli_task_id)
        payload = build_recent_payload(
            decision,
            timezone_name=args.timezone,
            local_time=args.local_time,
            recovery_after_minutes=args.recovery_after_minutes,
            max_lateness_minutes=args.max_lateness_minutes,
            window_hours=args.window_hours,
            overlap_hours=args.overlap_hours,
            cli_task_id=task_id,
            job_name=job_name,
        )
        if args.dry_run:
            plan = scheduler.redacted_plan(endpoint, decision, payload)
            plan["status"] = "dry_run_recent_chat_reconciliation"
            plan["completion_verification"] = "terminal receipt required in live mode"
            receipt.update(
                {"status": "dry_run", "plan_sha256": generic._canonical_digest(plan)}
            )
            generic.write_receipt(args.evidence_output, receipt)
            print(json.dumps(plan, indent=2, sort_keys=True))
            return 0

        generic.require_enabled(enabled_env)
        token = os.getenv(token_env, "")
        enqueue_result = scheduler.enqueue(
            endpoint, token, decision, payload, args.request_timeout_seconds
        )
        initial_job = generic.extract_job(enqueue_result)
        job_id = str(initial_job["id"])
        receipt["job_id"] = job_id
        job, validation = generic.poll_terminal_job(
            lambda: generic.fetch_job(
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
                "completion_summary": _completion_summary(job),
            }
        )
        generic.write_receipt(args.evidence_output, receipt)
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0
    except (generic.NightlyRunError, scheduler.SchedulerError) as exc:
        receipt["error_class"] = exc.__class__.__name__
        receipt["error"] = str(exc)
        generic.write_receipt(args.evidence_output, receipt)
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
