from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

CONFIG_SCHEMA = "scheduled_task_digest_config.v1"
DIGEST_SCHEMA = "scheduled_task_digest.v1"
RECEIPT_SCHEMA = "scheduled_task_digest_delivery_receipt.v1"
DEFAULT_TIMEZONE = "America/Chicago"
DEFAULT_LOCAL_TIME = "07:00"
DEFAULT_API_URL = "https://api.github.com"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
FAILURES = {"failure", "timed_out", "action_required", "startup_failure", "stale"}
CRITICAL = {"failure", "cancelled", "false_green", "missed", "missing", "not_deployed", "closed_unmerged", "delivery_failed"}
ATTENTION = {"suspended", "disabled", "skipped", "unverified_success", "unobserved", "partial", "neutral", "running", "unknown", "source_merged_unobserved"}
LABELS = {
    "failure": "FAILED", "cancelled": "CANCELLED", "false_green": "FALSE GREEN",
    "missed": "MISSED", "missing": "MISSING", "not_deployed": "NOT DEPLOYED",
    "closed_unmerged": "CLOSED / NOT MERGED", "suspended": "SUSPENDED",
    "disabled": "DISABLED", "skipped": "SKIPPED", "unverified_success": "UNVERIFIED GREEN",
    "unobserved": "NO RUNTIME EVIDENCE", "partial": "PARTIAL COVERAGE", "neutral": "NEUTRAL",
    "running": "RUNNING", "unknown": "UNKNOWN", "source_merged_unobserved": "SOURCE MERGED / RUNTIME UNVERIFIED",
    "not_due": "NOT DUE", "success": "SUCCESS",
}
VALIDATION_HINTS = ("validate", "validation", "lint", "actionlint", "compile", "unit test", "tests", "schema", "matrix", "render", "summary", "report", "upload", "collector")
WORKLOAD_HINTS = ("enqueue", "deliver", "discover", "audit", "maintain", "reconcile", "recovery", "outreach", "contact", "publish", "deploy", "build fleet", "execute", "worker", "scan", "harden")
GUARD_HINTS = ("timezone-guard", "timezone guard", "schedule guard")


class DigestError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScheduleDecision:
    due: bool
    local_time: datetime
    logical_date: str
    run_key: str
    artifact_name: str


@dataclass(frozen=True)
class CompiledCron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    day_wildcard: bool
    weekday_wildcard: bool

    def matches(self, value: datetime) -> bool:
        if value.minute not in self.minutes or value.hour not in self.hours or value.month not in self.months:
            return False
        day_ok = value.day in self.days
        weekday_ok = ((value.weekday() + 1) % 7) in self.weekdays
        if self.day_wildcard and self.weekday_wildcard:
            return True
        if self.day_wildcard:
            return weekday_ok
        if self.weekday_wildcard:
            return day_ok
        return day_ok or weekday_ok


def parse_instant(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise DigestError(f"invalid timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise DigestError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def format_instant(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_local_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        raise DigestError(f"invalid local time: {value!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise DigestError(f"invalid local time: {value!r}")
    return hour, minute


def schedule_decision(now: datetime, *, timezone_name: str = DEFAULT_TIMEZONE, local_time: str = DEFAULT_LOCAL_TIME, force: bool = False) -> ScheduleDecision:
    if now.tzinfo is None:
        raise DigestError("now must be timezone-aware")
    local = now.astimezone(ZoneInfo(timezone_name))
    hour, minute = parse_local_time(local_time)
    # Accept the full scheduled hour so GitHub's documented schedule delays do not turn a due run into a no-op.
    due = force or (local.hour == hour and local.minute >= minute)
    logical = local.date().isoformat()
    return ScheduleDecision(due, local, logical, f"scheduled-task-digest:{logical}", f"scheduled-task-digest-receipt-{logical}")


def _expand_atom(atom: str, minimum: int, maximum: int, *, weekday: bool = False) -> set[int]:
    base, slash, step_text = atom.partition("/")
    try:
        step = int(step_text) if slash else 1
    except ValueError as error:
        raise DigestError(f"invalid cron step: {atom!r}") from error
    if step < 1:
        raise DigestError(f"invalid cron step: {atom!r}")
    if base == "*":
        start, end = minimum, maximum
    elif "-" in base:
        left, right = base.split("-", 1)
        try:
            start, end = int(left), int(right)
        except ValueError as error:
            raise DigestError(f"invalid cron range: {atom!r}") from error
    else:
        try:
            start = end = int(base)
        except ValueError as error:
            raise DigestError(f"invalid cron value: {atom!r}") from error
    if not (minimum <= start <= maximum and minimum <= end <= maximum) or start > end:
        raise DigestError(f"cron value out of range: {atom!r}")
    values = set(range(start, end + 1, step))
    return {0 if weekday and value == 7 else value for value in values}


def _field(text: str, minimum: int, maximum: int, *, weekday: bool = False) -> tuple[frozenset[int], bool]:
    text = text.strip()
    if not text:
        raise DigestError("empty cron field")
    values: set[int] = set()
    for atom in text.split(","):
        values.update(_expand_atom(atom.strip(), minimum, maximum, weekday=weekday))
    return frozenset(values), text == "*"


def compile_cron(expression: str) -> CompiledCron:
    parts = expression.strip().split()
    if len(parts) != 5:
        raise DigestError(f"cron must contain five fields: {expression!r}")
    minutes, _ = _field(parts[0], 0, 59)
    hours, _ = _field(parts[1], 0, 23)
    days, day_wildcard = _field(parts[2], 1, 31)
    months, _ = _field(parts[3], 1, 12)
    weekdays, weekday_wildcard = _field(parts[4], 0, 7, weekday=True)
    return CompiledCron(minutes, hours, days, months, weekdays, day_wildcard, weekday_wildcard)


def extract_crons(workflow_text: str) -> list[str]:
    result: list[str] = []
    pattern = re.compile(r"^\s*-?\s*cron\s*:\s*(?P<value>.+?)\s*$")
    for line in workflow_text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        value = re.sub(r"\s+#.*$", "", match.group("value").strip()).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if value:
            result.append(value)
    return result


def expected_occurrences(expressions: Sequence[str], start: datetime, end: datetime, *, timezone_name: str = "UTC") -> int:
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise DigestError("cron window must be ordered and timezone-aware")
    compiled = [compile_cron(value) for value in expressions]
    zone = ZoneInfo(timezone_name)
    cursor = start.astimezone(timezone.utc).replace(second=0, microsecond=0)
    limit = end.astimezone(timezone.utc)
    count = 0
    while cursor < limit:
        local = cursor.astimezone(zone)
        if any(cron.matches(local) for cron in compiled):
            count += 1
        cursor += timedelta(minutes=1)
    return count


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, parsed.query, ""))


def _safe_message(body: str) -> str:
    try:
        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get("message"):
            return str(payload["message"])[:300]
    except json.JSONDecodeError:
        pass
    return re.sub(r"\s+", " ", body).strip()[:300] or "empty response"


class GitHubClient:
    def __init__(self, token: str, *, api_url: str = DEFAULT_API_URL, timeout: float = 30.0, opener: Any | None = None) -> None:
        self.token = token.strip()
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener()
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.rate_remaining: int | None = None

    def _url(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        base = path if path.startswith(("http://", "https://")) else f"{self.api_url}/{path.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None])
            base += ("&" if "?" in base else "?") + query
        return base

    def get_json(self, path: str, params: Mapping[str, Any] | None = None, *, optional: bool = False, cache: bool = False) -> Any:
        url = self._url(path, params)
        if cache:
            with self._lock:
                if url in self._cache:
                    return self._cache[url]
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "scheduled-task-digest/1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise DigestError("GitHub response exceeded safe size limit")
                remaining = response.headers.get("X-RateLimit-Remaining")
                if remaining and remaining.isdigit():
                    self.rate_remaining = int(remaining)
                payload = json.loads(raw.decode()) if raw else None
        except urllib.error.HTTPError as error:
            if optional and error.code in {403, 404, 409}:
                return None
            body = error.read(4096).decode(errors="replace")
            raise DigestError(f"GitHub HTTP {error.code} for {_safe_url(url)}: {_safe_message(body)}") from None
        except urllib.error.URLError as error:
            raise DigestError(f"GitHub request failed for {_safe_url(url)}: {error.reason}") from None
        except json.JSONDecodeError as error:
            raise DigestError(f"GitHub returned malformed JSON for {_safe_url(url)}") from error
        if cache:
            with self._lock:
                self._cache[url] = payload
        return payload

    def paginate(self, path: str, *, item_key: str | None = None, params: Mapping[str, Any] | None = None, max_pages: int = 10) -> list[Any]:
        out: list[Any] = []
        for page in range(1, max_pages + 1):
            payload = self.get_json(path, {**dict(params or {}), "per_page": 100, "page": page})
            items = payload.get(item_key, []) if item_key and isinstance(payload, dict) else payload
            if not isinstance(items, list):
                raise DigestError(f"GitHub list response had an unexpected shape for {path}")
            out.extend(items)
            if len(items) < 100:
                return out
        raise DigestError(f"GitHub pagination limit exceeded for {path}")

    def repositories(self, explicit: Sequence[str], maximum: int) -> tuple[list[dict[str, Any]], list[str]]:
        repos: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        if self.token:
            try:
                for item in self.paginate("/user/repos", params={"affiliation": "owner,organization_member,collaborator", "sort": "full_name"}, max_pages=max(1, (maximum + 99) // 100)):
                    if isinstance(item, dict) and item.get("full_name"):
                        repos[str(item["full_name"])] = item
            except DigestError as error:
                errors.append(str(error))
        for full_name in explicit:
            if full_name in repos:
                continue
            try:
                item = self.get_json(f"/repos/{full_name}", optional=True)
                if isinstance(item, dict):
                    repos[full_name] = item
                else:
                    errors.append(f"explicit repository is not visible: {full_name}")
            except DigestError as error:
                errors.append(str(error))
        values = sorted(repos.values(), key=lambda item: str(item.get("full_name", "")).lower())
        if len(values) > maximum:
            errors.append(f"repository inventory truncated from {len(values)} to {maximum}")
            values = values[:maximum]
        return values, errors

    def text_file(self, repository: str, path: str, ref: str | None = None) -> str | None:
        payload = self.get_json(f"/repos/{repository}/contents/{path}", {"ref": ref} if ref else None, optional=True)
        if not isinstance(payload, dict):
            return None
        content = payload.get("content")
        if payload.get("encoding") == "base64" and isinstance(content, str):
            return base64.b64decode(content).decode("utf-8", errors="replace")
        return None

    def receipt_artifacts(self, repository: str, name: str) -> list[dict[str, Any]]:
        payload = self.get_json(f"/repos/{repository}/actions/artifacts", {"name": name, "per_page": 100}, optional=True)
        if not isinstance(payload, dict):
            return []
        return [item for item in payload.get("artifacts", []) if isinstance(item, dict) and not item.get("expired")]


def _name(job: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", " ", str(job.get("name") or "").strip().lower())


def _has(name: str, hints: Sequence[str]) -> bool:
    return any(hint in name for hint in hints)


def classify_run(run: Mapping[str, Any], jobs: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if status and status != "completed":
        return "running", f"run is {status}"
    if conclusion in FAILURES:
        return "failure", f"run concluded {conclusion}"
    if conclusion == "cancelled":
        return "cancelled", "run was cancelled"
    if conclusion == "skipped":
        return "skipped", "workflow run was skipped"
    if conclusion in {"neutral", "action_required"}:
        return "neutral", f"run concluded {conclusion}"
    if conclusion != "success":
        return "unknown", f"run conclusion is {conclusion or 'unset'}"
    normalized = [(_name(job), str(job.get("conclusion") or "").lower()) for job in jobs]
    guard_ok = any(_has(name, GUARD_HINTS) and result == "success" for name, result in normalized)
    workload = [(name, result) for name, result in normalized if _has(name, WORKLOAD_HINTS)]
    if guard_ok and workload and all(result == "skipped" for _, result in workload):
        return "not_due", "timezone guard rejected the alternate UTC invocation"
    if any(result == "success" for _, result in workload):
        return "success", "a substantive workload job completed successfully"
    if workload and any(result in {"skipped", "cancelled", ""} for _, result in workload):
        return "false_green", "workflow was green while substantive workload jobs were skipped or absent"
    if normalized and all(_has(name, VALIDATION_HINTS) for name, _ in normalized):
        return "unverified_success", "only validation/reporting jobs completed; workload execution was not certified"
    return "unverified_success", "top-level success lacks certified substantive job evidence"


_STATUS_ORDER = {name: index for index, name in enumerate(["failure", "cancelled", "false_green", "missed", "missing", "not_deployed", "closed_unmerged", "delivery_failed", "suspended", "disabled", "partial", "skipped", "unverified_success", "unobserved", "running", "unknown", "neutral", "source_merged_unobserved", "success", "not_due"])}


def worst_status(statuses: Sequence[str]) -> str:
    return min(statuses or ["unknown"], key=lambda value: _STATUS_ORDER.get(value, 999))


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DigestError(f"unable to load digest config {path}: {error}") from error
    if config.get("schema_version") != CONFIG_SCHEMA:
        raise DigestError("scheduled-task digest config schema is missing or unsupported")
    if not str(config.get("recipient") or "").strip():
        raise DigestError("scheduled-task digest recipient is required")
    parse_local_time(str(config.get("delivery_local_time") or DEFAULT_LOCAL_TIME))
    ZoneInfo(str(config.get("timezone") or DEFAULT_TIMEZONE))
    return config

