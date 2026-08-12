from __future__ import annotations

import concurrent.futures
import html
import json
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping, Sequence

from scheduled_task_digest_core import *  # noqa: F401,F403
from scheduled_task_digest_core import _STATUS_ORDER, _safe_message

def _run_record(run: Mapping[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {"id": run.get("id"), "status": status, "reason": reason, "created_at": run.get("created_at"), "updated_at": run.get("updated_at"), "url": run.get("html_url"), "conclusion": run.get("conclusion")}


def scan_repository(repo: Mapping[str, Any], client: GitHubClient, *, start: datetime, end: datetime, max_workflows: int, exclude_workflow: str | None = None) -> tuple[list[dict[str, Any]], list[str], int]:
    full_name = str(repo.get("full_name") or "")
    if not full_name or repo.get("archived") or repo.get("disabled"):
        return [], [], 0
    errors: list[str] = []
    try:
        workflows = client.paginate(f"/repos/{full_name}/actions/workflows", item_key="workflows", max_pages=max(1, (max_workflows + 99) // 100))
    except DigestError as error:
        return [], [f"{full_name}: {error}"], 0
    if len(workflows) > max_workflows:
        errors.append(f"{full_name}: workflow inventory truncated to {max_workflows}")
        workflows = workflows[:max_workflows]
    try:
        runs = client.paginate(f"/repos/{full_name}/actions/runs", item_key="workflow_runs", params={"event": "schedule", "created": f">={format_instant(start)}"}, max_pages=10)
    except DigestError as error:
        errors.append(f"{full_name}: {error}")
        runs = []
    runs = [run for run in runs if isinstance(run, dict) and parse_instant(str(run.get("created_at"))) < end]
    by_workflow: dict[int, list[dict[str, Any]]] = {}
    for run in runs:
        try:
            key = int(run.get("workflow_id"))
        except (TypeError, ValueError):
            continue
        by_workflow.setdefault(key, []).append(run)
    records: list[dict[str, Any]] = []
    scheduled_count = 0
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        path = str(workflow.get("path") or "")
        if not path or path == exclude_workflow:
            continue
        text = client.text_file(full_name, path, str(repo.get("default_branch") or "main"))
        if text is None:
            errors.append(f"{full_name}:{path}: workflow source could not be read")
            continue
        crons = extract_crons(text)
        if not crons:
            continue
        scheduled_count += 1
        try:
            expected = expected_occurrences(crons, start, end)
        except DigestError as error:
            errors.append(f"{full_name}:{path}: {error}")
            expected = 0
        workflow_runs = sorted(by_workflow.get(int(workflow.get("id") or 0), []), key=lambda item: str(item.get("created_at") or ""), reverse=True)
        run_records: list[dict[str, Any]] = []
        for index, run in enumerate(workflow_runs):
            jobs: list[dict[str, Any]] = []
            if str(run.get("conclusion") or "").lower() == "success" and index < 10:
                try:
                    payload = client.get_json(f"/repos/{full_name}/actions/runs/{run.get('id')}/jobs", {"per_page": 100}, optional=True)
                    if isinstance(payload, dict):
                        jobs = [job for job in payload.get("jobs", []) if isinstance(job, dict)]
                except DigestError as error:
                    errors.append(f"{full_name}:{path}: jobs unavailable for run {run.get('id')}: {error}")
            status, reason = classify_run(run, jobs)
            run_records.append(_run_record(run, status, reason))
        observed = len(workflow_runs)
        statuses = [item["status"] for item in run_records]
        state = str(workflow.get("state") or "").lower()
        if expected == 0 and observed == 0:
            overall, reason = "not_due", "no cron occurrence fell inside the 24-hour window"
        elif state not in {"active", ""} and expected > 0:
            overall, reason = "disabled", f"workflow state is {state}"
        elif observed == 0 and expected > 0:
            overall, reason = "missed", f"expected {expected} scheduled invocation(s), observed none"
        else:
            overall = worst_status(statuses)
            reason = run_records[0]["reason"] if run_records else "runtime evidence is unavailable"
            if observed < expected and overall not in CRITICAL:
                overall, reason = "partial", f"expected {expected} invocation(s), observed {observed}"
        records.append({
            "source": "github_actions", "repository": full_name, "name": str(workflow.get("name") or path),
            "path": path, "workflow_id": workflow.get("id"), "workflow_state": state, "schedule": crons,
            "timezone": "UTC", "expected_occurrences": expected, "observed_runs": observed,
            "status": overall, "reason": reason, "runs": run_records, "url": workflow.get("html_url"),
        })
    return records, errors, scheduled_count


def evaluate_external_task(task: Mapping[str, Any], client: GitHubClient, *, start: datetime, end: datetime) -> dict[str, Any]:
    schedule = [str(item) for item in task.get("schedule", [])]
    zone = str(task.get("timezone") or "UTC")
    try:
        expected = expected_occurrences(schedule, start, end, timezone_name=zone) if schedule else 0
    except DigestError as error:
        expected = 0
        status, reason = "unknown", str(error)
    else:
        probe = task.get("probe") if isinstance(task.get("probe"), dict) else {}
        kind = str(probe.get("type") or "manual")
        status, reason = "unobserved", "no runtime evidence adapter is configured"
        if kind == "manual":
            status = str(probe.get("state") or "unobserved")
            reason = str(probe.get("reason") or "manual evidence is unavailable")
        elif kind == "github_pull_request":
            repository, number = str(probe.get("repository") or ""), int(probe.get("number") or 0)
            payload = client.get_json(f"/repos/{repository}/pulls/{number}", optional=True)
            if not isinstance(payload, dict):
                status, reason = "missing", "source pull request is not visible"
            elif payload.get("merged_at"):
                status, reason = "source_merged_unobserved", "source PR is merged, but no runtime ledger is connected"
            elif payload.get("state") == "open":
                body = str(payload.get("body") or "")
                suspended_regex = str(probe.get("suspended_body_regex") or "")
                if suspended_regex and re.search(suspended_regex, body, re.I):
                    status, reason = "suspended", "source PR explicitly says the scheduler remains suspended"
                else:
                    status, reason = "not_deployed", "source PR is open and not on the deployment branch"
            else:
                status, reason = "closed_unmerged", "source PR is closed without merge"
        elif kind == "github_file_regex":
            repository, path = str(probe.get("repository") or ""), str(probe.get("path") or "")
            text = client.text_file(repository, path, str(probe.get("ref") or "main"))
            if text is None:
                status, reason = "missing", "configured schedule source file is not visible"
            elif str(probe.get("suspended_regex") or "") and re.search(str(probe["suspended_regex"]), text, re.M):
                status, reason = "suspended", "deployment source explicitly suspends the schedule"
            elif str(probe.get("active_regex") or "") and re.search(str(probe["active_regex"]), text, re.M):
                status, reason = "unobserved", "schedule source is active, but no runtime ledger is connected"
            else:
                status, reason = "unknown", "schedule source exists but activation state is ambiguous"
    if expected == 0 and status in {"unobserved", "missing", "not_deployed", "suspended"}:
        status, reason = "not_due", "task had no expected occurrence inside the 24-hour window"
    return {"source": "external_inventory", "id": task.get("id"), "name": task.get("name"), "schedule": schedule, "timezone": zone, "expected_occurrences": expected, "observed_runs": None, "status": status, "reason": reason, "url": task.get("source_url"), "notes": task.get("notes")}


def digest_sha256(digest: Mapping[str, Any]) -> str:
    payload = dict(digest)
    payload.pop("digest_sha256", None)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def collect_digest(config: Mapping[str, Any], client: GitHubClient, *, now: datetime, exclude_repository: str = "", exclude_run_id: int = 0) -> dict[str, Any]:
    del exclude_run_id  # The digest workflow itself is excluded by path below, avoiding a self-referential in-progress record.
    hours = int(config.get("lookback_hours") or 24)
    end = now.astimezone(timezone.utc)
    start = end - timedelta(hours=hours)
    repositories, errors = client.repositories([str(item) for item in config.get("explicit_repositories", [])], int(config.get("max_repositories") or 1000))
    max_workflows = int(config.get("max_workflow_files") or 3000)
    per_repo_limit = max(10, max_workflows // max(1, len(repositories)))
    concurrency = max(1, min(16, int(config.get("repository_concurrency") or 8)))
    records: list[dict[str, Any]] = []
    scheduled_total = 0
    def scan(repo: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str], int]:
        exclusion = ".github/workflows/scheduled-task-digest.yml" if str(repo.get("full_name")) == exclude_repository else None
        return scan_repository(repo, client, start=start, end=end, max_workflows=per_repo_limit, exclude_workflow=exclusion)
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(scan, repo) for repo in repositories]
        for future in concurrent.futures.as_completed(futures):
            try:
                found, scan_errors, count = future.result()
            except Exception as error:  # Fail the coverage ledger safely without losing the email.
                errors.append(f"repository scan crashed: {type(error).__name__}: {error}")
                continue
            records.extend(found)
            errors.extend(scan_errors)
            scheduled_total += count
    for task in config.get("external_tasks", []):
        if isinstance(task, dict):
            try:
                records.append(evaluate_external_task(task, client, start=start, end=end))
            except DigestError as error:
                records.append({"source": "external_inventory", "id": task.get("id"), "name": task.get("name"), "status": "unknown", "reason": str(error), "expected_occurrences": 0, "observed_runs": None})
                errors.append(f"external task {task.get('id')}: {error}")
    records.sort(key=lambda item: (_STATUS_ORDER.get(str(item.get("status")), 999), str(item.get("repository") or ""), str(item.get("name") or "")))
    counts: dict[str, int] = {}
    for item in records:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    summary = {
        "critical": sum(counts.get(value, 0) for value in CRITICAL),
        "attention": sum(counts.get(value, 0) for value in ATTENTION),
        "success": counts.get("success", 0), "not_due": counts.get("not_due", 0),
        "total": len(records), "status_counts": counts,
    }
    digest: dict[str, Any] = {
        "schema_version": DIGEST_SCHEMA, "generated_at": format_instant(end),
        "window": {"start": format_instant(start), "end": format_instant(end), "hours": hours},
        "recipient": config["recipient"], "summary": summary,
        "coverage": {"complete": not errors, "repositories_scanned": len(repositories), "scheduled_workflows": scheduled_total, "schedule_runs": sum(int(item.get("observed_runs") or 0) for item in records if item.get("source") == "github_actions"), "errors": sorted(set(errors)), "github_rate_remaining": client.rate_remaining},
        "records": records,
    }
    digest["digest_sha256"] = digest_sha256(digest)
    return digest

