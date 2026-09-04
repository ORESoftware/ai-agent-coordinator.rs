#!/usr/bin/env python3
"""Deterministic, read-only portfolio remediation queue contract."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA = "portfolio-remediation-queue/v1"
MAX_BYTES = 1024 * 1024
MAX_TEXT = 2_000
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,95}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LINEAR = re.compile(r"^DEN-[1-9][0-9]*$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{3,240}$")
ROOT_KEYS = {
    "schema_version",
    "queue_id",
    "generated_at",
    "baseline",
    "repositories",
    "findings",
    "safety",
}
BASELINE_KEYS = {
    "observed_at",
    "catalog_sha256",
    "policy_sha256",
    "repository_count",
}
REPOSITORY_KEYS = {
    "repository",
    "visibility",
    "lifecycle",
    "canonical_role",
    "default_branch",
    "observed_head",
    "linear_project_url",
    "github_project_url",
    "agents_sha256",
    "toolchain",
    "ci_state",
    "security_policy",
    "license_spdx",
    "release_path",
    "test_maturity",
    "deployment_boundary",
    "expectation_profile",
    "current_state_sha256",
}
FINDING_KEYS = {
    "id",
    "rank",
    "repository",
    "category",
    "state",
    "severity",
    "disposition",
    "evidence_sha256",
    "baseline_value_sha256",
    "current_value_sha256",
    "linear_anchor",
    "github_anchor",
    "owner",
    "duplicate_of",
    "depends_on",
    "scores",
    "remediation",
}
SCORE_KEYS = {
    "security",
    "data_integrity",
    "user_impact",
    "dependency_centrality",
    "operational_risk",
    "effort",
    "test_evidence",
}
REMEDIATION_KEYS = {
    "branch_name",
    "base_commit",
    "acceptance_checks",
    "dry_run",
    "apply_authorized",
    "residual_risk",
}
SAFETY_KEYS = {
    "read_only",
    "dry_run",
    "repository_creation_authorized",
    "visibility_changes_authorized",
    "branch_protection_changes_authorized",
    "merge_authorized",
    "deployment_authorized",
    "dns_changes_authorized",
    "database_changes_authorized",
    "notes",
}
VISIBILITIES = {"public", "private", "internal"}
LIFECYCLES = {"active", "archived", "superseded", "fork"}
ROLES = {
    "interfaces",
    "clients",
    "lib_core",
    "orm_core",
    "sync",
    "web_server",
    "api_server",
    "flutter",
    "native_desktop",
    "lambdas",
    "infra",
    "monorepo",
    "tests",
    "docs",
    "governance",
    "package",
    "mcp",
    "cli",
    "control_plane",
    "other",
}
CI_STATES = {"present_green", "present_unknown", "missing", "exempt"}
RELEASE_PATHS = {
    "none",
    "source_artifact",
    "native_artifact",
    "container",
    "site",
    "multiple",
}
TEST_MATURITIES = {"L0", "L1", "L2", "L3", "L4", "L5"}
DEPLOYMENT_BOUNDARIES = {"none", "test", "production", "shared"}
CATEGORIES = {
    "missing_repository",
    "misplaced_repository",
    "instruction_drift",
    "ci_missing",
    "security_policy_missing",
    "license_missing",
    "release_gap",
    "test_maturity_gap",
    "deployment_isolation",
    "dependency_boundary",
    "branch_protection_drift",
    "stale_baseline",
    "ownership_unresolved",
    "duplicate_tracking",
    "visibility_mismatch",
}
FINDING_STATES = {
    "missing",
    "drifted",
    "superseded",
    "intentionally_exempt",
    "externally_blocked",
    "already_remediated",
}
SEVERITIES = {"critical", "high", "medium", "low"}
DISPOSITIONS = {
    "draft_pr",
    "amend_existing",
    "noop_completed",
    "noop_exempt",
    "noop_blocked",
    "noop_duplicate",
}
ACTIONABLE_DISPOSITIONS = {"draft_pr", "amend_existing"}
NOOP_DISPOSITIONS = DISPOSITIONS - ACTIONABLE_DISPOSITIONS
SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
DISPOSITION_TIER = {
    "draft_pr": 0,
    "amend_existing": 0,
    "noop_blocked": 1,
    "noop_completed": 2,
    "noop_exempt": 2,
    "noop_duplicate": 2,
}


class QueueError(ValueError):
    """Raised when a queue cannot be parsed safely."""


@dataclass(frozen=True)
class QueueReport:
    valid: bool
    queue_sha256: str | None
    repository_count: int
    finding_count: int
    actionable_count: int
    blocked_count: int
    noop_count: int
    errors: tuple[str, ...]


def _pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise QueueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load(path: str | Path) -> dict[str, Any]:
    payload = Path(path).read_bytes()
    if len(payload) > MAX_BYTES:
        raise QueueError(f"queue exceeds {MAX_BYTES} bytes")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise QueueError("queue must be UTF-8") from exc
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise QueueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise QueueError("queue root must be an object")
    return value


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _object(
    value: Any, expected: set[str], path: str, errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return None
    actual = set(value)
    if actual != expected:
        errors.append(
            f"{path} keys mismatch: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    return value


def _text(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int = 1,
    maximum: int = MAX_TEXT,
) -> str | None:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        errors.append(
            f"{path} must be a string of length {minimum}..{maximum}"
        )
        return None
    return value


def _slug(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 3, 96)
    if text is not None and not SLUG.fullmatch(text):
        errors.append(f"{path} has an invalid slug format")
        return None
    return text


def _sha(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 64, 64)
    if text is not None and not SHA256.fullmatch(text):
        errors.append(f"{path} must be lowercase SHA-256")
        return None
    return text


def _optional_sha(value: Any, path: str, errors: list[str]) -> str | None:
    if value is None:
        return None
    return _sha(value, path, errors)


def _commit(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 40, 40)
    if text is not None and not COMMIT.fullmatch(text):
        errors.append(f"{path} must be a lowercase 40-character commit SHA")
        return None
    return text


def _timestamp(value: Any, path: str, errors: list[str]) -> datetime | None:
    text = _text(value, path, errors, 20, 64)
    if text is None:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{path} must be ISO-8601")
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        errors.append(f"{path} must include a UTC offset")
        return None
    return result


def _integer(
    value: Any, path: str, errors: list[str], minimum: int, maximum: int
) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        errors.append(f"{path} must be an integer in {minimum}..{maximum}")
        return None
    return value


def _strings(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int,
    maximum: int,
    item_minimum: int = 1,
    item_maximum: int = 500,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        errors.append(f"{path} must contain {minimum}..{maximum} strings")
        return []
    result: list[str] = []
    for index, raw in enumerate(value):
        text = _text(
            raw,
            f"{path}[{index}]",
            errors,
            item_minimum,
            item_maximum,
        )
        if text is not None:
            result.append(text)
    if len(result) != len(set(result)):
        errors.append(f"{path} must not contain duplicates")
    return result


def _repository(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 3, 200)
    if text is not None and not REPOSITORY.fullmatch(text):
        errors.append(f"{path} must be an owner/repository identity")
        return None
    return text


def _url(
    value: Any,
    path: str,
    errors: list[str],
    *,
    allowed_hosts: set[str] | None = None,
) -> str | None:
    if value is None:
        return None
    text = _text(value, path, errors, 12, 1_000)
    if text is None:
        return None
    parsed = urlparse(text)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        errors.append(
            f"{path} must be canonical HTTPS without userinfo, query, or fragment"
        )
        return None
    if allowed_hosts is not None and parsed.netloc not in allowed_hosts:
        errors.append(f"{path} host is not allowed")
        return None
    return text


def _github_anchor(value: Any, path: str, errors: list[str]) -> str | None:
    text = _url(value, path, errors, allowed_hosts={"github.com"})
    if text is None:
        return None
    parsed = urlparse(text)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) != 4
        or parts[2] not in {"issues", "pull"}
        or not parts[3].isdigit()
        or int(parts[3]) < 1
    ):
        errors.append(f"{path} must be an exact GitHub issue or pull-request URL")
        return None
    return text


def _branch(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 3, 240)
    if text is None:
        return None
    if (
        not BRANCH.fullmatch(text)
        or text.startswith("/")
        or text.endswith("/")
        or "//" in text
        or ".." in text
        or text.endswith(".")
        or "@{" in text
    ):
        errors.append(f"{path} is not a safe Git branch name")
        return None
    return text


def _walk_safe(value: Any, path: str, errors: list[str]) -> None:
    forbidden = {
        "access_token",
        "api_key",
        "authorization",
        "credential",
        "secret",
        "private_key",
        "raw_repository_payload",
        "apply_token",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} contains a non-string key")
            elif key.casefold() in forbidden:
                errors.append(f"{path}.{key} is prohibited")
            _walk_safe(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_safe(child, f"{path}[{index}]", errors)
    elif isinstance(value, str) and len(value) > MAX_TEXT:
        errors.append(f"{path} exceeds the text bound")


def _validate_repository_record(
    raw: Any,
    index: int,
    errors: list[str],
) -> str | None:
    path = f"repositories[{index}]"
    record = _object(raw, REPOSITORY_KEYS, path, errors) or {}
    repository = _repository(record.get("repository"), f"{path}.repository", errors)
    if record.get("visibility") not in VISIBILITIES:
        errors.append(f"{path}.visibility is unsupported")
    if record.get("lifecycle") not in LIFECYCLES:
        errors.append(f"{path}.lifecycle is unsupported")
    if record.get("canonical_role") not in ROLES:
        errors.append(f"{path}.canonical_role is unsupported")
    default_branch = record.get("default_branch")
    if default_branch is not None:
        _branch(default_branch, f"{path}.default_branch", errors)
    observed_head = record.get("observed_head")
    if observed_head is not None:
        _commit(observed_head, f"{path}.observed_head", errors)
    if record.get("lifecycle") == "active":
        if default_branch is None:
            errors.append(f"{path}.default_branch is required for active repositories")
        if observed_head is None:
            errors.append(f"{path}.observed_head is required for active repositories")
    _url(
        record.get("linear_project_url"),
        f"{path}.linear_project_url",
        errors,
        allowed_hosts={"linear.app"},
    )
    _url(
        record.get("github_project_url"),
        f"{path}.github_project_url",
        errors,
        allowed_hosts={"github.com"},
    )
    _optional_sha(record.get("agents_sha256"), f"{path}.agents_sha256", errors)
    _strings(record.get("toolchain"), f"{path}.toolchain", errors, 0, 24, 1, 120)
    if record.get("ci_state") not in CI_STATES:
        errors.append(f"{path}.ci_state is unsupported")
    if not isinstance(record.get("security_policy"), bool):
        errors.append(f"{path}.security_policy must be boolean")
    license_spdx = record.get("license_spdx")
    if license_spdx is not None:
        _text(license_spdx, f"{path}.license_spdx", errors, 1, 64)
    if record.get("release_path") not in RELEASE_PATHS:
        errors.append(f"{path}.release_path is unsupported")
    if record.get("test_maturity") not in TEST_MATURITIES:
        errors.append(f"{path}.test_maturity is unsupported")
    if record.get("deployment_boundary") not in DEPLOYMENT_BOUNDARIES:
        errors.append(f"{path}.deployment_boundary is unsupported")
    _slug(record.get("expectation_profile"), f"{path}.expectation_profile", errors)
    _sha(
        record.get("current_state_sha256"),
        f"{path}.current_state_sha256",
        errors,
    )
    return repository


def _score_composite(scores: Mapping[str, int]) -> int:
    return (
        scores["security"] * 25
        + scores["data_integrity"] * 20
        + scores["user_impact"] * 15
        + scores["dependency_centrality"] * 15
        + scores["operational_risk"] * 15
        + (100 - scores["effort"]) * 5
        + scores["test_evidence"] * 5
    )


def _validate_remediation(
    value: Any,
    path: str,
    disposition: str | None,
    errors: list[str],
) -> None:
    remediation = _object(value, REMEDIATION_KEYS, path, errors) or {}
    branch_name = remediation.get("branch_name")
    base_commit = remediation.get("base_commit")
    checks = remediation.get("acceptance_checks")
    if disposition in ACTIONABLE_DISPOSITIONS:
        _branch(branch_name, f"{path}.branch_name", errors)
        _commit(base_commit, f"{path}.base_commit", errors)
        _strings(checks, f"{path}.acceptance_checks", errors, 2, 16, 8, 500)
    elif disposition in NOOP_DISPOSITIONS:
        if branch_name is not None or base_commit is not None:
            errors.append(f"{path} no-op dispositions must not declare a branch or base")
        _strings(checks, f"{path}.acceptance_checks", errors, 0, 8, 8, 500)
    if remediation.get("dry_run") is not True:
        errors.append(f"{path}.dry_run must be true")
    if remediation.get("apply_authorized") is not False:
        errors.append(f"{path}.apply_authorized must be false")
    _text(remediation.get("residual_risk"), f"{path}.residual_risk", errors, 8, 500)


def _validate_safety(root: Mapping[str, Any], errors: list[str]) -> None:
    safety = _object(root.get("safety"), SAFETY_KEYS, "safety", errors) or {}
    if safety.get("read_only") is not True:
        errors.append("safety.read_only must be true")
    if safety.get("dry_run") is not True:
        errors.append("safety.dry_run must be true")
    for key in (
        "repository_creation_authorized",
        "visibility_changes_authorized",
        "branch_protection_changes_authorized",
        "merge_authorized",
        "deployment_authorized",
        "dns_changes_authorized",
        "database_changes_authorized",
    ):
        if safety.get(key) is not False:
            errors.append(f"safety.{key} must be false")
    _strings(safety.get("notes"), "safety.notes", errors, 1, 12, 8, 500)


def _find_cycles(graph: Mapping[str, Sequence[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: list[str] = []

    def visit(node: str, stack: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            cycle = stack[stack.index(node) :] + [node]
            cycles.append(" -> ".join(cycle))
            return
        visiting.add(node)
        stack.append(node)
        for dependency in graph.get(node, ()):
            if dependency in graph:
                visit(dependency, stack)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node, [])
    return cycles


def validate(value: Mapping[str, Any]) -> QueueReport:
    errors: list[str] = []
    _walk_safe(value, "$", errors)
    root = _object(value, ROOT_KEYS, "$", errors) or {}
    if root.get("schema_version") != SCHEMA:
        errors.append(f"schema_version must be {SCHEMA}")
    _slug(root.get("queue_id"), "queue_id", errors)
    generated = _timestamp(root.get("generated_at"), "generated_at", errors)

    baseline = _object(root.get("baseline"), BASELINE_KEYS, "baseline", errors) or {}
    observed = _timestamp(baseline.get("observed_at"), "baseline.observed_at", errors)
    _sha(baseline.get("catalog_sha256"), "baseline.catalog_sha256", errors)
    _sha(baseline.get("policy_sha256"), "baseline.policy_sha256", errors)
    declared_repository_count = _integer(
        baseline.get("repository_count"),
        "baseline.repository_count",
        errors,
        0,
        100_000,
    )
    if generated is not None and observed is not None:
        if observed > generated:
            errors.append("baseline.observed_at cannot follow generated_at")
        if generated - observed > timedelta(days=31):
            errors.append("baseline is older than the 31-day remediation limit")

    repositories = root.get("repositories")
    if not isinstance(repositories, list) or not 1 <= len(repositories) <= 100_000:
        errors.append("repositories must contain 1..100000 records")
        repositories = []
    repository_ids: list[str] = []
    for index, raw in enumerate(repositories):
        repository = _validate_repository_record(raw, index, errors)
        if repository is not None:
            repository_ids.append(repository)
    if len(repository_ids) != len(set(repository_ids)):
        errors.append("repositories must not contain duplicate identities")
    if (
        declared_repository_count is not None
        and declared_repository_count != len(repositories)
    ):
        errors.append("baseline.repository_count must equal the repository records")

    findings = root.get("findings")
    if not isinstance(findings, list) or not 1 <= len(findings) <= 100_000:
        errors.append("findings must contain 1..100000 records")
        findings = []
    ids: set[str] = set()
    github_anchors: dict[str, str] = {}
    linear_anchors: dict[str, str] = {}
    graph: dict[str, list[str]] = {}
    rank_inputs: list[tuple[int, int, int, int, str]] = []
    rank_by_id: dict[str, int] = {}
    actionable_count = 0
    blocked_count = 0
    noop_count = 0

    for index, raw in enumerate(findings):
        path = f"findings[{index}]"
        finding = _object(raw, FINDING_KEYS, path, errors) or {}
        finding_id = _slug(finding.get("id"), f"{path}.id", errors)
        if finding_id is not None:
            if finding_id in ids:
                errors.append(f"duplicate finding id: {finding_id}")
            ids.add(finding_id)
        rank = _integer(finding.get("rank"), f"{path}.rank", errors, 1, 100_000)
        if finding_id is not None and rank is not None:
            rank_by_id[finding_id] = rank
        repository = _repository(
            finding.get("repository"), f"{path}.repository", errors
        )
        if repository is not None and repository not in set(repository_ids):
            errors.append(f"{path}.repository is absent from the inventory")
        if finding.get("category") not in CATEGORIES:
            errors.append(f"{path}.category is unsupported")
        state = finding.get("state")
        if state not in FINDING_STATES:
            errors.append(f"{path}.state is unsupported")
        severity = finding.get("severity")
        if severity not in SEVERITIES:
            errors.append(f"{path}.severity is unsupported")
        disposition = finding.get("disposition")
        if disposition not in DISPOSITIONS:
            errors.append(f"{path}.disposition is unsupported")
        if disposition in ACTIONABLE_DISPOSITIONS:
            actionable_count += 1
            if state in {
                "already_remediated",
                "intentionally_exempt",
                "superseded",
            }:
                errors.append(
                    f"{path} state {state} cannot use an actionable disposition"
                )
        elif disposition == "noop_blocked":
            blocked_count += 1
            noop_count += 1
            if state != "externally_blocked":
                errors.append(f"{path}.noop_blocked requires externally_blocked state")
        elif disposition in NOOP_DISPOSITIONS:
            noop_count += 1
        _sha(finding.get("evidence_sha256"), f"{path}.evidence_sha256", errors)
        _optional_sha(
            finding.get("baseline_value_sha256"),
            f"{path}.baseline_value_sha256",
            errors,
        )
        _optional_sha(
            finding.get("current_value_sha256"),
            f"{path}.current_value_sha256",
            errors,
        )
        linear = _text(
            finding.get("linear_anchor"), f"{path}.linear_anchor", errors, 5, 32
        )
        if linear is not None and not LINEAR.fullmatch(linear):
            errors.append(f"{path}.linear_anchor must be a DEN-N identifier")
        github = _github_anchor(
            finding.get("github_anchor"), f"{path}.github_anchor", errors
        )
        owner = _text(finding.get("owner"), f"{path}.owner", errors, 2, 160)
        if owner is not None and owner.strip() != owner:
            errors.append(f"{path}.owner must not contain edge whitespace")
        duplicate_of = finding.get("duplicate_of")
        if disposition == "noop_duplicate":
            duplicate_of = _slug(duplicate_of, f"{path}.duplicate_of", errors)
        elif duplicate_of is not None:
            errors.append(f"{path}.duplicate_of must be null unless noop_duplicate")
        dependencies = _strings(
            finding.get("depends_on"), f"{path}.depends_on", errors, 0, 32, 3, 96
        )
        if finding_id is not None:
            graph[finding_id] = dependencies
        score_object = _object(
            finding.get("scores"), SCORE_KEYS, f"{path}.scores", errors
        ) or {}
        scores: dict[str, int] = {}
        for name in SCORE_KEYS:
            score = _integer(
                score_object.get(name), f"{path}.scores.{name}", errors, 0, 100
            )
            if score is not None:
                scores[name] = score
        _validate_remediation(
            finding.get("remediation"), f"{path}.remediation", disposition, errors
        )
        if finding_id is not None and rank is not None and linear is not None:
            previous = linear_anchors.get(linear)
            if previous is not None and disposition != "noop_duplicate":
                errors.append(
                    f"Linear anchor {linear} is shared by non-duplicate findings "
                    f"{previous} and {finding_id}"
                )
            else:
                linear_anchors.setdefault(linear, finding_id)
        if finding_id is not None and rank is not None and github is not None:
            previous = github_anchors.get(github)
            if previous is not None and disposition != "noop_duplicate":
                errors.append(
                    f"GitHub anchor {github} is shared by non-duplicate findings "
                    f"{previous} and {finding_id}"
                )
            else:
                github_anchors.setdefault(github, finding_id)
        if (
            finding_id is not None
            and rank is not None
            and severity in SEVERITIES
            and disposition in DISPOSITIONS
            and len(scores) == len(SCORE_KEYS)
        ):
            rank_inputs.append(
                (
                    rank,
                    DISPOSITION_TIER[disposition],
                    -SEVERITY_WEIGHT[severity],
                    -_score_composite(scores),
                    finding_id,
                )
            )

    if sorted(rank for rank, *_rest in rank_inputs) != list(
        range(1, len(findings) + 1)
    ):
        errors.append("finding ranks must be unique and contiguous from 1")
    deterministic = sorted(
        rank_inputs, key=lambda item: (item[1], item[2], item[3], item[4])
    )
    for expected_rank, item in enumerate(deterministic, start=1):
        if item[0] != expected_rank:
            errors.append(
                "finding ranks must follow disposition tier, severity, risk score, "
                "and finding-id tie-breaking"
            )
            break
    if len(rank_inputs) != len(findings):
        errors.append("every finding must have complete deterministic ranking data")

    for finding_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency == finding_id:
                errors.append(f"finding {finding_id} cannot depend on itself")
            elif dependency not in ids:
                errors.append(
                    f"finding {finding_id} depends on unknown finding {dependency}"
                )
            elif (
                finding_id in rank_by_id
                and dependency in rank_by_id
                and rank_by_id[dependency] >= rank_by_id[finding_id]
            ):
                errors.append(
                    f"finding {finding_id} dependency {dependency} must rank earlier"
                )
    for cycle in _find_cycles(graph):
        errors.append(f"finding dependency cycle: {cycle}")

    by_id = {
        raw.get("id"): raw
        for raw in findings
        if isinstance(raw, dict) and isinstance(raw.get("id"), str)
    }
    for finding_id, raw in by_id.items():
        if raw.get("disposition") == "noop_duplicate":
            target = raw.get("duplicate_of")
            if target not in by_id:
                errors.append(
                    f"duplicate finding {finding_id} references unknown target {target}"
                )
            elif by_id[target].get("disposition") == "noop_duplicate":
                errors.append(
                    f"duplicate finding {finding_id} cannot target another duplicate"
                )

    _validate_safety(root, errors)
    try:
        checksum = canonical_sha256(value)
    except (TypeError, ValueError) as exc:
        errors.append(f"queue cannot be canonically serialized: {exc}")
        checksum = None
    return QueueReport(
        valid=not errors,
        queue_sha256=checksum,
        repository_count=len(repositories),
        finding_count=len(findings),
        actionable_count=actionable_count,
        blocked_count=blocked_count,
        noop_count=noop_count,
        errors=tuple(errors),
    )
