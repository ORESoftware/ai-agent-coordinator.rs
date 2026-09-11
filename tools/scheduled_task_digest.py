#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scheduled_task_digest_core import *  # noqa: F401,F403
from scheduled_task_digest_scan import *  # noqa: F401,F403
from scheduled_task_digest_delivery import *  # noqa: F401,F403
from scheduled_task_digest_delivery import _send_sendgrid  # re-exported for focused tests


def _same_day_catchup(
    value: ScheduleDecision,
    *,
    local_time: str,
) -> tuple[ScheduleDecision, bool]:
    """Recover a delayed scheduled event without crossing a local-date boundary."""
    if value.due:
        return value, False
    hour, minute = parse_local_time(local_time)
    scheduled = value.local_time.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if value.local_time < scheduled:
        return value, False
    return (
        ScheduleDecision(
            due=True,
            local_time=value.local_time,
            logical_date=value.logical_date,
            run_key=value.run_key,
            artifact_name=value.artifact_name,
        ),
        True,
    )


def _decision(args: argparse.Namespace) -> int:
    now = parse_instant(args.now) if args.now else datetime.now(timezone.utc)
    value = schedule_decision(
        now,
        timezone_name=args.timezone,
        local_time=args.local_time,
        force=args.force,
    )
    catchup = False
    if args.same_day_catchup and not args.force:
        value, catchup = _same_day_catchup(value, local_time=args.local_time)
    payload = {
        "due": value.due,
        "catchup": catchup,
        "local_time": value.local_time.isoformat(),
        "logical_date": value.logical_date,
        "run_key": value.run_key,
        "artifact_name": value.artifact_name,
    }
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as stream:
            for key in (
                "due",
                "catchup",
                "logical_date",
                "run_key",
                "artifact_name",
            ):
                item = payload[key]
                stream.write(
                    f"{key}={str(item).lower() if isinstance(item, bool) else item}\n"
                )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _run(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    now = parse_instant(args.now) if args.now else datetime.now(timezone.utc)
    decision = schedule_decision(
        now,
        timezone_name=str(config.get("timezone") or DEFAULT_TIMEZONE),
        local_time=str(config.get("delivery_local_time") or DEFAULT_LOCAL_TIME),
        force=args.force,
    )
    if not decision.due and not args.ignore_schedule_gate:
        print(
            json.dumps(
                {
                    "status": "not_due",
                    "logical_date": decision.logical_date,
                    "local_time": decision.local_time.isoformat(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    token = str(
        args.github_token
        or os.environ.get("SCHEDULE_DIGEST_GH_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    )
    client = GitHubClient(
        token,
        api_url=str(config.get("github_api_url") or DEFAULT_API_URL),
        timeout=args.timeout,
    )
    repository = str(
        args.repository
        or os.environ.get("GITHUB_REPOSITORY")
        or "ORESoftware/ai-agent-coordinator.rs"
    )
    if (
        args.delivery_mode == "send"
        and not args.force
        and client.receipt_artifacts(repository, decision.artifact_name)
    ):
        print(
            json.dumps(
                {
                    "status": "already_delivered",
                    "logical_date": decision.logical_date,
                    "artifact_name": decision.artifact_name,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    run_id = 0
    try:
        run_id = int(os.environ.get("GITHUB_RUN_ID") or 0)
    except ValueError:
        pass
    digest = collect_digest(
        config,
        client,
        now=now,
        exclude_repository=repository,
        exclude_run_id=run_id,
    )
    plain, rich = render_plain_text(digest), render_html(digest)
    output_dir = Path(args.output_dir)
    write_outputs(output_dir, digest=digest, plain_text=plain, html_text=rich)
    if args.delivery_mode == "stdout":
        print(plain)
        write_receipt(
            output_dir,
            decision=decision,
            digest=digest,
            delivery={"provider": "stdout", "accepted": False, "dry_run": True},
            recipient=str(config["recipient"]),
            sent_at=now,
        )
        return 0
    try:
        delivery = deliver_digest(
            recipient=str(config["recipient"]),
            subject=digest_subject(digest, decision.logical_date),
            plain_text=plain,
            html_text=rich,
        )
    except DigestError as error:
        failure = {
            "schema_version": RECEIPT_SCHEMA,
            "run_key": decision.run_key,
            "logical_date": decision.logical_date,
            "artifact_name": decision.artifact_name,
            "recipient": config["recipient"],
            "digest_sha256": digest["digest_sha256"],
            "sent_at": None,
            "delivery": {"accepted": False, "error": str(error)},
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "delivery-receipt.json").write_text(
            json.dumps(failure, indent=2, sort_keys=True) + "\n"
        )
        raise
    receipt = write_receipt(
        output_dir,
        decision=decision,
        digest=digest,
        delivery=delivery,
        recipient=str(config["recipient"]),
        sent_at=datetime.now(timezone.utc),
    )
    print(
        json.dumps(
            {
                "status": "delivered",
                "logical_date": decision.logical_date,
                "recipient": config["recipient"],
                "provider": receipt["delivery"].get("provider"),
                "digest_sha256": digest["digest_sha256"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect and deliver one 24-hour scheduled-task digest."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    decision = sub.add_parser("decision")
    decision.add_argument("--now", default="")
    decision.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    decision.add_argument("--local-time", default=DEFAULT_LOCAL_TIME)
    decision.add_argument("--force", action="store_true")
    decision.add_argument(
        "--same-day-catchup",
        action="store_true",
        help="Treat a delayed event later on the intended local date as due.",
    )
    decision.add_argument("--github-output", default="")
    decision.set_defaults(handler=_decision)
    run = sub.add_parser("run")
    run.add_argument("--config", default="config/scheduled-task-digest.json")
    run.add_argument("--now", default="")
    run.add_argument("--output-dir", default="artifacts/scheduled-task-digest")
    run.add_argument("--delivery-mode", choices=("stdout", "send"), default="stdout")
    run.add_argument("--github-token", default="")
    run.add_argument("--repository", default="")
    run.add_argument("--timeout", type=float, default=30.0)
    run.add_argument("--force", action="store_true")
    run.add_argument("--ignore-schedule-gate", action="store_true")
    run.set_defaults(handler=_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except DigestError as error:
        print(f"scheduled-task-digest: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
