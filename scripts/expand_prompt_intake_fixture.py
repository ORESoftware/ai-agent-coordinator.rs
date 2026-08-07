#!/usr/bin/env python3
"""Expand the compact, public-safe 60-item prompt-intake acceptance fixture."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import prompt_intake_corpus as corpus

STORAGE_FORMAT = "compact_v1"
ITEM_COLUMNS = [
    "canonical_issue",
    "project_key",
    "priority",
    "status",
    "classification",
    "duplicate_of",
]
TOP_LEVEL_KEYS = {
    "schema_version",
    "storage_format",
    "corpus_id",
    "snapshot_at",
    "cutoff_at",
    "retention_policy",
    "expected",
    "telemetry_samples_ms",
    "daily_briefing",
    "relation_cases",
    "item_columns",
    "rows",
}


def _strict_load(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    if len(payload) > corpus.MAX_CORPUS_BYTES:
        raise corpus.CorpusError(
            f"compact fixture exceeds {corpus.MAX_CORPUS_BYTES} bytes"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise corpus.CorpusError("compact fixture must be UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=corpus._reject_duplicate_object_pairs,
        )
    except json.JSONDecodeError as exc:
        raise corpus.CorpusError(f"invalid compact JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise corpus.CorpusError("compact fixture root must be an object")
    corpus.validate_public_safety(value)
    return value


def _expect_exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise corpus.CorpusError(
            f"{field} keys mismatch: missing={missing}, extra={extra}"
        )


def load_compact(path: Path | str) -> dict[str, Any]:
    raw = _strict_load(Path(path))
    _expect_exact_keys(raw, TOP_LEVEL_KEYS, "compact fixture")
    if raw["schema_version"] != corpus.SCHEMA_VERSION:
        raise corpus.CorpusError(
            f"schema_version must be {corpus.SCHEMA_VERSION}"
        )
    if raw["storage_format"] != STORAGE_FORMAT:
        raise corpus.CorpusError("unsupported compact fixture format")
    if raw["item_columns"] != ITEM_COLUMNS:
        raise corpus.CorpusError("item_columns must match the canonical order")

    retention = raw["retention_policy"]
    if not isinstance(retention, dict):
        raise corpus.CorpusError("retention_policy must be an object")
    _expect_exact_keys(retention, corpus.RETENTION_KEYS, "retention_policy")
    source_days = corpus._expect_int(
        retention["source_metadata_days"],
        "source_metadata_days",
        1,
        365,
    )
    receipt_days = corpus._expect_int(
        retention["receipt_days"],
        "receipt_days",
        source_days,
        3_650,
    )
    cutoff = corpus.parse_timestamp(raw["cutoff_at"], "cutoff_at")
    snapshot = corpus.parse_timestamp(raw["snapshot_at"], "snapshot_at")
    rows = raw["rows"]
    if not isinstance(rows, list) or not rows:
        raise corpus.CorpusError("rows must be a non-empty array")

    items: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, list) or len(row) != len(ITEM_COLUMNS):
            raise corpus.CorpusError(f"row {index} has an invalid width")
        record = dict(zip(ITEM_COLUMNS, row, strict=True))
        fixture_id = f"chat-{index:03d}"
        status = record["status"]
        if status in corpus.LIVE_STATUSES:
            decision = "amend_existing"
            exclusion_reason = None
        elif status == "done":
            decision = "no_op_reference"
            exclusion_reason = "already_completed"
        elif status == "duplicate":
            decision = "no_op_reference"
            exclusion_reason = "duplicate_reference"
        else:
            raise corpus.CorpusError(
                f"row {index} has unsupported status {status}"
            )
        observed = cutoff - timedelta(minutes=len(rows) - index)
        source_fingerprint = corpus.sha256_text(
            f"synthetic-redacted-source:v1:{fixture_id}"
        )
        result_fingerprint = corpus.sha256_text(
            "canonical-disposition:v1:"
            f"{record['canonical_issue']}:"
            f"{record['project_key']}:"
            f"{record['priority']}:"
            f"{status}:"
            f"{decision}:"
            f"{exclusion_reason}:"
            f"{record['duplicate_of']}"
        )
        operation_id = corpus.sha256_text(
            "acceptance-oracle:v1:"
            f"{fixture_id}:"
            f"{record['canonical_issue']}:"
            f"{source_fingerprint}"
        )
        items.append(
            {
                "fixture_id": fixture_id,
                "source": {
                    "kind": "synthetic_redacted",
                    "observed_at": observed.isoformat().replace("+00:00", "Z"),
                    "source_fingerprint": source_fingerprint,
                    "retained_until": (observed + timedelta(days=source_days))
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
                "canonical_issue": record["canonical_issue"],
                "project_key": record["project_key"],
                "priority": record["priority"],
                "status": status,
                "classification": record["classification"],
                "decision": decision,
                "exclusion_reason": exclusion_reason,
                "duplicate_of": record["duplicate_of"],
                "receipt": {
                    "kind": "acceptance_oracle",
                    "operation_id": operation_id,
                    "recorded_at": snapshot.isoformat().replace("+00:00", "Z"),
                    "result_fingerprint": result_fingerprint,
                    "retained_until": (snapshot + timedelta(days=receipt_days))
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            }
        )

    expanded = {
        "schema_version": raw["schema_version"],
        "corpus_id": raw["corpus_id"],
        "snapshot_at": raw["snapshot_at"],
        "cutoff_at": raw["cutoff_at"],
        "source_kind": "synthetic_redacted_acceptance",
        "retention_policy": copy.deepcopy(raw["retention_policy"]),
        "expected": copy.deepcopy(raw["expected"]),
        "telemetry_samples_ms": copy.deepcopy(raw["telemetry_samples_ms"]),
        "daily_briefing": copy.deepcopy(raw["daily_briefing"]),
        "relation_cases": copy.deepcopy(raw["relation_cases"]),
        "items": items,
    }
    corpus.validate_corpus(expanded)
    return expanded


def write_expanded(value: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        expanded = load_compact(args.fixture)
        if args.output is None:
            json.dump(expanded, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        else:
            write_expanded(expanded, args.output)
    except (corpus.CorpusError, OSError) as exc:
        print(f"compact fixture expansion failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
