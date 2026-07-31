#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from behavior import advance_cursor, build_plan, redact_headers, redact_url, should_retry
from contract import validate_operations, validate_policy
from io_helpers import arr, digest, load_json, obj, text, write_json

OUTPUTS = {
    "rust": Path("clients/rust/src/generated.rs"),
    "dart": Path("clients/dart/lib/src/generated.dart"),
    "typescript": Path("clients/typescript/src/generated.ts"),
}


def root() -> Path:
    return Path(__file__).resolve().parents[1]


def generated(base: Path, contract_digest: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for language, relative in OUTPUTS.items():
        content = (base / relative).read_text(encoding="utf-8")
        if f"contract-digest: {contract_digest}" not in content or "DO NOT EDIT" not in content:
            raise ValueError(f"generated output is stale: {relative}")
        result[language] = digest(content)
    manifest = obj(load_json(base / "generated/manifest.json"), "manifest")
    if manifest.get("contract_digest") != contract_digest or manifest.get("outputs") != result:
        raise ValueError("generated manifest is stale")
    return result


def scenarios(document: Any, operations: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, int]:
    root_doc = obj(document, "fixtures")
    by_id = {item["operation_id"]: item for item in operations}
    counts = {"request_scenarios": 0, "retry_scenarios": 0, "redaction_scenarios": 0, "sync_scenarios": 0}
    for index, raw in enumerate(arr(root_doc.get("request_scenarios"), "request_scenarios")):
        item = obj(raw, f"request[{index}]")
        op_id = text(item.get("operation_id"), "operation_id")
        if op_id not in by_id:
            raise ValueError(f"unknown operation {op_id}")
        plan = build_plan(by_id[op_id], obj(item["path_parameters"], "path_parameters"), obj(item["query"], "query"), obj(item["headers"], "headers"), policy)
        if plan["method"] != item.get("expected_method") or plan["path"] != item.get("expected_path") or plan["retryable"] is not item.get("expected_retryable"):
            raise ValueError(f"request scenario {index} mismatch")
        counts["request_scenarios"] += 1
    for index, raw in enumerate(arr(root_doc.get("retry_scenarios"), "retry_scenarios")):
        item = obj(raw, f"retry[{index}]")
        actual = should_retry(item["retry_class"], item["status"], item["attempt"], item["body_started"], policy)
        if actual is not item.get("expected"):
            raise ValueError(f"retry scenario {index} mismatch")
        counts["retry_scenarios"] += 1
    for index, raw in enumerate(arr(root_doc.get("redaction_scenarios"), "redaction_scenarios")):
        item = obj(raw, f"redaction[{index}]")
        actual = redact_headers(obj(item["headers"], "headers"), policy) if "headers" in item else redact_url(text(item["url"], "url"), policy)
        if actual != item.get("expected"):
            raise ValueError(f"redaction scenario {index} mismatch")
        counts["redaction_scenarios"] += 1
    for index, raw in enumerate(arr(root_doc.get("sync_scenarios"), "sync_scenarios")):
        item = obj(raw, f"sync[{index}]")
        if advance_cursor(obj(item["current"], "current"), obj(item["through_cursor"], "through_cursor")) != item.get("expected"):
            raise ValueError(f"sync scenario {index} mismatch")
        counts["sync_scenarios"] += 1
    return counts


def validate(base: Path) -> dict[str, Any]:
    operations_doc = load_json(base / "contract/operations.json")
    policy_doc = load_json(base / "contract/client-policy.json")
    operations = validate_operations(operations_doc)
    policy = validate_policy(policy_doc)
    contract_digest = digest({"operations": operations_doc, "policy": policy_doc})
    outputs = generated(base, contract_digest)
    counts = scenarios(load_json(base / "fixtures/golden-scenarios.json"), operations, policy)
    deferred = operations_doc["deferred_families"]
    return {
        "schema_version": 1,
        "package": "memebank/mb-clients",
        "valid": True,
        "interface_package": operations_doc["interface_package"],
        "interface_version": operations_doc["interface_version"],
        "contract_digest": contract_digest,
        "operation_count": len(operations),
        "operation_ids": [item["operation_id"] for item in operations],
        "language_outputs": outputs,
        "language_count": len(outputs),
        "native_language_targets": ["rust", "dart", "typescript"],
        "scenario_counts": counts,
        "deferred_family_count": len(deferred),
        "deferred_families": deferred,
        "canonical_repository_available": False,
        "live_api_conformance_complete": False,
        "shared_auth_binding_complete": False,
        "publication_ready": False,
        "blockers": [
            "github.com/memebank/mb-clients is not authorized or created",
            "shared-auth concrete binding is pending DEN-1008",
            "deferred API families are absent from mb-interfaces",
            "full TypeScript type-check and registry publication evidence are absent",
            "live API and downstream-consumer conformance evidence are absent",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=root())
    parser.add_argument("--report", type=Path)
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv or sys.argv[1:])
    report = validate(args.root)
    if args.report:
        path = args.report if args.report.is_absolute() else args.root / args.report
        write_json(path, report)
    print(json.dumps(report, indent=None if args.compact else 2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
