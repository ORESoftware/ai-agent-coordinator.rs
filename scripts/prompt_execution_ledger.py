#!/usr/bin/env python3
"""Strict, public-safe validation for prompt execution ledgers."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA = "prompt-execution-ledger/v1"
WORKSTREAMS = 15
MAX_BYTES = 256 * 1024
MAX_TEXT = 2_000
ROOT = {"schema_version", "ledger_id", "generated_at", "window", "source_segments", "coverage", "workstreams", "safety"}
WINDOW = {"start", "end", "timezone", "selection_basis"}
SEGMENT = {"segment_id", "start", "end", "complete", "receipt_type", "receipt_anchor", "assertion_sha256", "record_counts", "incomplete_reason"}
COUNTS = {"human_messages", "threads", "replies", "bots", "deleted", "empty_or_attachment_only"}
COVERAGE = {"source_complete", "verified_through", "actionable_prompt_count", "mapped_prompt_count", "unresolved_prompt_count", "closure_blockers"}
WORK = {"id", "title", "priority", "status", "linear_anchors", "github_anchors", "acceptance_checks", "depends_on", "safety_boundary"}
SAFETY = {"contains_raw_messages", "contains_credentials", "live_mutations_authorized", "merge_authorized", "source_gap_blocks_closure", "notes"}
PRIORITIES = {"urgent", "high", "medium", "low"}
STATUSES = {"blocked", "active", "queued", "in_review", "done"}
RECEIPTS = {"google_chat_bridge", "linear_reconciliation", "github_tracking_issue"}
LINEAR = re.compile(r"^DEN-[1-9][0-9]*$")
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,95}$")
SHA = re.compile(r"^[0-9a-f]{64}$")
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
BAD_FIELDS = {"raw_message", "raw_message_body", "message_body", "message_text", "raw_prompt", "prompt_text", "credential", "credential_value", "secret_value", "access_token", "api_key", "authorization"}
BAD_TEXT = (
    re.compile(r"(?i)chat[_-]?bridge[_-]?token\s*[:=]"),
    re.compile(r"\bghp_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\blin_api_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"(?i)[?&](?:token|access_token|api[_-]?key|secret|password)="),
    re.compile(r"(?i)\b(?:token|api[_-]?key|secret|password)\s*[:=]\s*[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class Report:
    valid: bool
    closure_ready: bool
    mode: str
    ledger_sha256: str | None
    workstream_count: int
    incomplete_segments: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            raise LedgerError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _unsafe(text: str) -> bool:
    return bool(EMAIL.search(text) or any(pattern.search(text) for pattern in BAD_TEXT))


def load(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BYTES:
        raise LedgerError(f"ledger exceeds {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LedgerError("ledger must be UTF-8") from exc
    if EMAIL.search(text):
        raise LedgerError("ledger must not contain email addresses")
    if any(pattern.search(text) for pattern in BAD_TEXT):
        raise LedgerError("ledger contains prohibited credential material")
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise LedgerError("ledger root must be an object")
    return value


def digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _keys(value: Any, expected: set[str], path: str, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return None
    if set(value) != expected:
        errors.append(f"{path} keys mismatch: missing={sorted(expected-set(value))}, extra={sorted(set(value)-expected)}")
    return value


def _text(value: Any, path: str, errors: list[str], low: int = 1, high: int = MAX_TEXT) -> str | None:
    if not isinstance(value, str) or not low <= len(value) <= high:
        errors.append(f"{path} must be a string of length {low}..{high}")
        return None
    return value


def _time(value: Any, path: str, errors: list[str]) -> datetime | None:
    text = _text(value, path, errors, 20, 64)
    if text is None:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path} must be ISO-8601")
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        errors.append(f"{path} must include a UTC offset")
        return None
    return stamp


def _list(value: Any, path: str, errors: list[str], low: int, high: int) -> list[Any] | None:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return None
    if not low <= len(value) <= high:
        errors.append(f"{path} must contain between {low} and {high} items")
    return value


def _strings(value: Any, path: str, errors: list[str], low: int, high: int, min_len: int = 1, max_len: int = 500) -> list[str]:
    items = _list(value, path, errors, low, high) or []
    out = [item for index, item in enumerate(items) if _text(item, f"{path}[{index}]", errors, min_len, max_len) is not None]
    if len(out) != len(set(out)):
        errors.append(f"{path} must not contain duplicates")
    return out


def _count(value: Any, path: str, errors: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{path} must be a non-negative integer")
        return None
    return value


def _github(value: str, path: str, errors: list[str]) -> None:
    url = urlparse(value)
    parts = [part for part in url.path.split("/") if part]
    if not (url.scheme == "https" and url.netloc == "github.com" and not url.query and not url.fragment and len(parts) == 4 and parts[2] in {"issues", "pull"} and parts[3].isdigit() and int(parts[3]) > 0):
        errors.append(f"{path} must be an exact https://github.com/owner/repo/issues|pull/N URL")


def _walk(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} has a non-string key")
            elif key.casefold() in BAD_FIELDS:
                errors.append(f"{path}.{key} is a prohibited raw/sensitive field")
            _walk(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk(child, f"{path}[{index}]", errors)
    elif isinstance(value, str):
        if len(value) > MAX_TEXT:
            errors.append(f"{path} exceeds the public-ledger text bound")
        if _unsafe(value):
            errors.append(f"{path} contains prohibited personal or credential material")


def _cycles(graph: Mapping[str, Sequence[str]]) -> list[str]:
    visiting: set[str] = set()
    done: set[str] = set()
    stack: list[str] = []
    found: list[str] = []

    def visit(node: str) -> None:
        if node in done:
            return
        if node in visiting:
            found.append(" -> ".join(stack[stack.index(node):] + [node]))
            return
        visiting.add(node)
        stack.append(node)
        for edge in graph.get(node, ()):
            if edge in graph:
                visit(edge)
        stack.pop()
        visiting.remove(node)
        done.add(node)

    for node in graph:
        visit(node)
    return found
