#!/usr/bin/env python3
"""Certify a nightly ChatGPT reconciliation from workflow and receipt evidence."""

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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

API_ROOT = "https://api.github.com"
DEFAULT_REPOSITORY = "ORESoftware/ai-agent-coordinator.rs"
DEFAULT_WORKFLOW = "nightly-artifact-recovery.yml"
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_LOCAL_TIME = "00:37"
DEFAULT_MAX_DELAY = 240
DEFAULT_SETTLEMENT = 310
DEFAULT_JOB = "enqueue-and-verify"
DEFAULT_ARTIFACT_PREFIX = "artifact-recovery-terminal-"
MAX_BYTES = 8 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
WORKFLOW_RE = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")
TIME_RE = re.compile(r"^(?P<h>[01]\d|2[0-3]):(?P<m>[0-5]\d)$")
OUTPUT_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class WatchdogError(RuntimeError):
    pass


@dataclass(frozen=True)
class WatchdogPolicy:
    repository: str
    workflow: str
    timezone_name: str
    local_time: str
    max_dispatch_delay_minutes: int
    settlement_minutes: int
    execution_job: str
    artifact_prefix: str
    expected_branch: str


@dataclass(frozen=True)
class LogicalNight:
    logical_date: date
    scheduled_at: datetime
    latest_dispatch_at: datetime
    settlement_at: datetime
    expected_run_key: str


def obj(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WatchdogError(f"{field} must be an object")
    return value


def objects(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise WatchdogError(f"{field} must be an array of objects")
    return value


def positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WatchdogError(f"{field} must be a positive integer")
    return value


def non_negative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WatchdogError(f"{field} must be a non-negative integer")
    return value


def text(value: Any, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise WatchdogError(f"{field} must be a non-empty bounded string")
    return value


def digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise WatchdogError(f"{field} must be a lowercase SHA-256 digest")
    return value


def parse_instant(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    value = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise WatchdogError("timestamp must use ISO-8601") from exc
    if parsed.tzinfo is None:
        raise WatchdogError("timestamp must include an offset")
    return parsed.astimezone(timezone.utc)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise WatchdogError(f"unknown timezone: {name}") from exc


def local_time(value: str) -> tuple[int, int]:
    match = TIME_RE.fullmatch(value)
    if not match:
        raise WatchdogError("local time must use HH:MM")
    return int(match.group("h")), int(match.group("m"))


def canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class GitHubClient:
    def __init__(self, token: str, timeout: float = 15.0) -> None:
        if not token.strip():
            raise WatchdogError("GITHUB_TOKEN is required for live reads")
        if not 0 < timeout <= 60:
            raise WatchdogError("timeout must be in (0, 60]")
        self.token, self.timeout = token.strip(), timeout

    def get(self, path: str) -> dict[str, Any]:
        request = Request(
            API_ROOT + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "ai-agent-coordinator-nightly-watchdog/1",
            },
        )
        last: Exception | None = None
        for attempt in range(4):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read(MAX_BYTES + 1)
                    status = response.status
                if len(body) > MAX_BYTES:
                    raise WatchdogError("GitHub response exceeded size limit")
                if not 200 <= status < 300:
                    raise WatchdogError(f"GitHub returned HTTP {status}")
                return obj(json.loads(body) if body else {}, "GitHub response")
            except HTTPError as exc:
                exc.read(4096)
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                    raise WatchdogError(f"GitHub returned HTTP {exc.code}") from exc
                last = exc
            except (URLError, TimeoutError, socket.timeout) as exc:
                if attempt == 3:
                    raise WatchdogError("GitHub request failed") from exc
                last = exc
            time.sleep(0.25 * (2**attempt))
        raise WatchdogError("GitHub request failed") from last

    def list_runs(self, repository: str, workflow: str) -> list[dict[str, Any]]:
        return objects(
            self.get(
                f"/repos/{repository}/actions/workflows/{quote(workflow, safe='')}/runs"
                "?event=schedule&per_page=50"
            ).get("workflow_runs"),
            "workflow_runs",
        )

    def list_jobs(self, repository: str, run_id: int) -> list[dict[str, Any]]:
        return objects(
            self.get(
                f"/repos/{repository}/actions/runs/{positive(run_id, 'run_id')}/jobs"
                "?filter=all&per_page=100"
            ).get("jobs"),
            "jobs",
        )

    def list_artifacts(self, repository: str, run_id: int) -> list[dict[str, Any]]:
        return objects(
            self.get(
                f"/repos/{repository}/actions/runs/{positive(run_id, 'run_id')}/artifacts"
                "?per_page=100"
            ).get("artifacts"),
            "artifacts",
        )


class SnapshotClient:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.runs = objects(payload.get("workflow_runs"), "workflow_runs")
        self.jobs = obj(payload.get("jobs_by_run"), "jobs_by_run")
        self.artifacts = obj(payload.get("artifacts_by_run"), "artifacts_by_run")

    def list_runs(self, repository: str, workflow: str) -> list[dict[str, Any]]:
        return list(self.runs)

    def list_jobs(self, repository: str, run_id: int) -> list[dict[str, Any]]:
        return objects(self.jobs.get(str(run_id), []), f"jobs_by_run.{run_id}")

    def list_artifacts(self, repository: str, run_id: int) -> list[dict[str, Any]]:
        return objects(self.artifacts.get(str(run_id), []), f"artifacts_by_run.{run_id}")


def build_policy(args: argparse.Namespace) -> WatchdogPolicy:
    if not REPO_RE.fullmatch(args.repository):
        raise WatchdogError("repository must use owner/name")
    if not WORKFLOW_RE.fullmatch(args.workflow):
        raise WatchdogError("workflow must be a YAML filename")
    zone(args.timezone)
    local_time(args.local_time)
    if not 1 <= args.max_dispatch_delay_minutes <= 720:
        raise WatchdogError("max dispatch delay must be 1..720")
    if not args.max_dispatch_delay_minutes <= args.settlement_minutes <= 1440:
        raise WatchdogError("settlement must follow dispatch window and be <=1440")
    return WatchdogPolicy(
        args.repository,
        args.workflow,
        args.timezone,
        args.local_time,
        args.max_dispatch_delay_minutes,
        args.settlement_minutes,
        text(args.execution_job, "execution job", 100),
        text(args.artifact_prefix, "artifact prefix", 100),
        text(args.expected_branch, "expected branch", 255),
    )


def logical_night(
    now: datetime, policy: WatchdogPolicy, explicit_date: str | None = None
) -> LogicalNight:
    z = zone(policy.timezone_name)
    local_now = now.astimezone(z)
    hour, minute = local_time(policy.local_time)
    if explicit_date:
        try:
            logical_date = date.fromisoformat(explicit_date)
        except ValueError as exc:
            raise WatchdogError("logical date must use YYYY-MM-DD") from exc
    else:
        today = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        logical_date = (
            local_now.date()
            if local_now >= today + timedelta(minutes=policy.settlement_minutes)
            else local_now.date() - timedelta(days=1)
        )
    scheduled = datetime(
        logical_date.year, logical_date.month, logical_date.day, hour, minute, tzinfo=z
    )
    return LogicalNight(
        logical_date,
        scheduled,
        scheduled + timedelta(minutes=policy.max_dispatch_delay_minutes),
        scheduled + timedelta(minutes=policy.settlement_minutes),
        f"artifact-recovery:scheduled:{logical_date.isoformat()}",
    )


def started_at(run: Mapping[str, Any]) -> datetime:
    value = run.get("run_started_at") or run.get("created_at")
    return parse_instant(text(value, "run timestamp", 64))


def unique_named(items: Sequence[Mapping[str, Any]], name: str, kind: str):
    matches = [item for item in items if item.get("name") == name]
    if len(matches) > 1:
        raise WatchdogError(f"duplicate {kind} {name!r}")
    return matches[0] if matches else None


def evaluate_attempt(
    run: Mapping[str, Any],
    jobs: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    *,
    policy: WatchdogPolicy,
    night: LogicalNight,
) -> dict[str, Any]:
    run_id = positive(run.get("id"), "run id")
    attempt = positive(run.get("run_attempt", 1), "run attempt")
    started = started_at(run)
    local_started = started.astimezone(zone(policy.timezone_name))
    delay = int((local_started - night.scheduled_at).total_seconds() // 60)
    artifact_name = f"{policy.artifact_prefix}{run_id}-{attempt}"
    job = unique_named(jobs, policy.execution_job, "job")
    artifact = unique_named(artifacts, artifact_name, "artifact")
    reasons: list[str] = []
    if run.get("event") != "schedule":
        reasons.append("event was not schedule")
    if run.get("head_branch") != policy.expected_branch:
        reasons.append("run did not use expected default branch")
    if not 0 <= delay <= policy.max_dispatch_delay_minutes:
        reasons.append(f"dispatch delay {delay}m was outside governed window")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        reasons.append(f"workflow concluded {run.get('conclusion') or run.get('status')}")
    if job is None:
        reasons.append(f"required job {policy.execution_job!r} was absent")
    elif job.get("status") != "completed" or job.get("conclusion") != "success":
        reasons.append(
            f"required job {policy.execution_job!r} concluded "
            f"{job.get('conclusion') or job.get('status')}"
        )
    if artifact is None:
        reasons.append(f"terminal receipt artifact {artifact_name!r} was absent")
    else:
        if artifact.get("expired") is not False:
            reasons.append("terminal receipt artifact is expired")
        size = artifact.get("size_in_bytes")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size <= 0):
            reasons.append("terminal receipt artifact is empty")
    return {
        "run_id": run_id,
        "run_attempt": attempt,
        "run_url": run.get("html_url"),
        "head_sha": run.get("head_sha"),
        "started_at": started.isoformat(),
        "started_local": local_started.isoformat(),
        "dispatch_delay_minutes": delay,
        "workflow_status": run.get("status"),
        "workflow_conclusion": run.get("conclusion"),
        "execution_job_status": job.get("status") if job else None,
        "execution_job_conclusion": job.get("conclusion") if job else None,
        "artifact_name": artifact_name,
        "artifact_id": artifact.get("id") if artifact else None,
        "artifact_digest": artifact.get("digest") if artifact else None,
        "status": "success" if not reasons else "failure",
        "reasons": reasons,
    }


def build_report(client, *, policy: WatchdogPolicy, night: LogicalNight, now: datetime):
    attempts: list[tuple[datetime, dict[str, Any]]] = []
    inspection_errors = 0
    for run in client.list_runs(policy.repository, policy.workflow)[:20]:
        try:
            when = started_at(run)
            local_when = when.astimezone(zone(policy.timezone_name))
            if not night.scheduled_at <= local_when <= night.latest_dispatch_at:
                continue
            run_id = positive(run.get("id"), "run id")
            attempts.append(
                (
                    when,
                    evaluate_attempt(
                        run,
                        client.list_jobs(policy.repository, run_id),
                        client.list_artifacts(policy.repository, run_id),
                        policy=policy,
                        night=night,
                    ),
                )
            )
        except WatchdogError:
            inspection_errors += 1
    ordered = [entry for _, entry in sorted(attempts, key=lambda pair: pair[0])]
    successful = [entry for entry in ordered if entry["status"] == "success"]
    selected = successful[-1] if successful else None
    reasons: list[str] = []
    if now.astimezone(zone(policy.timezone_name)) < night.settlement_at:
        reasons.append("settlement deadline has not passed")
    if not ordered:
        reasons.append("no scheduled workflow run began inside governed window")
    elif not successful:
        reasons.append("no attempt had successful execution and terminal artifact")
    if inspection_errors:
        reasons.append("one or more attempts could not be inspected safely")
    return {
        "schema_version": "nightly_artifact_recovery_watchdog_report.v1",
        "generated_at": now.isoformat(),
        "repository": policy.repository,
        "workflow": policy.workflow,
        "timezone": policy.timezone_name,
        "logical_date": night.logical_date.isoformat(),
        "expected_run_key": night.expected_run_key,
        "scheduled_at": night.scheduled_at.isoformat(),
        "latest_dispatch_at": night.latest_dispatch_at.isoformat(),
        "settlement_at": night.settlement_at.isoformat(),
        "status": "success" if selected and not reasons else "failure",
        "selected_attempt": selected,
        "attempts": ordered,
        "inspection_error_count": inspection_errors,
        "reasons": reasons,
    }


def validate_terminal_receipt(path: Path, expected_run_key: str) -> dict[str, Any]:
    try:
        receipt = obj(json.loads(path.read_text()), "terminal receipt")
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchdogError("terminal receipt is not readable JSON") from exc
    if receipt.get("schema_version") != "artifact_recovery_schedule_receipt.v1":
        raise WatchdogError("unsupported terminal receipt schema")
    supplied = digest(receipt.get("receipt_sha256"), "receipt_sha256")
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    if canonical_digest(unsigned) != supplied:
        raise WatchdogError("terminal receipt digest mismatch")
    if receipt.get("status") != "complete" or receipt.get("job_status") != "succeeded":
        raise WatchdogError("terminal receipt is not complete/succeeded")
    if receipt.get("run_key") != expected_run_key:
        raise WatchdogError("terminal receipt run key mismatch")
    positive(receipt.get("attempts"), "receipt attempts")
    validation = obj(receipt.get("validation"), "receipt validation")
    if validation.get("run_key") != expected_run_key or validation.get("status") != "complete":
        raise WatchdogError("completion validation does not bind to logical night")
    if validation.get("source_coverage_status") != "complete":
        raise WatchdogError("source coverage is incomplete")
    prompts = non_negative(validation.get("prompts_scanned"), "prompts_scanned")
    actionable = non_negative(validation.get("actionable_items"), "actionable_items")
    dispositioned = non_negative(validation.get("dispositioned_items"), "dispositioned_items")
    if actionable != dispositioned:
        raise WatchdogError("not every actionable item was dispositioned")
    return {
        "schema_version": "nightly_artifact_recovery_receipt_validation.v1",
        "status": "success",
        "run_key": expected_run_key,
        "job_id": text(receipt.get("job_id"), "job_id", 200),
        "prompts_scanned": prompts,
        "actionable_items": actionable,
        "dispositioned_items": dispositioned,
        "source_coverage_status": "complete",
        "report_sha256": digest(validation.get("report_sha256"), "report_sha256"),
        "manifest_sha256": digest(validation.get("manifest_sha256"), "manifest_sha256"),
        "receipt_sha256": supplied,
    }


def build_example_snapshot() -> dict[str, Any]:
    return {
        "workflow_runs": [
            {
                "id": 1001,
                "event": "schedule",
                "status": "completed",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "a" * 40,
                "run_started_at": "2026-08-10T05:07:00Z",
                "created_at": "2026-08-10T05:07:00Z",
                "run_attempt": 1,
                "html_url": "https://github.example.invalid/runs/1001",
            }
        ],
        "jobs_by_run": {
            "1001": [
                {"name": "validate", "status": "completed", "conclusion": "success"},
                {"name": DEFAULT_JOB, "status": "completed", "conclusion": "success"},
            ]
        },
        "artifacts_by_run": {
            "1001": [
                {
                    "id": 5001,
                    "name": f"{DEFAULT_ARTIFACT_PREFIX}1001-1",
                    "expired": False,
                    "size_in_bytes": 2048,
                    "digest": "sha256:" + "b" * 64,
                }
            ]
        },
    }


def build_example_terminal_receipt() -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": "artifact_recovery_schedule_receipt.v1",
        "status": "complete",
        "recorded_at": "2026-08-10T05:20:00+00:00",
        "run_key": "artifact-recovery:scheduled:2026-08-10",
        "scheduled_for": "2026-08-10T00:37:00-04:00",
        "observed_local_time": "2026-08-10T01:07:00-04:00",
        "recovered": False,
        "job_id": "job-1",
        "job_status": "succeeded",
        "attempts": 1,
        "updated_at": "2026-08-10T05:19:00Z",
        "validation": {
            "run_key": "artifact-recovery:scheduled:2026-08-10",
            "status": "complete",
            "prompts_scanned": 42,
            "actionable_items": 7,
            "dispositioned_items": 7,
            "source_coverage_status": "complete",
            "report_sha256": "c" * 64,
            "manifest_sha256": "d" * 64,
        },
    }
    receipt["receipt_sha256"] = canonical_digest(receipt)
    return receipt


def render_markdown(report: Mapping[str, Any]) -> str:
    selected = report.get("selected_attempt")
    lines = [
        "# Nightly ChatGPT reconciliation watchdog",
        "",
        f"**Outcome: {str(report['status']).upper()}**",
        "",
        f"- Logical date: `{report['logical_date']}`",
        f"- Expected run key: `{report['expected_run_key']}`",
        f"- Scheduled at: `{report['scheduled_at']}`",
        f"- Settlement at: `{report['settlement_at']}`",
        f"- Attempts inspected: `{len(report['attempts'])}`",
    ]
    if isinstance(selected, dict):
        lines += [
            f"- Selected run: `{selected['run_id']}` attempt `{selected['run_attempt']}`",
            f"- Dispatch delay: `{selected['dispatch_delay_minutes']} minutes`",
            f"- Terminal artifact: `{selected['artifact_name']}`",
        ]
    if report["reasons"]:
        lines += ["", "## Reasons", ""] + [f"- {reason}" for reason in report["reasons"]]
    lines += [
        "",
        "> This audit is separate from execution but still uses GitHub Actions. DEN-3474 "
        "must mirror it to Kubernetes/gha-indie-worker to detect a total Actions outage.",
        "",
    ]
    return "\n".join(lines)


def write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.chmod(temp, 0o600)
    temp.replace(target)


def write_text(path: str | Path, payload: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(payload)
    os.chmod(temp, 0o600)
    temp.replace(target)


def write_outputs(path: str | None, values: Mapping[str, Any]) -> None:
    if not path:
        return
    with Path(path).open("a") as stream:
        for key, raw in values.items():
            if not OUTPUT_RE.fullmatch(key):
                raise WatchdogError(f"invalid output key: {key}")
            value = "" if raw is None else str(raw)
            if "\n" in value or "\r" in value:
                raise WatchdogError(f"output {key} must be single-line")
            stream.write(f"{key}={value}\n")


def locate(args: argparse.Namespace) -> int:
    policy = build_policy(args)
    now = parse_instant(args.now)
    night = logical_night(now, policy, args.logical_date)
    if args.snapshot:
        client = SnapshotClient(obj(json.loads(Path(args.snapshot).read_text()), "snapshot"))
    else:
        client = GitHubClient(os.getenv(args.token_env, ""), args.timeout_seconds)
    report = build_report(client, policy=policy, night=night, now=now)
    write_json(args.output_json, report)
    write_text(args.output_markdown, render_markdown(report))
    selected = report["selected_attempt"] if report["status"] == "success" else None
    write_outputs(
        args.github_output,
        {
            "status": report["status"],
            "logical_date": report["logical_date"],
            "expected_run_key": report["expected_run_key"],
            "run_id": selected.get("run_id") if selected else "",
            "artifact_name": selected.get("artifact_name") if selected else "",
        },
    )
    return 0 if report["status"] == "success" else 1


def validate_receipt(args: argparse.Namespace) -> int:
    write_json(args.output_json, validate_terminal_receipt(Path(args.receipt), args.expected_run_key))
    write_outputs(args.github_output, {"status": "success"})
    return 0


def emit_fixtures(args: argparse.Namespace) -> int:
    write_json(args.snapshot_output, build_example_snapshot())
    write_json(args.receipt_output, build_example_terminal_receipt())
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    find = commands.add_parser("locate")
    find.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", DEFAULT_REPOSITORY))
    find.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    find.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    find.add_argument("--local-time", default=DEFAULT_LOCAL_TIME)
    find.add_argument("--max-dispatch-delay-minutes", type=int, default=DEFAULT_MAX_DELAY)
    find.add_argument("--settlement-minutes", type=int, default=DEFAULT_SETTLEMENT)
    find.add_argument("--execution-job", default=DEFAULT_JOB)
    find.add_argument("--artifact-prefix", default=DEFAULT_ARTIFACT_PREFIX)
    find.add_argument("--expected-branch", default="main")
    find.add_argument("--logical-date")
    find.add_argument("--now")
    find.add_argument("--snapshot")
    find.add_argument("--token-env", default="GITHUB_TOKEN")
    find.add_argument("--timeout-seconds", type=float, default=15.0)
    find.add_argument("--output-json", required=True)
    find.add_argument("--output-markdown", required=True)
    find.add_argument("--github-output")
    find.set_defaults(handler=locate)
    receipt = commands.add_parser("validate-receipt")
    receipt.add_argument("--receipt", required=True)
    receipt.add_argument("--expected-run-key", required=True)
    receipt.add_argument("--output-json", required=True)
    receipt.add_argument("--github-output")
    receipt.set_defaults(handler=validate_receipt)
    fixtures = commands.add_parser("emit-fixtures")
    fixtures.add_argument("--snapshot-output", required=True)
    fixtures.add_argument("--receipt-output", required=True)
    fixtures.set_defaults(handler=emit_fixtures)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (WatchdogError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
