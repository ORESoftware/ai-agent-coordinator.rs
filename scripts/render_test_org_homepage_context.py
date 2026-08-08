#!/usr/bin/env python3
"""Render deterministic public context for one registered test organization."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from validate_test_org_homepage_canaries import (
    RegistryError,
    canonical_sha256,
    load_registry,
    resolve_canary,
    validate_registry,
)

MAX_RENDERED_FILE_BYTES = 128 * 1024
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SEMANTIC_CONFLICT_DIRECTIVE = (
    "resolve any and all git conflicts semantically, will full context, even "
    "looking back 3-10 commits in git log history for more context - never "
    "hastily pick sides in a conflict but merge things conceptually, using max "
    "context and complete conceptual awareness for a given github organization's "
    "repos and external org repos too"
)


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
            "test_repository",
            "parent_production_organization_repositories",
            "relevant_external_organization_repositories",
            "linear_project_context",
            "pull_requests_issues_architecture_decisions_tests_and_docs",
        ],
        "forbidden_shortcuts": [
            "wholesale_ours",
            "wholesale_theirs",
            "wholesale_current",
            "wholesale_incoming",
        ],
        "required_outcome": (
            "preserve compatible intent, test evidence, schemas, interfaces, "
            "documentation, security controls, and operational safeguards"
        ),
    }


def _project_context(
    registry: Mapping[str, Any],
    canary: Mapping[str, Any],
    registry_ref: str,
) -> dict[str, Any]:
    authority = registry["authority"]
    team = registry["linear"]
    github = canary["github"]
    login = github["login"]
    return {
        "schema_version": 1,
        "context_kind": "test_organization_acceptance",
        "generated_from": {
            "repository": authority["source_of_truth"],
            "path": authority["registry_path"],
            "ref": registry_ref,
            "ref_type": "commit",
            "immutable": True,
            "canonical_sha256": canonical_sha256(registry),
            "url": (
                f"https://github.com/{authority['source_of_truth']}/blob/"
                f"{registry_ref}/{authority['registry_path']}"
            ),
            "raw_url": (
                f"https://raw.githubusercontent.com/{authority['source_of_truth']}/"
                f"{registry_ref}/{authority['registry_path']}"
            ),
        },
        "github": github,
        "linear": {
            **canary["linear"],
            "workspace_slug": team["workspace_slug"],
            "team_id": team["team_id"],
            "team_key": team["team_key"],
            "team_name": team["team_name"],
        },
        "parent_production": canary["parent_production"],
        "test_program": canary["test_program"],
        "resolution": {
            "owner_identity_key": registry["resolution"]["owner_identity_key"],
            "login_case_insensitive": registry["resolution"][
                "login_case_insensitive"
            ],
            "on_unmapped": registry["resolution"]["on_unmapped"],
            "on_ambiguous": registry["resolution"]["on_ambiguous"],
            "runtime_routing": registry["resolution"]["runtime_routing"],
            "repository_selection": "explicit_only",
        },
        "runtime_route": None,
        "relationships": {
            "path": "repository-relationships.json",
            "inference": "forbidden",
        },
        "mirrors": {
            "github_repository": github["repository"],
            "github_profile": "profile/README.md",
            "github_project_context": "project-context.yaml",
            "github_relationships": "repository-relationships.json",
            "github_org_context": "ORG_CONTEXT.md",
            "github_custom_agent": "agents/org-context.agent.md",
            "bundle_manifest": "test-org-context-manifest.json",
        },
        "precedence": {
            "test_identity_and_parent_link": "test_canary_registry",
            "production_identity_and_runtime_routing": "production_registry",
            "implementation_instructions": "repository_local",
        },
        "authorization": {
            "repository_access": "not_implied",
            "production_data_access": "not_implied",
            "private_context_publication": "forbidden",
        },
        "git_conflict_resolution": _semantic_conflict_policy(),
        "organization": login,
        "public_context_only": True,
    }


def _relationships(context: Mapping[str, Any]) -> dict[str, Any]:
    github = context["github"]
    parent = context["parent_production"]
    return {
        "schema_version": 1,
        "context_kind": "test_organization_relationships",
        "organization": github["login"],
        "relationship_policy": "explicit_only",
        "declared_relationships": [
            {
                "source_repository": github["repository"],
                "target_organization": parent["github_login"],
                "kind": "black_box_acceptance_governance_for",
                "access": "not_implied",
                "data_boundary": "published_contracts_and_authorized_test_inputs_only",
            }
        ],
        "policies": {
            "infer_dependencies_from_names": False,
            "direct_production_database_access": False,
            "runtime_routing": "forbidden",
            "unlisted_relationships": "unknown",
        },
        "public_context_only": True,
    }


def _org_context_markdown(context: Mapping[str, Any]) -> str:
    github = context["github"]
    linear = context["linear"]
    parent = context["parent_production"]
    test_program = context["test_program"]
    scope = "\n".join(f"- {item}" for item in test_program["scope"])
    return f"""# {github['login']} organization context

`{github['login']}` is a test-only acceptance organization for the production organization [`{parent['github_login']}`](https://github.com/{parent['github_login']}). It does not provide a production runtime route and does not imply access to any private repository, production system, database, credential, customer record, or incident detail.

## Canonical identity

- GitHub test organization: [`{github['login']}`](https://github.com/{github['login']})
- Immutable GitHub test owner ID: `{github['account_id']}`
- Test planning project: [`{linear['project_name']}`]({linear['project_url']})
- Immutable test Linear project ID: `{linear['project_id']}`
- GitHub execution project: [project board]({github['github_project_url']})
- Production parent: [`{parent['github_login']}`](https://github.com/{parent['github_login']})
- Immutable production parent owner ID: `{parent['github_account_id']}`
- Production planning project: [`{parent['linear_project_name']}`]({parent['linear_project_url']})
- Immutable production Linear project ID: `{parent['linear_project_id']}`

The immutable central test-canary registry controls this identity and parent link. The production registry remains authoritative for production identity and runtime routing. Repository-local instructions, tests, fixtures, workflows, and documentation remain authoritative for implementation and evidence.

## Acceptance scope

{test_program['purpose']}

{scope}

The existing specialized organization profile and readiness notes must be preserved. A missing dependency, upstream artifact, environment, authorization, or credential is blocked readiness—not a passing result and not automatically a product regression.

## Agent operating contract

1. Read `project-context.yaml`, `repository-relationships.json`, this file, `AGENTS.md`, and every applicable repository-local instruction.
2. Select the exact test repository and work item explicitly; this organization has no default runtime repository.
3. Treat the production parent as a black-box acceptance target through published contracts and separately authorized test inputs only.
4. Do not infer private repository access, production topology, credentials, customer data, or database access from this public context.
5. Fail closed on missing, unmapped, ambiguous, stale, or contradictory context.
6. Link substantial work to Linear and a GitHub issue or pull request so humans and agents can recover intent and evidence.

## Semantic Git conflict resolution

> {SEMANTIC_CONFLICT_DIRECTIVE}

Inspect the merge base, both sides, path-scoped history, and 3–10 relevant commits when available. Consult linked issues, pull requests, test evidence, schemas, fixtures, workflows, architecture decisions, documentation, parent-production repositories, and relevant external repositories. Never accept `ours`, `theirs`, current, or incoming wholesale. Preserve compatible intent and evidence, scan the full worktree for conflict markers, and run every affected validation contract.

## Public context boundary

This file is intentionally public. It may contain public identifiers, links, test scope, and operating rules. It must not contain credentials, private repository inventories, customer or user data, production test data, incident details, security-sensitive topology, or unpublished vulnerabilities.
"""


def _custom_agent(context: Mapping[str, Any]) -> str:
    github = context["github"]
    linear = context["linear"]
    parent = context["parent_production"]
    generated = context["generated_from"]
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", github["login"]).lower()
    return f"""---
name: {safe_name}-org-context
description: Resolves test-only acceptance work without inventing production access or routing
tools: ["read", "search"]
target: github-copilot
---

You are the organization-context resolver for test owner `{github['login']}` (immutable GitHub owner ID `{github['account_id']}`).

Map planning to Linear project [`{linear['project_name']}`]({linear['project_url']}) (immutable project ID `{linear['project_id']}`). The declared production parent is `{parent['github_login']}`, but this relationship grants no repository, data, system, or credential access. This test organization has no runtime route and no default repository; require an explicit repository and work item.

Read `project-context.yaml`, `repository-relationships.json`, `ORG_CONTEXT.md`, organization `AGENTS.md`, repository-local agent instructions, linked Linear issues, GitHub issues and pull requests, tests, fixtures, workflows, and relevant history before proposing changes.

Resolve every Git conflict semantically. Inspect the merge base, both sides, path-scoped history, and 3–10 relevant commits when available; consider relevant parent-production and external repositories; never accept `ours`, `theirs`, current, or incoming wholesale.

Fail closed on missing, unmapped, ambiguous, stale, contradictory, or unauthorized context. Never publish credentials, private repository inventories, production data, customer information, incident details, security-sensitive topology, or hidden reasoning.

Canonical immutable registry: {generated['url']}
"""


def _bundle_manifest(
    context: Mapping[str, Any], bundle: Mapping[str, str]
) -> str:
    manifest = {
        "schema_version": 1,
        "context_kind": "test_organization_context_manifest",
        "github_owner": context["github"]["login"],
        "github_account_id": context["github"]["account_id"],
        "registry_ref": context["generated_from"]["ref"],
        "registry_sha256": context["generated_from"]["canonical_sha256"],
        "files": {
            path: hashlib.sha256(content.encode("utf-8")).hexdigest()
            for path, content in sorted(bundle.items())
        },
    }
    return json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def render_bundle(
    registry: Mapping[str, Any], owner: str | int, registry_ref: str
) -> dict[str, str]:
    validate_registry(registry)
    registry_ref = _immutable_registry_ref(registry_ref)
    canary = resolve_canary(registry, owner)
    context = _project_context(registry, canary, registry_ref)
    relationships = _relationships(context)
    bundle = {
        "project-context.yaml": json.dumps(
            context, indent=2, ensure_ascii=False, sort_keys=True
        )
        + "\n",
        "repository-relationships.json": json.dumps(
            relationships, indent=2, ensure_ascii=False, sort_keys=True
        )
        + "\n",
        "ORG_CONTEXT.md": _org_context_markdown(context),
        "agents/org-context.agent.md": _custom_agent(context),
    }
    bundle["test-org-context-manifest.json"] = _bundle_manifest(context, bundle)
    return bundle


def write_bundle(output_dir: Path, bundle: Mapping[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_root = output_dir.resolve()
    for relative, content in bundle.items():
        encoded = content.encode("utf-8")
        if not encoded or len(encoded) > MAX_RENDERED_FILE_BYTES:
            raise RegistryError(f"rendered file has invalid size: {relative}")
        destination = output_dir / relative
        if destination.is_symlink():
            raise RegistryError(f"refusing to replace symlink: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_destination = destination.resolve()
        if resolved_root != resolved_destination.parent and resolved_root not in resolved_destination.parents:
            raise RegistryError(f"rendered path escapes output directory: {relative}")
        temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
        try:
            temporary.write_text(content, encoding="utf-8", newline="\n")
            temporary.replace(destination)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("config/test-org-homepage-canaries.yaml"),
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--registry-ref", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        registry = load_registry(args.registry)
        bundle = render_bundle(registry, args.owner, args.registry_ref)
        write_bundle(args.output_dir, bundle)
    except (OSError, RegistryError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "pass",
                "owner": args.owner,
                "registry_sha256": canonical_sha256(registry),
                "files": sorted(bundle),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
