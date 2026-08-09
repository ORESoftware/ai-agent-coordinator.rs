#!/usr/bin/env python3
"""Validate and emit the bounded Wave 5 ChatGPT artifact-recovery ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "artifact-recovery-wave5.json"
PAYLOAD_SHA256 = "35eb9d0e0b9d5364162f7d4fe1a03c54a6fa563d37256ba95eb31771742cf9da"
TOKEN_RE = re.compile(r"\b(?:gh[pousr]_|github_pat_|lin_api_|cfat_|sk-)[A-Za-z0-9_-]{12,}\b")
ALLOWED_VALIDATION_STATES = {"green", "pending", "infrastructure_blocked"}


def payload_bytes() -> bytes:
    raw = DATA_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != PAYLOAD_SHA256:
        raise ValueError(f"wave-5 payload digest mismatch: {digest}")
    return raw


def build_fixture() -> dict[str, Any]:
    value = json.loads(payload_bytes())
    validate_fixture(value)
    return value


def validate_fixture(value: dict[str, Any]) -> None:
    if value.get("schema_version") != "artifact_recovery_wave5.v1":
        raise ValueError("unexpected schema version")
    if value.get("window") != {"start": "2026-06-29", "end": "2026-08-08"}:
        raise ValueError("unexpected recovery window")
    if value.get("excluded_targets") != ["dancing-dragons"]:
        raise ValueError("excluded-target boundary changed")

    policy = value.get("policy")
    required_false = {
        "force_push",
        "automatic_merge",
        "production_mutation",
        "credential_values_stored",
        "binary_payloads_checked_in",
        "recovery_is_acceptance",
    }
    if not isinstance(policy, dict) or any(policy.get(key) is not False for key in required_false):
        raise ValueError("unsafe or incomplete Wave 5 policy")

    artifacts = value.get("artifacts")
    evidence = value.get("github_evidence")
    if not isinstance(artifacts, list) or len(artifacts) != 7:
        raise ValueError("Wave 5 must contain exactly seven late artifacts")
    if not isinstance(evidence, list) or len(evidence) != 16:
        raise ValueError("Wave 5 must contain exactly sixteen GitHub evidence rows")

    evidence_ids: set[str] = set()
    repositories: set[str] = set()
    states: dict[str, int] = {}
    for item in evidence:
        evidence_id = item["id"]
        if evidence_id in evidence_ids:
            raise ValueError(f"duplicate evidence id: {evidence_id}")
        evidence_ids.add(evidence_id)
        repository = item["repository"]
        repositories.add(repository)
        if repository.lower().startswith("dancing-dragons/"):
            raise ValueError("excluded target appears in GitHub evidence")
        if item["url"] != f"https://github.com/{repository}/pull/{item['pull_request']}":
            raise ValueError(f"non-canonical PR URL: {evidence_id}")
        state = item["state"]
        if state not in {"open", "closed", "merged"}:
            raise ValueError(f"unsupported PR state: {state}")
        states[state] = states.get(state, 0) + 1
        validation = item.get("validation")
        if isinstance(validation, dict):
            status = validation.get("status")
            if status not in ALLOWED_VALIDATION_STATES:
                raise ValueError(f"unsupported validation status: {evidence_id}={status}")

    if states != {"open": 14, "merged": 1, "closed": 1}:
        raise ValueError(f"unexpected PR state matrix: {states}")

    artifact_names: set[str] = set()
    for artifact in artifacts:
        name = artifact["name"]
        if name in artifact_names:
            raise ValueError(f"duplicate artifact: {name}")
        artifact_names.add(name)
        if not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
            raise ValueError(f"invalid SHA-256: {name}")
        if not isinstance(artifact["bytes"], int) or artifact["bytes"] <= 0:
            raise ValueError(f"invalid byte count: {name}")
        for evidence_id in artifact["evidence_ids"]:
            if evidence_id not in evidence_ids:
                raise ValueError(f"unknown evidence id {evidence_id} for {name}")

    by_id = {item["id"]: item for item in evidence}
    if by_id["canonical-pr19"]["state"] != "closed":
        raise ValueError("unsafe Canonical transport PR must remain closed")
    if by_id["canonical-pr20"]["validation"]["status"] != "green":
        raise ValueError("Canonical source-only successor must be green")
    if by_id["memebank-rest-test-pr2"]["certifies"] != by_id["memebank-rest-pr9"]["head"]:
        raise ValueError("REST test snapshot does not pin the product head")
    if by_id["memebank-ocr-test-pr2"]["certifies"] != by_id["memebank-ocr-pr26"]["head"]:
        raise ValueError("OCR test snapshot does not pin the product head")
    if {
        by_id["cm-pay-pr1"]["validation"]["status"],
        by_id["cp-go-pr1"]["validation"]["status"],
    } != {"infrastructure_blocked"}:
        raise ValueError("Go billing blockers are not recorded")
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
