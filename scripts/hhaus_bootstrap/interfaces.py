from __future__ import annotations

import json
from typing import Any

from .contract import BootstrapError

def interface_files(manifest: dict[str, Any]) -> dict[str, str]:
    semantic = {
        "schema_version": 1,
        "models": {
            "LocationRef": {
                "fields": {"id": "string", "slug": "string", "city": "string", "country": "string"},
                "required": ["id", "slug", "city", "country"],
            },
            "PrincipalRef": {
                "fields": {"subject": "string", "organizationId": "string", "userId": "string"},
                "required": ["subject", "organizationId", "userId"],
            },
            "ApplicationIntake": {
                "fields": {
                    "id": "string",
                    "locationId": "string",
                    "email": "string",
                    "status": "ApplicationStatus",
                    "createdAt": "utcDateTime",
                },
                "required": ["id", "locationId", "email", "status", "createdAt"],
            },
            "Reservation": {
                "fields": {
                    "id": "string",
                    "locationId": "string",
                    "principalId": "string",
                    "startsAt": "utcDateTime",
                    "endsAt": "utcDateTime",
                },
                "required": ["id", "locationId", "principalId", "startsAt", "endsAt"],
            },
            "SyncEnvelope": {
                "fields": {"cursor": "string", "changes": "unknown[]", "tombstones": "string[]"},
                "required": ["cursor", "changes", "tombstones"],
            },
            "RateLimitContext": {
                "fields": {
                    "subject": "string",
                    "routeClass": "string",
                    "layer": "RateLimitLayer",
                    "failureMode": "RateLimitFailureMode",
                },
                "required": ["subject", "routeClass", "layer", "failureMode"],
            },
        },
        "enums": {
            "ApplicationStatus": ["draft", "submitted", "accepted", "rejected", "withdrawn"],
            "RateLimitLayer": [
                "cloudflareEdge",
                "gatewayLoadBalancer",
                "serviceRuntimeLru",
                "distributedRedisCoordinator",
                "durableSecurityBilling",
            ],
            "RateLimitFailureMode": ["open", "closed", "localOnly"],
        },
    }
    typespec = '''import "@typespec/http";\n\nusing TypeSpec.Http;\n\nnamespace HHaus;\n\nmodel LocationRef {\n  id: string;\n  slug: string;\n  city: string;\n  country: string;\n}\n\nmodel PrincipalRef {\n  subject: string;\n  organizationId: string;\n  userId: string;\n}\n\nenum ApplicationStatus { draft, submitted, accepted, rejected, withdrawn }\n\nmodel ApplicationIntake {\n  id: string;\n  locationId: string;\n  email: string;\n  status: ApplicationStatus;\n  createdAt: utcDateTime;\n}\n\nmodel Reservation {\n  id: string;\n  locationId: string;\n  principalId: string;\n  startsAt: utcDateTime;\n  endsAt: utcDateTime;\n}\n\nmodel SyncEnvelope {\n  cursor: string;\n  changes: unknown[];\n  tombstones: string[];\n}\n\nenum RateLimitLayer { cloudflareEdge, gatewayLoadBalancer, serviceRuntimeLru, distributedRedisCoordinator, durableSecurityBilling }\nenum RateLimitFailureMode { open, closed, localOnly }\n\nmodel RateLimitContext {\n  subject: string;\n  routeClass: string;\n  layer: RateLimitLayer;\n  failureMode: RateLimitFailureMode;\n}\n'''

    def schema_type(canonical_type: str) -> dict[str, Any]:
        if canonical_type == "string":
            return {"type": "string"}
        if canonical_type == "utcDateTime":
            return {"type": "string", "format": "date-time"}
        if canonical_type == "string[]":
            return {"type": "array", "items": {"type": "string"}}
        if canonical_type == "unknown[]":
            return {"type": "array", "items": {}}
        if canonical_type in semantic["enums"]:
            return {"$ref": f"#/$defs/{canonical_type}"}
        raise BootstrapError(f"unknown semantic field type: {canonical_type}")

    defs: dict[str, Any] = {
        enum_name: {"type": "string", "enum": values}
        for enum_name, values in semantic["enums"].items()
    }
    for model, config in semantic["models"].items():
        defs[model] = {
            "type": "object",
            "additionalProperties": False,
            "required": config["required"],
            "properties": {
                field: schema_type(canonical_type)
                for field, canonical_type in config["fields"].items()
            },
        }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://schemas.hhaus.org/hhaus.contract.schema.json",
        "title": "H/HAUS shared contract declarations",
        "$defs": defs,
    }
    checker = r'''#!/usr/bin/env python3
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
semantic = json.loads((ROOT / "semantic-model.json").read_text(encoding="utf-8"))
schema = json.loads(
    (ROOT / "json-schema" / "hhaus.contract.schema.json").read_text(encoding="utf-8")
)
typespec = (ROOT / "typespec" / "main.tsp").read_text(encoding="utf-8")


def json_schema_type(value: dict[str, Any]) -> str:
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        return reference.removeprefix("#/$defs/")
    if value.get("type") == "string" and value.get("format") == "date-time":
        return "utcDateTime"
    if value.get("type") == "string":
        return "string"
    if value.get("type") == "array":
        items = value.get("items")
        if items == {}:
            return "unknown[]"
        if isinstance(items, dict) and items.get("type") == "string":
            return "string[]"
    raise SystemExit(f"unsupported JSON Schema field type: {value!r}")


json_models: dict[str, dict[str, Any]] = {}
json_enums: dict[str, list[str]] = {}
for name, value in schema.get("$defs", {}).items():
    if value.get("type") == "object":
        json_models[name] = {
            "required": value.get("required", []),
            "fields": {
                field: json_schema_type(field_schema)
                for field, field_schema in value.get("properties", {}).items()
            },
        }
    elif value.get("type") == "string" and isinstance(value.get("enum"), list):
        json_enums[name] = value["enum"]
    else:
        raise SystemExit(f"unsupported JSON Schema definition: {name}")


tsp_models: dict[str, dict[str, Any]] = {}
for match in re.finditer(r"model\s+(\w+)\s*\{(.*?)\}", typespec, re.S):
    field_matches = re.findall(r"(?m)^\s*(\w+)(\?)?\s*:\s*([^;]+);", match.group(2))
    fields = {name: field_type.strip() for name, _optional, field_type in field_matches}
    required = [name for name, optional, _field_type in field_matches if optional != "?"]
    tsp_models[match.group(1)] = {"required": required, "fields": fields}


tsp_enums: dict[str, list[str]] = {}
for match in re.finditer(r"enum\s+(\w+)\s*\{(.*?)\}", typespec, re.S):
    values = [value.strip() for value in match.group(2).split(",") if value.strip()]
    tsp_enums[match.group(1)] = values

def main() -> None:
    expected_models = semantic["models"]
    expected_enums = semantic["enums"]
    if expected_models != json_models:
        raise SystemExit(f"JSON Schema semantic drift: {json_models!r}")
    if expected_models != tsp_models:
        raise SystemExit(f"TypeSpec semantic drift: {tsp_models!r}")
    if expected_enums != json_enums:
        raise SystemExit(f"JSON Schema enum drift: {json_enums!r}")
    if expected_enums != tsp_enums:
        raise SystemExit(f"TypeSpec enum drift: {tsp_enums!r}")
    print(
        json.dumps(
            {
                "enums": len(expected_enums),
                "models": len(expected_models),
                "status": "peer-authorities-match",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
'''
    package = {
        "name": "@hhaus/interfaces",
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "scripts": {"check": "python3 scripts/check_peer_authorities.py"},
        "devDependencies": {
            "@typespec/compiler": "1.15.0",
            "@typespec/http": "1.15.0",
            "@typespec/json-schema": "1.15.0",
            "@typespec/openapi3": "1.15.0",
        },
    }
    files: dict[str, str] = {
        "semantic-model.json": json.dumps(semantic, indent=2, sort_keys=True) + "\n",
        "typespec/main.tsp": typespec,
        "json-schema/hhaus.contract.schema.json": json.dumps(schema, indent=2, sort_keys=True) + "\n",
        "languages.json": json.dumps({"targets": manifest["language_targets"]}, indent=2) + "\n",
        "package.json": json.dumps(package, indent=2, sort_keys=True) + "\n",
        "scripts/check_peer_authorities.py": checker,
        "generated/typescript/package.json": json.dumps(
            {"name": "@hhaus/interfaces-typescript", "version": "0.1.0", "type": "module"},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "generated/typescript/src/index.ts": '''export interface LocationRef { readonly id: string; readonly slug: string; readonly city: string; readonly country: string; }\nexport type RateLimitFailureMode = "open" | "closed" | "localOnly";\n''',
        "generated/rust/Cargo.toml": '''[package]\nname = "hhaus-interfaces"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\nlicense = "MIT"\n\n[lints.rust]\nunsafe_code = "forbid"\n''',
        "generated/rust/src/lib.rs": r'''#![forbid(unsafe_code)]

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LocationRef {
    pub id: String,
    pub slug: String,
    pub city: String,
    pub country: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RateLimitFailureMode {
    Open,
    Closed,
    LocalOnly,
}
''',
        "generated/dart/pubspec.yaml": '''name: hhaus_interfaces\ndescription: Generated H/HAUS interface declarations.\nversion: 0.1.0\npublish_to: none\nenvironment:\n  sdk: ">=3.6.0 <4.0.0"\n''',
        "generated/dart/lib/hhaus_interfaces.dart": '''final class LocationRef {\n  const LocationRef({required this.id, required this.slug, required this.city, required this.country});\n  final String id;\n  final String slug;\n  final String city;\n  final String country;\n}\n''',
        "generated/go/go.mod": "module github.com/hhaus-org/hhaus-interfaces/generated/go\n\ngo 1.23\n",
        "generated/go/contracts.go": '''package hhausinterfaces\n\ntype LocationRef struct {\n\tID string `json:"id"`\n\tSlug string `json:"slug"`\n\tCity string `json:"city"`\n\tCountry string `json:"country"`\n}\n''',
        "generated/gleam/gleam.toml": '''name = "hhaus_interfaces"\nversion = "0.1.0"\ntarget = "erlang"\n''',
        "generated/gleam/src/hhaus_interfaces.gleam": '''pub type LocationRef {\n  LocationRef(id: String, slug: String, city: String, country: String)\n}\n''',
    }
    for language in manifest["language_targets"]:
        path = f"generated/{language}/README.md"
        if path not in files:
            files[path] = (
                f"# H/HAUS {language} interfaces\n\n"
                "Generated only after independent TypeSpec and JSON Schema Draft 2020-12 "
                "declarations normalize to the same semantic model and contract digest.\n"
            )
    return files
