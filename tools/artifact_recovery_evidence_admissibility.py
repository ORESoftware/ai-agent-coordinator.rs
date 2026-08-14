#!/usr/bin/env python3
"""Build and validate evidence-admissibility reports and recertification queues."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from artifact_recovery.common import RecoveryError, canonical_json, load_json, now_utc
from artifact_recovery.evidence_admissibility import (
    build_evidence_admissibility_report,
    build_example_evidence_admissibility,
)
from artifact_recovery.ledger import atomic_write_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    aggregate = commands.add_parser(
        "aggregate", help="derive admissibility and recertification work"
    )
    aggregate.add_argument("--input", required=True)
    aggregate.add_argument("--output", required=True)
    aggregate.add_argument("--now")

    validate = commands.add_parser(
        "validate", help="validate a canonical admissibility report"
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
        if args.command == "aggregate":
            report = build_evidence_admissibility_report(
                load_json(args.input), now=now_utc(args.now)
            )
            atomic_write_json(args.output, report)
            print(json.dumps(report["summary"], indent=2, sort_keys=True))
            return 0
        if args.command == "validate":
            report = build_evidence_admissibility_report(
                load_json(args.path), now=now_utc(args.now)
            )
            print(
                json.dumps(
                    {
                        "status": "valid",
                        "admissibility_status": report["summary"]["status"],
                        "digest": report["report_sha256"],
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "example":
            report = build_example_evidence_admissibility(now=now_utc(args.now))
            atomic_write_json(args.output, report)
            print(canonical_json(report["summary"]))
            return 0
        raise RecoveryError("unsupported command")
    except (OSError, RecoveryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
