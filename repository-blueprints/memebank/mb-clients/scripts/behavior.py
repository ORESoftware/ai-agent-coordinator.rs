#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

PATH_PARAM = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def build_plan(operation: dict[str, Any], path_parameters: dict[str, Any], query: dict[str, Any], headers: dict[str, str], policy: dict[str, Any]) -> dict[str, Any]:
    expected = set(PATH_PARAM.findall(operation["path_template"]))
    if set(path_parameters) != expected:
        raise ValueError(f"path parameters for {operation['operation_id']} must equal {sorted(expected)}")
    path = operation["path_template"]
    for name, value in sorted(path_parameters.items()):
        path = path.replace("{" + name + "}", quote(str(value), safe=""))
    if query:
        values = []
        for key, value in sorted(query.items()):
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            elif isinstance(value, (str, int)):
                rendered = str(value)
            else:
                raise ValueError(f"unsupported query value for {key}")
            values.append((key, rendered))
        path += "?" + urlencode(values)
    lowered = {name.lower(): value for name, value in headers.items()}
    present = "idempotency-key" in lowered
    mode = operation["idempotency_key"]
    if mode == "required" and not present:
        raise ValueError(f"{operation['operation_id']} requires Idempotency-Key")
    if mode == "forbidden" and present:
        raise ValueError(f"{operation['operation_id']} forbids Idempotency-Key")
    if present and (not 16 <= len(lowered["idempotency-key"]) <= 200 or not re.fullmatch(r"[A-Za-z0-9._:-]+", lowered["idempotency-key"])):
        raise ValueError("Idempotency-Key is invalid")
    return {
        "operation_id": operation["operation_id"],
        "method": operation["method"],
        "path": path,
        "content_type": operation["request_content_type"],
        "timeout_ms": policy["request"]["default_timeout_ms"],
        "retry_class": operation["retry_class"],
        "retryable": operation["retry_class"] != "never",
    }


def should_retry(retry_class: str, status: int, attempt: int, body_started: bool, policy: dict[str, Any]) -> bool:
    retry = policy["retry"]
    return retry_class != "never" and attempt < retry["max_attempts"] and not body_started and status in retry["retry_statuses"]


def redact_headers(headers: dict[str, str], policy: dict[str, Any]) -> dict[str, str]:
    names = set(policy["redaction"]["header_names"])
    replacement = policy["redaction"]["replacement"]
    return {key: replacement if key.lower() in names else value for key, value in sorted(headers.items())}


def redact_url(url: str, policy: dict[str, Any]) -> str:
    split = urlsplit(url)
    names = set(policy["redaction"]["query_parameter_names"])
    replacement = policy["redaction"]["replacement"]
    query = urlencode([(key, replacement if key.lower() in names else value) for key, value in parse_qsl(split.query, keep_blank_values=True)])
    return urlunsplit((split.scheme, split.netloc, split.path, query, split.fragment))


def advance_cursor(current: dict[str, int], through: dict[str, int]) -> dict[str, int]:
    if (through["epoch"], through["sequence"]) < (current["epoch"], current["sequence"]):
        raise ValueError("through_cursor must not move backwards")
    return {"epoch": through["epoch"], "sequence": through["sequence"]}
