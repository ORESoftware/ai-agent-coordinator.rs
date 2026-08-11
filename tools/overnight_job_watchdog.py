#!/usr/bin/env python3
"""Fail closed when an overnight workflow lacks successful jobs and evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_RESPONSE_BYTES = 2_097_152


class WatchdogError(RuntimeError):
    pass


def parse_instant(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WatchdogError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise WatchdogError("--now must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise WatchdogError("--date must use YYYY-MM-DD") from exc


def parse_local_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise WatchdogError("catalog local_time must use HH:MM") from exc
    if parsed.second or parsed.microsecond:
        raise WatchdogError("catalog local_time must not include seconds")
    return parsed


def parse_github_instant(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise WatchdogError(f"{field} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WatchdogError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise WatchdogError(f"{field} is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def load_catalog(path: str) -> list[dict[str, Any]]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchdogError("could not read watchdog catalog") from exc
    if document.get("schema_version") != "overnight_jobs.v1":
        raise WatchdogError("unsupported watchdog catalog schema")
    jobs = document.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise WatchdogError("watchdog catalog must contain jobs")
    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for raw in jobs:
        if not isinstance(raw, dict):
            raise WatchdogError("watchdog job entries must be objects")
        job_id = raw.get("id")
        if not isinstance(job_id, str) or not job_id or job_id in seen:
            raise WatchdogError("watchdog job IDs must be unique non-empty strings")
        seen.add(job_id)
        workflow = raw.get("workflow")
        if not isinstance(workflow, str) or not workflow.endswith((".yml", ".yaml")):
            raise WatchdogError(f"{job_id}: workflow must name a YAML file")
        try:
            ZoneInfo(str(raw.get("timezone")))
        except ZoneInfoNotFoundError as exc:
            raise WatchdogError(f"{job_id}: timezone is invalid") from exc
        parse_local_time(str(raw.get("local_time")))
        maximum = raw.get("max_dispatch_delay_minutes")
        if (
            isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= maximum <= 720
        ):
            raise WatchdogError(f"{job_id}: max dispatch delay must be 1-720 minutes")
        for field in ("required_job_names", "required_artifact_prefixes"):
            values = raw.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(item, str) and item for item in values
            ):
                raise WatchdogError(f"{job_id}: {field} must be a non-empty string list")
        validated.append(dict(raw))
    return validated


def api_get(url: str, token: str) -> Mapping[str, Any]:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-agent-coordinator-overnight-watchdog/1",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        exc.read(4096)
        raise WatchdogError(f"GitHub API returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise WatchdogError("GitHub API request failed") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise WatchdogError("GitHub API response exceeded the size limit")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WatchdogError("GitHub API returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise WatchdogError("GitHub API response must be an object")
    return parsed


def evaluate_job(
    entry: Mapping[str, Any],
    *,
    now: datetime,
    target_date: date | None,
    list_runs: Callable[[str], Sequence[Mapping[str, Any]]],
    list_jobs: Callable[[int], Sequence[Mapping[str, Any]]],
    list_artifacts: Callable[[int], Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    job_id = str(entry["id"])
    zone = ZoneInfo(str(entry["timezone"]))
    local_date = target_date or now.astimezone(zone).date()
    scheduled_local = datetime.combine(
        local_date, parse_local_time(str(entry["local_time"])), tzinfo=zone
    )
    expected_utc = scheduled_local.astimezone(timezone.utc)
    window_start = expected_utc - timedelta(minutes=5)
    window_end = expected_utc + timedelta(
        minutes=int(entry["max_dispatch_delay_minutes"])
    )
    runs = []
    for run in list_runs(str(entry["workflow"])):
        try:
            created = parse_github_instant(run.get("created_at"), "run.created_at")
        except WatchdogError:
            continue
        if window_start <= created <= window_end:
            runs.append((created, run))
    runs.sort(key=lambda item: item[0], reverse=True)
    if not runs:
        return {
            "id": job_id,
            "status": "failure",
            "expected_for": scheduled_local.isoformat(),
            "errors": ["no schedule-event run was recorded inside the delivery window"],
        }

    candidate_errors: list[str] = []
    for created, run in runs:
        run_id = run.get("id")
        if isinstance(run_id, bool) or not isinstance(run_id, int):
            candidate_errors.append("candidate run has no numeric ID")
            continue
        errors: list[str] = []
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            errors.append(
                "workflow concluded "
                f"status={run.get('status')!r} "
                f"conclusion={run.get('conclusion')!r}"
            )
        branch = entry.get("branch")
        if branch and run.get("head_branch") != branch:
            errors.append(f"workflow ran on unexpected branch {run.get('head_branch')!r}")
        jobs = list(list_jobs(run_id))
        for required in entry["required_job_names"]:
            matches = [job for job in jobs if job.get("name") == required]
            if not matches:
                errors.append(f"required job {required!r} is missing")
            elif not any(job.get("conclusion") == "success" for job in matches):
                conclusions = sorted({str(job.get("conclusion")) for job in matches})
                errors.append(f"required job {required!r} did not succeed: {conclusions}")
        artifacts = list(list_artifacts(run_id))
        for prefix in entry["required_artifact_prefixes"]:
            matches = [
                artifact
                for artifact in artifacts
                if isinstance(artifact.get("name"), str)
                and artifact["name"].startswith(prefix)
                and not artifact.get("expired", False)
            ]
            if not matches:
                errors.append(f"required non-expired artifact prefix {prefix!r} is missing")
        if not errors:
            return {
                "id": job_id,
                "status": "success",
                "expected_for": scheduled_local.isoformat(),
                "run_id": run_id,
                "run_created_at": created.isoformat(),
                "run_url": run.get("html_url"),
                "dispatch_delay_minutes": int(
                    (created - expected_utc).total_seconds() // 60
                ),
                "errors": [],
            }
        candidate_errors.append(f"run {run_id}: " + "; ".join(errors))

    return {
        "id": job_id,
        "status": "failure",
        "expected_for": scheduled_local.isoformat(),
        "run_id": runs[0][1].get("id"),
        "errors": candidate_errors[:5],
    }


def write_report(output_dir: str, report: Mapping[str, Any]) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "watchdog.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    lines = [
        "# Overnight job watchdog",
        "",
        f"- Outcome: **{str(report['status']).upper()}**",
        f"- Checked at: `{report['checked_at']}`",
        "",
    ]
    for result in report["jobs"]:
        lines.append(f"## {result['id']}")
        lines.append(f"- Status: **{result['status'].upper()}**")
        lines.append(f"- Expected: `{result['expected_for']}`")
        if result.get("run_id") is not None:
            lines.append(f"- Run ID: `{result['run_id']}`")
        for error in result.get("errors", []):
            lines.append(f"- Error: {error}")
        lines.append("")
    (root / "watchdog.md").write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="config/overnight-jobs.json")
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument(
        "--api-url", default=os.getenv("GITHUB_API_URL", "https://api.github.com")
    )
    parser.add_argument("--now")
    parser.add_argument("--date")
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if "/" not in args.repository:
            raise WatchdogError("repository must use owner/name")
        token = os.getenv(args.token_env, "")
        if not token:
            raise WatchdogError(f"{args.token_env} is unavailable")
        now = parse_instant(args.now)
        target_date = parse_date(args.date)
        catalog = load_catalog(args.catalog)
        base = args.api_url.rstrip("/") + f"/repos/{args.repository}"

        def runs(workflow: str) -> Sequence[Mapping[str, Any]]:
            encoded = quote(workflow, safe="")
            response = api_get(
                f"{base}/actions/workflows/{encoded}/runs?event=schedule&per_page=30",
                token,
            )
            value = response.get("workflow_runs", [])
            return value if isinstance(value, list) else []

        def jobs(run_id: int) -> Sequence[Mapping[str, Any]]:
            response = api_get(f"{base}/actions/runs/{run_id}/jobs?per_page=100", token)
            value = response.get("jobs", [])
            return value if isinstance(value, list) else []

        def artifacts(run_id: int) -> Sequence[Mapping[str, Any]]:
            response = api_get(
                f"{base}/actions/runs/{run_id}/artifacts?per_page=100", token
            )
            value = response.get("artifacts", [])
            return value if isinstance(value, list) else []

        results = [
            evaluate_job(
                entry,
                now=now,
                target_date=target_date,
                list_runs=runs,
                list_jobs=jobs,
                list_artifacts=artifacts,
            )
            for entry in catalog
        ]
        report = {
            "schema_version": "overnight_watchdog_report.v1",
            "repository": args.repository,
            "checked_at": now.isoformat(),
            "status": (
                "success"
                if all(item["status"] == "success" for item in results)
                else "failure"
            ),
            "jobs": results,
        }
        write_report(args.output_dir, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "success" else 2
    except WatchdogError as exc:
        report = {
            "schema_version": "overnight_watchdog_report.v1",
            "repository": args.repository,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": "failure",
            "jobs": [],
            "error": str(exc),
        }
        write_report(args.output_dir, report)
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
