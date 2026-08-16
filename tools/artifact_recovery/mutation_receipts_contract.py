"""Strict normalization for coordinator mutation intents and results."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .common import (
    RecoveryError,
    expect_bool,
    expect_list,
    expect_object,
    expect_string,
    parse_timestamp,
    sha256_value,
)

SCHEMA_VERSION = "coordinator_mutation_receipts.v1"
FAILURE_LEDGER_SCHEMA = "coordinator_mutation_failure_ledger.v1"
SYSTEMS = ("linear", "github")
RESOURCE_KINDS = (
    "linear_issue",
    "linear_comment_thread",
    "github_branch",
    "github_pull_request",
    "github_issue",
    "github_comment_thread",
)
OPERATIONS = (
    "linear_update_issue",
    "linear_create_comment",
    "linear_set_relation",
    "linear_update_status",
    "github_create_branch",
    "github_update_branch",
    "github_create_pull_request",
    "github_update_pull_request",
    "github_create_comment",
)
OPERATION_SYSTEM = {
    "linear_update_issue": "linear",
    "linear_create_comment": "linear",
    "linear_set_relation": "linear",
    "linear_update_status": "linear",
    "github_create_branch": "github",
    "github_update_branch": "github",
    "github_create_pull_request": "github",
    "github_update_pull_request": "github",
    "github_create_comment": "github",
}
RELATION_OPERATIONS = {"linear_set_relation"}
CHAIN_STEPS = (
    "linear_issue",
    "routing",
    "branch",
    "commit",
    "pull_request",
    "evidence",
    "linear_status",
)
COMPENSATIONS = (
    "none",
    "restore_previous_state",
    "remove_created_relation",
    "close_created_pull_request",
    "delete_created_branch",
    "revert_comment",
    "manual_review",
)
RESULT_OUTCOMES = ("accepted", "rejected", "provider_error", "compensated")
DECISIONS = (
    "ready",
    "replay",
    "conflict",
    "expired",
    "denied",
    "unverifiable",
    "blocked_by_chain",
)
REASONS = (
    "idempotent_replay",
    "idempotency_key_reused",
    "concurrent_intent_collision",
    "target_unresolved",
    "target_system_mismatch",
    "target_resource_mismatch",
    "target_version_changed",
    "routing_changed",
    "authorization_changed",
    "policy_changed",
    "snapshot_stale",
    "lease_expired",
    "lease_changed",
    "attempt_budget_exhausted",
    "previous_result_missing",
    "previous_result_not_accepted",
    "previous_chain_mismatch",
    "previous_subject_changed",
    "provider_failure",
    "compensation_required",
    "compensation_unavailable",
)
SHA256_LENGTH = 64
MAX_RECORDS = 10_000
MAX_CLOCK_SKEW_SECONDS = 3_600
MAX_SNAPSHOT_AGE_SECONDS = 86_400
MAX_LEASE_SECONDS = 86_400
MAX_ATTEMPTS = 20


def _strict_object(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    obj = expect_object(value, field)
    extra = set(obj) - allowed
    if extra:
        raise RecoveryError(f"{field} has unsupported keys: {sorted(extra)}")
    return obj


def _expect_int(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecoveryError(f"{field} must be an integer")
    if value < minimum:
        raise RecoveryError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise RecoveryError(f"{field} must be at most {maximum}")
    return value


def _expect_sha256(value: Any, field: str) -> str:
    digest = expect_string(value, field, SHA256_LENGTH)
    if len(digest) != SHA256_LENGTH or any(
        char not in "0123456789abcdef" for char in digest
    ):
        raise RecoveryError(f"{field} must be a lowercase SHA-256 digest")
    return digest


def _optional_sha256(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _expect_sha256(value, field)


def _parse_datetime(value: Any, field: str) -> tuple[str, datetime]:
    normalized = parse_timestamp(value, field)
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    return normalized, parsed.astimezone(timezone.utc)


def _normalize_lease(
    value: Any,
    field: str,
    *,
    max_lease_seconds: int,
) -> dict[str, Any] | None:
    if value is None:
        return None
    lease = _strict_object(
        value,
        field,
        {"token_sha256", "owner_sha256", "acquired_at", "expires_at"},
    )
    acquired_at, acquired_dt = _parse_datetime(
        lease.get("acquired_at"), f"{field}.acquired_at"
    )
    expires_at, expires_dt = _parse_datetime(
        lease.get("expires_at"), f"{field}.expires_at"
    )
    duration = int((expires_dt - acquired_dt).total_seconds())
    if duration <= 0:
        raise RecoveryError(f"{field} must expire after acquisition")
    if duration > max_lease_seconds:
        raise RecoveryError(
            f"{field} exceeds the policy maximum lease duration"
        )
    return {
        "token_sha256": _expect_sha256(
            lease.get("token_sha256"), f"{field}.token_sha256"
        ),
        "owner_sha256": _expect_sha256(
            lease.get("owner_sha256"), f"{field}.owner_sha256"
        ),
        "acquired_at": acquired_at,
        "expires_at": expires_at,
    }


def _normalize_policy(value: Any) -> dict[str, Any]:
    policy = _strict_object(
        value,
        "policy",
        {
            "version_sha256",
            "max_clock_skew_seconds",
            "max_snapshot_age_seconds",
            "max_lease_seconds",
            "max_attempts",
        },
    )
    material = {
        "max_clock_skew_seconds": _expect_int(
            policy.get("max_clock_skew_seconds"),
            "policy.max_clock_skew_seconds",
            maximum=MAX_CLOCK_SKEW_SECONDS,
        ),
        "max_snapshot_age_seconds": _expect_int(
            policy.get("max_snapshot_age_seconds"),
            "policy.max_snapshot_age_seconds",
            minimum=1,
            maximum=MAX_SNAPSHOT_AGE_SECONDS,
        ),
        "max_lease_seconds": _expect_int(
            policy.get("max_lease_seconds"),
            "policy.max_lease_seconds",
            minimum=1,
            maximum=MAX_LEASE_SECONDS,
        ),
        "max_attempts": _expect_int(
            policy.get("max_attempts"),
            "policy.max_attempts",
            minimum=1,
            maximum=MAX_ATTEMPTS,
        ),
    }
    version = _expect_sha256(
        policy.get("version_sha256"), "policy.version_sha256"
    )
    if version != sha256_value(material):
        raise RecoveryError(
            "policy.version_sha256 must bind the canonical policy"
        )
    return {"version_sha256": version, **material}


def _normalize_target(
    value: Any,
    index: int,
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    field = f"targets[{index}]"
    target = _strict_object(
        value,
        field,
        {
            "system",
            "resource_kind",
            "identity_sha256",
            "version_sha256",
            "routing_sha256",
            "authorization_sha256",
            "observed_at",
            "resolvable",
            "lease",
        },
    )
    system = expect_string(target.get("system"), f"{field}.system", 16)
    if system not in SYSTEMS:
        raise RecoveryError(f"{field}.system is unsupported")
    resource_kind = expect_string(
        target.get("resource_kind"), f"{field}.resource_kind", 64
    )
    if resource_kind not in RESOURCE_KINDS:
        raise RecoveryError(f"{field}.resource_kind is unsupported")
    observed_at, _ = _parse_datetime(
        target.get("observed_at"), f"{field}.observed_at"
    )
    return {
        "system": system,
        "resource_kind": resource_kind,
        "identity_sha256": _expect_sha256(
            target.get("identity_sha256"), f"{field}.identity_sha256"
        ),
        "version_sha256": _expect_sha256(
            target.get("version_sha256"), f"{field}.version_sha256"
        ),
        "routing_sha256": _expect_sha256(
            target.get("routing_sha256"), f"{field}.routing_sha256"
        ),
        "authorization_sha256": _expect_sha256(
            target.get("authorization_sha256"),
            f"{field}.authorization_sha256",
        ),
        "observed_at": observed_at,
        "resolvable": expect_bool(
            target.get("resolvable"), f"{field}.resolvable"
        ),
        "lease": _normalize_lease(
            target.get("lease"),
            f"{field}.lease",
            max_lease_seconds=policy["max_lease_seconds"],
        ),
    }


def _normalize_chain(value: Any, field: str) -> dict[str, Any]:
    chain = _strict_object(
        value,
        field,
        {
            "workflow_sha256",
            "step",
            "sequence",
            "previous_result_sha256",
            "trace_sha256",
        },
    )
    step = expect_string(chain.get("step"), f"{field}.step", 32)
    if step not in CHAIN_STEPS:
        raise RecoveryError(f"{field}.step is unsupported")
    sequence = _expect_int(
        chain.get("sequence"), f"{field}.sequence", maximum=64
    )
    previous_result = _optional_sha256(
        chain.get("previous_result_sha256"),
        f"{field}.previous_result_sha256",
    )
    if sequence == 0 and previous_result is not None:
        raise RecoveryError(
            f"{field}.previous_result_sha256 must be null for sequence 0"
        )
    if sequence > 0 and previous_result is None:
        raise RecoveryError(
            f"{field}.previous_result_sha256 is required after sequence 0"
        )
    material = {
        "workflow_sha256": _expect_sha256(
            chain.get("workflow_sha256"), f"{field}.workflow_sha256"
        ),
        "step": step,
        "sequence": sequence,
        "previous_result_sha256": previous_result,
    }
    trace = _expect_sha256(
        chain.get("trace_sha256"), f"{field}.trace_sha256"
    )
    if trace != sha256_value(material):
        raise RecoveryError(f"{field}.trace_sha256 does not bind the chain")
    return {**material, "trace_sha256": trace}


def _normalize_intent(
    value: Any,
    index: int,
    *,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    field = f"intents[{index}]"
    intent = _strict_object(
        value,
        field,
        {
            "intent_sha256",
            "system",
            "operation",
            "resource_kind",
            "target_identity_sha256",
            "observed_version_sha256",
            "observed_at",
            "desired_patch_sha256",
            "relation_analysis_sha256",
            "actor_sha256",
            "run_sha256",
            "idempotency_key_sha256",
            "policy_sha256",
            "routing_sha256",
            "authorization_sha256",
            "lease",
            "compensation",
            "chain",
        },
    )
    system = expect_string(intent.get("system"), f"{field}.system", 16)
    if system not in SYSTEMS:
        raise RecoveryError(f"{field}.system is unsupported")
    operation = expect_string(
        intent.get("operation"), f"{field}.operation", 64
    )
    if operation not in OPERATIONS:
        raise RecoveryError(f"{field}.operation is unsupported")
    if OPERATION_SYSTEM[operation] != system:
        raise RecoveryError(f"{field}.operation does not match system")
    resource_kind = expect_string(
        intent.get("resource_kind"), f"{field}.resource_kind", 64
    )
    if resource_kind not in RESOURCE_KINDS:
        raise RecoveryError(f"{field}.resource_kind is unsupported")
    observed_at, _ = _parse_datetime(
        intent.get("observed_at"), f"{field}.observed_at"
    )
    relation_analysis = _optional_sha256(
        intent.get("relation_analysis_sha256"),
        f"{field}.relation_analysis_sha256",
    )
    if operation in RELATION_OPERATIONS and relation_analysis is None:
        raise RecoveryError(
            f"{field}.relation_analysis_sha256 is required for relation mutations"
        )
    compensation = expect_string(
        intent.get("compensation"), f"{field}.compensation", 64
    )
    if compensation not in COMPENSATIONS:
        raise RecoveryError(f"{field}.compensation is unsupported")
    material = {
        "system": system,
        "operation": operation,
        "resource_kind": resource_kind,
        "target_identity_sha256": _expect_sha256(
            intent.get("target_identity_sha256"),
            f"{field}.target_identity_sha256",
        ),
        "observed_version_sha256": _expect_sha256(
            intent.get("observed_version_sha256"),
            f"{field}.observed_version_sha256",
        ),
        "observed_at": observed_at,
        "desired_patch_sha256": _expect_sha256(
            intent.get("desired_patch_sha256"),
            f"{field}.desired_patch_sha256",
        ),
        "relation_analysis_sha256": relation_analysis,
        "actor_sha256": _expect_sha256(
            intent.get("actor_sha256"), f"{field}.actor_sha256"
        ),
        "run_sha256": _expect_sha256(
            intent.get("run_sha256"), f"{field}.run_sha256"
        ),
        "idempotency_key_sha256": _expect_sha256(
            intent.get("idempotency_key_sha256"),
            f"{field}.idempotency_key_sha256",
        ),
        "policy_sha256": _expect_sha256(
            intent.get("policy_sha256"), f"{field}.policy_sha256"
        ),
        "routing_sha256": _expect_sha256(
            intent.get("routing_sha256"), f"{field}.routing_sha256"
        ),
        "authorization_sha256": _expect_sha256(
            intent.get("authorization_sha256"),
            f"{field}.authorization_sha256",
        ),
        "lease": _normalize_lease(
            intent.get("lease"),
            f"{field}.lease",
            max_lease_seconds=policy["max_lease_seconds"],
        ),
        "compensation": compensation,
        "chain": _normalize_chain(intent.get("chain"), f"{field}.chain"),
    }
    intent_sha = _expect_sha256(
        intent.get("intent_sha256"), f"{field}.intent_sha256"
    )
    if intent_sha != sha256_value(material):
        raise RecoveryError(
            f"{field}.intent_sha256 does not bind the canonical intent"
        )
    return {"intent_sha256": intent_sha, **material}


def _normalize_result(
    value: Any,
    index: int,
    *,
    intents_by_sha: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    field = f"results[{index}]"
    result = _strict_object(
        value,
        field,
        {
            "result_sha256",
            "intent_sha256",
            "idempotency_key_sha256",
            "system",
            "target_identity_sha256",
            "outcome",
            "provider_receipt_sha256",
            "before_version_sha256",
            "after_version_sha256",
            "occurred_at",
            "actor_sha256",
            "detail_sha256",
            "compensation_of_result_sha256",
        },
    )
    intent_sha = _expect_sha256(
        result.get("intent_sha256"), f"{field}.intent_sha256"
    )
    intent = intents_by_sha.get(intent_sha)
    if intent is None:
        raise RecoveryError(f"{field}.intent_sha256 is not in this report")
    system = expect_string(result.get("system"), f"{field}.system", 16)
    if system not in SYSTEMS:
        raise RecoveryError(f"{field}.system is unsupported")
    outcome = expect_string(result.get("outcome"), f"{field}.outcome", 32)
    if outcome not in RESULT_OUTCOMES:
        raise RecoveryError(f"{field}.outcome is unsupported")
    occurred_at, _ = _parse_datetime(
        result.get("occurred_at"), f"{field}.occurred_at"
    )
    material = {
        "intent_sha256": intent_sha,
        "idempotency_key_sha256": _expect_sha256(
            result.get("idempotency_key_sha256"),
            f"{field}.idempotency_key_sha256",
        ),
        "system": system,
        "target_identity_sha256": _expect_sha256(
            result.get("target_identity_sha256"),
            f"{field}.target_identity_sha256",
        ),
        "outcome": outcome,
        "provider_receipt_sha256": _optional_sha256(
            result.get("provider_receipt_sha256"),
            f"{field}.provider_receipt_sha256",
        ),
        "before_version_sha256": _expect_sha256(
            result.get("before_version_sha256"),
            f"{field}.before_version_sha256",
        ),
        "after_version_sha256": _optional_sha256(
            result.get("after_version_sha256"),
            f"{field}.after_version_sha256",
        ),
        "occurred_at": occurred_at,
        "actor_sha256": _expect_sha256(
            result.get("actor_sha256"), f"{field}.actor_sha256"
        ),
        "detail_sha256": _expect_sha256(
            result.get("detail_sha256"), f"{field}.detail_sha256"
        ),
        "compensation_of_result_sha256": _optional_sha256(
            result.get("compensation_of_result_sha256"),
            f"{field}.compensation_of_result_sha256",
        ),
    }
    if material["idempotency_key_sha256"] != intent["idempotency_key_sha256"]:
        raise RecoveryError(f"{field} does not match the intent idempotency key")
    if material["system"] != intent["system"]:
        raise RecoveryError(f"{field} does not match the intent system")
    if material["target_identity_sha256"] != intent["target_identity_sha256"]:
        raise RecoveryError(f"{field} does not match the intent target")
    if material["before_version_sha256"] != intent["observed_version_sha256"]:
        raise RecoveryError(f"{field} does not match the intent precondition")
    if outcome in {"accepted", "compensated"}:
        if material["provider_receipt_sha256"] is None:
            raise RecoveryError(f"{field} requires a provider receipt")
        if material["after_version_sha256"] is None:
            raise RecoveryError(f"{field} requires an after-version digest")
    elif material["after_version_sha256"] is not None:
        raise RecoveryError(
            f"{field}.after_version_sha256 must be null when no mutation was accepted"
        )
    if outcome == "compensated":
        if material["compensation_of_result_sha256"] is None:
            raise RecoveryError(f"{field} must reference the compensated result")
    elif material["compensation_of_result_sha256"] is not None:
        raise RecoveryError(
            f"{field}.compensation_of_result_sha256 is only valid for compensation"
        )
    result_sha = _expect_sha256(
        result.get("result_sha256"), f"{field}.result_sha256"
    )
    if result_sha != sha256_value(material):
        raise RecoveryError(
            f"{field}.result_sha256 does not bind the canonical result"
        )
    return {"result_sha256": result_sha, **material}
