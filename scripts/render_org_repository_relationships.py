#!/usr/bin/env python3
"""Render deterministic, public-safe repository relationship declarations."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from render_org_project_context import SEMANTIC_CONFLICT_DIRECTIVE
from validate_org_project_registry import (
    RegistryError,
    canonical_sha256,
    load_registry,
    resolve_owner,
    validate_registry,
)

COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_RENDERED_FILE_BYTES = 128 * 1024


def _immutable_registry_ref(value: str) -> str:
    if not isinstance(value, str) or not COMMIT_SHA_RE.fullmatch(value):
        raise RegistryError("registry_ref must be an immutable lowercase commit SHA")
    return value


def _semantic_conflict_policy() -> dict[str, Any]:
    return {
        "mode": "semantic_conceptual_merge",
        "directive_verbatim": SEMANTIC_CONFLICT_DIRECTIVE,
        "history_lookback_commits": {
            "minimum": 3,
            "maximum": 10,
            "when_available": True,
            "inspect_both_sides": True,
            "inspect_merge_base": True,
            "path_scoped_history": True,
        },
        "context_scope": [
            "conflicted_repository",
            "same_github_organization_repositories",
            "relevant_external_github_organization_repositories",
            "linear_project_context",
            "pull_requests_issues_architecture_decisions_tests_and_docs",
        ],
        "forbidden_shortcuts": [
            "wholesale_ours",
            "wholesale_theirs",
            "wholesale_current",
            "wholesale_incoming",
            "discarding_one_side_without_conceptual_analysis",
        ],
    }


def _entity(entity_type: str, entity_id: str, **metadata: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"type": entity_type, "id": entity_id}
    result.update(metadata)
    return result


def _edge(kind: str, source: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "source": dict(source), "target": dict(target)}


def _overrides_for_owner(
    registry: Mapping[str, Any], owner_login: str
) -> list[dict[str, Any]]:
    workspace = registry["linear"]
    overrides: list[dict[str, Any]] = []
    for raw_override in registry["repository_overrides"]:
        repository_owner, _, _ = raw_override["repository"].partition("/")
        if repository_owner.casefold() != owner_login.casefold():
            continue
        overrides.append(
            {
                "repository": raw_override["repository"],
                "linear": {
                    **raw_override["linear"],
                    "workspace_slug": workspace["workspace_slug"],
                    "team_id": workspace["team_id"],
                    "team_key": workspace["team_key"],
                },
            }
        )
    return sorted(overrides, key=lambda item: item["repository"].casefold())


def build_relationship_manifest(
    registry: Mapping[str, Any], owner: str | int, registry_ref: str
) -> dict[str, Any]:
    validate_registry(registry)
    registry_ref = _immutable_registry_ref(registry_ref)
    mapping = resolve_owner(registry, owner)
    authority = registry["authority"]
    workspace = registry["linear"]
    github = mapping["github"]
    linear = {
        **mapping["linear"],
        "workspace_slug": workspace["workspace_slug"],
        "team_id": workspace["team_id"],
        "team_key": workspace["team_key"],
    }
    owner_login = github["login"]
    governance_repository = f"{owner_login}/.github"
    route = mapping["runtime_route"]
    runtime_allowlist = sorted(
        route["repository_allowlist"] if route else [], key=str.casefold
    )
    default_repository = route["default_repository"] if route else None
    overrides = _overrides_for_owner(registry, owner_login)

    governance_entity = _entity("github_repository", governance_repository)
    edges = [
        _edge(
            "governs_public_context_for",
            governance_entity,
            _entity(
                "github_owner",
                owner_login,
                account_id=github["account_id"],
                account_type=github["account_type"],
            ),
        ),
        _edge(
            "generated_from",
            governance_entity,
            _entity(
                "github_file",
                f"{authority['source_of_truth']}:{authority['registry_path']}@{registry_ref}",
            ),
        ),
        _edge(
            "mirrors_linear_project",
            governance_entity,
            _entity(
                "linear_project",
                linear["project_id"],
                name=linear["project_name"],
                url=linear["project_url"],
            ),
        ),
    ]
    for repository in runtime_allowlist:
        edges.append(
            _edge(
                "permits_runtime_routing_to",
                governance_entity,
                _entity("github_repository", repository),
            )
        )
    if default_repository is not None:
        edges.append(
            _edge(
                "defaults_runtime_routing_to",
                governance_entity,
                _entity("github_repository", default_repository),
            )
        )
    for override in overrides:
        edges.append(
            _edge(
                "overrides_linear_project_for",
                _entity("github_repository", override["repository"]),
                _entity(
                    "linear_project",
                    override["linear"]["project_id"],
                    name=override["linear"]["project_name"],
                    url=override["linear"]["project_url"],
                ),
            )
        )
    edges.sort(
        key=lambda edge: (
            edge["kind"],
            edge["source"]["id"].casefold(),
            edge["target"]["id"].casefold(),
        )
    )

    registry_sha256 = canonical_sha256(registry)
    return {
        "schema_version": 1,
        "generated_from": {
            "repository": authority["source_of_truth"],
            "path": authority["registry_path"],
            "ref": registry_ref,
            "ref_type": "commit",
            "immutable": True,
            "canonical_sha256": registry_sha256,
            "url": (
                f"https://github.com/{authority['source_of_truth']}/blob/"
                f"{registry_ref}/{authority['registry_path']}"
            ),
        },
        "github": github,
        "governance": {
            "repository": governance_repository,
            "repository_scope": f"{owner_login}/*",
            "automatic_agent_instruction_inheritance": False,
            "repository_local_instruction_mirror_required": True,
            "public_context_only": True,
        },
        "linear": linear,
        "repository_selection": {
            "policy": registry["resolution"]["repository_selection"],
            "on_unmapped": registry["resolution"]["on_unmapped"],
            "on_ambiguous": registry["resolution"]["on_ambiguous"],
            "default_repository": default_repository,
            "runtime_allowlist": runtime_allowlist,
            "linear_project_overrides": overrides,
            "unregistered_dependencies": "unknown_not_assumed",
        },
        "relationships": edges,
        "git_conflict_resolution": _semantic_conflict_policy(),
    }


def render_relationship_file(
    registry: Mapping[str, Any], owner: str | int, registry_ref: str
) -> str:
    manifest = build_relationship_manifest(registry, owner, registry_ref)
    return json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def render_all_relationships(
    registry: Mapping[str, Any], registry_ref: str
) -> dict[str, str]:
    validate_registry(registry)
    registry_ref = _immutable_registry_ref(registry_ref)
    rendered: dict[str, str] = {}
    for mapping in sorted(
        registry["mappings"], key=lambda item: item["github"]["login"].casefold()
    ):
        login = mapping["github"]["login"]
        rendered[f"{login}/repository-relationships.json"] = render_relationship_file(
            registry, login, registry_ref
        )
    index = {
        "schema_version": 1,
        "registry_ref": registry_ref,
        "registry_sha256": canonical_sha256(registry),
        "owner_count": len(registry["mappings"]),
        "files": {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in sorted(rendered.items())
        },
    }
    rendered["repository-relationships-index.json"] = (
        json.dumps(index, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )
    return rendered


def _write_files(output_dir: Path, files: Mapping[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    root = output_dir.resolve()
    for relative, content in files.items():
        encoded = content.encode("utf-8")
        if not encoded or len(encoded) > MAX_RENDERED_FILE_BYTES:
            raise RegistryError(f"rendered file has invalid size: {relative}")
        destination = output_dir / relative
        if destination.is_symlink():
            raise RegistryError(f"refusing to replace symlink: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if root not in destination.resolve().parents:
            raise RegistryError(f"rendered path escapes output directory: {relative}")
        destination.write_text(content, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("config/org-project-registry.yaml"),
    )
    owner_group = parser.add_mutually_exclusive_group(required=True)
    owner_group.add_argument("--owner")
    owner_group.add_argument("--all", action="store_true")
    parser.add_argument("--registry-ref", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        registry = load_registry(args.registry)
        if args.all:
            files = render_all_relationships(registry, args.registry_ref)
        else:
            assert args.owner is not None
            files = {
                "repository-relationships.json": render_relationship_file(
                    registry, args.owner, args.registry_ref
                )
            }
        _write_files(args.output_dir, files)
    except (OSError, RegistryError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"rendered {len(files)} relationship file(s) to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
