#!/usr/bin/env python3
"""Validate a repository-fleet manifest and render fail-closed bootstrap requests.

This tool never performs network I/O and never reads credentials. It renders
request bodies for the coordinator's POST /v1/github/repositories endpoint.
Live rendering is deliberately one repository at a time and requires both a
manifest approval switch and an exact repository confirmation.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ORGANIZATION_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
TRACKING_ISSUE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]+$")
ALLOWED_VISIBILITIES = {"public", "private"}
ALLOWED_INITIALIZATIONS = {"readme", "empty"}
MAX_REPOSITORIES = 100
MAX_DESCRIPTION_CHARS = 350


@dataclass(frozen=True)
class RepositorySpec:
    order: int
    name: str
    visibility: str | None
    description: str | None


@dataclass(frozen=True)
class FleetManifest:
    schema_version: int
    organization: str
    tracking_issue: str
    default_branch: str
    initialization: str
    live_creation_enabled: bool
    forbidden_repositories: frozenset[str]
    deferred_repositories: frozenset[str]
    repositories: tuple[RepositorySpec, ...]


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _validate_repository_name(name: str, field: str) -> str:
    if (
        name in {".", ".."}
        or not REPOSITORY_PATTERN.fullmatch(name)
        or name.startswith("-")
        or name.endswith(".")
        or ".." in name
    ):
        raise ValueError(f"{field} contains an unsupported repository name")
    return name


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    text = _require_string(value, field)
    if len(text) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"{field} exceeds {MAX_DESCRIPTION_CHARS} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in text):
        raise ValueError(f"{field} must not contain control characters")
    return text


def _name_set(value: Any, field: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    names: list[str] = []
    for index, item in enumerate(value):
        name = _validate_repository_name(
            _require_string(item, f"{field}[{index}]"),
            f"{field}[{index}]",
        )
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError(f"{field} must not contain duplicates")
    return frozenset(names)


def load_manifest(path: Path) -> FleetManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"cannot read manifest {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"manifest is not valid JSON: {error}") from error

    root = _require_object(raw, "manifest")
    allowed_root_fields = {
        "schema_version",
        "organization",
        "tracking_issue",
        "default_branch",
        "initialization",
        "live_creation_enabled",
        "forbidden_repositories",
        "deferred_repositories",
        "repositories",
    }
    unknown_fields = sorted(set(root) - allowed_root_fields)
    if unknown_fields:
        raise ValueError(f"manifest contains unknown fields: {', '.join(unknown_fields)}")

    if root.get("schema_version") != 1:
        raise ValueError("schema_version must equal 1")

    organization = _require_string(root.get("organization"), "organization")
    if not ORGANIZATION_PATTERN.fullmatch(organization):
        raise ValueError("organization contains unsupported characters")

    tracking_issue = _require_string(root.get("tracking_issue"), "tracking_issue").upper()
    if not TRACKING_ISSUE_PATTERN.fullmatch(tracking_issue):
        raise ValueError("tracking_issue must look like DEN-123")

    default_branch = _require_string(root.get("default_branch"), "default_branch")
    if default_branch != "main":
        raise ValueError("default_branch must be main for canonical bootstrap fleets")

    initialization = _require_string(root.get("initialization"), "initialization")
    if initialization not in ALLOWED_INITIALIZATIONS:
        raise ValueError("initialization must be readme or empty")

    live_creation_enabled = root.get("live_creation_enabled")
    if not isinstance(live_creation_enabled, bool):
        raise ValueError("live_creation_enabled must be a boolean")

    forbidden = _name_set(root.get("forbidden_repositories"), "forbidden_repositories")
    deferred = _name_set(root.get("deferred_repositories"), "deferred_repositories")
    overlap = sorted(forbidden & deferred)
    if overlap:
        raise ValueError(
            "repositories cannot be both forbidden and deferred: " + ", ".join(overlap)
        )

    raw_repositories = root.get("repositories")
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise ValueError("repositories must be a non-empty array")
    if len(raw_repositories) > MAX_REPOSITORIES:
        raise ValueError(f"repositories must contain no more than {MAX_REPOSITORIES} entries")

    repositories: list[RepositorySpec] = []
    names: list[str] = []
    orders: list[int] = []
    for index, raw_repository in enumerate(raw_repositories):
        repository = _require_object(raw_repository, f"repositories[{index}]")
        allowed_repository_fields = {"order", "name", "visibility", "description"}
        unknown_repository_fields = sorted(set(repository) - allowed_repository_fields)
        if unknown_repository_fields:
            raise ValueError(
                f"repositories[{index}] contains unknown fields: "
                + ", ".join(unknown_repository_fields)
            )

        order = repository.get("order")
        if not isinstance(order, int) or isinstance(order, bool) or order < 1:
            raise ValueError(f"repositories[{index}].order must be a positive integer")

        name = _validate_repository_name(
            _require_string(repository.get("name"), f"repositories[{index}].name"),
            f"repositories[{index}].name",
        )
        visibility = repository.get("visibility")
        if visibility is not None and visibility not in ALLOWED_VISIBILITIES:
            raise ValueError(
                f"repositories[{index}].visibility must be public, private, or null"
            )
        description = _optional_string(
            repository.get("description"),
            f"repositories[{index}].description",
        )

        names.append(name)
        orders.append(order)
        repositories.append(
            RepositorySpec(
                order=order,
                name=name,
                visibility=visibility,
                description=description,
            )
        )

    if len(names) != len(set(names)):
        raise ValueError("repository names must be unique")
    if len(orders) != len(set(orders)):
        raise ValueError("repository orders must be unique")
    expected_orders = list(range(1, len(repositories) + 1))
    if sorted(orders) != expected_orders:
        raise ValueError(
            f"repository orders must be contiguous from 1 through {len(repositories)}"
        )

    forbidden_present = sorted(set(names) & forbidden)
    if forbidden_present:
        raise ValueError(
            "manifest includes forbidden repositories: " + ", ".join(forbidden_present)
        )
    deferred_present = sorted(set(names) & deferred)
    if deferred_present:
        raise ValueError(
            "manifest includes deferred repositories: " + ", ".join(deferred_present)
        )

    repositories.sort(key=lambda item: item.order)
    if any(item.name == "memebank-infra" for item in repositories):
        raise ValueError("memebank-infra is superseded; use mb-infra")
    if {item.name for item in repositories} >= {"mb-infra", "memebank-infra"}:
        raise ValueError("mb-infra and memebank-infra must never coexist")
    monorepo_positions = [
        item.order for item in repositories if item.name.endswith("-monorepo")
    ]
    if monorepo_positions and monorepo_positions[-1] != len(repositories):
        raise ValueError("the orchestration monorepo must be created last")

    return FleetManifest(
        schema_version=1,
        organization=organization,
        tracking_issue=tracking_issue,
        default_branch=default_branch,
        initialization=initialization,
        live_creation_enabled=live_creation_enabled,
        forbidden_repositories=forbidden,
        deferred_repositories=deferred,
        repositories=tuple(repositories),
    )


def unresolved_visibility(manifest: FleetManifest) -> list[str]:
    return [
        f"{manifest.organization}/{repository.name}"
        for repository in manifest.repositories
        if repository.visibility is None
    ]


def request_for(
    manifest: FleetManifest,
    repository: RepositorySpec,
    *,
    live: bool,
) -> dict[str, object]:
    if repository.visibility is None:
        raise ValueError(
            f"visibility decision is required for "
            f"{manifest.organization}/{repository.name}"
        )
    request: dict[str, object] = {
        "organization": manifest.organization,
        "name": repository.name,
        "visibility": repository.visibility,
        "initialization": manifest.initialization,
        "dry_run": not live,
    }
    if repository.description is not None:
        request["description"] = repository.description
    if live:
        request["confirm_repository"] = f"{manifest.organization}/{repository.name}"
    return request


def render(
    manifest: FleetManifest,
    *,
    mode: str,
    repository_name: str | None,
    confirmation: str | None,
) -> dict[str, object]:
    missing_visibility = unresolved_visibility(manifest)

    if mode == "plan":
        return {
            "schema_version": 1,
            "organization": manifest.organization,
            "tracking_issue": manifest.tracking_issue,
            "mode": "plan",
            "ready_for_dry_run": not missing_visibility,
            "ready_for_live": (
                manifest.live_creation_enabled and not missing_visibility
            ),
            "blockers": [
                f"visibility decision required for {full_name}"
                for full_name in missing_visibility
            ],
            "repositories": [
                {
                    "order": repository.order,
                    "full_name": f"{manifest.organization}/{repository.name}",
                    "visibility": repository.visibility,
                    "description": repository.description,
                }
                for repository in manifest.repositories
            ],
        }

    if missing_visibility:
        raise ValueError(
            "cannot render executable requests until visibility is decided for: "
            + ", ".join(missing_visibility)
        )

    if mode == "dry-run":
        if repository_name is not None:
            selected = [
                repository
                for repository in manifest.repositories
                if repository.name == repository_name
            ]
            if not selected:
                raise ValueError(f"repository {repository_name!r} is not in the fleet")
        else:
            selected = list(manifest.repositories)
        return {
            "schema_version": 1,
            "organization": manifest.organization,
            "tracking_issue": manifest.tracking_issue,
            "mode": "dry-run",
            "requests": [
                request_for(manifest, repository, live=False)
                for repository in selected
            ],
        }

    if mode != "live":
        raise ValueError(f"unsupported mode {mode!r}")
    if not manifest.live_creation_enabled:
        raise ValueError("live_creation_enabled is false in the manifest")
    if repository_name is None:
        raise ValueError("live mode requires --repository and renders only one request")
    selected_repository = next(
        (
            repository
            for repository in manifest.repositories
            if repository.name == repository_name
        ),
        None,
    )
    if selected_repository is None:
        raise ValueError(f"repository {repository_name!r} is not in the fleet")
    expected_confirmation = f"{manifest.organization}/{repository_name}"
    if confirmation != expected_confirmation:
        raise ValueError(
            f"live mode requires --confirm-repository {expected_confirmation}"
        )

    return {
        "schema_version": 1,
        "organization": manifest.organization,
        "tracking_issue": manifest.tracking_issue,
        "mode": "live",
        "request": request_for(manifest, selected_repository, live=True),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("repository-fleets/memebank.json"),
    )
    parser.add_argument(
        "--mode",
        choices=("plan", "dry-run", "live"),
        default="plan",
    )
    parser.add_argument("--repository")
    parser.add_argument("--confirm-repository")
    parser.add_argument("--compact", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    manifest = load_manifest(args.manifest)
    output = render(
        manifest,
        mode=args.mode,
        repository_name=args.repository,
        confirmation=args.confirm_repository,
    )
    if args.compact:
        print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    else:
        print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2) from error
