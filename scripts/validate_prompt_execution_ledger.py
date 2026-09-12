#!/usr/bin/env python3
"""Validate a public-safe prompt execution ledger."""
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

import prompt_execution_ledger as ledger
from prompt_execution_ledger_validation import validate

LedgerError = ledger.LedgerError
load_ledger = ledger.load
validate_ledger = validate
canonical_sha256 = ledger.digest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("ledger", type=Path)
    result.add_argument("--mode", choices=("planning", "closure"), default="closure")
    result.add_argument("--json", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = validate(ledger.load(args.ledger), args.mode)
    except (ledger.LedgerError, OSError) as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "valid": False,
                        "closure_ready": False,
                        "errors": [str(exc)],
                    },
                    sort_keys=True,
                )
            )
        else:
            print(f"prompt execution ledger validation failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(report), sort_keys=True))
    else:
        print(f"valid={str(report.valid).lower()}")
        print(f"closure_ready={str(report.closure_ready).lower()}")
        print(f"workstreams={report.workstream_count}")
        print(f"ledger_sha256={report.ledger_sha256}")
        for warning in report.warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    return 0 if report.valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
