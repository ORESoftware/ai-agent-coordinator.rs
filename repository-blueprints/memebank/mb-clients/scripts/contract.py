#!/usr/bin/env python3
from __future__ import annotations

import re
from typing import Any

from io_helpers import arr, boolean, integer, obj, text

OP_ID = re.compile(r"^[a-z][A-Za-z0-9]+$")
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
RETRY = {"safe_read", "idempotent_write", "never"}
IDEMPOTENCY = {"required", "optional", "forbidden"}


def validate_operations(document: Any) -> list[dict[str, Any]]:
    root = obj(document, "operations")
    if root.get("schema_version") != 1:
        raise ValueError("operations.schema_version must equal 1")
    if root.get("interface_package") != "memebank/mb-interfaces":
        raise ValueError("operations.interface_package must equal memebank/mb-interfaces")
    for name in ("interface_version", "openapi_contract_version", "source_path"):
        text(root.get(name), f"operations.{name}")
    deferred = arr(root.get("deferred_families"), "operations.deferred_families")
    if len(deferred) != len(set(deferred)):
        raise ValueError("deferred families must be unique")

    results: list[dict[str, Any]] = []
    ids: set[str] = set()
    routes: set[tuple[str, str]] = set()
    for index, raw in enumerate(arr(root.get("operations"), "operations.operations")):
        field = f"operations[{index}]"
        item = obj(raw, field)
        op_id = text(item.get("operation_id"), f"{field}.operation_id")
        if not OP_ID.fullmatch(op_id):
            raise ValueError(f"{field}.operation_id is invalid")
        if op_id in ids:
            raise ValueError(f"duplicate operation_id: {op_id}")
        ids.add(op_id)
        method = text(item.get("method"), f"{field}.method").upper()
        path = text(item.get("path_template"), f"{field}.path_template")
        if method not in METHODS or not path.startswith("/v1/") or "?" in path or ".." in path:
            raise ValueError(f"{field} has an invalid method/path")
        if (method, path) in routes:
            raise ValueError(f"duplicate method/path: {method} {path}")
        routes.add((method, path))
        statuses = arr(item.get("success_statuses"), f"{field}.success_statuses")
        if not statuses or any(not 100 <= integer(value, f"{field}.status", 100) <= 399 for value in statuses):
            raise ValueError(f"{field}.success_statuses is invalid")
        if boolean(item.get("auth_required"), f"{field}.auth_required") is not True:
            raise ValueError(f"{field}.auth_required must be true")
        if boolean(item.get("request_id_required"), f"{field}.request_id_required") is not True:
            raise ValueError(f"{field}.request_id_required must be true")
        idem = text(item.get("idempotency_key"), f"{field}.idempotency_key")
        retry = text(item.get("retry_class"), f"{field}.retry_class")
        if idem not in IDEMPOTENCY or retry not in RETRY:
            raise ValueError(f"{field} has invalid retry/idempotency policy")
        if method == "GET" and idem != "forbidden":
            raise ValueError(f"{field} GET must forbid idempotency keys")
        if retry == "idempotent_write" and idem == "forbidden":
            raise ValueError(f"{field} retryable write must permit an idempotency key")
        for name in ("streaming_request", "streaming_response"):
            boolean(item.get(name), f"{field}.{name}")
        sensitive = arr(item.get("sensitive_response_fields"), f"{field}.sensitive_response_fields")
        if len(sensitive) != len(set(sensitive)):
            raise ValueError(f"{field}.sensitive_response_fields must be unique")
        normalized = dict(item)
        normalized["method"] = method
        results.append(normalized)
    if not results:
        raise ValueError("operations must not be empty")
    return results


def validate_policy(document: Any) -> dict[str, Any]:
    policy = obj(document, "policy")
    if policy.get("schema_version") != 1:
        raise ValueError("policy.schema_version must equal 1")
    request = obj(policy.get("request"), "policy.request")
    timeout = integer(request.get("default_timeout_ms"), "default_timeout_ms", 1)
    if integer(request.get("connect_timeout_ms"), "connect_timeout_ms", 1) > timeout:
        raise ValueError("connect timeout cannot exceed request timeout")
    for name in ("max_error_body_bytes", "max_json_body_bytes"):
        integer(request.get(name), name, 1)
    retry = obj(policy.get("retry"), "policy.retry")
    attempts = integer(retry.get("max_attempts"), "max_attempts", 1)
    if attempts > 10:
        raise ValueError("policy.retry.max_attempts must be bounded at 10")
    base = integer(retry.get("base_delay_ms"), "base_delay_ms", 1)
    if integer(retry.get("max_delay_ms"), "max_delay_ms", base) > 60_000:
        raise ValueError("max retry delay is too high")
    if retry.get("jitter") != "full" or retry.get("never_retry_after_response_body_started") is not True:
        raise ValueError("retry policy must use full jitter and stop after body start")
    statuses = arr(retry.get("retry_statuses"), "retry_statuses")
    if not statuses or len(statuses) != len(set(statuses)):
        raise ValueError("retry statuses must be unique and non-empty")
    auth = obj(policy.get("auth"), "policy.auth")
    if auth != {
        "provider_contract": "shared-auth/access-token-provider-v1",
        "refresh_on_statuses": [401],
        "max_refresh_attempts_per_request": 1,
        "token_debug_output": "redacted",
    }:
        raise ValueError("auth policy must use bounded shared-auth refresh and redaction")
    redaction = obj(policy.get("redaction"), "policy.redaction")
    if redaction.get("replacement") != "[REDACTED]":
        raise ValueError("redaction replacement must be [REDACTED]")
    for name in ("header_names", "query_parameter_names", "json_field_names"):
        values = arr(redaction.get(name), name)
        if not values or len(values) != len(set(values)) or any(value != value.lower() for value in values):
            raise ValueError(f"{name} must be unique lowercase values")
    pagination = obj(policy.get("pagination"), "pagination")
    if pagination.get("sync_cursor_fields") != ["epoch", "sequence"] or pagination.get("advance_sync_cursor_from") != "through_cursor" or pagination.get("advance_on_final_page") is not True:
        raise ValueError("sync cursor policy must advance from through_cursor on final pages")
    forbidden = set(obj(policy.get("observability"), "observability").get("forbidden_attributes", []))
    if not {"authorization", "request_body", "response_body", "ocr_text", "caption", "signed_url"} <= forbidden:
        raise ValueError("policy.observability.forbidden_attributes is incomplete")
    publication = obj(policy.get("publication"), "publication")
    if publication.get("source_branch") != "main" or not all(publication.get(name) is True for name in ("require_clean_generated_tree", "require_reviewed_commit", "require_provenance", "require_changelog")):
        raise ValueError("publication gates are incomplete")
    return policy
