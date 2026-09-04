#!/usr/bin/env python3
"""Validate a Zed package-quartet clean-consumer certification receipt."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import PurePosixPath, Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

SCHEMA = "zed-clean-consumer-receipt/v1"
MAX_BYTES = 512 * 1024
MAX_TEXT = 2_000
ROOT_KEYS = {
    "schema_version",
    "receipt_id",
    "generated_at",
    "packages",
    "submodules",
    "consumers",
    "operations",
    "graph_edges",
    "blockers",
    "safety",
}
PACKAGE_KEYS = {
    "id",
    "role",
    "version",
    "repository",
    "commit",
    "manifest_sha256",
    "lock_sha256",
    "artifact_sha256",
    "registry",
    "status",
}
SUBMODULE_KEYS = {
    "id",
    "path",
    "repository",
    "commit",
    "recursive",
    "role",
    "status",
}
CONSUMER_KEYS = {
    "id",
    "language",
    "status",
    "clean_checkout",
    "local_path_dependencies",
    "monorepo_leakage",
    "compiled",
    "executed",
    "package_ids",
    "submodule_ids",
    "evidence_sha256",
}
OPERATION_KEYS = {
    "id",
    "kind",
    "status",
    "offline",
    "concurrent",
    "evidence_sha256",
    "blockers",
}
EDGE_KEYS = {"from", "to", "kind"}
SAFETY_KEYS = {
    "contains_credentials",
    "production_registry_mutation_authorized",
    "repository_creation_authorized",
    "merge_authorized",
    "notes",
}
PACKAGE_ROLES = {"clients", "library", "interfaces", "cli"}
PACKAGE_STATUSES = {"planned", "verified", "blocked"}
SUBMODULE_ROLES = {"source", "test", "tooling"}
SUBMODULE_STATUSES = {"planned", "verified", "blocked"}
CONSUMER_STATUSES = {"planned", "green", "red", "blocked"}
OPERATION_STATUSES = {"planned", "green", "red", "blocked"}
EDGE_KINDS = {"package-dependency", "git-submodule"}
REQUIRED_LANGUAGES = {"rust", "typescript", "dart", "go", "native"}
REQUIRED_OPERATIONS = {
    "install",
    "restore",
    "offline-reuse",
    "uninstall",
    "downgrade",
    "concurrent-install",
}
SLUG = re.compile(r"^[a-z][a-z0-9-]{2,95}$")
VERSION = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
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
    "message_body",
    "credential",
    "access_token",
    "api_key",
    "private_key",
    "secret_value",
}


class ReceiptError(ValueError):
    pass


@dataclass(frozen=True)
class ReceiptReport:
    valid: bool
    closure_ready: bool
    mode: str
    receipt_sha256: str | None
    package_count: int
    submodule_count: int
    consumer_count: int
    operation_count: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


def _pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ReceiptError(f"duplicate JSON key: {key}")
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
        raise ReceiptError(f"receipt exceeds {MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReceiptError("receipt must be UTF-8") from exc
    if EMAIL.search(text):
        raise ReceiptError("receipt must not contain email addresses")
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        raise ReceiptError("receipt contains prohibited credential material")
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise ReceiptError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ReceiptError("receipt root must be an object")
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
    result: list[str] = []
    for index, item in enumerate(
        _array(value, path, errors, minimum, maximum)
    ):
        text = _text(item, f"{path}[{index}]", errors, 1, 300)
        if text is not None:
            result.append(text)
    if len(result) != len(set(result)):
        errors.append(f"{path} must not contain duplicates")
    return result


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
    label: str,
) -> str | None:
    if value is None:
        return None
    text = _text(value, path, errors, length, length)
    if text is not None and not pattern.fullmatch(text):
        errors.append(f"{path} must be null or a lowercase {label}")
    return text


def _boolean(value: Any, path: str, errors: list[str]) -> bool | None:
    if not isinstance(value, bool):
        errors.append(f"{path} must be boolean")
        return None
    return value


def _objects(
    value: Any,
    expected: set[str],
    path: str,
    errors: list[str],
    minimum: int,
) -> list[dict[str, Any]]:
    return [
        _object(raw, expected, f"{path}[{index}]", errors)
        for index, raw in enumerate(_array(value, path, errors, minimum))
    ]


def _infra_like(repository: str | None, path: str | None) -> bool:
    names: list[str] = []
    if repository:
        parsed = urlparse(repository)
        parts = [part.casefold() for part in parsed.path.split("/") if part]
        if parts:
            names.append(parts[-1])
    if path:
        names.extend(part.casefold() for part in PurePosixPath(path).parts)
    return any(
        name == "infra"
        or name.endswith("-infra")
        or name.endswith("_infra")
        for name in names
    )


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
        for target in graph.get(node, ()):
            if target in graph:
                visit(target)
        stack.pop()
        visiting.remove(node)
        done.add(node)

    for node in graph:
        visit(node)
    return found


def validate(value: Mapping[str, Any], mode: str = "closure") -> ReceiptReport:
    if mode not in {"planning", "closure"}:
        raise ReceiptError("mode must be planning or closure")
    errors: list[str] = []
    warnings: list[str] = []
    _walk(value, "$", errors)
    root = _object(value, ROOT_KEYS, "$", errors)
    if root.get("schema_version") != SCHEMA:
        errors.append(f"schema_version must be {SCHEMA}")
    _slug(root.get("receipt_id"), "receipt_id", errors)
    _timestamp(root.get("generated_at"), "generated_at", errors)

    packages = _objects(root.get("packages"), PACKAGE_KEYS, "packages", errors, 4)
    if len(packages) != 4:
        errors.append(f"packages must contain exactly four entries; got {len(packages)}")
    package_ids: set[str] = set()
    roles: set[str] = set()
    for index, item in enumerate(packages):
        path = f"packages[{index}]"
        package_id = _slug(item.get("id"), f"{path}.id", errors)
        if package_id:
            if package_id in package_ids:
                errors.append(f"duplicate package id: {package_id}")
            package_ids.add(package_id)
        role = item.get("role")
        if role not in PACKAGE_ROLES:
            errors.append(f"{path}.role is unsupported")
        else:
            if role in roles:
                errors.append(f"duplicate package role: {role}")
            roles.add(role)
        version = _text(item.get("version"), f"{path}.version", errors, 5, 80)
        if version and not VERSION.fullmatch(version):
            errors.append(f"{path}.version must be semantic version text")
        _github_repo(item.get("repository"), f"{path}.repository", errors)
        commit = _optional_pattern(
            item.get("commit"), f"{path}.commit", errors, GIT_SHA, 40, "Git SHA"
        )
        manifest = _optional_pattern(
            item.get("manifest_sha256"),
            f"{path}.manifest_sha256",
            errors,
            SHA256,
            64,
            "SHA-256 digest",
        )
        lock = _optional_pattern(
            item.get("lock_sha256"),
            f"{path}.lock_sha256",
            errors,
            SHA256,
            64,
            "SHA-256 digest",
        )
        artifact = _optional_pattern(
            item.get("artifact_sha256"),
            f"{path}.artifact_sha256",
            errors,
            SHA256,
            64,
            "SHA-256 digest",
        )
        if item.get("registry") != "zed":
            errors.append(f"{path}.registry must be zed")
        status = item.get("status")
        if status not in PACKAGE_STATUSES:
            errors.append(f"{path}.status is unsupported")
        if status == "verified" and None in (commit, manifest, lock, artifact):
            errors.append(
                f"{path} verified package requires commit, manifest, lock, and artifact digests"
            )
    if roles != PACKAGE_ROLES:
        errors.append(
            "package roles must be exactly clients, library, interfaces, and cli"
        )

    submodules = _objects(
        root.get("submodules"), SUBMODULE_KEYS, "submodules", errors, 0
    )
    submodule_ids: set[str] = set()
    for index, item in enumerate(submodules):
        path = f"submodules[{index}]"
        submodule_id = _slug(item.get("id"), f"{path}.id", errors)
        if submodule_id:
            if submodule_id in submodule_ids:
                errors.append(f"duplicate submodule id: {submodule_id}")
            submodule_ids.add(submodule_id)
        raw_path = _text(item.get("path"), f"{path}.path", errors, 1, 300)
        if raw_path:
            parsed_path = PurePosixPath(raw_path)
            if (
                parsed_path.is_absolute()
                or ".." in parsed_path.parts
                or raw_path.startswith(".git/")
            ):
                errors.append(f"{path}.path must be a safe relative path")
        repository = _github_repo(
            item.get("repository"), f"{path}.repository", errors
        )
        commit = _optional_pattern(
            item.get("commit"), f"{path}.commit", errors, GIT_SHA, 40, "Git SHA"
        )
        _boolean(item.get("recursive"), f"{path}.recursive", errors)
        if item.get("role") not in SUBMODULE_ROLES:
            errors.append(f"{path}.role is unsupported")
        status = item.get("status")
        if status not in SUBMODULE_STATUSES:
            errors.append(f"{path}.status is unsupported")
        if _infra_like(repository, raw_path):
            errors.append(f"{path} must not include an infrastructure repository")
        if status == "verified" and commit is None:
            errors.append(f"{path}.commit is required when status is verified")

    consumers = _objects(
        root.get("consumers"), CONSUMER_KEYS, "consumers", errors, 5
    )
    consumer_ids: set[str] = set()
    languages: set[str] = set()
    for index, item in enumerate(consumers):
        path = f"consumers[{index}]"
        consumer_id = _slug(item.get("id"), f"{path}.id", errors)
        if consumer_id:
            if consumer_id in consumer_ids:
                errors.append(f"duplicate consumer id: {consumer_id}")
            consumer_ids.add(consumer_id)
        language = _slug(item.get("language"), f"{path}.language", errors)
        if language:
            languages.add(language)
        status = item.get("status")
        if status not in CONSUMER_STATUSES:
            errors.append(f"{path}.status is unsupported")
        booleans = {
            field: _boolean(item.get(field), f"{path}.{field}", errors)
            for field in (
                "clean_checkout",
                "local_path_dependencies",
                "monorepo_leakage",
                "compiled",
                "executed",
            )
        }
        referenced_packages = _strings(
            item.get("package_ids"), f"{path}.package_ids", errors, 4, 4
        )
        if set(referenced_packages) != package_ids:
            errors.append(f"{path}.package_ids must reference the complete quartet")
        for submodule_id in _strings(
            item.get("submodule_ids"), f"{path}.submodule_ids", errors
        ):
            if submodule_id not in submodule_ids:
                errors.append(f"{path} references unknown submodule {submodule_id}")
        evidence = _optional_pattern(
            item.get("evidence_sha256"),
            f"{path}.evidence_sha256",
            errors,
            SHA256,
            64,
            "SHA-256 digest",
        )
        if status == "green" and (
            booleans.get("clean_checkout") is not True
            or booleans.get("local_path_dependencies") is not False
            or booleans.get("monorepo_leakage") is not False
            or booleans.get("compiled") is not True
            or booleans.get("executed") is not True
            or evidence is None
        ):
            errors.append(
                f"{path} green consumer requires clean compile-and-execute evidence without leakage"
            )

    operations = _objects(
        root.get("operations"), OPERATION_KEYS, "operations", errors, 6
    )
    operation_ids: set[str] = set()
    operation_kinds: set[str] = set()
    for index, item in enumerate(operations):
        path = f"operations[{index}]"
        operation_id = _slug(item.get("id"), f"{path}.id", errors)
        if operation_id:
            if operation_id in operation_ids:
                errors.append(f"duplicate operation id: {operation_id}")
            operation_ids.add(operation_id)
        kind = _slug(item.get("kind"), f"{path}.kind", errors)
        if kind:
            if kind in operation_kinds:
                errors.append(f"duplicate operation kind: {kind}")
            operation_kinds.add(kind)
        status = item.get("status")
        if status not in OPERATION_STATUSES:
            errors.append(f"{path}.status is unsupported")
        _boolean(item.get("offline"), f"{path}.offline", errors)
        _boolean(item.get("concurrent"), f"{path}.concurrent", errors)
        evidence = _optional_pattern(
            item.get("evidence_sha256"),
            f"{path}.evidence_sha256",
            errors,
            SHA256,
            64,
            "SHA-256 digest",
        )
        blockers = _strings(item.get("blockers"), f"{path}.blockers", errors)
        if status == "green" and (evidence is None or blockers):
            errors.append(
                f"{path} green operation requires evidence and zero blockers"
            )
    if operation_kinds != REQUIRED_OPERATIONS:
        errors.append(
            "operation kinds must be exactly install, restore, offline-reuse, "
            "uninstall, downgrade, and concurrent-install"
        )

    node_ids = package_ids | submodule_ids
    graph: dict[str, list[str]] = {node: [] for node in node_ids}
    seen_edges: set[tuple[str, str, str]] = set()
    edges = _objects(
        root.get("graph_edges"), EDGE_KEYS, "graph_edges", errors, 0
    )
    for index, item in enumerate(edges):
        path = f"graph_edges[{index}]"
        source = _slug(item.get("from"), f"{path}.from", errors)
        target = _slug(item.get("to"), f"{path}.to", errors)
        kind = item.get("kind")
        if kind not in EDGE_KINDS:
            errors.append(f"{path}.kind is unsupported")
        if source and source not in node_ids:
            errors.append(f"{path}.from references unknown node {source}")
        if target and target not in node_ids:
            errors.append(f"{path}.to references unknown node {target}")
        if source and target and kind in EDGE_KINDS:
            edge = (source, target, kind)
            if edge in seen_edges:
                errors.append(f"duplicate graph edge: {edge}")
            seen_edges.add(edge)
            if source == target:
                errors.append(f"{path} cannot be a self-edge")
            elif source in graph and target in graph:
                graph[source].append(target)
    for cycle in _cycles(graph):
        errors.append(f"dependency cycle: {cycle}")

    blockers = _strings(root.get("blockers"), "blockers", errors)
    safety = _object(root.get("safety"), SAFETY_KEYS, "safety", errors)
    for field in (
        "contains_credentials",
        "production_registry_mutation_authorized",
        "repository_creation_authorized",
        "merge_authorized",
    ):
        if safety.get(field) is not False:
            errors.append(f"safety.{field} must be false")
    _strings(safety.get("notes"), "safety.notes", errors, 1, 20)

    closure_reasons: list[str] = []
    if any(item.get("status") != "verified" for item in packages):
        closure_reasons.append("all four packages must be verified")
    if any(item.get("status") != "verified" for item in submodules):
        closure_reasons.append("all declared submodules must be verified")
    if not REQUIRED_LANGUAGES.issubset(languages):
        closure_reasons.append(
            "required languages are missing: "
            + ", ".join(sorted(REQUIRED_LANGUAGES - languages))
        )
    for index, item in enumerate(consumers):
        if (
            item.get("status") != "green"
            or item.get("clean_checkout") is not True
            or item.get("local_path_dependencies") is not False
            or item.get("monorepo_leakage") is not False
            or item.get("compiled") is not True
            or item.get("executed") is not True
            or item.get("evidence_sha256") is None
        ):
            closure_reasons.append(f"consumer {index} is not certified")
    for kind in REQUIRED_OPERATIONS:
        candidate = next(
            (item for item in operations if item.get("kind") == kind), None
        )
        if (
            candidate is None
            or candidate.get("status") != "green"
            or candidate.get("evidence_sha256") is None
            or candidate.get("blockers")
        ):
            closure_reasons.append(f"operation is not certified: {kind}")
    offline = next(
        (item for item in operations if item.get("kind") == "offline-reuse"),
        None,
    )
    if offline is not None and offline.get("offline") is not True:
        closure_reasons.append("offline-reuse must execute without network access")
    concurrent = next(
        (
            item
            for item in operations
            if item.get("kind") == "concurrent-install"
        ),
        None,
    )
    if concurrent is not None and concurrent.get("concurrent") is not True:
        closure_reasons.append("concurrent-install must exercise real contention")
    if blockers:
        closure_reasons.append("receipt blockers must be empty")

    closure_ready = not closure_reasons
    if blockers:
        warnings.append(f"receipt has {len(blockers)} explicit blocker(s)")
    if mode == "closure" and not closure_ready:
        errors.extend(f"closure: {reason}" for reason in closure_reasons)

    try:
        receipt_sha: str | None = digest(value)
    except (TypeError, ValueError) as exc:
        errors.append(f"receipt cannot be serialized: {exc}")
        receipt_sha = None

    return ReceiptReport(
        valid=not errors,
        closure_ready=closure_ready and not errors,
        mode=mode,
        receipt_sha256=receipt_sha,
        package_count=len(packages),
        submodule_count=len(submodules),
        consumer_count=len(consumers),
        operation_count=len(operations),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("receipt", type=Path)
    result.add_argument("--mode", choices=("planning", "closure"), default="closure")
    result.add_argument("--json", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = validate(load(args.receipt), mode=args.mode)
    except (ReceiptError, OSError) as exc:
        if args.json:
            print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        else:
            print(f"Zed clean-consumer receipt validation failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(report), sort_keys=True))
    else:
        print(f"valid={str(report.valid).lower()}")
        print(f"closure_ready={str(report.closure_ready).lower()}")
        print(f"packages={report.package_count}")
        print(f"submodules={report.submodule_count}")
        print(f"consumers={report.consumer_count}")
        print(f"operations={report.operation_count}")
        print(f"receipt_sha256={report.receipt_sha256}")
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    return 0 if report.valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
