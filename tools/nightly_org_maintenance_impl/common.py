"""Implementation module for bounded nightly organization maintenance."""


from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

TIME_ZONE_NAME = "America/Lima"
TIME_ZONE = ZoneInfo(TIME_ZONE_NAME)
TARGET_HOUR = 0
TARGET_MINUTE = 30
DEFAULT_REGISTRY = Path("config/org-project-registry.yaml")
DEFAULT_POLICY = Path("config/nightly-org-maintenance-policy.json")
DEFAULT_GITHUB_API = "https://api.github.com"
DEFAULT_LINEAR_API = "https://api.linear.app/graphql"
MAX_HTTP_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_JSON_FILE_BYTES = 8 * 1024 * 1024
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH_RE = re.compile(r"^agent/nightly-[a-z0-9][a-z0-9._/-]{4,180}$")
LINEAR_ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
CONFLICT_MARKER_RE = re.compile(r"^(?:<{7}|={7}|>{7})(?: |$)", re.MULTILINE)
SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\blin_api_[A-Za-z0-9_-]{16,}"),
)
ALLOWED_PLAN_ACTIONS = {"merge_if_green", "repair_with_replacement_pr", "skip"}
ALLOWED_RISK_LEVELS = {"low", "medium"}
ALLOWED_CHECK_CONCLUSIONS = {"SUCCESS", "SKIPPED", "NEUTRAL"}
TERMINAL_PR_STATES = {"MERGED", "CLOSED"}


class MaintenanceError(RuntimeError):
    """A bounded, user-facing workflow failure."""


class NoRedirect(HTTPRedirectHandler):
    """Never forward credentials to a redirect target."""

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
    local_time: datetime
    run_key: str


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str


def _clean_text(value: Any, *, limit: int) -> str:
    text = CONTROL_CHAR_RE.sub("", str(value or "")).strip()
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED_CREDENTIAL]", text)
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MaintenanceError(f"{label} must be a JSON object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise MaintenanceError(f"{label} must be a JSON array")
    return value


def load_json(path: Path, *, label: str) -> Any:
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise MaintenanceError(f"{label} was not found: {path}") from exc
    if size > MAX_JSON_FILE_BYTES:
        raise MaintenanceError(f"{label} exceeds the size limit")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MaintenanceError(f"{label} is not valid JSON-compatible YAML") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(rendered, encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def parse_instant(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MaintenanceError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise MaintenanceError("--now must include a UTC offset or Z")
    return parsed.astimezone(timezone.utc)


def schedule_decision(now: datetime, *, force: bool = False) -> ScheduleDecision:
    if now.tzinfo is None:
        raise MaintenanceError("schedule time must be timezone-aware")
    local_time = now.astimezone(TIME_ZONE)
    run_key = f"nightly-org-maintenance:{local_time.date().isoformat()}"
    due = force or (local_time.hour == TARGET_HOUR and local_time.minute == TARGET_MINUTE)
    return ScheduleDecision(due=due, local_time=local_time, run_key=run_key)


def validate_endpoint(raw: str, *, label: str, allow_loopback_http: bool = True) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        raise MaintenanceError(f"{label} is required")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"}:
        raise MaintenanceError(f"{label} must use HTTPS or loopback HTTP")
    if parsed.username or parsed.password:
        raise MaintenanceError(f"{label} must not contain credentials")
    if parsed.query or parsed.fragment or not parsed.hostname:
        raise MaintenanceError(f"{label} must be a plain origin URL")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (allow_loopback_http and loopback):
        raise MaintenanceError(f"non-loopback {label} must use HTTPS")
    return value


def _mapping_by_owner(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    mappings = _require_list(registry.get("mappings"), "registry.mappings")
    result: dict[str, dict[str, Any]] = {}
    seen_account_ids: set[int] = set()
    for index, raw in enumerate(mappings):
        item = _require_mapping(raw, f"registry.mappings[{index}]")
        github = _require_mapping(item.get("github"), f"registry.mappings[{index}].github")
        login = _clean_text(github.get("login"), limit=100)
        account_type = _clean_text(github.get("account_type"), limit=30)
        account_id = github.get("account_id")
        if not OWNER_RE.fullmatch(login):
            raise MaintenanceError(f"registry owner is invalid: {login!r}")
        if login.casefold() in {owner.casefold() for owner in result}:
            raise MaintenanceError(f"registry contains duplicate owner {login}")
        if not isinstance(account_id, int) or account_id <= 0:
            raise MaintenanceError(f"registry owner {login} has an invalid account_id")
        if account_id in seen_account_ids:
            raise MaintenanceError(f"registry account_id {account_id} is duplicated")
        seen_account_ids.add(account_id)
        item = dict(item)
        item["_account_type"] = account_type
        result[login] = item
    return result


def _policy_set(policy: Mapping[str, Any], key: str) -> set[str]:
    values = policy.get(key, [])
    if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
        raise MaintenanceError(f"policy.{key} must be an array of strings")
    return {item.casefold() for item in values}

__all__ = [name for name in globals() if not name.startswith("__")]
