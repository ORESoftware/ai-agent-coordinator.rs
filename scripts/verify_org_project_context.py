#!/usr/bin/env python3
"""Verify a generated organization context bundle against its central registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

from render_org_project_context import render_bundle
from validate_org_project_registry import (
    RegistryError,
    canonical_sha256,
    load_registry,
    validate_registry,
)


def verify_bundle(
    bundle_dir: Path,
    expected: Mapping[str, str],
) -> dict[str, int]:
    resolved_root = bundle_dir.resolve(strict=True)
    verified_bytes = 0
    for relative, expected_content in sorted(expected.items()):
        candidate = bundle_dir / relative
        if candidate.is_symlink():
            raise RegistryError(f"managed bundle path is a symlink: {relative}")
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise RegistryError(f"managed bundle file is missing: {relative}") from exc
        if resolved_root not in resolved_candidate.parents:
            raise RegistryError(f"managed bundle path escapes its root: {relative}")
        if not resolved_candidate.is_file():
            raise RegistryError(f"managed bundle path is not a file: {relative}")
        try:
            actual_content = resolved_candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RegistryError(f"managed bundle file is not UTF-8: {relative}") from exc
        if actual_content != expected_content:
            raise RegistryError(f"managed bundle file has drifted: {relative}")
        verified_bytes += len(actual_content.encode("utf-8"))
    return {"files": len(expected), "bytes": verified_bytes}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--registry-ref", required=True)
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry)
        validate_registry(registry)
        expected = render_bundle(registry, args.owner, args.registry_ref)
        evidence = verify_bundle(args.bundle_dir, expected)
    except (OSError, RegistryError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "pass",
                "owner": args.owner,
                "registry_ref": args.registry_ref,
                "registry_sha256": canonical_sha256(registry),
                **evidence,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
