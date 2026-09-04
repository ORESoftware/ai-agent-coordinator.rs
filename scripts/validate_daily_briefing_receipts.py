#!/usr/bin/env python3
"""Validate daily-briefing lane manifests and pure delivery transitions."""
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

import daily_briefing_receipts as contracts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    lanes = subcommands.add_parser("lanes", help="validate a lane manifest")
    lanes.add_argument("manifest", type=Path)
    lanes.add_argument("--require-ready", action="store_true")
    lanes.add_argument("--json", action="store_true")

    transition = subcommands.add_parser(
        "transition", help="apply one side-effect-free delivery-state transition"
    )
    transition.add_argument("state", type=Path)
    transition.add_argument("operation", type=Path)
    transition.add_argument("--json", action="store_true")
    return parser


def _run_lanes(args: argparse.Namespace) -> int:
    report = contracts.validate_lane_manifest(contracts.load_json(args.manifest))
    payload = asdict(report)
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"valid={str(report.valid).lower()}")
        print(f"ready_for_composition={str(report.ready_for_composition).lower()}")
        print(f"expected_lane_count={report.expected_lane_count}")
        print(f"receipt_count={report.receipt_count}")
        print(f"manifest_sha256={report.manifest_sha256}")
        print(f"envelope_sha256={report.envelope_sha256}")
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    if not report.valid:
        return 3
    if args.require_ready and not report.ready_for_composition:
        return 4
    return 0


def _run_transition(args: argparse.Namespace) -> int:
    current = contracts.load_json(args.state)
    command = contracts.load_json(args.operation)
    successor = contracts.transition_delivery(current, command)
    if args.json:
        print(json.dumps(successor, sort_keys=True))
    else:
        print(json.dumps(successor, indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "lanes":
            return _run_lanes(args)
        if args.command == "transition":
            return _run_transition(args)
        raise AssertionError(f"unhandled command {args.command}")
    except (contracts.ContractError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        else:
            print(f"daily briefing contract failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
