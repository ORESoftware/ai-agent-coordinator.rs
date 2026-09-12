#!/usr/bin/env python3
"""Validate a five-workstream expansion of a public-safe execution ledger."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prompt_execution_ledger as ledger
from prompt_execution_ledger_validation import validate as validate_base

SCHEMA = "prompt-execution-expansion/v1"
ADDED_WORKSTREAMS = 5
COMBINED_WORKSTREAMS = 20
ROOT_KEYS = {
    "schema_version",
    "expansion_id",
    "generated_at",
    "base",
    "added_workstreams",
    "safety",
}
BASE_KEYS = {"ledger_id", "ledger_sha256", "workstream_count", "source_pr"}
SAFETY_KEYS = {
    "contains_raw_messages",
    "contains_credentials",
    "live_mutations_authorized",
    "merge_authorized",
    "notes",
}


@dataclass(frozen=True)
class ExpansionReport:
    valid: bool
    expansion_sha256: str | None
    base_sha256: str | None
    base_workstream_count: int
    added_workstream_count: int
    combined_workstream_count: int
    errors: tuple[str, ...]


def _pairs(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    return ledger._pairs(items)


def load_expansion(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    if len(raw) > ledger.MAX_BYTES:
        raise ledger.LedgerError(f"expansion exceeds {ledger.MAX_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ledger.LedgerError("expansion must be UTF-8") from exc
    if ledger.EMAIL.search(text):
        raise ledger.LedgerError("expansion must not contain email addresses")
    if any(pattern.search(text) for pattern in ledger.BAD_TEXT):
        raise ledger.LedgerError("expansion contains prohibited credential material")
    try:
        value = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise ledger.LedgerError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ledger.LedgerError("expansion root must be an object")
    return value


def _validate_workstream(
    raw: Any,
    path: str,
    errors: list[str],
) -> tuple[str | None, list[str], list[str]]:
    item = ledger._keys(raw, ledger.WORK, path, errors) or {}
    workstream_id = ledger._text(item.get("id"), f"{path}.id", errors, 3, 48)
    if workstream_id and not ledger.SLUG.fullmatch(workstream_id):
        errors.append(f"{path}.id has an invalid format")
    ledger._text(item.get("title"), f"{path}.title", errors, 8, 160)
    if item.get("priority") not in ledger.PRIORITIES:
        errors.append(f"{path}.priority is unsupported")
    if item.get("status") not in ledger.STATUSES:
        errors.append(f"{path}.status is unsupported")
    for index, anchor in enumerate(
        ledger._strings(
            item.get("linear_anchors"),
            f"{path}.linear_anchors",
            errors,
            1,
            8,
            5,
            32,
        )
    ):
        if not ledger.LINEAR.fullmatch(anchor):
            errors.append(
                f"{path}.linear_anchors[{index}] must be a DEN-N identifier"
            )
    github_anchors = ledger._strings(
        item.get("github_anchors"),
        f"{path}.github_anchors",
        errors,
        1,
        8,
        20,
        300,
    )
    for index, anchor in enumerate(github_anchors):
        ledger._github(anchor, f"{path}.github_anchors[{index}]", errors)
    ledger._strings(
        item.get("acceptance_checks"),
        f"{path}.acceptance_checks",
        errors,
        2,
        12,
        12,
        500,
    )
    dependencies = ledger._strings(
        item.get("depends_on"),
        f"{path}.depends_on",
        errors,
        0,
        19,
        3,
        48,
    )
    ledger._text(
        item.get("safety_boundary"),
        f"{path}.safety_boundary",
        errors,
        12,
        500,
    )
    return workstream_id, github_anchors, dependencies


def validate_expansion(
    base_value: Mapping[str, Any], expansion_value: Mapping[str, Any]
) -> ExpansionReport:
    errors: list[str] = []
    base_report = validate_base(base_value, mode="planning")
    if not base_report.valid:
        errors.extend(f"base ledger: {error}" for error in base_report.errors)

    ledger._walk(expansion_value, "$", errors)
    root = ledger._keys(expansion_value, ROOT_KEYS, "$", errors) or {}
    if root.get("schema_version") != SCHEMA:
        errors.append(f"schema_version must be {SCHEMA}")
    expansion_id = ledger._text(
        root.get("expansion_id"), "expansion_id", errors, 5, 96
    )
    if expansion_id and not ledger.SLUG.fullmatch(expansion_id):
        errors.append("expansion_id has an invalid format")
    ledger._time(root.get("generated_at"), "generated_at", errors)

    base = ledger._keys(root.get("base"), BASE_KEYS, "base", errors) or {}
    actual_base_sha = ledger.digest(base_value)
    if base.get("ledger_id") != base_value.get("ledger_id"):
        errors.append("base.ledger_id does not match the supplied base ledger")
    if base.get("ledger_sha256") != actual_base_sha:
        errors.append("base.ledger_sha256 does not match the supplied base ledger")
    if base.get("workstream_count") != len(base_value.get("workstreams", [])):
        errors.append("base.workstream_count does not match the supplied base ledger")
    source_pr = ledger._text(base.get("source_pr"), "base.source_pr", errors, 20, 300)
    if source_pr:
        ledger._github(source_pr, "base.source_pr", errors)

    added = (
        ledger._list(
            root.get("added_workstreams"),
            "added_workstreams",
            errors,
            0,
            1000,
        )
        or []
    )
    if len(added) != ADDED_WORKSTREAMS:
        errors.append(
            f"added_workstreams must contain exactly {ADDED_WORKSTREAMS} items; "
            f"got {len(added)}"
        )

    base_items = base_value.get("workstreams", [])
    base_ids = {
        item.get("id")
        for item in base_items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    base_github = {
        anchor
        for item in base_items
        if isinstance(item, dict)
        for anchor in item.get("github_anchors", [])
        if isinstance(anchor, str)
    }
    all_ids = set(base_ids)
    all_github = set(base_github)
    graph: dict[str, list[str]] = {
        item["id"]: list(item.get("depends_on", []))
        for item in base_items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    for index, raw in enumerate(added):
        path = f"added_workstreams[{index}]"
        workstream_id, github_anchors, dependencies = _validate_workstream(
            raw, path, errors
        )
        if workstream_id:
            if workstream_id in all_ids:
                errors.append(f"duplicate combined workstream id: {workstream_id}")
            all_ids.add(workstream_id)
            graph[workstream_id] = dependencies
        for anchor in github_anchors:
            if anchor in all_github:
                errors.append(f"duplicate combined GitHub anchor: {anchor}")
            all_github.add(anchor)

    for workstream_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency == workstream_id:
                errors.append(f"workstream {workstream_id} cannot depend on itself")
            elif dependency not in all_ids:
                errors.append(
                    f"workstream {workstream_id} depends on unknown workstream "
                    f"{dependency}"
                )
    for cycle in ledger._cycles(graph):
        errors.append(f"combined workstream dependency cycle: {cycle}")

    combined_count = len(base_items) + len(added)
    if combined_count != COMBINED_WORKSTREAMS:
        errors.append(
            f"combined workstream count must be {COMBINED_WORKSTREAMS}; "
            f"got {combined_count}"
        )

    safety = ledger._keys(root.get("safety"), SAFETY_KEYS, "safety", errors) or {}
    for name in (
        "contains_raw_messages",
        "contains_credentials",
        "live_mutations_authorized",
        "merge_authorized",
    ):
        if safety.get(name) is not False:
            errors.append(f"safety.{name} must be false")
    ledger._strings(safety.get("notes"), "safety.notes", errors, 1, 12, 12, 500)

    try:
        expansion_sha: str | None = ledger.digest(expansion_value)
    except (TypeError, ValueError) as exc:
        errors.append(f"expansion cannot be serialized: {exc}")
        expansion_sha = None

    return ExpansionReport(
        valid=not errors,
        expansion_sha256=expansion_sha,
        base_sha256=actual_base_sha,
        base_workstream_count=len(base_items),
        added_workstream_count=len(added),
        combined_workstream_count=combined_count,
        errors=tuple(errors),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("base_ledger", type=Path)
    result.add_argument("expansion", type=Path)
    result.add_argument("--json", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        base_value = ledger.load(args.base_ledger)
        expansion_value = load_expansion(args.expansion)
        report = validate_expansion(base_value, expansion_value)
    except (ledger.LedgerError, OSError) as exc:
        if args.json:
            print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        else:
            print(f"prompt execution expansion validation failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(report), sort_keys=True))
    else:
        print(f"valid={str(report.valid).lower()}")
        print(f"base_workstreams={report.base_workstream_count}")
        print(f"added_workstreams={report.added_workstream_count}")
        print(f"combined_workstreams={report.combined_workstream_count}")
        print(f"base_sha256={report.base_sha256}")
        print(f"expansion_sha256={report.expansion_sha256}")
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    return 0 if report.valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
