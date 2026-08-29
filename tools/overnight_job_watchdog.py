#!/usr/bin/env python3
"""Certify a recent-chat reconciliation from workflow and downloaded evidence."""

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

MAX_RESPONSE_BYTES = 2_097_152
MAX_EVIDENCE_BYTES = 2_097_152
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
OUTPUT_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class WatchdogError(RuntimeError):
    """A bounded watchdog validation error."""


@dataclass(frozen=True)
class LogicalNight:
    logical_date: date
    scheduled_at: datetime
    latest_dispatch_at: datetime
    settlement_at: datetime
    expected_run_key: str


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WatchdogError(f"{field} must be an object")
    return value


def _objects(value: Any, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise WatchdogError(f"{field} must be an array of objects")
    return value


def _text(value: Any, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise WatchdogError(f"{field} must be non-empty bounded text")
    if any(ord(char) < 32 for char in value):
        raise WatchdogError(f"{field} contains control characters")
    return value


def _positive(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WatchdogError(f"{field} must be a positive integer")
    return value


def _non_negative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise WatchdogError(f"{field} must be a non-negative integer")
    return value


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise WatchdogError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise WatchdogError(f"unknown timezone: {name}") from exc


def _parse_local_time(value: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise WatchdogError("local_time must use HH:MM") from exc
    return parsed.hour, parsed.minute


def _read_json(path: Path, field: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise WatchdogError(f"{field} is not a regular file")
        if path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise WatchdogError(f"{field} exceeded the size limit")
        return _object(json.loads(path.read_text(encoding="utf-8")), field)
    except WatchdogError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchdogError(f"{field} is not readable JSON") from exc


def load_catalog(path: str) -> list[dict[str, Any]]:
    document = _read_json(Path(path), "watchdog catalog")
    if document.get("schema_version") != "overnight_jobs.v2":
        raise WatchdogError("unsupported watchdog catalog schema")
    jobs = _objects(document.get("jobs"), "watchdog catalog.jobs")
    if not jobs:
        raise WatchdogError("watchdog catalog must contain jobs")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for raw in jobs:
        entry = dict(raw)
        job_id = _text(entry.get("id"), "job.id", 80)
        if job_id in seen:
            raise WatchdogError("watchdog job IDs must be unique")
        seen.add(job_id)
        workflow = _text(entry.get("workflow"), f"{job_id}.workflow", 160)
        if not workflow.endswith((".yml", ".yaml")):
            raise WatchdogError(f"{job_id}.workflow must name YAML")
        _text(entry.get("branch"), f"{job_id}.branch", 255)
        timezone_name = _text(entry.get("timezone"), f"{job_id}.timezone", 100)
        _zone(timezone_name)
        _parse_local_time(_text(entry.get("local_time"), f"{job_id}.local_time", 5))
        for integer_field, lower, upper in (
            ("max_dispatch_delay_minutes", 1, 720),
            ("settlement_minutes", 1, 1440),
        ):
            value = entry.get(integer_field)
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise WatchdogError(
                    f"{job_id}.{integer_field} must be between {lower} and {upper}"
                )
        if entry["settlement_minutes"] < entry["max_dispatch_delay_minutes"]:
            raise WatchdogError(f"{job_id}.settlement_minutes precedes dispatch window")
        for field, maximum in (
            ("execution_job", 100),
            ("run_key_prefix", 100),
            ("terminal_artifact_prefix", 160),
            ("report_artifact_prefix", 160),
            ("terminal_receipt_file", 160),
            ("terminal_receipt_schema", 160),
            ("report_ledger_file", 160),
            ("report_markdown_file", 160),
        ):
            _text(entry.get(field), f"{job_id}.{field}", maximum)
        validated.append(entry)
    return validated


def select_entry(entries: Sequence[Mapping[str, Any]], job_id: str) -> dict[str, Any]:
    matches = [dict(entry) for entry in entries if entry.get("id") == job_id]
    if len(matches) != 1:
        raise WatchdogError(f"catalog must contain exactly one job {job_id!r}")
    return matches[0]


def logical_night(
    now: datetime, entry: Mapping[str, Any], explicit_date: str | None = None
) -> LogicalNight:
    zone = _zone(str(entry["timezone"]))
    local_now = now.astimezone(zone)
    hour, minute = _parse_local_time(str(entry["local_time"]))
    if explicit_date:
        try:
            logical_date = date.fromisoformat(explicit_date)
        except ValueError as exc:
            raise WatchdogError("logical date must use YYYY-MM-DD") from exc
    else:
        today_schedule = datetime(
            local_now.year,
            local_now.month,
            local_now.day,
            hour,
            minute,
            tzinfo=zone,
        )
        logical_date = (
            local_now.date()
            if local_now >= today_schedule + timedelta(minutes=int(entry["settlement_minutes"]))
            else local_now.date() - timedelta(days=1)
        )
    scheduled = datetime(
        logical_date.year,
        logical_date.month,
        logical_date.day,
        hour,
        minute,
        tzinfo=zone,
    )
    return LogicalNight(
        logical_date=logical_date,
        scheduled_at=scheduled,
        latest_dispatch_at=scheduled
        + timedelta(minutes=int(entry["max_dispatch_delay_minutes"])),
        settlement_at=scheduled + timedelta(minutes=int(entry["settlement_minutes"])),
        expected_run_key=f"{entry['run_key_prefix']}:scheduled:{logical_date.isoformat()}",
    )


def _github_instant(value: Any, field: str) -> datetime:
    return parse_instant(_text(value, field, 64))


class GitHubClient:
    def __init__(self, repository: str, token: str, api_url: str, timeout: float = 20.0):
        if "/" not in repository:
            raise WatchdogError("repository must use owner/name")
        if not token.strip():
            raise WatchdogError("GITHUB_TOKEN is required")
        if not 0 < timeout <= 60:
            raise WatchdogError("timeout must be in (0, 60]")
        self.base = api_url.rstrip("/") + f"/repos/{repository}"
        self.token = token.strip()
        self.timeout = timeout

    def get(self, path: str) -> dict[str, Any]:
        request = Request(
            self.base + path,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "recent-chat-reconciliation-watchdog/2",
            },
        )
        last: Exception | None = None
        for attempt in range(4):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                    status = response.status
                if len(body) > MAX_RESPONSE_BYTES:
                    raise WatchdogError("GitHub response exceeded size limit")
                if not 200 <= status < 300:
                    raise WatchdogError(f"GitHub returned HTTP {status}")
                return _object(json.loads(body) if body else {}, "GitHub response")
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

    def list_runs(self, workflow: str) -> list[dict[str, Any]]:
        return _objects(
            self.get(
                f"/actions/workflows/{quote(workflow, safe='')}/runs"
                "?event=schedule&per_page=50"
            ).get("workflow_runs"),
            "workflow_runs",
        )

    def list_jobs(self, run_id: int) -> list[dict[str, Any]]:
        return _objects(
            self.get(f"/actions/runs/{_positive(run_id, 'run_id')}/jobs?per_page=100").get(
                "jobs"
            ),
            "jobs",
        )

    def list_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        return _objects(
            self.get(
                f"/actions/runs/{_positive(run_id, 'run_id')}/artifacts?per_page=100"
            ).get("artifacts"),
            "artifacts",
        )


class SnapshotClient:
    def __init__(self, payload: Mapping[str, Any]):
        self.runs = _objects(payload.get("workflow_runs"), "workflow_runs")
        self.jobs = _object(payload.get("jobs_by_run"), "jobs_by_run")
        self.artifacts = _object(payload.get("artifacts_by_run"), "artifacts_by_run")

    def list_runs(self, workflow: str) -> list[dict[str, Any]]:
        return list(self.runs)

    def list_jobs(self, run_id: int) -> list[dict[str, Any]]:
        return _objects(self.jobs.get(str(run_id), []), f"jobs_by_run.{run_id}")

    def list_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        return _objects(
            self.artifacts.get(str(run_id), []), f"artifacts_by_run.{run_id}"
        )


def _unique_named(items: Sequence[Mapping[str, Any]], name: str, kind: str):
    matches = [dict(item) for item in items if item.get("name") == name]
    if len(matches) > 1:
        raise WatchdogError(f"duplicate {kind} {name!r}")
    return matches[0] if matches else None


def evaluate_attempt(
    run: Mapping[str, Any],
    *,
    entry: Mapping[str, Any],
    night: LogicalNight,
    jobs: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    run_id = _positive(run.get("id"), "run.id")
    attempt = _positive(run.get("run_attempt", 1), "run.run_attempt")
    started = _github_instant(run.get("run_started_at") or run.get("created_at"), "run.started_at")
    local_started = started.astimezone(_zone(str(entry["timezone"])))
    delay = int((local_started - night.scheduled_at).total_seconds() // 60)
    terminal_name = f"{entry['terminal_artifact_prefix']}{run_id}-{attempt}"
    report_name = f"{entry['report_artifact_prefix']}{run_id}-{attempt}"
    execution = _unique_named(jobs, str(entry["execution_job"]), "job")
    terminal = _unique_named(artifacts, terminal_name, "artifact")
    report = _unique_named(artifacts, report_name, "artifact")
    reasons: list[str] = []
    if run.get("event") != "schedule":
        reasons.append("event was not schedule")
    if run.get("head_branch") != entry["branch"]:
        reasons.append("run did not use expected default branch")
    if not 0 <= delay <= int(entry["max_dispatch_delay_minutes"]):
        reasons.append(f"dispatch delay {delay}m was outside governed window")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        reasons.append(f"workflow concluded {run.get('conclusion') or run.get('status')}")
    if execution is None:
        reasons.append(f"required job {entry['execution_job']!r} was absent")
    elif execution.get("status") != "completed" or execution.get("conclusion") != "success":
        reasons.append(
            f"required job {entry['execution_job']!r} concluded "
            f"{execution.get('conclusion') or execution.get('status')}"
        )
    for artifact, name, label in (
        (terminal, terminal_name, "terminal"),
        (report, report_name, "report"),
    ):
        if artifact is None:
            reasons.append(f"{label} artifact {name!r} was absent")
            continue
        if artifact.get("expired") is not False:
            reasons.append(f"{label} artifact is expired")
        size = artifact.get("size_in_bytes")
        if size is not None and (
            isinstance(size, bool) or not isinstance(size, int) or size <= 0
        ):
            reasons.append(f"{label} artifact is empty")
    return {
        "run_id": run_id,
        "run_attempt": attempt,
        "run_url": run.get("html_url"),
        "head_sha": run.get("head_sha"),
        "started_at": started.isoformat(),
        "started_local": local_started.isoformat(),
        "dispatch_delay_minutes": delay,
        "terminal_artifact_name": terminal_name,
        "terminal_artifact_id": terminal.get("id") if terminal else None,
        "report_artifact_name": report_name,
        "report_artifact_id": report.get("id") if report else None,
        "status": "success" if not reasons else "failure",
        "reasons": reasons,
    }


def build_report(client, *, entry: Mapping[str, Any], night: LogicalNight, now: datetime):
    attempts: list[tuple[datetime, dict[str, Any]]] = []
    inspection_errors = 0
    for run in client.list_runs(str(entry["workflow"]))[:30]:
        try:
            started = _github_instant(
                run.get("run_started_at") or run.get("created_at"), "run.started_at"
            )
            local_started = started.astimezone(_zone(str(entry["timezone"])))
            if not night.scheduled_at <= local_started <= night.latest_dispatch_at:
                continue
            run_id = _positive(run.get("id"), "run.id")
            attempts.append(
                (
                    started,
                    evaluate_attempt(
                        run,
                        entry=entry,
                        night=night,
                        jobs=client.list_jobs(run_id),
                        artifacts=client.list_artifacts(run_id),
                    ),
                )
            )
        except WatchdogError:
            inspection_errors += 1
    ordered = [entry for _, entry in sorted(attempts, key=lambda pair: pair[0])]
    successful = [attempt for attempt in ordered if attempt["status"] == "success"]
    selected = successful[-1] if successful else None
    reasons: list[str] = []
    if now.astimezone(_zone(str(entry["timezone"]))) < night.settlement_at:
        reasons.append("settlement deadline has not passed")
    if not ordered:
        reasons.append("no scheduled workflow run began inside governed window")
    elif not successful:
        reasons.append("no attempt had successful execution and both evidence artifacts")
    if inspection_errors:
        reasons.append("one or more attempts could not be inspected safely")
    return {
        "schema_version": "overnight_watchdog_report.v2",
        "generated_at": now.isoformat(),
        "job_id": entry["id"],
        "workflow": entry["workflow"],
        "timezone": entry["timezone"],
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


def validate_evidence(
    *,
    entry: Mapping[str, Any],
    terminal_receipt: Path,
    report_dir: Path,
    expected_run_key: str,
    run_id: int,
    run_attempt: int,
    terminal_artifact_name: str,
    report_artifact_name: str,
) -> dict[str, Any]:
    receipt = _read_json(terminal_receipt, "terminal receipt")
    if receipt.get("schema_version") != entry["terminal_receipt_schema"]:
        raise WatchdogError("unsupported terminal receipt schema")
    supplied = _digest(receipt.get("receipt_sha256"), "receipt.receipt_sha256")
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    if _canonical_digest(unsigned) != supplied:
        raise WatchdogError("terminal receipt digest mismatch")
    if receipt.get("status") != "complete" or receipt.get("work_status") != "complete":
        raise WatchdogError("terminal receipt does not report complete work")
    if receipt.get("job_status") != "succeeded":
        raise WatchdogError("terminal receipt job did not succeed")
    if receipt.get("run_key") != expected_run_key:
        raise WatchdogError("terminal receipt run key mismatch")
    _text(receipt.get("job_id"), "receipt.job_id", 200)
    _positive(receipt.get("attempts"), "receipt.attempts")

    validation = _object(receipt.get("validation"), "receipt.validation")
    if validation.get("run_key") != expected_run_key or validation.get("status") != "complete":
        raise WatchdogError("completion validation does not bind to logical night")
    if validation.get("source_coverage_status") != "complete":
        raise WatchdogError("source coverage is incomplete")
    prompts = _non_negative(validation.get("prompts_scanned"), "prompts_scanned")
    actionable = _non_negative(validation.get("actionable_items"), "actionable_items")
    dispositioned = _non_negative(
        validation.get("dispositioned_items"), "dispositioned_items"
    )
    if actionable != dispositioned:
        raise WatchdogError("not every actionable item was dispositioned")
    report_sha256 = _digest(validation.get("report_sha256"), "report_sha256")
    manifest_sha256 = _digest(validation.get("manifest_sha256"), "manifest_sha256")

    completion = _object(receipt.get("completion_summary"), "completion_summary")
    if completion.get("all_work_complete") is not True:
        raise WatchdogError("all_work_complete is not true")
    if _non_negative(completion.get("unfinished_items"), "unfinished_items") != 0:
        raise WatchdogError("unfinished_items must be zero")
    if _non_negative(completion.get("prompts_scanned"), "completion.prompts_scanned") != prompts:
        raise WatchdogError("prompt counts disagree")
    if _non_negative(completion.get("actionable_items"), "completion.actionable_items") != actionable:
        raise WatchdogError("actionable counts disagree")
    dispositions = _object(completion.get("dispositions"), "completion.dispositions")
    complete = _non_negative(dispositions.get("complete", 0), "dispositions.complete")
    landed = _non_negative(
        dispositions.get("already_landed", 0), "dispositions.already_landed"
    )
    for name in ("in_review", "blocked_with_owner", "deferred_with_owner"):
        if _non_negative(dispositions.get(name, 0), f"dispositions.{name}") != 0:
            raise WatchdogError(f"dispositions.{name} must be zero")
    if complete + landed != actionable:
        raise WatchdogError("finished dispositions do not equal actionable items")

    if report_dir.is_symlink() or not report_dir.is_dir():
        raise WatchdogError("report evidence is not a regular directory")
    ledger = _read_json(report_dir / str(entry["report_ledger_file"]), "run ledger")
    markdown_path = report_dir / str(entry["report_markdown_file"])
    try:
        if markdown_path.is_symlink() or not markdown_path.is_file():
            raise WatchdogError("report markdown is not a regular file")
        if markdown_path.stat().st_size > MAX_EVIDENCE_BYTES:
            raise WatchdogError("report markdown exceeded the size limit")
        markdown = markdown_path.read_text(encoding="utf-8")
    except WatchdogError:
        raise
    except OSError as exc:
        raise WatchdogError("report markdown is unreadable") from exc
    if not markdown.strip() or "Outcome: **SUCCESS**" not in markdown:
        raise WatchdogError("report markdown does not record success")

    if ledger.get("schema_version") != "overnight_run_ledger.v1":
        raise WatchdogError("unsupported run ledger schema")
    if ledger.get("job_id") != entry["id"] or ledger.get("workflow") != entry["workflow"]:
        raise WatchdogError("run ledger job identity mismatch")
    if ledger.get("run_id") != run_id or ledger.get("run_attempt") != run_attempt:
        raise WatchdogError("run ledger execution identity mismatch")
    if ledger.get("outcome") != "success":
        raise WatchdogError("run ledger outcome is not success")
    if ledger.get("reconciliation_complete") is not True:
        raise WatchdogError("run ledger reconciliation_complete is not true")
    if ledger.get("all_work_complete") is not True:
        raise WatchdogError("run ledger all_work_complete is not true")
    if _non_negative(ledger.get("unfinished_items"), "ledger.unfinished_items") != 0:
        raise WatchdogError("run ledger unfinished_items must be zero")
    if ledger.get("terminal_receipt_sha256") != supplied:
        raise WatchdogError("run ledger terminal receipt digest mismatch")
    manifest = ledger.get("artifact_manifest")
    if not isinstance(manifest, list) or manifest != [
        terminal_artifact_name,
        report_artifact_name,
    ]:
        raise WatchdogError("run ledger artifact manifest mismatch")
    ledger_completion = _object(ledger.get("completion_summary"), "ledger.completion_summary")
    if ledger_completion != completion:
        raise WatchdogError("run ledger completion summary mismatch")

    return {
        "schema_version": "recent_chat_reconciliation_evidence_validation.v1",
        "status": "success",
        "job_id": entry["id"],
        "run_key": expected_run_key,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "prompts_scanned": prompts,
        "actionable_items": actionable,
        "dispositioned_items": dispositioned,
        "unfinished_items": 0,
        "all_work_complete": True,
        "source_coverage_status": "complete",
        "terminal_receipt_sha256": supplied,
        "report_sha256": report_sha256,
        "manifest_sha256": manifest_sha256,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    selected = report.get("selected_attempt")
    lines = [
        "# Recent-chat reconciliation watchdog",
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
            f"- Terminal artifact: `{selected['terminal_artifact_name']}`",
            f"- Report artifact: `{selected['report_artifact_name']}`",
        ]
    if report["reasons"]:
        lines += ["", "## Reasons", ""] + [f"- {reason}" for reason in report["reasons"]]
    lines.append("")
    return "\n".join(lines)


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def _write_text(path: str | Path, payload: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(target)


def _write_outputs(path: str | None, values: Mapping[str, Any]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, raw in values.items():
            if not OUTPUT_RE.fullmatch(key):
                raise WatchdogError(f"invalid output key: {key}")
            value = "" if raw is None else str(raw)
            if "\n" in value or "\r" in value:
                raise WatchdogError(f"output {key} must be single-line")
            stream.write(f"{key}={value}\n")


def locate(args: argparse.Namespace) -> int:
    entry = select_entry(load_catalog(args.catalog), args.job_id)
    now = parse_instant(args.now)
    night = logical_night(now, entry, args.logical_date)
    if args.snapshot:
        client = SnapshotClient(_read_json(Path(args.snapshot), "snapshot"))
    else:
        client = GitHubClient(
            args.repository,
            os.getenv(args.token_env, ""),
            args.api_url,
            args.timeout_seconds,
        )
    report = build_report(client, entry=entry, night=night, now=now)
    _write_json(args.output_json, report)
    _write_text(args.output_markdown, render_markdown(report))
    selected = report["selected_attempt"] if report["status"] == "success" else None
    _write_outputs(
        args.github_output,
        {
            "status": report["status"],
            "job_id": entry["id"],
            "expected_run_key": report["expected_run_key"],
            "run_id": selected.get("run_id") if selected else "",
            "run_attempt": selected.get("run_attempt") if selected else "",
            "terminal_artifact_name": (
                selected.get("terminal_artifact_name") if selected else ""
            ),
            "report_artifact_name": (
                selected.get("report_artifact_name") if selected else ""
            ),
            "terminal_receipt_file": entry["terminal_receipt_file"],
            "report_ledger_file": entry["report_ledger_file"],
            "report_markdown_file": entry["report_markdown_file"],
        },
    )
    return 0 if report["status"] == "success" else 1


def validate_command(args: argparse.Namespace) -> int:
    entry = select_entry(load_catalog(args.catalog), args.job_id)
    result = validate_evidence(
        entry=entry,
        terminal_receipt=Path(args.terminal_receipt),
        report_dir=Path(args.report_dir),
        expected_run_key=args.expected_run_key,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        terminal_artifact_name=args.terminal_artifact_name,
        report_artifact_name=args.report_artifact_name,
    )
    _write_json(args.output_json, result)
    _write_outputs(args.github_output, {"status": "success"})
    return 0


def _fixture_receipt() -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": "recent_chat_reconciliation_receipt.v1",
        "status": "complete",
        "work_status": "complete",
        "recorded_at": "2026-08-10T05:45:00+00:00",
        "run_key": "recent-chat-reconciliation:scheduled:2026-08-10",
        "scheduled_for": "2026-08-10T00:30:00-05:00",
        "observed_local_time": "2026-08-10T00:34:00-05:00",
        "recovered": False,
        "job_name": "Recent-96-hours ChatGPT introspection and reconciliation",
        "job_id": "job-1",
        "job_status": "succeeded",
        "attempts": 1,
        "updated_at": "2026-08-10T05:44:00Z",
        "validation": {
            "run_key": "recent-chat-reconciliation:scheduled:2026-08-10",
            "status": "complete",
            "prompts_scanned": 12,
            "actionable_items": 3,
            "dispositioned_items": 3,
            "source_coverage_status": "complete",
            "report_sha256": "a" * 64,
            "manifest_sha256": "b" * 64,
        },
        "completion_summary": {
            "prompts_scanned": 12,
            "actionable_items": 3,
            "dispositions": {
                "complete": 2,
                "already_landed": 1,
                "in_review": 0,
                "blocked_with_owner": 0,
                "deferred_with_owner": 0,
            },
            "unfinished_items": 0,
            "all_work_complete": True,
        },
    }
    receipt["receipt_sha256"] = _canonical_digest(receipt)
    return receipt


def emit_fixtures(args: argparse.Namespace) -> int:
    entry = select_entry(load_catalog(args.catalog), args.job_id)
    if entry["terminal_receipt_schema"] != "recent_chat_reconciliation_receipt.v1":
        # _fixture_receipt models one job's receipt shape. Refuse rather than
        # emit a fixture that would not survive validate_evidence.
        raise WatchdogError(
            f"no fixture receipt is modelled for job {entry['id']!r}"
        )
    run_id, attempt = 42, 1
    terminal_name = f"{entry['terminal_artifact_prefix']}{run_id}-{attempt}"
    report_name = f"{entry['report_artifact_prefix']}{run_id}-{attempt}"
    snapshot = {
        "workflow_runs": [
            {
                "id": run_id,
                "run_attempt": attempt,
                "event": "schedule",
                "status": "completed",
                "conclusion": "success",
                "head_branch": entry["branch"],
                "head_sha": "c" * 40,
                "run_started_at": "2026-08-10T05:34:00Z",
                "created_at": "2026-08-10T05:34:00Z",
                "html_url": "https://example.invalid/runs/42",
            }
        ],
        "jobs_by_run": {
            str(run_id): [
                {
                    "name": entry["execution_job"],
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        },
        "artifacts_by_run": {
            str(run_id): [
                {"id": 101, "name": terminal_name, "expired": False, "size_in_bytes": 1024},
                {"id": 102, "name": report_name, "expired": False, "size_in_bytes": 2048},
            ]
        },
    }
    receipt = _fixture_receipt()
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    ledger = {
        "schema_version": "overnight_run_ledger.v1",
        "job_id": entry["id"],
        "workflow": entry["workflow"],
        "repository": "ORESoftware/ai-agent-coordinator.rs",
        "timezone": entry["timezone"],
        "local_time": entry["local_time"],
        "lookback_hours": 96,
        "overlap_hours": 6,
        "run_id": run_id,
        "run_attempt": attempt,
        "run_url": "https://example.invalid/runs/42",
        "sha": "c" * 40,
        "started_at": "2026-08-10T05:34:00Z",
        "completed_at": "2026-08-10T05:45:00Z",
        "outcome": "success",
        "reconciliation_complete": True,
        "all_work_complete": True,
        "unfinished_items": 0,
        "completion_summary": receipt["completion_summary"],
        "error_summary": [],
        "terminal_receipt_sha256": receipt["receipt_sha256"],
        "artifact_manifest": [terminal_name, report_name],
    }
    _write_json(args.snapshot_output, snapshot)
    _write_json(args.terminal_receipt_output, receipt)
    _write_json(report_dir / str(entry["report_ledger_file"]), ledger)
    _write_text(
        report_dir / str(entry["report_markdown_file"]),
        "# Recent-96-hours ChatGPT reconciliation\n\n- Outcome: **SUCCESS**\n"
        "- All work complete: `True`\n- Unfinished items: `0`\n",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    locate_parser = commands.add_parser("locate")
    locate_parser.add_argument("--catalog", default="config/overnight-jobs.json")
    locate_parser.add_argument("--job-id", default="recent-chat-reconciliation")
    locate_parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    locate_parser.add_argument("--token-env", default="GITHUB_TOKEN")
    locate_parser.add_argument(
        "--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com")
    )
    locate_parser.add_argument("--timeout-seconds", type=float, default=20.0)
    locate_parser.add_argument("--now")
    locate_parser.add_argument("--logical-date")
    locate_parser.add_argument("--snapshot")
    locate_parser.add_argument("--output-json", required=True)
    locate_parser.add_argument("--output-markdown", required=True)
    locate_parser.add_argument("--github-output")
    locate_parser.set_defaults(handler=locate)

    validation = commands.add_parser("validate-evidence")
    validation.add_argument("--catalog", default="config/overnight-jobs.json")
    validation.add_argument("--job-id", default="recent-chat-reconciliation")
    validation.add_argument("--terminal-receipt", required=True)
    validation.add_argument("--report-dir", required=True)
    validation.add_argument("--expected-run-key", required=True)
    validation.add_argument("--run-id", required=True, type=int)
    validation.add_argument("--run-attempt", required=True, type=int)
    validation.add_argument("--terminal-artifact-name", required=True)
    validation.add_argument("--report-artifact-name", required=True)
    validation.add_argument("--output-json", required=True)
    validation.add_argument("--github-output")
    validation.set_defaults(handler=validate_command)

    fixtures = commands.add_parser("emit-fixtures")
    fixtures.add_argument("--catalog", default="config/overnight-jobs.json")
    fixtures.add_argument("--job-id", default="recent-chat-reconciliation")
    fixtures.add_argument("--snapshot-output", required=True)
    fixtures.add_argument("--terminal-receipt-output", required=True)
    fixtures.add_argument("--report-dir", required=True)
    fixtures.set_defaults(handler=emit_fixtures)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (WatchdogError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
