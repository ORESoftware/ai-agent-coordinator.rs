#!/usr/bin/env python3
"""Validate the MemeBank contract blueprint without third-party dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DRAFT = "https://json-schema.org/draft/2020-12/schema"
FORBIDDEN_PROPERTY_NAMES = {
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "credentials",
    "credential",
    "private_key",
    "presigned_url",
    "object_url",
    "download_url",
}
FORBIDDEN_FIXTURE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~-]+", re.IGNORECASE),
    re.compile(r"https://[^\s\"]+\.amazonaws\.com", re.IGNORECASE),
    re.compile(r"https://[^\s\"]+\.r2\.cloudflarestorage\.com", re.IGNORECASE),
)


class ContractValidationError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ContractValidationError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_reject_duplicate_keys)
    except OSError as error:
        raise ContractValidationError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ContractValidationError(f"invalid JSON in {path}: {error}") from error


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def json_pointer(document: Any, fragment: str, label: str) -> Any:
    if fragment in ("", "#"):
        return document
    pointer = fragment[1:] if fragment.startswith("#") else fragment
    if not pointer.startswith("/"):
        raise ContractValidationError(f"unsupported JSON pointer in {label}: {fragment!r}")
    current = document
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ContractValidationError(
                f"JSON pointer {fragment!r} does not resolve in {label}"
            )
    return current


def resolve_ref(root: Path, base_file: Path, reference: str) -> tuple[Any, Path]:
    if reference.startswith(("http://", "https://")):
        raise ContractValidationError(
            f"remote $ref values are forbidden in reproducible contracts: {reference}"
        )
    file_part, separator, fragment_part = reference.partition("#")
    target_file = base_file if not file_part else (base_file.parent / file_part)
    target_file = target_file.resolve()
    if not _inside(root, target_file):
        raise ContractValidationError(f"$ref escapes the contract root: {reference}")
    if not target_file.is_file():
        raise ContractValidationError(
            f"$ref from {base_file.relative_to(root)} points to missing file {reference}"
        )
    document = load_json(target_file)
    fragment = f"#{fragment_part}" if separator else "#"
    resolved = json_pointer(document, fragment, str(target_file.relative_to(root)))
    return resolved, target_file


def resolve_root_ref(root: Path, reference: str) -> tuple[Any, Path]:
    file_part, separator, fragment_part = reference.partition("#")
    if not file_part:
        raise ContractValidationError(f"root reference must include a file: {reference}")
    target_file = (root / file_part).resolve()
    if not _inside(root, target_file) or not target_file.is_file():
        raise ContractValidationError(f"baseline points to missing file: {reference}")
    document = load_json(target_file)
    fragment = f"#{fragment_part}" if separator else "#"
    return json_pointer(document, fragment, reference), target_file


def iter_nodes(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from iter_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nodes(child)


def validate_metadata(root: Path) -> None:
    schema_dir = root / "schemas" / "v1"
    schema_files = sorted(schema_dir.glob("*.schema.json"))
    if not schema_files:
        raise ContractValidationError("no JSON Schemas found")
    ids: set[str] = set()
    for path in schema_files:
        document = load_json(path)
        if not isinstance(document, dict):
            raise ContractValidationError(f"schema must be an object: {path}")
        if document.get("$schema") != DRAFT:
            raise ContractValidationError(f"{path.name} must use JSON Schema 2020-12")
        schema_id = document.get("$id")
        if not isinstance(schema_id, str) or not schema_id.startswith("https://"):
            raise ContractValidationError(f"{path.name} must have an HTTPS $id")
        if schema_id in ids:
            raise ContractValidationError(f"duplicate schema $id: {schema_id}")
        ids.add(schema_id)
        if not isinstance(document.get("title"), str) or not document["title"].strip():
            raise ContractValidationError(f"{path.name} must have a title")

    openapi_path = root / "openapi" / "v1" / "openapi.json"
    openapi = load_json(openapi_path)
    if openapi.get("openapi") != "3.1.0":
        raise ContractValidationError("OpenAPI must be version 3.1.0")
    if openapi.get("x-contract-version") != "1.0":
        raise ContractValidationError("OpenAPI x-contract-version must equal 1.0")
    if "BearerAuth" not in openapi.get("components", {}).get("securitySchemes", {}):
        raise ContractValidationError("OpenAPI must define BearerAuth")


def validate_local_refs(root: Path) -> int:
    files = sorted((root / "schemas").rglob("*.json")) + sorted(
        (root / "openapi").rglob("*.json")
    )
    count = 0
    for path in files:
        document = load_json(path)
        for node in iter_nodes(document):
            if isinstance(node, dict) and "$ref" in node:
                reference = node["$ref"]
                if not isinstance(reference, str):
                    raise ContractValidationError(
                        f"$ref must be a string in {path.relative_to(root)}"
                    )
                resolve_ref(root, path, reference)
                count += 1
    return count


def validate_package(root: Path, baseline: dict[str, Any]) -> None:
    manifest_path = root / ".zpkg.toml"
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ContractValidationError(f"invalid .zpkg.toml: {error}") from error
    package = manifest.get("package", {})
    if package.get("org") != "memebank" or package.get("name") != "mb-interfaces":
        raise ContractValidationError(".zpkg.toml package identity must be memebank/mb-interfaces")
    if package.get("version") != baseline.get("package_version"):
        raise ContractValidationError(
            ".zpkg.toml version must match compatibility/v1-baseline.json"
        )
    lock = tomllib.loads((root / ".zpkg.lock").read_text(encoding="utf-8"))
    if lock.get("version") != 1:
        raise ContractValidationError(".zpkg.lock version must equal 1")


def validate_openapi_baseline(root: Path, baseline: dict[str, Any]) -> None:
    openapi = load_json(root / "openapi" / "v1" / "openapi.json")
    if openapi.get("info", {}).get("version") != baseline.get("openapi_version"):
        raise ContractValidationError("OpenAPI info.version changed without a baseline update")
    paths = openapi.get("paths", {})
    for operation, expected_id in baseline.get("openapi_operations", {}).items():
        method, path = operation.split(" ", 1)
        operation_doc = paths.get(path, {}).get(method.lower())
        if not isinstance(operation_doc, dict):
            raise ContractValidationError(f"breaking change: missing operation {operation}")
        if operation_doc.get("operationId") != expected_id:
            raise ContractValidationError(
                f"breaking change: {operation} operationId must remain {expected_id!r}"
            )


def validate_compatibility(root: Path, baseline: dict[str, Any]) -> None:
    for entry in baseline.get("contracts", []):
        reference = entry.get("ref")
        if not isinstance(reference, str):
            raise ContractValidationError("compatibility contract ref must be a string")
        contract, _ = resolve_root_ref(root, reference)
        if not isinstance(contract, dict):
            raise ContractValidationError(f"compatibility contract is not an object: {reference}")
        required = contract.get("required", [])
        if not isinstance(required, list):
            raise ContractValidationError(f"required must be an array in {reference}")
        missing_required = sorted(set(entry.get("required", [])) - set(required))
        if missing_required:
            raise ContractValidationError(
                f"breaking change in {reference}: removed required fields {missing_required}"
            )
        properties = contract.get("properties", {})
        if not isinstance(properties, dict):
            raise ContractValidationError(f"properties must be an object in {reference}")
        for name, expected in entry.get("properties", {}).items():
            actual = properties.get(name)
            if not isinstance(actual, dict):
                raise ContractValidationError(
                    f"breaking change in {reference}: missing property {name!r}"
                )
            for key, expected_value in expected.items():
                if key == "oneOf" and expected_value is True:
                    if not isinstance(actual.get("oneOf"), list) or not actual["oneOf"]:
                        raise ContractValidationError(
                            f"breaking change in {reference}: property {name!r} lost oneOf"
                        )
                elif actual.get(key) != expected_value:
                    raise ContractValidationError(
                        f"breaking change in {reference}: property {name!r} changed {key}"
                    )


def validate_forbidden_fields(root: Path) -> None:
    for path in sorted((root / "schemas").rglob("*.json")):
        document = load_json(path)
        for node in iter_nodes(document):
            if isinstance(node, dict) and isinstance(node.get("properties"), dict):
                names = set(node["properties"])
                forbidden = sorted(names & FORBIDDEN_PROPERTY_NAMES)
                if forbidden:
                    raise ContractValidationError(
                        f"forbidden durable field names in {path.relative_to(root)}: {forbidden}"
                    )

    for path in sorted((root / "fixtures").rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_FIXTURE_PATTERNS:
            if pattern.search(text):
                raise ContractValidationError(
                    f"fixture appears to contain a credential or private object URL: {path.relative_to(root)}"
                )
        document = load_json(path)
        for node in iter_nodes(document):
            if isinstance(node, dict):
                forbidden = sorted(set(node) & FORBIDDEN_PROPERTY_NAMES)
                if forbidden:
                    raise ContractValidationError(
                        f"forbidden fixture fields in {path.relative_to(root)}: {forbidden}"
                    )


def _matches_type(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, dict)
    return True


def _try_validate(instance: Any, schema: Any, schema_file: Path, root: Path) -> bool:
    try:
        validate_instance(instance, schema, schema_file, root)
        return True
    except ContractValidationError:
        return False


def validate_instance(
    instance: Any,
    schema: Any,
    schema_file: Path,
    root: Path,
    instance_path: str = "$",
) -> None:
    if schema is True:
        return
    if schema is False:
        raise ContractValidationError(f"{instance_path}: schema rejects all values")
    if not isinstance(schema, dict):
        raise ContractValidationError(f"{instance_path}: schema must be an object or boolean")

    if "$ref" in schema:
        resolved, target_file = resolve_ref(root, schema_file, schema["$ref"])
        validate_instance(instance, resolved, target_file, root, instance_path)
        return

    for sub_schema in schema.get("allOf", []):
        validate_instance(instance, sub_schema, schema_file, root, instance_path)

    if "anyOf" in schema:
        if not any(
            _try_validate(instance, sub_schema, schema_file, root)
            for sub_schema in schema["anyOf"]
        ):
            raise ContractValidationError(f"{instance_path}: no anyOf branch matched")

    if "oneOf" in schema:
        matches = sum(
            1
            for sub_schema in schema["oneOf"]
            if _try_validate(instance, sub_schema, schema_file, root)
        )
        if matches != 1:
            raise ContractValidationError(
                f"{instance_path}: expected exactly one oneOf match, got {matches}"
            )

    if "if" in schema:
        branch = "then" if _try_validate(instance, schema["if"], schema_file, root) else "else"
        if branch in schema:
            validate_instance(instance, schema[branch], schema_file, root, instance_path)

    if "const" in schema and instance != schema["const"]:
        raise ContractValidationError(
            f"{instance_path}: expected constant {schema['const']!r}"
        )
    if "enum" in schema and instance not in schema["enum"]:
        raise ContractValidationError(f"{instance_path}: value is not in enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not isinstance(expected_types, list) or not any(
            isinstance(item, str) and _matches_type(instance, item)
            for item in expected_types
        ):
            raise ContractValidationError(
                f"{instance_path}: expected type {expected_type!r}, got {type(instance).__name__}"
            )

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise ContractValidationError(f"{instance_path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise ContractValidationError(f"{instance_path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise ContractValidationError(f"{instance_path}: string does not match pattern")
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError as error:
                raise ContractValidationError(
                    f"{instance_path}: invalid date-time"
                ) from error
        if schema.get("format") == "uri":
            parsed = urlparse(instance)
            if not parsed.scheme:
                raise ContractValidationError(f"{instance_path}: invalid URI")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise ContractValidationError(f"{instance_path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise ContractValidationError(f"{instance_path}: number is above maximum")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise ContractValidationError(f"{instance_path}: array has too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise ContractValidationError(f"{instance_path}: array has too many items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(canonical) != len(set(canonical)):
                raise ContractValidationError(f"{instance_path}: array items are not unique")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate_instance(
                    item,
                    schema["items"],
                    schema_file,
                    root,
                    f"{instance_path}[{index}]",
                )
        if "contains" in schema and not any(
            _try_validate(item, schema["contains"], schema_file, root) for item in instance
        ):
            raise ContractValidationError(f"{instance_path}: array does not satisfy contains")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                raise ContractValidationError(
                    f"{instance_path}: missing required property {name!r}"
                )
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for name, value in instance.items():
                if name in properties:
                    validate_instance(
                        value,
                        properties[name],
                        schema_file,
                        root,
                        f"{instance_path}.{name}",
                    )
                elif schema.get("additionalProperties") is False:
                    raise ContractValidationError(
                        f"{instance_path}: unexpected property {name!r}"
                    )
                elif isinstance(schema.get("additionalProperties"), dict):
                    validate_instance(
                        value,
                        schema["additionalProperties"],
                        schema_file,
                        root,
                        f"{instance_path}.{name}",
                    )


def validate_fixtures(root: Path) -> int:
    index_path = root / "fixtures" / "v1" / "index.json"
    index = load_json(index_path)
    if index.get("schema_version") != 1:
        raise ContractValidationError("fixture index schema_version must equal 1")
    fixtures = index.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise ContractValidationError("fixture index must contain fixtures")
    seen: set[str] = set()
    for entry in fixtures:
        if not isinstance(entry, dict):
            raise ContractValidationError("fixture index entries must be objects")
        filename = entry.get("file")
        reference = entry.get("schema")
        if not isinstance(filename, str) or not isinstance(reference, str):
            raise ContractValidationError("fixture file and schema must be strings")
        if filename in seen:
            raise ContractValidationError(f"duplicate fixture index entry: {filename}")
        seen.add(filename)
        fixture_path = (index_path.parent / filename).resolve()
        if not _inside(root, fixture_path) or not fixture_path.is_file():
            raise ContractValidationError(f"missing fixture: {filename}")
        schema, schema_file = resolve_ref(root, index_path, reference)
        validate_instance(load_json(fixture_path), schema, schema_file, root)
    return len(fixtures)


def build_report(root: Path, ref_count: int, fixture_count: int) -> dict[str, Any]:
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".artifacts" in path.parts or "__pycache__" in path.parts:
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        )
    openapi = load_json(root / "openapi" / "v1" / "openapi.json")
    return {
        "schema_version": 1,
        "package": "memebank/mb-interfaces",
        "package_version": tomllib.loads((root / ".zpkg.toml").read_text(encoding="utf-8"))["package"]["version"],
        "openapi_version": openapi["info"]["version"],
        "operations": sorted(
            f"{method.upper()} {path}"
            for path, methods in openapi["paths"].items()
            for method, operation in methods.items()
            if isinstance(operation, dict) and "operationId" in operation
        ),
        "resolved_ref_count": ref_count,
        "validated_fixture_count": fixture_count,
        "files": files,
    }


def validate_root(root: Path) -> dict[str, Any]:
    root = root.resolve()
    required_paths = [
        root / ".zpkg.toml",
        root / ".zpkg.lock",
        root / "openapi" / "v1" / "openapi.json",
        root / "compatibility" / "v1-baseline.json",
        root / "fixtures" / "v1" / "index.json",
    ]
    missing = [str(path.relative_to(root)) for path in required_paths if not path.is_file()]
    if missing:
        raise ContractValidationError(f"missing required contract files: {missing}")

    baseline = load_json(root / "compatibility" / "v1-baseline.json")
    if baseline.get("schema_version") != 1:
        raise ContractValidationError("compatibility baseline schema_version must equal 1")
    validate_metadata(root)
    ref_count = validate_local_refs(root)
    validate_package(root, baseline)
    validate_openapi_baseline(root, baseline)
    validate_compatibility(root, baseline)
    validate_forbidden_fields(root)
    fixture_count = validate_fixtures(root)
    return build_report(root, ref_count, fixture_count)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="mb-interfaces repository root",
    )
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = validate_root(args.root)
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ContractValidationError as error:
        print(f"contract validation failed: {error}", file=sys.stderr)
        raise SystemExit(2) from error
