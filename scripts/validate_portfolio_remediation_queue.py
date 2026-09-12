#!/usr/bin/env python3
"""Validate a read-only portfolio remediation queue."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import portfolio_remediation_queue as queue


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("queue", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = queue.validate(queue.load(args.queue))
    except (queue.QueueError, OSError) as exc:
        if args.json:
            print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        else:
            print(f"portfolio remediation queue failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(report), sort_keys=True))
    else:
        print(f"valid={str(report.valid).lower()}")
        print(f"repositories={report.repository_count}")
        print(f"findings={report.finding_count}")
        print(f"actionable={report.actionable_count}")
        print(f"blocked={report.blocked_count}")
        print(f"no_op={report.noop_count}")
        print(f"queue_sha256={report.queue_sha256}")
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    return 0 if report.valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
