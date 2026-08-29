"""Deterministic, fail-closed evidence admissibility and recertification planning."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .common import (
    RecoveryError,
    expect_list,
    expect_object,
    expect_string,
    parse_timestamp,
    sha256_value,
    validate_public_safety,
)

ADMISSIBILITY_SCHEMA = "artifact_recovery_evidence_admissibility.v1"
QUEUE_SCHEMA = "artifact_recovery_recertification_queue.v1"
RISK_CLASSES = ("security", "deployment", "code", "documentation")
EVIDENCE_KINDS = (
    "exact_head_test",
    "mergeability_review",
    "deployment_health",
    "dependency_certification",
    "policy_conformance",
    "documentation",
)
DEPENDENCY_KINDS = (
    "pr_head",
    "pr_base",
    "dependency_graph",
    "deployed_image",
    "environment_config",
    "source_coverage",
    "required_check_set",
    "workflow_policy",
)
STATES = (
    "current",
    "stale",
    "superseded",
    "invalidated",
    "unverifiable",
    "expired",
)
REASON_CODES = (
    "subject_unresolved",
    "subject_revision_changed",
    "policy_version_changed",
    "dependency_unresolved",
    "dependency_changed",
    "freshness_warning_elapsed",
    "admissibility_ttl_elapsed",
)
SHA256_LENGTH = 64
MAX_RECORDS = 10_000
MAX_CLOCK_SKEW_SECONDS = 60 * 60
MAX_TTL_SECONDS = 31 * 24 * 60 * 60
STATE_SEVERITY = {
    "current": 0,
    "stale": 1,
    "expired": 2,
    "unverifiable": 3,
    "superseded": 4,
    "invalidated": 5,
}


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


def _normalize_dependencies(value: Any, field: str) -> list[dict[str, str]]:
    raw = expect_list(value, field, len(DEPENDENCY_KINDS))
    dependencies: list[dict[str, str]] = []
    kinds: list[str] = []
    for index, dependency_value in enumerate(raw):
        item_field = f"{field}[{index}]"
        dependency = _strict_object(
            dependency_value, item_field, {"kind", "digest"}
        )
        kind = expect_string(dependency.get("kind"), f"{item_field}.kind", 64)
        if kind not in DEPENDENCY_KINDS:
            raise RecoveryError(f"{item_field}.kind is unsupported")
        digest = _expect_sha256(
            dependency.get("digest"), f"{item_field}.digest"
        )
        dependencies.append({"kind": kind, "digest": digest})
        kinds.append(kind)
    if len(kinds) != len(set(kinds)):
        raise RecoveryError(f"{field} contains duplicate dependency kinds")
    return sorted(dependencies, key=lambda item: item["kind"])


def _normalize_policy(value: Any) -> dict[str, Any]:
    policy = _strict_object(
        value,
        "policy",
        {"version_sha256", "max_clock_skew_seconds", "risk_ttls"},
    )
    version_sha256 = _expect_sha256(
        policy.get("version_sha256"), "policy.version_sha256"
    )
    max_clock_skew_seconds = _expect_int(
        policy.get("max_clock_skew_seconds", 300),
        "policy.max_clock_skew_seconds",
        maximum=MAX_CLOCK_SKEW_SECONDS,
    )
    risk_ttls_raw = _strict_object(
        policy.get("risk_ttls"),
        "policy.risk_ttls",
        set(RISK_CLASSES),
    )
    if set(risk_ttls_raw) != set(RISK_CLASSES):
        missing = sorted(set(RISK_CLASSES) - set(risk_ttls_raw))
        raise RecoveryError(
            f"policy.risk_ttls is missing risk classes: {missing}"
        )
    risk_ttls: dict[str, dict[str, int]] = {}
    for risk_class in RISK_CLASSES:
        ttl = _strict_object(
            risk_ttls_raw[risk_class],
            f"policy.risk_ttls.{risk_class}",
            {"stale_after_seconds", "expires_after_seconds"},
        )
        stale_after_seconds = _expect_int(
            ttl.get("stale_after_seconds"),
            f"policy.risk_ttls.{risk_class}.stale_after_seconds",
            minimum=1,
            maximum=MAX_TTL_SECONDS,
        )
        expires_after_seconds = _expect_int(
            ttl.get("expires_after_seconds"),
            f"policy.risk_ttls.{risk_class}.expires_after_seconds",
            minimum=1,
            maximum=MAX_TTL_SECONDS,
        )
        if stale_after_seconds >= expires_after_seconds:
            raise RecoveryError(
                f"policy.risk_ttls.{risk_class}.stale_after_seconds must be less than expires_after_seconds"
            )
        risk_ttls[risk_class] = {
            "stale_after_seconds": stale_after_seconds,
            "expires_after_seconds": expires_after_seconds,
        }
    policy_material = {
        "max_clock_skew_seconds": max_clock_skew_seconds,
        "risk_ttls": risk_ttls,
    }
    expected_version_sha256 = sha256_value(policy_material)
    if version_sha256 != expected_version_sha256:
        raise RecoveryError(
            "policy.version_sha256 must bind the canonical clock-skew and risk-TTL policy"
        )
    return {
        "version_sha256": version_sha256,
        **policy_material,
    }


def _normalize_subject(value: Any, index: int) -> dict[str, Any]:
    field = f"subjects[{index}]"
    subject = _strict_object(
        value,
        field,
        {"identity_sha256", "revision_sha256", "dependencies"},
    )
    return {
        "identity_sha256": _expect_sha256(
            subject.get("identity_sha256"), f"{field}.identity_sha256"
        ),
        "revision_sha256": _expect_sha256(
            subject.get("revision_sha256"), f"{field}.revision_sha256"
        ),
        "dependencies": _normalize_dependencies(
            subject.get("dependencies", []), f"{field}.dependencies"
        ),
    }


def _normalize_evidence_record(
    value: Any, index: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    field = f"evidence[{index}]"
    allowed = {
        "identity_sha256",
        "kind",
        "subject_identity_sha256",
        "subject_revision_sha256",
        "producer_sha256",
        "captured_at",
        "policy_version_sha256",
        "payload_sha256",
        "risk_class",
        "owner_sha256",
        "dependency_digests",
        "record_sha256",
        "current_subject_revision_sha256",
        "age_seconds",
        "state",
        "reason_codes",
        "changed_dependencies",
        "unresolved_dependencies",
    }
    evidence = _strict_object(value, field, allowed)
    kind = expect_string(evidence.get("kind"), f"{field}.kind", 64)
    if kind not in EVIDENCE_KINDS:
        raise RecoveryError(f"{field}.kind is unsupported")
    risk_class = expect_string(
        evidence.get("risk_class"), f"{field}.risk_class", 32
    )
    if risk_class not in RISK_CLASSES:
        raise RecoveryError(f"{field}.risk_class is unsupported")
    captured_at, _ = _parse_datetime(
        evidence.get("captured_at"), f"{field}.captured_at"
    )
    record = {
        "identity_sha256": _expect_sha256(
            evidence.get("identity_sha256"), f"{field}.identity_sha256"
        ),
        "kind": kind,
        "subject_identity_sha256": _expect_sha256(
            evidence.get("subject_identity_sha256"),
            f"{field}.subject_identity_sha256",
        ),
        "subject_revision_sha256": _expect_sha256(
            evidence.get("subject_revision_sha256"),
            f"{field}.subject_revision_sha256",
        ),
        "producer_sha256": _expect_sha256(
            evidence.get("producer_sha256"), f"{field}.producer_sha256"
        ),
        "captured_at": captured_at,
        "policy_version_sha256": _expect_sha256(
            evidence.get("policy_version_sha256"),
            f"{field}.policy_version_sha256",
        ),
        "payload_sha256": _expect_sha256(
            evidence.get("payload_sha256"), f"{field}.payload_sha256"
        ),
        "risk_class": risk_class,
        "owner_sha256": _expect_sha256(
            evidence.get("owner_sha256"), f"{field}.owner_sha256"
        ),
        "dependency_digests": _normalize_dependencies(
            evidence.get("dependency_digests", []),
            f"{field}.dependency_digests",
        ),
    }
    provided = {
        key: evidence[key]
        for key in (
            "record_sha256",
            "current_subject_revision_sha256",
            "age_seconds",
            "state",
            "reason_codes",
            "changed_dependencies",
            "unresolved_dependencies",
        )
        if key in evidence
    }
    return record, provided


def _validate_provided_assessment(
    provided: Mapping[str, Any],
    derived: Mapping[str, Any],
    field: str,
) -> None:
    for key, value in provided.items():
        expected = derived[key]
        if key in {
            "reason_codes",
            "changed_dependencies",
            "unresolved_dependencies",
        }:
            if not isinstance(value, list) or value != expected:
                raise RecoveryError(
                    f"{field}.{key} does not match the derived assessment"
                )
        elif value != expected:
            raise RecoveryError(
                f"{field}.{key} does not match the derived assessment"
            )


def _derive_assessment(
    record: Mapping[str, Any],
    *,
    generated_at: datetime,
    policy: Mapping[str, Any],
    subjects_by_identity: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    _, captured_at = _parse_datetime(
        record["captured_at"], "evidence.captured_at"
    )
    skew = timedelta(seconds=policy["max_clock_skew_seconds"])
    if captured_at > generated_at + skew:
        raise RecoveryError(
            "evidence.captured_at is beyond the allowed clock skew"
        )
    age_seconds = int((generated_at - captured_at).total_seconds())
    if age_seconds < -policy["max_clock_skew_seconds"]:
        raise RecoveryError(
            "evidence.captured_at is beyond the allowed clock skew"
        )
    age_seconds = max(0, age_seconds)

    subject = subjects_by_identity.get(record["subject_identity_sha256"])
    current_revision: str | None = None
    reason_codes: set[str] = set()
    changed_dependencies: list[str] = []
    unresolved_dependencies: list[str] = []

    if subject is None:
        reason_codes.add("subject_unresolved")
        state = "unverifiable"
    else:
        current_revision = subject["revision_sha256"]
        current_dependencies = {
            item["kind"]: item["digest"] for item in subject["dependencies"]
        }
        if record["subject_revision_sha256"] != current_revision:
            reason_codes.add("subject_revision_changed")
        if record["policy_version_sha256"] != policy["version_sha256"]:
            reason_codes.add("policy_version_changed")
        for dependency in record["dependency_digests"]:
            current_digest = current_dependencies.get(dependency["kind"])
            if current_digest is None:
                unresolved_dependencies.append(dependency["kind"])
                reason_codes.add("dependency_unresolved")
            elif current_digest != dependency["digest"]:
                changed_dependencies.append(dependency["kind"])
                reason_codes.add("dependency_changed")

        if "subject_revision_changed" in reason_codes:
            state = "superseded"
        elif {"policy_version_changed", "dependency_changed"} & reason_codes:
            state = "invalidated"
        elif "dependency_unresolved" in reason_codes:
            state = "unverifiable"
        else:
            ttl = policy["risk_ttls"][record["risk_class"]]
            if age_seconds > ttl["expires_after_seconds"]:
                reason_codes.add("admissibility_ttl_elapsed")
                state = "expired"
            elif age_seconds > ttl["stale_after_seconds"]:
                reason_codes.add("freshness_warning_elapsed")
                state = "stale"
            else:
                state = "current"

    assessment = {
        **record,
        "record_sha256": sha256_value(record),
        "current_subject_revision_sha256": current_revision,
        "age_seconds": age_seconds,
        "state": state,
        "reason_codes": sorted(reason_codes),
        "changed_dependencies": sorted(changed_dependencies),
        "unresolved_dependencies": sorted(unresolved_dependencies),
    }
    validate_public_safety(assessment)
    return assessment


def _build_recertification_queue(
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in evidence:
        if item["state"] == "current":
            continue
        fingerprint_material = {
            "subject_identity_sha256": item["subject_identity_sha256"],
            "evidence_kind": item["kind"],
            "current_revision_sha256": item[
                "current_subject_revision_sha256"
            ],
        }
        fingerprint = sha256_value(fingerprint_material)
        queue_item = grouped.get(fingerprint)
        if queue_item is None:
            queue_item = {
                "fingerprint_sha256": fingerprint,
                **fingerprint_material,
                "owner_sha256": item["owner_sha256"],
                "action": "recertify",
                "highest_state": item["state"],
                "observed_states": [],
                "evidence_identity_sha256s": [],
                "reason_codes": [],
                "changed_dependencies": [],
                "unresolved_dependencies": [],
            }
            grouped[fingerprint] = queue_item
        elif queue_item["owner_sha256"] != item["owner_sha256"]:
            raise RecoveryError(
                "recertification fingerprint has conflicting owners; ownership must be resolved"
            )
        queue_item["observed_states"].append(item["state"])
        queue_item["evidence_identity_sha256s"].append(
            item["identity_sha256"]
        )
        queue_item["reason_codes"].extend(item["reason_codes"])
        queue_item["changed_dependencies"].extend(
            item["changed_dependencies"]
        )
        queue_item["unresolved_dependencies"].extend(
            item["unresolved_dependencies"]
        )
        if (
            STATE_SEVERITY[item["state"]]
            > STATE_SEVERITY[queue_item["highest_state"]]
        ):
            queue_item["highest_state"] = item["state"]

    items: list[dict[str, Any]] = []
    for fingerprint in sorted(grouped):
        item = grouped[fingerprint]
        item["observed_states"] = sorted(set(item["observed_states"]))
        item["evidence_identity_sha256s"] = sorted(
            set(item["evidence_identity_sha256s"])
        )
        item["reason_codes"] = sorted(set(item["reason_codes"]))
        item["changed_dependencies"] = sorted(
            set(item["changed_dependencies"])
        )
        item["unresolved_dependencies"] = sorted(
            set(item["unresolved_dependencies"])
        )
        items.append(item)
    queue_without_digest = {
        "schema_version": QUEUE_SCHEMA,
        "summary": {"items": len(items)},
        "items": items,
    }
    queue = {
        **queue_without_digest,
        "queue_sha256": sha256_value(queue_without_digest),
    }
    validate_public_safety(queue)
    return queue


def build_evidence_admissibility_report(
    value: Mapping[str, Any],
    *,
    now: str | None = None,
) -> dict[str, Any]:
    """Validate evidence, derive admissibility, and emit recertification work."""

    root = _strict_object(
        value,
        "$",
        {
            "schema_version",
            "generated_at",
            "policy",
            "subjects",
            "evidence",
            "summary",
            "recertification_queue",
            "report_sha256",
        },
    )
    if root.get("schema_version") != ADMISSIBILITY_SCHEMA:
        raise RecoveryError(
            f"schema_version must be {ADMISSIBILITY_SCHEMA}"
        )
    generated_at, generated_at_dt = _parse_datetime(
        root.get("generated_at"), "generated_at"
    )
    if now is not None:
        _, now_dt = _parse_datetime(now, "--now")
        if generated_at_dt > now_dt + timedelta(
            seconds=MAX_CLOCK_SKEW_SECONDS
        ):
            raise RecoveryError(
                "generated_at is beyond the allowed clock skew"
            )

    policy = _normalize_policy(root.get("policy"))
    raw_subjects = expect_list(
        root.get("subjects"), "subjects", MAX_RECORDS
    )
    subjects = [
        _normalize_subject(item, index)
        for index, item in enumerate(raw_subjects)
    ]
    subject_identities = [item["identity_sha256"] for item in subjects]
    if len(subject_identities) != len(set(subject_identities)):
        raise RecoveryError("subjects contain duplicate identities")
    subjects = sorted(subjects, key=lambda item: item["identity_sha256"])
    subjects_by_identity = {
        item["identity_sha256"]: item for item in subjects
    }

    raw_evidence = expect_list(
        root.get("evidence"), "evidence", MAX_RECORDS
    )
    evidence: list[dict[str, Any]] = []
    identities: list[str] = []
    for index, value_item in enumerate(raw_evidence):
        record, provided = _normalize_evidence_record(value_item, index)
        assessment = _derive_assessment(
            record,
            generated_at=generated_at_dt,
            policy=policy,
            subjects_by_identity=subjects_by_identity,
        )
        _validate_provided_assessment(
            provided, assessment, f"evidence[{index}]"
        )
        evidence.append(assessment)
        identities.append(assessment["identity_sha256"])
    if len(identities) != len(set(identities)):
        raise RecoveryError("evidence contains duplicate identities")
    evidence = sorted(evidence, key=lambda item: item["identity_sha256"])

    counts = Counter(item["state"] for item in evidence)
    if any(
        counts[state]
        for state in (
            "superseded",
            "invalidated",
            "unverifiable",
            "expired",
        )
    ):
        status = "blocked"
    elif counts["stale"]:
        status = "partial"
    else:
        status = "complete"
    queue = _build_recertification_queue(evidence)
    summary = {
        "status": status,
        "complete": status == "complete",
        "subjects": len(subjects),
        "evidence": len(evidence),
        "current": counts["current"],
        "stale": counts["stale"],
        "superseded": counts["superseded"],
        "invalidated": counts["invalidated"],
        "unverifiable": counts["unverifiable"],
        "expired": counts["expired"],
        "recertification_items": queue["summary"]["items"],
    }
    report_without_digest = {
        "schema_version": ADMISSIBILITY_SCHEMA,
        "generated_at": generated_at,
        "policy": policy,
        "subjects": subjects,
        "evidence": evidence,
        "summary": summary,
        "recertification_queue": queue,
    }
    report = {
        **report_without_digest,
        "report_sha256": sha256_value(report_without_digest),
    }
    if "summary" in root and root["summary"] != summary:
        raise RecoveryError("summary does not match the derived report")
    if (
        "recertification_queue" in root
        and root["recertification_queue"] != queue
    ):
        raise RecoveryError(
            "recertification_queue does not match the derived report"
        )
    if (
        "report_sha256" in root
        and root["report_sha256"] != report["report_sha256"]
    ):
        raise RecoveryError(
            "report_sha256 does not match the canonical report"
        )
    validate_public_safety(report)
    return report


def build_example_evidence_admissibility(*, now: str) -> dict[str, Any]:
    generated_at, generated_at_dt = _parse_datetime(now, "--now")
    risk_ttls = {
        "security": {
            "stale_after_seconds": 3600,
            "expires_after_seconds": 21600,
        },
        "deployment": {
            "stale_after_seconds": 7200,
            "expires_after_seconds": 43200,
        },
        "code": {
            "stale_after_seconds": 21600,
            "expires_after_seconds": 86400,
        },
        "documentation": {
            "stale_after_seconds": 604800,
            "expires_after_seconds": 2592000,
        },
    }
    policy_version = sha256_value(
        {"max_clock_skew_seconds": 300, "risk_ttls": risk_ttls}
    )
    subject_identity = sha256_value(
        {"fixture": "subject", "id": "example"}
    )
    subject_revision = sha256_value(
        {"fixture": "subject-revision", "id": "example", "v": 1}
    )
    workflow_digest = sha256_value(
        {"fixture": "workflow-policy", "v": 1}
    )
    captured_at = (
        generated_at_dt - timedelta(minutes=5)
    ).isoformat().replace("+00:00", "Z")
    raw = {
        "schema_version": ADMISSIBILITY_SCHEMA,
        "generated_at": generated_at,
        "policy": {
            "version_sha256": policy_version,
            "max_clock_skew_seconds": 300,
            "risk_ttls": risk_ttls,
        },
        "subjects": [
            {
                "identity_sha256": subject_identity,
                "revision_sha256": subject_revision,
                "dependencies": [
                    {
                        "kind": "workflow_policy",
                        "digest": workflow_digest,
                    }
                ],
            }
        ],
        "evidence": [
            {
                "identity_sha256": sha256_value(
                    {"fixture": "evidence", "id": "example"}
                ),
                "kind": "policy_conformance",
                "subject_identity_sha256": subject_identity,
                "subject_revision_sha256": subject_revision,
                "producer_sha256": sha256_value(
                    {"fixture": "producer"}
                ),
                "captured_at": captured_at,
                "policy_version_sha256": policy_version,
                "payload_sha256": sha256_value({"fixture": "payload"}),
                "risk_class": "security",
                "owner_sha256": sha256_value({"fixture": "owner"}),
                "dependency_digests": [
                    {
                        "kind": "workflow_policy",
                        "digest": workflow_digest,
                    }
                ],
            }
        ],
    }
    return build_evidence_admissibility_report(raw, now=generated_at)
