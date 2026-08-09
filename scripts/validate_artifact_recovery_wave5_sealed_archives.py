#!/usr/bin/env python3
"""Validate the Wave 5 sealed-archive recovery supplement."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "artifact-recovery-wave5-sealed-archives.json"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TOKEN_RE = re.compile(r"\b(?:gh[pousr]_|lin_api_|cfat_|sk-)[A-Za-z0-9_-]{12,}\b")
EXPECTED = {
    "hypesiege-streempilot-bootstrap": (2025578, "0b38d197161b91db0a2a3d08def94d1e965fa8d48bb6aa998ba9614b667728e0", 32),
    "four-org-rust-bootstrap": (2195312, "a715a4919e3a7d9e7eb5b8b7878e2d9485e4228504e57fe7a6d703a166cb1d37", 32),
    "mcp-rust-libs-scaffold": (120181, "a88df67f4c0ffc6cf0ee74f7cd2eec46004e2d8021bfbef6051cd1af7143ca3b", 1),
}


def load(path: Path = DATA) -> dict[str, Any]:
    value = json.loads(path.read_text())
    validate(value)
    return value


def validate(value: dict[str, Any]) -> None:
    if value.get("schema_version") != "artifact_recovery_sealed_archives.v1":
        raise ValueError("unexpected schema version")
    aggregate = value.get("aggregate_bundle")
    if not isinstance(aggregate, dict):
        raise ValueError("missing aggregate bundle")
    if aggregate.get("bytes") != 3445824:
        raise ValueError("aggregate bundle byte length changed")
    if aggregate.get("sha256") != "f3495474b963096451cd32b26f560f78be9d7e556ad2c598f13dc18aeaae19e5":
        raise ValueError("aggregate bundle checksum changed")
    if aggregate.get("disposition") != "conversation_attachment_checksum_anchored_not_github_binary":
        raise ValueError("aggregate bundle publication claim changed")
    contents = aggregate.get("contents")
    if not isinstance(contents, list) or len(contents) != 9 or len(contents) != len(set(contents)):
        raise ValueError("aggregate bundle contents changed")

    policy = value.get("policy")
    if not isinstance(policy, dict):
        raise ValueError("missing policy")
    if policy.get("force_push") is not False or policy.get("history_rewrite") is not False:
        raise ValueError("history rewrite must remain disabled")
    if policy.get("credential_persistence") is not False:
        raise ValueError("credential persistence must remain disabled")
    if policy.get("binary_payload_committed") is not False:
        raise ValueError("opaque archives must not be committed to the coordinator repository")
    if policy.get("excluded_targets") != ["dancing-dragons"]:
        raise ValueError("excluded target contract changed")

    archives = value.get("archives")
    if not isinstance(archives, list) or len(archives) != len(EXPECTED):
        raise ValueError("unexpected archive count")
    seen: set[str] = set()
    for archive in archives:
        archive_id = archive.get("id")
        if archive_id in seen or archive_id not in EXPECTED:
            raise ValueError(f"unexpected or duplicate archive: {archive_id}")
        seen.add(archive_id)
        expected_bytes, expected_sha, expected_histories = EXPECTED[archive_id]
        if archive.get("bytes") != expected_bytes:
            raise ValueError(f"byte length changed: {archive_id}")
        if archive.get("sha256") != expected_sha or not SHA_RE.fullmatch(expected_sha):
            raise ValueError(f"checksum changed: {archive_id}")
        if archive.get("expected_repository_histories") != expected_histories:
            raise ValueError(f"repository-history count changed: {archive_id}")
        if "no_destructive_replay" not in archive.get("disposition", "") and archive_id == "hypesiege-streempilot-bootstrap":
            raise ValueError("historical fleet must remain non-destructive")
        heads = archive.get("canonical_heads")
        if not isinstance(heads, list) or not heads:
            raise ValueError(f"canonical evidence missing: {archive_id}")
        for head in heads:
            if "/" not in head.get("repository", "") or not COMMIT_RE.fullmatch(head.get("commit", "")):
                raise ValueError(f"invalid canonical head: {archive_id}")

    scan = value.get("scan")
    if not isinstance(scan, dict) or scan.get("result") != "pass":
        raise ValueError("scan must pass")
    for key in ("credential_shaped_material", "git_remotes_with_embedded_credentials", "private_key_material"):
        if scan.get(key) is not False:
            raise ValueError(f"unsafe scan result: {key}")
    serialized = json.dumps(value, sort_keys=True)
    if TOKEN_RE.search(serialized):
        raise ValueError("credential-shaped value detected")
    if "dancing-dragons/" in serialized.lower():
        raise ValueError("excluded organization appears as a repository target")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA)
    parser.add_argument("--print", action="store_true", dest="print_value")
    args = parser.parse_args(argv)
    value = load(args.input)
    if args.print_value:
        json.dump(value, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
