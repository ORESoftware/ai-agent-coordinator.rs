#!/usr/bin/env python3
"""Validate a truthful portfolio release manifest in planning or closure mode."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA = "portfolio-release-manifest/v1"
MAX_BYTES = 512 * 1024
MAX_TEXT = 2_000
ROOT_KEYS = {
    "schema_version",
    "release_id",
    "generated_at",
    "coverage",
    "repositories",
    "capabilities",
    "artifacts",
    "lanes",
    "blockers",
    "decision",
    "safety",
}
COVERAGE_KEYS = {
    "catalog_complete",
    "expected_repository_count",
    "observed_repository_count",
    "unresolved_repository_count",
}
REPOSITORY_KEYS = {
    "id",
    "github_url",
    "role",
    "environment",
    "status",
    "head_sha",
    "default_branch",
    "visibility",
    "archived",
    "superseded",
    "linear_anchor",
}
CAPABILITY_KEYS = {
    "id",
    "implementation_repositories",
    "independent_lanes",
    "maturity",
    "status",
    "blockers",
}
ARTIFACT_KEYS = {
    "id",
    "repository_id",
    "kind",
    "status",
    "sha256",
    "provenance_sha256",
}
LANE_KEYS = {
    "id",
    "kind",
    "status",
    "head_sha",
    "evidence_sha256",
    "destructive",
    "independent",
    "blockers",
}
DECISION_KEYS = {"value", "rationale", "approved_by", "decided_at"}
SAFETY_KEYS = {
    "read_only",
    "deployment_authorized",
    "migration_authorized",
    "credential_rotation_authorized",
    "real_user_enrollment_authorized",
    "notes",
}
REQUIRED_LANE_KINDS = {
    "contract",
    "sdk-clean-consumer",
    "browser",
    "security",
    "migration",
    "recovery",
    "scale",
    "rollback",
}
DESTRUCTIVE_LANE_KINDS = {"security", "recovery", "scale", "rollback"}
STATUSES = {
    "repository": {"planned", "verified", "blocked"},
    "capability": {"planned", "verified", "blocked"},
    "artifact": {"planned", "verified", "blocked"},
    "lane": {"planned", "green", "red", "blocked"},
}
ENVIRONMENTS = {"production", "test", "shared-tooling"}
VISIBILITIES = {"public", "private", "internal"}
DECISIONS = {"go", "hold", "stop"}
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,95}$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
LINEAR = re.compile(r"^DEN-[1-9][0-9]*$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
SECRET_PATTERNS = (
    re.compile(r"\bghp_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"\blin_api_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"(?i)[?&](?:token|access_token|api[_-]?key|secret|password)="),
    re.compile(r"(?i)\b(?:token|api[_-]?key|secret|password)\s*[:=]\s*[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
PROHIBITED_FIELDS = {
    "raw_prompt",
    "prompt_text",
    "message_body",
    "credential",
    "access_token",
    "api_key",
    "private_key",
    "secret_value",
}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class ManifestReport:
    valid: bool
    closure_ready: bool
    mode: str
    manifest_sha256: str | None
    repository_count: int
    capability_count: int
    artifact_count: int
    lane_count: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ManifestError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def load(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if len(raw) > MAX_BYTES:
        raise ManifestError(f"manifest exceeds {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError("manifest must be UTF-8") from exc
    if EMAIL.search(text):
        raise ManifestError("manifest must not contain email addresses")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise ManifestError("manifest contains prohibited credential material")
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ManifestError("manifest root must be an object")
    return value


def _walk(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                errors.append(f"{path} contains a non-string key")
            elif key.casefold() in PROHIBITED_FIELDS:
                errors.append(f"{path}.{key} is a prohibited sensitive field")
            _walk(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk(child, f"{path}[{index}]", errors)
    elif isinstance(value, str):
        if len(value) > MAX_TEXT:
            errors.append(f"{path} exceeds the text bound")
        if EMAIL.search(value) or any(
            pattern.search(value) for pattern in SECRET_PATTERNS
        ):
            errors.append(f"{path} contains prohibited personal or credential material")


def _object(
    value: Any, expected: set[str], path: str, errors: list[str]
) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return {}
    actual = set(value)
    if actual != expected:
        errors.append(
            f"{path} keys mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _array(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int = 0,
    maximum: int = 10_000,
) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{path} must be an array")
        return []
    if not minimum <= len(value) <= maximum:
        errors.append(
            f"{path} must contain between {minimum} and {maximum} items"
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
        errors.append(f"{path} must be text of length {minimum}..{maximum}")
        return None
    return value


def _slug(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 3, 96)
    if text is not None and not SLUG.fullmatch(text):
        errors.append(f"{path} must be a lowercase slug")
    return text


def _strings(
    value: Any,
    path: str,
    errors: list[str],
    minimum: int = 0,
    maximum: int = 1_000,
) -> list[str]:
    items = _array(value, path, errors, minimum, maximum)
    result: list[str] = []
    for index, item in enumerate(items):
        text = _text(item, f"{path}[{index}]", errors, 1, 500)
        if text is not None:
            result.append(text)
    if len(result) != len(set(result)):
        errors.append(f"{path} must not contain duplicates")
    return result


def _count(value: Any, path: str, errors: list[str]) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{path} must be null or a non-negative integer")
        return None
    return value


def _timestamp(value: Any, path: str, errors: list[str]) -> datetime | None:
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


def _github_repo(value: Any, path: str, errors: list[str]) -> str | None:
    text = _text(value, path, errors, 20, 300)
    if text is None:
        return None
    parsed = urlparse(text)
    parts = [part for part in parsed.path.split("/") if part]
    if not (
        parsed.scheme == "https"
        and parsed.netloc == "github.com"
        and not parsed.query
        and not parsed.fragment
        and len(parts) == 2
    ):
        errors.append(f"{path} must be an exact GitHub repository URL")
    return text


def _optional_pattern(
    value: Any,
    path: str,
    errors: list[str],
    pattern: re.Pattern[str],
    length: int,
    name: str,
) -> str | None:
    if value is None:
        return None
    text = _text(value, path, errors, length, length)
    if text is not None and not pattern.fullmatch(text):
        errors.append(f"{path} must be null or a lowercase {name}")
    return text


def _normalize_objects(
    value: Any,
    expected: set[str],
    path: str,
    errors: list[str],
    minimum: int = 1,
) -> list[dict[str, Any]]:
    return [
        _object(raw, expected, f"{path}[{index}]", errors)
        for index, raw in enumerate(_array(value, path, errors, minimum))
    ]


def validate(value: Mapping[str, Any], mode: str = "closure") -> ManifestReport:
    if mode not in {"planning", "closure"}:
        raise ManifestError("mode must be planning or closure")
    errors: list[str] = []
    warnings: list[str] = []
    _walk(value, "$", errors)
    root = _object(value, ROOT_KEYS, "$", errors)
    if root.get("schema_version") != SCHEMA:
        errors.append(f"schema_version must be {SCHEMA}")
    _slug(root.get("release_id"), "release_id", errors)
    _timestamp(root.get("generated_at"), "generated_at", errors)

    coverage = _object(root.get("coverage"), COVERAGE_KEYS, "coverage", errors)
    catalog_complete = coverage.get("catalog_complete")
    if not isinstance(catalog_complete, bool):
        errors.append("coverage.catalog_complete must be boolean")
        catalog_complete = False
    expected_count = _count(
        coverage.get("expected_repository_count"),
        "coverage.expected_repository_count",
        errors,
    )
    observed_count = _count(
        coverage.get("observed_repository_count"),
        "coverage.observed_repository_count",
        errors,
    )
    unresolved_count = _count(
        coverage.get("unresolved_repository_count"),
        "coverage.unresolved_repository_count",
        errors,
    )

    repositories = _normalize_objects(
        root.get("repositories"), REPOSITORY_KEYS, "repositories", errors
    )
    repository_ids: set[str] = set()
    repository_urls: set[str] = set()
    for index, item in enumerate(repositories):
        path = f"repositories[{index}]"
        repository_id = _slug(item.get("id"), f"{path}.id", errors)
        if repository_id:
            if repository_id in repository_ids:
                errors.append(f"duplicate repository id: {repository_id}")
            repository_ids.add(repository_id)
        url = _github_repo(item.get("github_url"), f"{path}.github_url", errors)
        if url:
            if url in repository_urls:
                errors.append(f"duplicate repository URL: {url}")
            repository_urls.add(url)
        _text(item.get("role"), f"{path}.role", errors, 3, 120)
        if item.get("environment") not in ENVIRONMENTS:
            errors.append(f"{path}.environment is unsupported")
        status = item.get("status")
        if status not in STATUSES["repository"]:
            errors.append(f"{path}.status is unsupported")
        head = _optional_pattern(
            item.get("head_sha"), f"{path}.head_sha", errors, GIT_SHA, 40, "Git SHA"
        )
        branch = _text(
            item.get("default_branch"), f"{path}.default_branch", errors, 1, 200
        )
        if branch and not BRANCH.fullmatch(branch):
            errors.append(f"{path}.default_branch has an invalid format")
        if item.get("visibility") not in VISIBILITIES:
            errors.append(f"{path}.visibility is unsupported")
        for field in ("archived", "superseded"):
            if not isinstance(item.get(field), bool):
                errors.append(f"{path}.{field} must be boolean")
        linear = _text(
            item.get("linear_anchor"), f"{path}.linear_anchor", errors, 5, 32
        )
        if linear and not LINEAR.fullmatch(linear):
            errors.append(f"{path}.linear_anchor must be a DEN-N identifier")
        if status == "verified" and head is None:
            errors.append(f"{path}.head_sha is required when status is verified")

    if observed_count is not None and observed_count != len(repositories):
        errors.append(
            "coverage.observed_repository_count must equal repositories length"
        )
    if None not in (expected_count, observed_count, unresolved_count):
        assert expected_count is not None
        assert observed_count is not None
        assert unresolved_count is not None
        if observed_count + unresolved_count != expected_count:
            errors.append(
                "observed_repository_count + unresolved_repository_count must equal "
                "expected_repository_count"
            )

    lanes = _normalize_objects(root.get("lanes"), LANE_KEYS, "lanes", errors)
    lane_ids: set[str] = set()
    lanes_by_id: dict[str, dict[str, Any]] = {}
    lanes_by_kind: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(lanes):
        path = f"lanes[{index}]"
        lane_id = _slug(item.get("id"), f"{path}.id", errors)
        if lane_id:
            if lane_id in lane_ids:
                errors.append(f"duplicate lane id: {lane_id}")
            lane_ids.add(lane_id)
            lanes_by_id[lane_id] = item
        kind = _slug(item.get("kind"), f"{path}.kind", errors)
        if kind:
            lanes_by_kind.setdefault(kind, []).append(item)
        status = item.get("status")
        if status not in STATUSES["lane"]:
            errors.append(f"{path}.status is unsupported")
        head = _optional_pattern(
            item.get("head_sha"), f"{path}.head_sha", errors, GIT_SHA, 40, "Git SHA"
        )
        evidence = _optional_pattern(
            item.get("evidence_sha256"),
            f"{path}.evidence_sha256",
            errors,
            SHA256,
            64,
            "SHA-256 digest",
        )
        for field in ("destructive", "independent"):
            if not isinstance(item.get(field), bool):
                errors.append(f"{path}.{field} must be boolean")
        blockers = _strings(item.get("blockers"), f"{path}.blockers", errors)
        if status == "green" and (head is None or evidence is None or blockers):
            errors.append(
                f"{path} green lane requires head, evidence, and zero blockers"
            )

    capabilities = _normalize_objects(
        root.get("capabilities"), CAPABILITY_KEYS, "capabilities", errors
    )
    capability_ids: set[str] = set()
    for index, item in enumerate(capabilities):
        path = f"capabilities[{index}]"
        capability_id = _slug(item.get("id"), f"{path}.id", errors)
        if capability_id:
            if capability_id in capability_ids:
                errors.append(f"duplicate capability id: {capability_id}")
            capability_ids.add(capability_id)
        for repository_id in _strings(
            item.get("implementation_repositories"),
            f"{path}.implementation_repositories",
            errors,
            1,
        ):
            if repository_id not in repository_ids:
                errors.append(
                    f"{path} references unknown repository {repository_id}"
                )
        for lane_id in _strings(
            item.get("independent_lanes"),
            f"{path}.independent_lanes",
            errors,
            1,
        ):
            if lane_id not in lane_ids:
                errors.append(f"{path} references unknown lane {lane_id}")
        maturity = item.get("maturity")
        if (
            isinstance(maturity, bool)
            or not isinstance(maturity, int)
            or not 0 <= maturity <= 5
        ):
            errors.append(f"{path}.maturity must be an integer from 0 through 5")
        if item.get("status") not in STATUSES["capability"]:
            errors.append(f"{path}.status is unsupported")
        _strings(item.get("blockers"), f"{path}.blockers", errors)

    artifacts = _normalize_objects(
        root.get("artifacts"), ARTIFACT_KEYS, "artifacts", errors
    )
    artifact_ids: set[str] = set()
    for index, item in enumerate(artifacts):
        path = f"artifacts[{index}]"
        artifact_id = _slug(item.get("id"), f"{path}.id", errors)
        if artifact_id:
            if artifact_id in artifact_ids:
                errors.append(f"duplicate artifact id: {artifact_id}")
            artifact_ids.add(artifact_id)
        repository_id = _slug(
            item.get("repository_id"), f"{path}.repository_id", errors
        )
        if repository_id and repository_id not in repository_ids:
            errors.append(f"{path} references unknown repository {repository_id}")
        _slug(item.get("kind"), f"{path}.kind", errors)
        status = item.get("status")
        if status not in STATUSES["artifact"]:
            errors.append(f"{path}.status is unsupported")
        sha = _optional_pattern(
            item.get("sha256"), f"{path}.sha256", errors, SHA256, 64, "SHA-256 digest"
        )
        provenance = _optional_pattern(
            item.get("provenance_sha256"),
            f"{path}.provenance_sha256",
            errors,
            SHA256,
            64,
            "SHA-256 digest",
        )
        if status == "verified" and (sha is None or provenance is None):
            errors.append(
                f"{path} verified artifact requires content and provenance digests"
            )

    blockers = _strings(root.get("blockers"), "blockers", errors)
    decision = _object(root.get("decision"), DECISION_KEYS, "decision", errors)
    decision_value = decision.get("value")
    if decision_value not in DECISIONS:
        errors.append("decision.value is unsupported")
    _text(decision.get("rationale"), "decision.rationale", errors, 12, 1_000)
    approved_by = _strings(decision.get("approved_by"), "decision.approved_by", errors)
    _timestamp(decision.get("decided_at"), "decision.decided_at", errors)

    safety = _object(root.get("safety"), SAFETY_KEYS, "safety", errors)
    if safety.get("read_only") is not True:
        errors.append("safety.read_only must be true")
    for field in (
        "deployment_authorized",
        "migration_authorized",
        "credential_rotation_authorized",
        "real_user_enrollment_authorized",
    ):
        if safety.get(field) is not False:
            errors.append(f"safety.{field} must be false")
    _strings(safety.get("notes"), "safety.notes", errors, 1, 20)

    closure_reasons: list[str] = []
    if not catalog_complete:
        closure_reasons.append("catalog_complete must be true")
    if None in (expected_count, observed_count, unresolved_count):
        closure_reasons.append("coverage counts must be known")
    elif unresolved_count != 0 or expected_count != observed_count:
        closure_reasons.append("repository coverage must be complete")
    for index, item in enumerate(repositories):
        if (
            item.get("status") != "verified"
            or item.get("head_sha") is None
            or item.get("archived") is True
            or item.get("superseded") is True
        ):
            closure_reasons.append(f"repository {index} is not releasable")
    for index, item in enumerate(capabilities):
        lane_refs = item.get("independent_lanes", [])
        maturity = item.get("maturity")
        if (
            item.get("status") != "verified"
            or not isinstance(maturity, int)
            or isinstance(maturity, bool)
            or maturity < 4
            or item.get("blockers")
            or not lane_refs
            or any(
                lanes_by_id.get(lane_id, {}).get("status") != "green"
                or lanes_by_id.get(lane_id, {}).get("independent") is not True
                for lane_id in lane_refs
            )
        ):
            closure_reasons.append(
                f"capability {index} lacks independent maturity-four evidence"
            )
    for index, item in enumerate(artifacts):
        if (
            item.get("status") != "verified"
            or item.get("sha256") is None
            or item.get("provenance_sha256") is None
        ):
            closure_reasons.append(f"artifact {index} is not verified")
    missing_kinds = REQUIRED_LANE_KINDS - set(lanes_by_kind)
    if missing_kinds:
        closure_reasons.append(
            "missing required lane kinds: " + ", ".join(sorted(missing_kinds))
        )
    for kind in REQUIRED_LANE_KINDS:
        if not any(
            lane.get("status") == "green"
            and lane.get("independent") is True
            and (
                kind not in DESTRUCTIVE_LANE_KINDS
                or lane.get("destructive") is True
            )
            for lane in lanes_by_kind.get(kind, [])
        ):
            closure_reasons.append(f"required lane kind is not certified: {kind}")
    if blockers:
        closure_reasons.append("manifest blockers must be empty")
    if decision_value != "go" or not approved_by:
        closure_reasons.append("decision must be approved go")

    closure_ready = not closure_reasons
    if decision_value == "go" and not closure_ready:
        errors.append(
            "decision.value cannot be go before closure: "
            + "; ".join(closure_reasons)
        )
    if not catalog_complete:
        warnings.append("catalog is incomplete; closure is blocked")
    if blockers:
        warnings.append(f"manifest has {len(blockers)} explicit blocker(s)")
    if mode == "closure" and not closure_ready:
        errors.extend(f"closure: {reason}" for reason in closure_reasons)

    try:
        manifest_sha: str | None = digest(value)
    except (TypeError, ValueError) as exc:
        errors.append(f"manifest cannot be serialized: {exc}")
        manifest_sha = None

    return ManifestReport(
        valid=not errors,
        closure_ready=closure_ready and not errors,
        mode=mode,
        manifest_sha256=manifest_sha,
        repository_count=len(repositories),
        capability_count=len(capabilities),
        artifact_count=len(artifacts),
        lane_count=len(lanes),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("manifest", type=Path)
    result.add_argument("--mode", choices=("planning", "closure"), default="closure")
    result.add_argument("--json", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = validate(load(args.manifest), mode=args.mode)
    except (ManifestError, OSError) as exc:
        if args.json:
            print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        else:
            print(f"portfolio release manifest validation failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(report), sort_keys=True))
    else:
        print(f"valid={str(report.valid).lower()}")
        print(f"closure_ready={str(report.closure_ready).lower()}")
        print(f"repositories={report.repository_count}")
        print(f"capabilities={report.capability_count}")
        print(f"artifacts={report.artifact_count}")
        print(f"lanes={report.lane_count}")
        print(f"manifest_sha256={report.manifest_sha256}")
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    return 0 if report.valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
