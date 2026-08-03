#!/usr/bin/env python3
"""Compare a bounded GitHub installation snapshot with the sealed fleet ledger.

The sealed schema-v2 ledger remains the source of repository identities and
history. A remote snapshot is evidence only: it must never silently redefine the
canonical fleet, treat a legacy alias as canonical, or equate an installed GitHub
App with a complete publication.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any


FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_ORGS = ("hypesiege", "streempilot")
EXPECTED_COUNTS = {"hypesiege": 15, "streempilot": 17}
EXPECTED_GITLINKS = {"hypesiege": 14, "streempilot": 16}
LEGACY_ALIASES = {
    "hypesiege/hypesiege-cli": "hypesiege/hypesiege-cli.rs",
    "streempilot/streempilot-cli": "streempilot/streempilot-cli.rs",
}


class FleetAuditError(ValueError):
    """The ledger or remote snapshot is malformed or ambiguous."""


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FleetAuditError(f"cannot read JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise FleetAuditError(f"{path} must contain a JSON object")
    return value


def normalized_full_name(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or FULL_NAME_RE.fullmatch(value) is None:
        raise FleetAuditError(f"{field} is not a valid owner/repository identity")
    return value.casefold()


def validate_manifest(manifest: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    if manifest.get("schema_version") != 2:
        raise FleetAuditError("sealed manifest must use schema_version 2")
    repositories = manifest.get("repositories")
    if not isinstance(repositories, list):
        raise FleetAuditError("sealed manifest has no repository ledger")
    if manifest.get("repository_count") != len(repositories):
        raise FleetAuditError("sealed manifest repository_count drift")
    if manifest.get("organizations") != EXPECTED_COUNTS:
        raise FleetAuditError("sealed manifest organization-count drift")

    by_org: dict[str, dict[str, dict[str, Any]]] = {
        org: {} for org in ALLOWED_ORGS
    }
    for index, record in enumerate(repositories):
        if not isinstance(record, dict):
            raise FleetAuditError(f"manifest repository {index} is not an object")
        org = record.get("org")
        if org not in ALLOWED_ORGS:
            raise FleetAuditError(f"manifest repository {index} has unexpected org")
        key = normalized_full_name(record.get("full_name"), field="manifest full_name")
        if key in by_org[org]:
            raise FleetAuditError(f"manifest contains duplicate repository {key}")
        if key.split("/", 1)[0] != org:
            raise FleetAuditError(f"manifest repository {key} has inconsistent org")
        commit = record.get("commit")
        if not isinstance(commit, str) or SHA_RE.fullmatch(commit) is None:
            raise FleetAuditError(f"manifest repository {key} has invalid commit")
        if record.get("default_branch") != "main":
            raise FleetAuditError(f"manifest repository {key} is not pinned to main")
        if record.get("visibility") not in {"public", "private"}:
            raise FleetAuditError(f"manifest repository {key} has invalid visibility")
        by_org[org][key] = record

    for org, expected_count in EXPECTED_COUNTS.items():
        if len(by_org[org]) != expected_count:
            raise FleetAuditError(f"manifest {org} count drift")
        monorepos = [
            record for record in by_org[org].values() if record.get("kind") == "monorepo"
        ]
        if len(monorepos) != 1 or monorepos[0].get("gitlinks") != EXPECTED_GITLINKS[org]:
            raise FleetAuditError(f"manifest {org} monorepo topology drift")
    return by_org


def validate_snapshot(snapshot: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    if snapshot.get("schema_version") != 1:
        raise FleetAuditError("remote snapshot must use schema_version 1")
    repositories = snapshot.get("repositories")
    if not isinstance(repositories, list):
        raise FleetAuditError("remote snapshot has no repository list")
    monorepo_gitlinks = snapshot.get("monorepo_gitlinks")
    if not isinstance(monorepo_gitlinks, dict):
        raise FleetAuditError("remote snapshot has no monorepo_gitlinks object")

    actual: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(repositories):
        if not isinstance(record, dict):
            raise FleetAuditError(f"remote repository {index} is not an object")
        key = normalized_full_name(record.get("full_name"), field="remote full_name")
        if key in actual:
            raise FleetAuditError(f"remote snapshot contains duplicate repository {key}")
        org = key.split("/", 1)[0]
        if org not in ALLOWED_ORGS:
            raise FleetAuditError(f"remote snapshot contains unexpected org {org}")
        if record.get("default_branch") not in {"main", "master", None}:
            raise FleetAuditError(f"remote repository {key} has invalid default_branch")
        if record.get("visibility") not in {"public", "private"}:
            raise FleetAuditError(f"remote repository {key} has invalid visibility")
        if not isinstance(record.get("admin"), bool):
            raise FleetAuditError(f"remote repository {key} has invalid admin flag")
        actual[key] = record

    gitlinks: dict[str, int] = {}
    for org in ALLOWED_ORGS:
        value = monorepo_gitlinks.get(org)
        if type(value) is not int or value < 0:
            raise FleetAuditError(f"remote snapshot has invalid {org} gitlink count")
        gitlinks[org] = value
    return actual, gitlinks


def audit_fleet(
    manifest: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    expected_visibility: str = "private",
) -> dict[str, Any]:
    if expected_visibility not in {"public", "private"}:
        raise FleetAuditError("expected visibility must be public or private")

    canonical = validate_manifest(manifest)
    actual, actual_gitlinks = validate_snapshot(snapshot)
    aliases = {key.casefold(): value.casefold() for key, value in LEGACY_ALIASES.items()}

    result: dict[str, Any] = {
        "schema_version": 1,
        "captured_at": snapshot.get("captured_at"),
        "expected_visibility": expected_visibility,
        "organizations": {},
        "complete": True,
    }

    for org in ALLOWED_ORGS:
        expected = canonical[org]
        actual_for_org = {
            key: record for key, record in actual.items() if key.startswith(f"{org}/")
        }
        canonical_present = sorted(key for key in actual_for_org if key in expected)
        legacy_aliases = [
            {"actual": key, "canonical": aliases[key]}
            for key in sorted(actual_for_org)
            if key in aliases
        ]
        legacy_keys = {entry["actual"] for entry in legacy_aliases}
        unexpected = sorted(
            key for key in actual_for_org if key not in expected and key not in legacy_keys
        )
        missing = sorted(key for key in expected if key not in actual_for_org)
        branch_drift = sorted(
            key
            for key, record in actual_for_org.items()
            if record.get("default_branch") != "main"
        )
        visibility_drift = sorted(
            key
            for key, record in actual_for_org.items()
            if record.get("visibility") != expected_visibility
        )
        admin_missing = sorted(
            key for key, record in actual_for_org.items() if record.get("admin") is not True
        )
        monorepo_gitlinks = actual_gitlinks[org]
        monorepo_reseal_required = monorepo_gitlinks != EXPECTED_GITLINKS[org]
        org_complete = not any(
            (
                missing,
                legacy_aliases,
                unexpected,
                branch_drift,
                visibility_drift,
                admin_missing,
            )
        ) and not monorepo_reseal_required

        result["organizations"][org] = {
            "expected_count": EXPECTED_COUNTS[org],
            "actual_count": len(actual_for_org),
            "canonical_present_count": len(canonical_present),
            "canonical_present": canonical_present,
            "missing_canonical": missing,
            "legacy_aliases": legacy_aliases,
            "unexpected": unexpected,
            "default_branch_drift": branch_drift,
            "visibility_drift": visibility_drift,
            "admin_access_missing": admin_missing,
            "monorepo_gitlinks": monorepo_gitlinks,
            "expected_monorepo_gitlinks": EXPECTED_GITLINKS[org],
            "monorepo_reseal_required": monorepo_reseal_required,
            "publish_children_first": [
                key for key in missing if expected[key].get("kind") != "monorepo"
            ],
            "complete": org_complete,
        }
        result["complete"] = result["complete"] and org_complete

    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("repository-fleets/hypesiege-streempilot.json"),
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-visibility", choices=("public", "private"), default="private")
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = read_json(args.manifest)
    snapshot = read_json(args.snapshot)
    reviewed_copy = deepcopy(manifest)
    result = audit_fleet(
        manifest,
        snapshot,
        expected_visibility=args.expected_visibility,
    )
    if manifest != reviewed_copy:
        raise FleetAuditError("audit mutated the sealed manifest")
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 1 if args.require_complete and not result["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
