#!/usr/bin/env python3
"""Plan and validate optimistic-concurrency mutation receipts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from artifact_recovery.common import RecoveryError, canonical_json, load_json, now_utc
from artifact_recovery.mutation_receipts import (
    build_example_mutation_receipts,
    build_mutation_receipts_report,
)


def _atomic_write_json(path_value: str, value: Any) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan", help="derive safe write decisions and immutable receipts"
    )
    plan.add_argument("--input", required=True)
    plan.add_argument("--output", required=True)
    plan.add_argument("--now")

    validate = commands.add_parser(
        "validate", help="validate and re-derive a canonical receipt report"
    )
    validate.add_argument("path")
    validate.add_argument("--now")

    example = commands.add_parser(
        "example", help="emit a deterministic synthetic CI fixture"
    )
    example.add_argument("--output", required=True)
    example.add_argument("--now", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "plan":
            report = build_mutation_receipts_report(
                load_json(args.input), now=now_utc(args.now)
            )
            _atomic_write_json(args.output, report)
            print(json.dumps(report["summary"], indent=2, sort_keys=True))
            return 0
        if args.command == "validate":
            report = build_mutation_receipts_report(
                load_json(args.path), now=now_utc(args.now)
            )
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "mutation_status": report["summary"]["status"],
                        "digest": report["report_sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "example":
            report = build_example_mutation_receipts(now=now_utc(args.now))
            _atomic_write_json(args.output, report)
            print(canonical_json(report["summary"]))
            return 0
        raise RecoveryError("unsupported command")
    except (OSError, RecoveryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
