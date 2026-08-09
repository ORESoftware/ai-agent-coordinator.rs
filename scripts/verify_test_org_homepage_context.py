#!/usr/bin/env python3
"""Verify one test organization's public context against an immutable registry ref."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from render_test_org_homepage_context import render_bundle
from validate_org_homepage import validate_path as validate_homepage_path
from validate_test_org_homepage_canaries import (
    RegistryError,
    canonical_sha256,
    load_registry,
    resolve_canary,
    validate_registry,
)

MAX_VERIFIED_FILE_BYTES = 128 * 1024


def _read_exact_file(root: Path, relative: str) -> bytes:
    path = root / relative
    if path.is_symlink():
        raise RegistryError(f"managed path must not be a symlink: {relative}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RegistryError(f"unable to read managed path {relative}: {exc}") from exc
    if not raw or len(raw) > MAX_VERIFIED_FILE_BYTES:
        raise RegistryError(f"managed path has invalid size: {relative}")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegistryError(f"managed path must be UTF-8: {relative}") from exc
    return raw


def verify_bundle(
    registry: Mapping[str, Any],
    owner: str | int,
    registry_ref: str,
    bundle_dir: Path,
) -> dict[str, Any]:
    validate_registry(registry)
    canary = resolve_canary(registry, owner)
    expected = render_bundle(registry, owner, registry_ref)
    verified_hashes: dict[str, str] = {}

    for relative, expected_text in sorted(expected.items()):
        actual = _read_exact_file(bundle_dir, relative)
        expected_bytes = expected_text.encode("utf-8")
        if actual != expected_bytes:
            raise RegistryError(
                f"managed path differs from deterministic registry output: {relative}"
            )
        verified_hashes[relative] = hashlib.sha256(actual).hexdigest()

    profile = bundle_dir / "profile" / "README.md"
    homepage_errors = validate_homepage_path(
        profile,
        expect_org=canary["github"]["login"],
    )
    if homepage_errors:
        details = "; ".join(homepage_errors)
        raise RegistryError(f"organization homepage validation failed: {details}")

    return {
        "owner": canary["github"]["login"],
        "github_account_id": canary["github"]["account_id"],
        "linear_project_id": canary["linear"]["project_id"],
        "parent_production": canary["parent_production"]["github_login"],
        "registry_sha256": canonical_sha256(registry),
        "managed_files": verified_hashes,
        "profile_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
    }


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("config/test-org-homepage-canaries.yaml"),
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--registry-ref", required=True)
    parser.add_argument("--bundle-dir", type=Path, default=Path("."))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        registry = load_registry(args.registry)
        report = verify_bundle(
            registry,
            args.owner,
            args.registry_ref,
            args.bundle_dir,
        )
    except (OSError, RegistryError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"status": "pass", **report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
