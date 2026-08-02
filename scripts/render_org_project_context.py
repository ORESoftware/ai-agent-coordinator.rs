#!/usr/bin/env python3
"""Render one public-safe organization context bundle from the central registry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from validate_org_project_registry import (
    RegistryError,
    canonical_sha256,
    load_registry,
    resolve_owner,
    validate_registry,
)

MAX_RENDERED_FILE_BYTES = 128 * 1024

SEMANTIC_CONFLICT_DIRECTIVE = (
    "resolve any and all git conflicts semantically, will full context, even "
    "looking back 3-10 commits in git log history for more context - never "
    "hastily pick sides in a conflict but merge things conceptually, using max "
    "context and complete conceptual awareness for a given github organization's "
    "repos and external org repos too"
)


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
        "required_outcome": (
            "preserve compatible intent, invariants, APIs, schemas, migrations, "
            "tests, documentation, security controls, and operational safeguards "
            "from every relevant side"
        ),
    }


def _semantic_conflict_markdown() -> str:
    return f"""## Semantic Git conflict resolution

> {SEMANTIC_CONFLICT_DIRECTIVE}

Before resolving a conflict, inspect the merge base and 3–10 relevant commits from both sides when available, including path-scoped history for every conflicted file. Read repository-local instructions, linked Linear issues, pull requests, architecture decisions, tests, migrations, schemas, and documentation. When a contract crosses repository boundaries, inspect relevant repositories in the same GitHub organization and relevant repositories in external GitHub organizations too.

Never resolve by blindly or wholesale selecting `ours`, `theirs`, current, or incoming. Produce a conceptual merge that preserves compatible intent, invariants, APIs, schemas, migrations, tests, documentation, security controls, and operational safeguards from all relevant sides. Document non-obvious decisions, scan the whole worktree for conflict markers, and run every affected validation contract. “Max context” means all relevant authorized context; it never authorizes exposing credentials, private data, or hidden reasoning.
"""


def _project_context(
    registry: Mapping[str, Any], mapping: Mapping[str, Any], registry_ref: str
) -> dict[str, Any]:
    authority = registry["authority"]
    linear = registry["linear"]
    return {
        "schema_version": 1,
        "generated_from": {
            "repository": authority["source_of_truth"],
            "path": authority["registry_path"],
            "ref": registry_ref,
            "canonical_sha256": canonical_sha256(registry),
            "url": (
                f"https://github.com/{authority['source_of_truth']}/blob/"
                f"{registry_ref}/{authority['registry_path']}"
            ),
        },
        "github": mapping["github"],
        "linear": {
            **mapping["linear"],
            "workspace_slug": linear["workspace_slug"],
            "team_id": linear["team_id"],
            "team_key": linear["team_key"],
        },
        "resolution": {
            "owner_identity_key": registry["resolution"]["owner_identity_key"],
            "repository_override_precedence": registry["resolution"][
                "repository_override_precedence"
            ],
            "on_unmapped": registry["resolution"]["on_unmapped"],
            "on_ambiguous": registry["resolution"]["on_ambiguous"],
            "repository_selection": registry["resolution"]["repository_selection"],
        },
        "runtime_route": mapping["runtime_route"],
        "git_conflict_resolution": _semantic_conflict_policy(),
        "mirrors": {
            "github_profile": "profile/README.md",
            "github_custom_agent": "agents/org-context.agent.md",
            "linear_document_title": authority["linear_document_title"],
            "linear_marker": "org-project-context:v1",
        },
        "precedence": {
            "identity_and_routing": "central_registry",
            "implementation_instructions": "repository_local",
            "semantic_conflict_policy": "organization_context",
        },
        "public_context_only": True,
    }


def _profile_readme(context: Mapping[str, Any]) -> str:
    github = context["github"]
    linear = context["linear"]
    generated = context["generated_from"]
    route = context["runtime_route"]
    route_text = (
        f"The reviewed runtime entry defaults to "
        f"[`{route['default_repository']}`](https://github.com/{route['default_repository']}) "
        "within its explicit allowlist."
        if route
        else "No default repository is declared; agents must resolve the exact repository and fail closed on ambiguity."
    )
    return f"""# {github['login']}

This organization is mapped to the Linear project [{linear['project_name']}]({linear['project_url']}).

## AI agent context

- GitHub owner ID: `{github['account_id']}`
- Linear project ID: `{linear['project_id']}`
- Linear team: `{linear['team_key']}` (`{linear['team_id']}`)
- Machine-readable context: [`project-context.yaml`](../project-context.yaml)
- Canonical registry: [`{generated['repository']}/{generated['path']}`]({generated['url']})

{route_text}

Repository-local `AGENTS.md`, `agents.md`, and tool instructions remain authoritative for build, test, and implementation details. The central registry remains authoritative for GitHub/Linear identity and routing. Unmapped or ambiguous work must be rejected rather than guessed.

{_semantic_conflict_markdown()}
This public repository contains identifiers, links, and public operating guidance only. Do not place credentials, private customer data, or private operational details here.
"""


def _repository_readme(context: Mapping[str, Any]) -> str:
    github = context["github"]
    return f"""# {github['login']} organization context

This special public `.github` repository is the discoverable organization anchor for humans and AI agents.

- `profile/README.md` is the visible organization profile.
- `project-context.yaml` is the generated GitHub owner ↔ Linear project mapping.
- `agents/org-context.agent.md` is the organization-level GitHub Copilot custom-agent profile.
- The generated profile and custom agent carry the mandatory semantic Git conflict-resolution policy.

The source of truth is the reviewed central registry named in `project-context.yaml`. Generated files should not be edited independently. Keep this repository public-safe.
"""


def _custom_agent(context: Mapping[str, Any]) -> str:
    github = context["github"]
    linear = context["linear"]
    generated = context["generated_from"]
    route = context["runtime_route"]
    route_line = (
        f"For routed work, the reviewed default repository is `{route['default_repository']}` "
        f"and the allowlist is `{', '.join(route['repository_allowlist'])}`."
        if route
        else "There is no reviewed default repository; require an explicit repository or one unambiguous repository match."
    )
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", github["login"]).lower()
    return f"""---
name: {safe_name}-org-context
description: Resolves {github['login']} repositories to the canonical Linear project without guessing
tools: ["read", "search"]
target: github-copilot
---

You are the organization-context resolver for GitHub owner `{github['login']}` (immutable account ID `{github['account_id']}`).

Map organization-level work to Linear project `{linear['project_name']}` (immutable project ID `{linear['project_id']}`) in team `{linear['team_key']}`. Exact repository overrides in the central registry take precedence over this owner-level mapping. {route_line}

Read repository-local `AGENTS.md`, lowercase `agents.md`, `.github/copilot-instructions.md`, and narrower path instructions before proposing implementation changes. Repository-local instructions control implementation details; the central registry controls GitHub/Linear identity and routing.

{_semantic_conflict_markdown()}
Fail closed when the owner, repository, or Linear project is missing or ambiguous. Never route by a mutable display name alone. Never expose credentials, private issue content, customer data, or hidden reasoning in public context.

Canonical registry: {generated['url']}
"""


def render_bundle(
    registry: Mapping[str, Any], owner: str | int, registry_ref: str = "main"
) -> dict[str, str]:
    mapping = resolve_owner(registry, owner)
    context = _project_context(registry, mapping, registry_ref)
    return {
        "README.md": _repository_readme(context),
        "project-context.yaml": json.dumps(context, indent=2, ensure_ascii=False) + "\n",
        "profile/README.md": _profile_readme(context),
        "agents/org-context.agent.md": _custom_agent(context),
    }


def _write_bundle(output_dir: Path, bundle: Mapping[str, str]) -> None:
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
        if resolved_root not in destination.resolve().parents:
            raise RegistryError(f"rendered path escapes output directory: {relative}")
        destination.write_text(content, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("config/org-project-registry.yaml"),
    )
    parser.add_argument("--owner", required=True)
    parser.add_argument("--registry-ref", default="main")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry)
        validate_registry(registry)
        bundle = render_bundle(registry, args.owner, args.registry_ref)
        _write_bundle(args.output_dir, bundle)
    except (OSError, RegistryError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "pass",
                "owner": args.owner,
                "files": sorted(bundle),
                "registry_sha256": canonical_sha256(registry),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
