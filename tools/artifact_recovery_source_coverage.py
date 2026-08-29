#!/usr/bin/env python3
"""Build and validate fail-closed artifact-recovery source coverage."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from artifact_recovery.common import RecoveryError, canonical_json, load_json, now_utc
from artifact_recovery.ledger import atomic_write_json
from artifact_recovery.source_coverage import (
    build_example_source_coverage,
    build_source_coverage_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    aggregate = subparsers.add_parser("aggregate", help="normalize receipts and derive run status")
    aggregate.add_argument("--input", required=True)
    aggregate.add_argument("--output", required=True)
    aggregate.add_argument("--now")

    validate = subparsers.add_parser("validate", help="validate a canonical coverage report")
    validate.add_argument("path")
    validate.add_argument("--now")

    example = subparsers.add_parser("example", help="emit a deterministic synthetic CI fixture")
    example.add_argument("--output", required=True)
    example.add_argument("--now", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "aggregate":
            report = build_source_coverage_report(load_json(args.input), now=now_utc(args.now))
            atomic_write_json(args.output, report)
            print(json.dumps(report["summary"], indent=2, sort_keys=True))
            return 0
        if args.command == "validate":
            report = build_source_coverage_report(load_json(args.path), now=now_utc(args.now))
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "coverage_status": report["summary"]["status"],
                        "digest": report["report_sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "example":
            report = build_example_source_coverage(now=now_utc(args.now))
            atomic_write_json(args.output, report)
            print(canonical_json(report["summary"]))
            return 0
        raise RecoveryError("unsupported command")
    except (OSError, RecoveryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
