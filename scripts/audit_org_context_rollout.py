#!/usr/bin/env python3
"""Audit mapped GitHub owners for public `.github` context repositories."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from validate_org_project_registry import (
    RegistryError,
    canonical_sha256,
    load_registry,
    validate_registry,
)

COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
FORBIDDEN_VALUE_MARKERS = ("ghp_", "github_pat_", "xoxb-", "bearer ", "-----begin private key-----")


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise RegistryError(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistryError(f"duplicate inventory JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise RegistryError(f"non-finite inventory JSON number is forbidden: {value}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RegistryError(f"{label} must be a non-empty trimmed string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise RegistryError(f"{label} contains forbidden characters")
    if any(marker in value.casefold() for marker in FORBIDDEN_VALUE_MARKERS):
        raise RegistryError(f"{label} contains a credential-like marker")
    return value


def load_inventory(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if not raw or len(raw) > 256 * 1024:
        raise RegistryError("rollout inventory size is outside the allowed range")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegistryError("rollout inventory must be UTF-8") from exc
    if text.startswith("\ufeff"):
        raise RegistryError("rollout inventory must not contain a UTF-8 byte-order mark")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise RegistryError("rollout inventory must be strict JSON") from exc
    if not isinstance(value, dict):
        raise RegistryError("rollout inventory root must be an object")
    validate_inventory(value)
    return value


def validate_inventory(inventory: Mapping[str, Any]) -> None:
    _exact_keys(
        inventory,
        {"schema_version", "observed_at", "source", "query", "repositories"},
        "inventory",
    )
    if inventory["schema_version"] != 1:
        raise RegistryError("inventory.schema_version must be 1")
    if _text(inventory["source"], "inventory.source") != "connected_github_installations":
        raise RegistryError("inventory.source is unsupported")
    if _text(inventory["query"], "inventory.query") != "exact_repository_name:.github":
        raise RegistryError("inventory.query must target the exact .github repository name")
    observed_at = _text(inventory["observed_at"], "inventory.observed_at")
    try:
        observed = date.fromisoformat(observed_at)
    except ValueError as exc:
        raise RegistryError("inventory.observed_at must be a real YYYY-MM-DD date") from exc
    if observed.isoformat() != observed_at:
        raise RegistryError("inventory.observed_at must use YYYY-MM-DD")
    repositories = inventory["repositories"]
    if not isinstance(repositories, list):
        raise RegistryError("inventory.repositories must be an array")
    seen: set[str] = set()
    ids: set[int] = set()
    for index, repository in enumerate(repositories):
        label = f"inventory.repositories[{index}]"
        if not isinstance(repository, dict):
            raise RegistryError(f"{label} must be an object")
        _exact_keys(
            repository,
            {
                "full_name",
                "repository_id",
                "owner_account_id",
                "visibility",
                "default_branch",
            },
            label,
        )
        full_name = _text(repository["full_name"], f"{label}.full_name")
        if full_name.count("/") != 1:
            raise RegistryError(f"{label}.full_name must use owner/.github form")
        owner, name = full_name.split("/", 1)
        if not LOGIN_RE.fullmatch(owner) or name != ".github":
            raise RegistryError(f"{label}.full_name must identify an exact .github repository")
        normalized = full_name.casefold()
        if normalized in seen:
            raise RegistryError(f"duplicate rollout inventory repository: {full_name}")
        seen.add(normalized)
        repository_id = repository["repository_id"]
        owner_account_id = repository["owner_account_id"]
        if (
            not isinstance(repository_id, int)
            or isinstance(repository_id, bool)
            or repository_id <= 0
            or repository_id in ids
        ):
            raise RegistryError(f"{label}.repository_id must be a unique positive integer")
        ids.add(repository_id)
        if (
            not isinstance(owner_account_id, int)
            or isinstance(owner_account_id, bool)
            or owner_account_id <= 0
        ):
            raise RegistryError(f"{label}.owner_account_id must be a positive integer")
        visibility = _text(repository["visibility"], f"{label}.visibility")
        if visibility not in {"public", "private"}:
            raise RegistryError(f"{label}.visibility is unsupported")
        branch = _text(repository["default_branch"], f"{label}.default_branch")
        if (
            len(branch) > 255
            or branch.startswith(("/", "."))
            or branch.endswith(("/", "."))
            or ".." in branch
            or "@{" in branch
            or "//" in branch
            or any(character in branch for character in " ~^:?*[\\")
        ):
            raise RegistryError(f"{label}.default_branch is not a safe Git ref")


def _immutable_registry_ref(value: str) -> str:
    if not isinstance(value, str) or not COMMIT_SHA_RE.fullmatch(value):
        raise RegistryError("registry_ref must be an immutable lowercase commit SHA")
    return value


def _dry_run_request(owner: str) -> dict[str, Any]:
    return {
        "method": "POST",
        "path": "/v1/github/repositories",
        "body": {
            "organization": owner,
            "name": ".github",
            "visibility": "public",
            "initialization": "readme",
            "description": f"Public organization-wide GitHub and Linear context for {owner}",
            "dry_run": True,
        },
    }


def build_rollout_audit(
    registry: Mapping[str, Any],
    inventory: Mapping[str, Any],
    registry_ref: str,
) -> dict[str, Any]:
    counts = validate_registry(registry)
    validate_inventory(inventory)
    registry_ref = _immutable_registry_ref(registry_ref)

    mapped_by_login = {
        mapping["github"]["login"].casefold(): mapping
        for mapping in registry["mappings"]
    }
    unmapped_by_login = {
        gap["github"]["login"].casefold(): gap
        for gap in registry["unmapped_installed_organizations"]
    }
    inventory_by_login: dict[str, Mapping[str, Any]] = {}
    for repository in inventory["repositories"]:
        owner = repository["full_name"].split("/", 1)[0]
        normalized = owner.casefold()
        if normalized not in mapped_by_login and normalized not in unmapped_by_login:
            raise RegistryError(
                f"inventory repository owner is neither mapped nor explicitly unmapped: {owner}"
            )
        expected_account_id = (
            mapped_by_login.get(normalized) or unmapped_by_login[normalized]
        )["github"]["account_id"]
        if repository["owner_account_id"] != expected_account_id:
            raise RegistryError(f"inventory owner account ID drift for {owner}")
        inventory_by_login[normalized] = repository

    owners: list[dict[str, Any]] = []
    eligible_organizations = 0
    existing_public = 0
    missing = 0
    visibility_mismatch = 0
    unsupported_account_type = 0
    for mapping in sorted(
        registry["mappings"], key=lambda item: item["github"]["login"].casefold()
    ):
        github = mapping["github"]
        login = github["login"]
        expected_repository = f"{login}/.github"
        observed = inventory_by_login.get(login.casefold())
        if github["account_type"] != "Organization":
            status = "unsupported_account_type"
            unsupported_account_type += 1
            bootstrap = None
        else:
            eligible_organizations += 1
            if observed is None:
                status = "missing"
                missing += 1
                bootstrap = _dry_run_request(login)
            elif observed["visibility"] == "public":
                status = "existing_public"
                existing_public += 1
                bootstrap = None
            else:
                status = "visibility_mismatch"
                visibility_mismatch += 1
                bootstrap = None
        owners.append(
            {
                "github": github,
                "linear": mapping["linear"],
                "expected_repository": expected_repository,
                "status": status,
                "observed_repository": dict(observed) if observed is not None else None,
                "bootstrap_dry_run": bootstrap,
            }
        )

    excluded = []
    for gap in sorted(
        registry["unmapped_installed_organizations"],
        key=lambda item: item["github"]["login"].casefold(),
    ):
        excluded.append(
            {
                "github": gap["github"],
                "reason": gap["reason"],
                "linear_issue": gap["linear_issue"],
                "eligible_for_bootstrap": False,
            }
        )

    complete = missing == 0 and visibility_mismatch == 0
    missing_logins = [
        owner["github"]["login"] for owner in owners if owner["status"] == "missing"
    ]
    return {
        "schema_version": 1,
        "registry": {
            "ref": registry_ref,
            "sha256": canonical_sha256(registry),
            "mapped_owners": counts["mappings"],
            "unmapped_installed_organizations": counts["unmapped"],
        },
        "inventory": {
            "observed_at": inventory["observed_at"],
            "source": inventory["source"],
            "repository_count": len(inventory["repositories"]),
        },
        "summary": {
            "eligible_organizations": eligible_organizations,
            "existing_public": existing_public,
            "missing": missing,
            "visibility_mismatch": visibility_mismatch,
            "unsupported_account_type": unsupported_account_type,
            "excluded_unmapped": len(excluded),
            "complete": complete,
        },
        "bootstrap_contract": {
            "endpoint": "/v1/github/repositories",
            "supported_account_type": "Organization",
            "dry_run_default": True,
            "live_creation_authorized_by_this_artifact": False,
            "missing_owner_allowlist": missing_logins,
            "live_requires": [
                "authenticated coordinator request",
                "GITHUB_REPOSITORY_ADMIN_ENABLED=true",
                "explicit GITHUB_REPOSITORY_ADMIN_ALLOWED_ORGS entry",
                "short-lived GITHUB_REPOSITORY_ADMIN_TOKEN",
                "dry_run=false",
                "confirm_repository equal to organization/.github",
            ],
        },
        "owners": owners,
        "excluded_unmapped_organizations": excluded,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("config/org-project-registry.yaml"),
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path("config/org-context-rollout-inventory.json"),
    )
    parser.add_argument("--registry-ref", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        audit = build_rollout_audit(
            load_registry(args.registry),
            load_inventory(args.inventory),
            args.registry_ref,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(audit, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, RegistryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(audit["summary"], sort_keys=True))
    if args.require_complete and not audit["summary"]["complete"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
