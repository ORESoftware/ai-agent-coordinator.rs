#!/usr/bin/env python3
"""Enqueue deterministic 50-day artifact-recovery work in three-minute buckets.

This program is intentionally enqueue-only. Durable workers claim jobs through the
coordinator's lease protocol; repeated invocations in the same bucket converge on
one idempotency key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import enqueue_artifact_recovery as scheduler

INTERVAL_SECONDS = 180
LOOKBACK_HOURS = 50 * 24
OVERLAP_HOURS = 6
MAX_WORKERS = 3
RUN_KEY_PREFIX = "continuous-50-day-reconciliation"
CANONICAL_ISSUE = "DEN-3179"
RELATED_ISSUES = ("DEN-2797", "DEN-3180", "DEN-2190", "DEN-3474")


class ContinuousRecoveryError(RuntimeError):
    """A bounded, public-safe continuous-recovery configuration failure."""


def _bounded_int(value: int, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ContinuousRecoveryError(f"{name} must be between {minimum} and {maximum}")
    return value


def validate_settings(
    *, interval_seconds: int, lookback_hours: int, overlap_hours: int, max_workers: int
) -> None:
    _bounded_int(interval_seconds, "interval-seconds", 60, 3600)
    _bounded_int(lookback_hours, "lookback-hours", 24, LOOKBACK_HOURS)
    _bounded_int(overlap_hours, "overlap-hours", 0, 48)
    _bounded_int(max_workers, "max-workers", 1, MAX_WORKERS)
    if overlap_hours >= lookback_hours:
        raise ContinuousRecoveryError("overlap-hours must be smaller than lookback-hours")


def floor_bucket(now: datetime, interval_seconds: int = INTERVAL_SECONDS) -> datetime:
    if now.tzinfo is None:
        raise ContinuousRecoveryError("bucket time must be timezone-aware")
    _bounded_int(interval_seconds, "interval-seconds", 60, 3600)
    timestamp = int(now.astimezone(timezone.utc).timestamp())
    bucket = timestamp - (timestamp % interval_seconds)
    return datetime.fromtimestamp(bucket, tz=timezone.utc)


def bucket_key(bucket: datetime) -> str:
    if bucket.tzinfo is None:
        raise ContinuousRecoveryError("bucket time must be timezone-aware")
    stamp = bucket.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{RUN_KEY_PREFIX}:scheduled:{stamp}"


def schedule_decision(
    now: datetime,
    *,
    interval_seconds: int = INTERVAL_SECONDS,
    manual_id: str | None = None,
) -> scheduler.ScheduleDecision:
    bucket = floor_bucket(now, interval_seconds)
    scheduled_key = bucket_key(bucket)
    if manual_id is None:
        return scheduler.ScheduleDecision(
            due=True,
            kind="scheduled",
            local_time=now.astimezone(timezone.utc),
            scheduled_for=bucket,
            run_key=scheduled_key,
            scheduled_run_key=scheduled_key,
            recovered=False,
            manual_id=None,
        )
    identifier = scheduler.validate_manual_id(manual_id)
    return scheduler.ScheduleDecision(
        due=True,
        kind="manual",
        local_time=now.astimezone(timezone.utc),
        scheduled_for=bucket,
        run_key=f"{RUN_KEY_PREFIX}:manual:{bucket.strftime('%Y%m%dT%H%M%SZ')}:{identifier}",
        scheduled_run_key=scheduled_key,
        recovered=False,
        manual_id=identifier,
    )


def build_payload(
    decision: scheduler.ScheduleDecision,
    *,
    interval_seconds: int = INTERVAL_SECONDS,
    lookback_hours: int = LOOKBACK_HOURS,
    overlap_hours: int = OVERLAP_HOURS,
    max_workers: int = MAX_WORKERS,
    cli_task_id: str = scheduler.DEFAULT_CLI_TASK_ID,
) -> dict[str, Any]:
    validate_settings(
        interval_seconds=interval_seconds,
        lookback_hours=lookback_hours,
        overlap_hours=overlap_hours,
        max_workers=max_workers,
    )
    task_id = scheduler.validate_task_id(cli_task_id)
    payload = scheduler.build_payload(
        decision,
        timezone_name="UTC",
        local_time=decision.scheduled_for.strftime("%H:%M"),
        recovery_minutes=max(1, interval_seconds // 60),
        cli_task_id=task_id,
    )
    payload["priority"] = 90
    payload["max_attempts"] = 3
    payload["budget_usd"] = 12.0
    contract = payload["payload"]
    contract["tracking"].update(
        {
            "canonical_issue": CANONICAL_ISSUE,
            "related_issues": list(RELATED_ISSUES),
            "policy_repository": "ORESoftware/my-ai",
            "policy_path": "AGENTS.md",
        }
    )
    contract["run"].update(
        {
            "interval_seconds": interval_seconds,
            "bucket_started_at": decision.scheduled_for.isoformat(),
        }
    )
    contract["source_contract"].update(
        {
            "rolling_window_hours": lookback_hours,
            "overlap_hours": overlap_hours,
            "window_selection": "threads_or_tasks_created_or_updated_since_cutoff",
            "revisit_unresolved_items_outside_window": True,
            "require_full_pagination": True,
            "require_fresh_source_coverage_receipt": True,
            "scan_accessible_authorized_codex_tasks": True,
            "worker_concurrency_limit": max_workers,
        }
    )
    contract["ledger_contract"].update(
        {
            "current_truth_sources": [
                "linear_issues",
                "github_issues",
                "github_commits",
                "github_branches",
                "github_pull_requests",
            ],
            "skip_archived_cancelled_duplicate_superseded_or_outmoded": True,
            "never_reanimate_closed_superseded_work": True,
            "require_current_remote_read_before_every_mutation": True,
            "require_optimistic_concurrency_receipt": True,
        }
    )
    contract["delivery_contract"].update(
        {
            "feature_branch_only": True,
            "independent_review_required": True,
            "exact_head_checks_required": True,
            "merge_and_deploy_require_separate_reviewed_gate": True,
        }
    )
    mandatory_forbidden = {
        "reuse_chat_pasted_credentials",
        "store_pat_or_token",
        "revoke_or_rotate_credentials",
        "force_push",
        "direct_default_branch_write",
        "bypass_protection",
        "auto_merge",
        "claim_delivery_without_remote_evidence",
        "revive_archived_cancelled_duplicate_or_superseded_work",
    }
    contract["forbidden_actions"] = sorted(
        set(contract.get("forbidden_actions", [])) | mandatory_forbidden
    )
    return payload


def redacted_plan(endpoint: str, decision: scheduler.ScheduleDecision, payload: Mapping[str, Any]) -> dict[str, Any]:
    plan = scheduler.redacted_plan(endpoint, decision, dict(payload))
    plan["status"] = "dry_run_continuous_50_day_reconciliation"
    plan["interval_seconds"] = payload["payload"]["run"]["interval_seconds"]
    plan["worker_concurrency_limit"] = payload["payload"]["source_contract"]["worker_concurrency_limit"]
    return plan


def run_once(args: argparse.Namespace, now: datetime | None = None) -> dict[str, Any]:
    observed = now or scheduler.parse_instant(args.now)
    decision = schedule_decision(
        observed,
        interval_seconds=args.interval_seconds,
        manual_id=args.manual_id,
    )
    endpoint_env = scheduler.validate_env_name(args.endpoint_env)
    token_env = scheduler.validate_env_name(args.token_env)
    endpoint = scheduler.validate_endpoint(args.endpoint or os.getenv(endpoint_env, ""))
    payload = build_payload(
        decision,
        interval_seconds=args.interval_seconds,
        lookback_hours=args.lookback_hours,
        overlap_hours=args.overlap_hours,
        max_workers=args.max_workers,
        cli_task_id=args.cli_task_id,
    )
    if args.dry_run:
        return redacted_plan(endpoint, decision, payload)
    result = scheduler.enqueue(
        endpoint,
        os.getenv(token_env, ""),
        decision,
        payload,
        args.timeout_seconds,
    )
    job = result["response"].get("job")
    job_id = job.get("id") if isinstance(job, dict) else None
    job_status = job.get("status") if isinstance(job, dict) else None
    return {
        "schema_version": "continuous_artifact_recovery_enqueue.v1",
        "status": result["status"],
        "run_key": decision.run_key,
        "scheduled_for": decision.scheduled_for.isoformat(),
        "coordinator_status": result["coordinator_status"],
        "job_id": job_id,
        "job_status": job_status,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint")
    parser.add_argument("--endpoint-env", default=scheduler.DEFAULT_ENDPOINT_ENV)
    parser.add_argument("--token-env", default=scheduler.DEFAULT_TOKEN_ENV)
    parser.add_argument("--cli-task-id", default=os.getenv("ARTIFACT_RECOVERY_CLI_TASK_ID", scheduler.DEFAULT_CLI_TASK_ID))
    parser.add_argument("--interval-seconds", type=int, default=int(os.getenv("CONTINUOUS_RECOVERY_INTERVAL_SECONDS", str(INTERVAL_SECONDS))))
    parser.add_argument("--lookback-hours", type=int, default=int(os.getenv("CONTINUOUS_RECOVERY_LOOKBACK_HOURS", str(LOOKBACK_HOURS))))
    parser.add_argument("--overlap-hours", type=int, default=int(os.getenv("CONTINUOUS_RECOVERY_OVERLAP_HOURS", str(OVERLAP_HOURS))))
    parser.add_argument("--max-workers", type=int, default=int(os.getenv("CONTINUOUS_RECOVERY_MAX_WORKERS", str(MAX_WORKERS))))
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--now")
    parser.add_argument("--manual-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--loop", action="store_true", help="run continuously, aligning each enqueue to the next bucket")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        validate_settings(
            interval_seconds=args.interval_seconds,
            lookback_hours=args.lookback_hours,
            overlap_hours=args.overlap_hours,
            max_workers=args.max_workers,
        )
        if not 0 < args.timeout_seconds <= 60:
            raise ContinuousRecoveryError("timeout-seconds must be greater than 0 and at most 60")
        if args.loop and args.now:
            raise ContinuousRecoveryError("--now cannot be combined with --loop")
        while True:
            result = run_once(args)
            print(json.dumps(result, indent=2, sort_keys=True), flush=True)
            if not args.loop:
                return 0
            now = datetime.now(timezone.utc)
            next_bucket = floor_bucket(now, args.interval_seconds).timestamp() + args.interval_seconds
            time.sleep(max(1.0, next_bucket - now.timestamp()))
    except (ContinuousRecoveryError, scheduler.SchedulerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
