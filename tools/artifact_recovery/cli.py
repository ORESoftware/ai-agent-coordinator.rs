"""Command-line interface for the durable artifact-recovery ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .common import (
    CLI_QUEUE_SCHEMA,
    DEFAULT_CLI_TASK_ID,
    LEDGER_SCHEMA,
    OBSERVATION_SCHEMA,
    RecoveryError,
    load_json,
    now_utc,
    sha256_value,
    validate_public_safety,
)
from .ledger import atomic_write_json, reconcile, summary_document, validate_ledger
from .observation import validate_observation

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    reconcile_parser = subparsers.add_parser("reconcile", help="merge one bounded observation batch")
    reconcile_parser.add_argument("--input", required=True)
    reconcile_parser.add_argument("--ledger")
    reconcile_parser.add_argument("--output", required=True)
    reconcile_parser.add_argument("--cli-queue", required=True)
    reconcile_parser.add_argument("--batch-size", type=int, default=50)
    reconcile_parser.add_argument("--target-task-id", default=DEFAULT_CLI_TASK_ID)
    reconcile_parser.add_argument("--now")

    validate_parser = subparsers.add_parser("validate", help="validate an observation or ledger")
    validate_parser.add_argument("path")

    summarize_parser = subparsers.add_parser("summarize", help="summarize a durable ledger")
    summarize_parser.add_argument("--ledger", required=True)
    summarize_parser.add_argument("--output")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "reconcile":
            observation = load_json(args.input)
            previous = load_json(args.ledger) if args.ledger and Path(args.ledger).exists() else None
            ledger, queue = reconcile(
                observation,
                previous,
                now=now_utc(args.now),
                batch_size=args.batch_size,
                target_task_id=args.target_task_id,
            )
            atomic_write_json(args.output, ledger)
            atomic_write_json(args.cli_queue, queue)
            print(json.dumps(ledger["summary"], indent=2, sort_keys=True))
            return 0
        if args.command == "validate":
            value = load_json(args.path)
            schema = value.get("schema_version")
            if schema == OBSERVATION_SCHEMA:
                normalized = validate_observation(value)
            elif schema == LEDGER_SCHEMA:
                normalized = validate_ledger(value)
            elif schema == CLI_QUEUE_SCHEMA:
                validate_public_safety(value)
                normalized = value
            else:
                raise RecoveryError("unsupported schema_version")
            print(json.dumps({"status": "valid", "digest": sha256_value(normalized)}, sort_keys=True))
            return 0
        if args.command == "summarize":
            result = summary_document(load_json(args.ledger))
            if args.output:
                atomic_write_json(args.output, result)
            else:
                print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        raise RecoveryError("unsupported command")
    except (OSError, RecoveryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2



if __name__ == "__main__":
    raise SystemExit(main())
