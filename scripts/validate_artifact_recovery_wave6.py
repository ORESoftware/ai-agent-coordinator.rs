#!/usr/bin/env python3
"""Validate the Wave 6 ChatGPT/Library artifact recovery ledger."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path
from typing import Any

SCHEMA = "ore.chatgpt-artifact-recovery.wave6.v1"
WINDOW = {"start": "2026-06-29", "end": "2026-08-08", "days": 40}
MISSING_REPOSITORIES = {
    "apostille-me/apme-e2e",
    "embedded-alerts/eal-e2e",
    "evento-globolo/evgl-e2e",
    "hacker-house-medellin/hhm-e2e",
}
REQUIRED_LINEAR = {"DEN-2797", "DEN-319", "DEN-2957", "DEN-977", "DEN-2253"}
CREDENTIAL_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"lin_api_[A-Za-z0-9]{20,}"),
    re.compile(r"cfat_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
UNSAFE_SAFE_SCRIPT_PATTERNS = (
    re.compile(r"^\s*git\s+rebase(?:\s|$)", re.MULTILINE),
    re.compile(r"^\s*git\s+push[^\n#]*--force(?:-with-lease)?(?:\s|$)", re.MULTILINE),
    re.compile(r"^\s*gh\s+api\s+-X\s+PATCH[^\n#]*archived=true", re.MULTILINE),
)


class ValidationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"invalid ledger JSON: {path}") from error
    require(isinstance(value, dict), "ledger root must be an object")
    return value


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_relative_safe(path: str) -> bool:
    candidate = Path(path)
    return bool(path) and not candidate.is_absolute() and ".." not in candidate.parts


def validate_credential_free(root: Path, paths: list[Path]) -> None:
    for path in paths:
        data = path.read_bytes()
        if b"\x00" in data[:8192]:
            continue
        text = data.decode("utf-8", errors="replace")
        for pattern in CREDENTIAL_PATTERNS:
            require(
                pattern.search(text) is None,
                f"credential-shaped material found in {path.relative_to(root)}",
            )



def read_parts(root: Path, values: Any, label: str) -> tuple[bytes, list[Path]]:
    require(isinstance(values, list) and values, f"{label} must be a non-empty array")
    require(len(values) == len(set(values)), f"{label} contains duplicate paths")
    chunks: list[bytes] = []
    paths: list[Path] = []
    for index, value in enumerate(values):
        require(isinstance(value, str) and is_relative_safe(value), f"invalid {label}[{index}]")
        path = (root / value).resolve()
        require(root in path.parents and path.is_file(), f"missing {label}[{index}]: {value}")
        require(path.stat().st_size <= 7600, f"oversized {label}[{index}]: {value}")
        chunks.append(path.read_bytes())
        paths.append(path)
    return b"".join(chunks), paths

def validate(root: Path, ledger_path: Path) -> dict[str, Any]:
    root = root.resolve()
    ledger_path = ledger_path.resolve()
    ledger = read_json(ledger_path)

    require(ledger.get("schemaVersion") == SCHEMA, "unexpected schemaVersion")
    require(ledger.get("window") == WINDOW, "recovery window drifted")
    require(ledger.get("excludedTargets") == ["dancing-dragons"], "excluded target drifted")

    safety = ledger.get("safety")
    require(isinstance(safety, dict), "safety must be an object")
    for false_field in (
        "forcePushUsed",
        "historyRewriteUsed",
        "automaticMergeUsed",
        "productionDeploymentUsed",
        "cloudflareMutationUsed",
        "credentialsCommitted",
        "recoveryIsAcceptance",
    ):
        require(safety.get(false_field) is False, f"{false_field} must be false")

    carrier = ledger.get("archiveCarrier")
    require(isinstance(carrier, dict), "archiveCarrier must be an object")
    historical_b64, historical_part_paths = read_parts(root, carrier.get("parts"), "archiveCarrier.parts")
    require(len(historical_b64) == carrier.get("concatenatedBytes"), "archive carrier size mismatch")
    require(
        hashlib.sha256(historical_b64).hexdigest() == carrier.get("concatenatedSha256"),
        "archive carrier digest mismatch",
    )
    require(
        carrier.get("encoding") == "concatenated-base64(gzip(tar))",
        "unexpected archive carrier encoding",
    )
    require(carrier.get("deterministic") is True, "archive carrier must be deterministic")
    try:
        compressed = base64.b64decode(historical_b64, validate=True)
        tar_bytes = gzip.decompress(compressed)
    except (ValueError, OSError) as error:
        raise ValidationError("archive carrier cannot be decoded") from error

    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as archive:
            for member in archive.getmembers():
                require(member.isfile(), f"archive member must be a regular file: {member.name}")
                require(is_relative_safe(member.name), f"unsafe archive member path: {member.name}")
                extracted = archive.extractfile(member)
                require(extracted is not None, f"archive member cannot be read: {member.name}")
                require(member.name not in members, f"duplicate archive member: {member.name}")
                members[member.name] = extracted.read()
    except (tarfile.TarError, OSError) as error:
        raise ValidationError("archive carrier is not a valid tar") from error
    require(len(members) == carrier.get("memberCount") == 5, "archive carrier member count drifted")

    wrapper = ledger.get("executionWrapper")
    require(isinstance(wrapper, dict), "executionWrapper must be an object")
    wrapper_rel = wrapper.get("path")
    require(isinstance(wrapper_rel, str) and is_relative_safe(wrapper_rel), "invalid execution wrapper path")
    wrapper_path = (root / wrapper_rel).resolve()
    require(root in wrapper_path.parents and wrapper_path.is_file(), "execution wrapper is missing")
    wrapper_bytes = wrapper_path.read_bytes()
    require(len(wrapper_bytes) == wrapper.get("bytes"), "execution wrapper size mismatch")
    require(hashlib.sha256(wrapper_bytes).hexdigest() == wrapper.get("sha256"), "execution wrapper digest mismatch")
    require(wrapper.get("payloadEncoding") == "concatenated-base64(gzip)", "unexpected safe payload encoding")
    safe_b64, safe_part_paths = read_parts(root, wrapper.get("payloadParts"), "executionWrapper.payloadParts")
    try:
        safe_compressed = base64.b64decode(safe_b64, validate=True)
        safe_payload = gzip.decompress(safe_compressed)
    except (ValueError, OSError) as error:
        raise ValidationError("safe reconciler payload cannot be decoded") from error
    require(
        hashlib.sha256(safe_compressed).hexdigest() == wrapper.get("payloadCompressedSha256"),
        "safe payload compressed digest mismatch",
    )
    require(len(safe_payload) == wrapper.get("payloadDecodedBytes"), "safe payload size mismatch")
    require(
        hashlib.sha256(safe_payload).hexdigest() == wrapper.get("payloadDecodedSha256"),
        "safe payload decoded digest mismatch",
    )
    wrapper_text = wrapper_bytes.decode("utf-8")
    require(wrapper.get("payloadDecodedSha256") in wrapper_text, "wrapper does not pin decoded payload digest")
    require("base64 --decode | gzip --decompress" in wrapper_text, "wrapper reconstruction contract drifted")
    require('exec bash "$tmp" "$@"' in wrapper_text, "wrapper execution contract drifted")

    artifacts = ledger.get("artifacts")
    require(isinstance(artifacts, list) and len(artifacts) == 6, "expected six recovered artifacts")
    seen_paths: set[str] = set()
    resolved_paths: list[Path] = [ledger_path, wrapper_path, *historical_part_paths, *safe_part_paths]
    artifact_bytes: dict[str, bytes] = {}
    original: dict[str, Any] | None = None
    safe: dict[str, Any] | None = None
    historical_parts = carrier.get("parts")
    safe_parts = wrapper.get("payloadParts")
    for index, artifact in enumerate(artifacts):
        require(isinstance(artifact, dict), f"artifact {index} must be an object")
        rel = artifact.get("path")
        require(isinstance(rel, str) and is_relative_safe(rel), f"invalid artifact path at {index}")
        require(rel not in seen_paths, f"duplicate artifact path: {rel}")
        seen_paths.add(rel)
        storage = artifact.get("storage")
        if storage == "archive-member":
            require(artifact.get("carrierParts") == historical_parts, f"artifact carrier drifted: {rel}")
            require(rel in members, f"archive member missing: {rel}")
            data = members[rel]
        elif storage == "chunked-gzip-base64":
            require(artifact.get("carrierParts") == safe_parts, f"safe artifact carrier drifted: {rel}")
            data = safe_payload
        else:
            raise ValidationError(f"unknown artifact storage for {rel}")
        require(len(data) == artifact.get("bytes"), f"artifact size mismatch: {rel}")
        require(hashlib.sha256(data).hexdigest() == artifact.get("sha256"), f"artifact digest mismatch: {rel}")
        artifact_bytes[rel] = data
        if rel.endswith("zed-fleet-reconcile.original.sh"):
            original = artifact
        if rel.endswith("zed-fleet-reconcile-no-force.payload.sh"):
            safe = artifact

    require(
        set(members) == {a["path"] for a in artifacts if a.get("storage") == "archive-member"},
        "archive contains unexpected members",
    )
    require(original is not None, "original reconciler is missing")
    require(original.get("executeAllowed") is False, "original reconciler must remain non-executable evidence")
    require(safe is not None, "no-force reconciler payload is missing")
    require(safe.get("executeAllowed") is True, "no-force reconciler should be the only execution candidate")

    original_text = artifact_bytes[original["path"]].decode("utf-8")
    require("git rebase" in original_text, "original-risk evidence no longer records rebase")
    require("--force-with-lease" in original_text, "original-risk evidence no longer records force push")

    safe_text = artifact_bytes[safe["path"]].decode("utf-8")
    for pattern in UNSAFE_SAFE_SCRIPT_PATTERNS:
        require(pattern.search(safe_text) is None, "unsafe history or archive command in no-force script")
    for fragment in (
        'git merge --no-edit "origin/$default_branch"',
        'git push -u origin "$branch"',
        '--merge-ready is disabled by the no-force recovery contract',
        '--archive-superseded is disabled by the no-force recovery contract',
        'resolve semantically in a fresh branch without rewriting either history',
    ):
        require(fragment in safe_text, f"missing no-force contract fragment: {fragment}")

    missing = ledger.get("missingRepositories")
    require(isinstance(missing, list), "missingRepositories must be an array")
    missing_names = {entry.get("repository") for entry in missing if isinstance(entry, dict)}
    require(missing_names == MISSING_REPOSITORIES, "missing repository inventory drifted")
    for entry in missing:
        require(entry.get("verified") == "404", f"missing repository not fail-closed: {entry}")

    linear = ledger.get("linearUpdatesRequired")
    require(isinstance(linear, list) and set(linear) == REQUIRED_LINEAR, "Linear update set drifted")

    evidence = ledger.get("githubEvidence")
    require(isinstance(evidence, list) and len(evidence) >= 9, "GitHub evidence is incomplete")
    by_pr = {(item.get("repository"), item.get("pullRequest")): item for item in evidence if isinstance(item, dict)}
    for number in (133, 134, 136):
        item = by_pr.get(("ORESoftware/ai-agent-coordinator.rs", number))
        require(item is not None, f"recovery PR #{number} missing")
        require(item.get("state") == "open" and item.get("draft") is True, f"recovery PR #{number} status drifted")
        conclusions = item.get("workflowConclusions")
        require(isinstance(conclusions, list) and conclusions and set(conclusions) == {"success"}, f"recovery PR #{number} is not green")

    durable = by_pr.get(("ORESoftware/k8s-cluster", 1215))
    require(durable is not None and durable.get("draft") is True and durable.get("state") == "open", "durable-worker recovery status drifted")
    for number in (789, 823):
        item = by_pr.get(("ORESoftware/k8s-cluster", number))
        require(item is not None and item.get("merged") is True, f"Messaging Intel PR #{number} is not recorded merged")

    findings = ledger.get("unresolvedFindings")
    require(isinstance(findings, list) and len(findings) == 5, "unresolved finding set drifted")
    owners = {item.get("owner") for item in findings if isinstance(item, dict)}
    require(owners == REQUIRED_LINEAR, "unresolved findings are not owned by the expected Linear tickets")

    reconciliations = ledger.get("semanticReconciliation")
    require(isinstance(reconciliations, list) and len(reconciliations) == 4, "semantic reconciliation set drifted")
    zed_resolution = next((item for item in reconciliations if item.get("subject") == "Zed dependency-inventory post-merge audit"), None)
    require(zed_resolution is not None, "Zed audit reconciliation is missing")
    require("do not fabricate a patch" in zed_resolution.get("resolution", ""), "Zed audit must remain a non-implementation claim")

    validate_credential_free(root, resolved_paths + [root / "docs/artifact-recovery-wave-6-2026-08-08.md"])
    for rel, data in artifact_bytes.items():
        text_data = data.decode("utf-8", errors="replace")
        for pattern in CREDENTIAL_PATTERNS:
            require(pattern.search(text_data) is None, f"credential-shaped material found in archive member {rel}")

    deterministic = json.dumps(ledger, indent=2, sort_keys=True) + "\n"
    require(ledger_path.read_text(encoding="utf-8") == deterministic, "ledger JSON is not deterministic")

    return {
        "valid": True,
        "schemaVersion": SCHEMA,
        "artifactCount": len(artifacts),
        "missingRepositoryCount": len(missing),
        "recoveryPullRequestsGreen": [133, 134, 136],
        "forcePushUsed": False,
        "promotionReady": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    ledger = args.ledger.resolve() if args.ledger else root / "data/artifact-recovery-wave6.json"
    try:
        result = validate(root, ledger)
    except (ValidationError, OSError, TypeError, KeyError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
