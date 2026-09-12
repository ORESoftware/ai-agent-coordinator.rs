#!/usr/bin/env python3
"""Emit the fourth bounded, public-safe artifact-recovery observation batch."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "artifact-recovery-wave4.json.gz"
PAYLOAD_SHA256 = "7b1c0f329f86f558c948750eaa0298652e6f0fb42fc4f9620eac12ce5b81e8af"
BOUNDARY_ORIGIN = "file_00000000f330822f878003559199ac9b"
SHARED_AUTH_ORIGIN = "file_000000003d1c822fa0ef62d4c2eb4d9a"
TOKEN_RE = re.compile(r"\b(?:gh[pousr]_|lin_api_|cfat_|sk-)[A-Za-z0-9_-]{12,}\b")


def payload_bytes() -> bytes:
    raw = gzip.decompress(DATA_PATH.read_bytes())
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PAYLOAD_SHA256:
        raise ValueError(f"wave-4 payload digest mismatch: {digest}")
    return raw


def build_fixture() -> dict[str, Any]:
    value = json.loads(payload_bytes())
    validate_fixture(value)
    return value


def validate_fixture(value: dict[str, Any]) -> None:
    if value.get("schema_version") != "artifact_recovery_observation.v1":
        raise ValueError("unexpected schema version")
    batch = value.get("batch")
    if not isinstance(batch, dict) or batch.get("complete") is not True or batch.get("next_cursor") is not None:
        raise ValueError("wave-4 batch must be complete and unpaged")
    items = value.get("items")
    if not isinstance(items, list) or len(items) != 20:
        raise ValueError("wave-4 batch must contain exactly 20 items")

    durable_keys: set[str] = set()
    merged = 0
    opened = 0
    for item in items:
        target = item["target"]
        identity = f"{target['owner'].lower()}/{target['repository'].lower()}"
        if identity.startswith("dancing-dragons/"):
            raise ValueError("excluded space may not appear as a target")
        remote = item["remote"]
        if not remote["collected"] or not remote["repository"]["exists"]:
            raise ValueError(f"unverified repository: {identity}")
        claims = item["claims"]
        prs = remote["pull_requests"]
        if claims["pull_request_url"] not in {entry["url"] for entry in prs}:
            raise ValueError(f"unverified pull request claim: {identity}")
        if claims["commit_sha"] not in {entry["sha"] for entry in remote["commits"]}:
            raise ValueError(f"unverified commit claim: {identity}")
        if claims["branch"] not in {entry["name"] for entry in remote["branches"]}:
            raise ValueError(f"unverified branch claim: {identity}")
        state = prs[0]["state"]
        if state == "merged":
            merged += 1
            if item["intent"]["branch"] is not None:
                raise ValueError(f"merged item retained an intended branch: {identity}")
        elif state == "open":
            opened += 1
            if item["intent"]["branch"] != prs[0]["head"]:
                raise ValueError(f"open item does not require the exact PR head: {identity}")
        else:
            raise ValueError(f"unsupported PR state: {state}")
        key = f"{item['origin']['source']}:{item['origin']['id']}::{identity}"
        if key in durable_keys:
            raise ValueError(f"duplicate durable ledger key: {key}")
        durable_keys.add(key)

    if (merged, opened) != (10, 10):
        raise ValueError(f"unexpected merge matrix: merged={merged}, open={opened}")
    if TOKEN_RE.search(json.dumps(value, sort_keys=True)):
        raise ValueError("credential-shaped material detected")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    raw = payload_bytes()
    validate_fixture(json.loads(raw))
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    else:
        sys.stdout.buffer.write(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
