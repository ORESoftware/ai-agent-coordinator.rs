#!/usr/bin/env python3
"""Validate opportunity or research digest provenance."""
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

import digest_provenance as provenance


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", type=Path)
    parser.add_argument("--expect-kind", choices=("opportunity", "research"))
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = provenance.validate(provenance.load(args.digest))
    except (provenance.DigestError, OSError) as exc:
        if args.json:
            print(json.dumps({"valid": False, "errors": [str(exc)]}, sort_keys=True))
        else:
            print(f"digest provenance validation failed: {exc}", file=sys.stderr)
        return 2
    if args.expect_kind is not None and report.digest_kind != args.expect_kind:
        report = provenance.DigestReport(
            valid=False,
            digest_kind=report.digest_kind,
            digest_sha256=report.digest_sha256,
            item_count=report.item_count,
            company_count=report.company_count,
            fiducia_company_count=report.fiducia_company_count,
            errors=report.errors
            + (
                f"expected digest kind {args.expect_kind}; got {report.digest_kind}",
            ),
        )
    if args.json:
        print(json.dumps(asdict(report), sort_keys=True))
    else:
        print(f"valid={str(report.valid).lower()}")
        print(f"kind={report.digest_kind}")
        print(f"items={report.item_count}")
        print(f"companies={report.company_count}")
        print(f"fiducia_companies={report.fiducia_company_count}")
        print(f"digest_sha256={report.digest_sha256}")
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    return 0 if report.valid else 3


if __name__ == "__main__":
    raise SystemExit(main())
