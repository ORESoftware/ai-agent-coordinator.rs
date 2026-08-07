#!/usr/bin/env python3
"""Render one public-safe organization context bundle from the central registry."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from validate_org_homepage import validate_text as validate_org_homepage_text
from validate_org_project_registry import (
    RegistryError,
    canonical_sha256,
    load_registry,
    resolve_owner,
    validate_registry,
)

MAX_RENDERED_FILE_BYTES = 128 * 1024
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _immutable_registry_ref(value: str) -> str:
    if not isinstance(value, str) or not COMMIT_SHA_RE.fullmatch(value):
        raise RegistryError("registry_ref must be an immutable lowercase commit SHA")
    return value


def _markdown_link_text(value: str) -> str:
    escaped = html.escape(value, quote=False).replace("\\", "\\\\")
    return escaped.replace("[", "\\[").replace("]", "\\]")


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
    github = mapping["github"]
    return {
        "schema_version": 1,
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
            "github_repository": f"{github['login']}/.github",
            "github_profile": "profile/README.md",
            "github_project_context_url": (
                f"https://github.com/{github['login']}/.github/blob/"
                "main/project-context.yaml"
            ),
            "github_custom_agent": "agents/org-context.agent.md",
            "github_integrity_workflow": (
                ".github/workflows/org-context-integrity.yml"
            ),
            "bundle_manifest": "org-context-manifest.json",
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
    mirrors = context["mirrors"]
    route = context["runtime_route"] or {}
    default_repository = route.get("default_repository")
    allowlist = route.get("repository_allowlist", [])

    if default_repository:
        rendered_allowlist = ", ".join(
            f"`{repository}`" for repository in allowlist
        )
        route_text = (
            "For reviewed routed work, the default repository is "
            f"[`{default_repository}`](https://github.com/{default_repository}). "
            f"The runtime allowlist is {rendered_allowlist}. Exact repository "
            "overrides take precedence. Ambiguous or unmapped work must stop "
            "rather than be guessed."
        )
    elif allowlist:
        rendered_allowlist = ", ".join(
            f"`{repository}`" for repository in allowlist
        )
        route_text = (
            "No default repository is declared. The reviewed runtime allowlist is "
            f"{rendered_allowlist}; select one explicitly. Ambiguous or unmapped "
            "work must stop rather than be guessed."
        )
    else:
        route_text = (
            "Resolve the exact repository explicitly. This organization has no "
            "reviewed default runtime repository, so ambiguous or unmapped work "
            "must stop rather than be guessed."
        )

    login = github["login"]
    project_name = _markdown_link_text(linear["project_name"])
    profile = f"""# {login}

The `{login}` GitHub organization hosts repositories and shared work mapped to the Linear project `{project_name}`. This public profile gives people and authorized AI agents a safe starting point without replacing repository-specific documentation.

## Start here

### For people

- Browse the [`{login}` organization repositories](https://github.com/{login}).
- Use the [canonical Linear project]({linear['project_url']}) for planning, priorities, dependencies, and delivery context.
- Read the organization [contribution guide](https://github.com/{login}/.github/blob/main/CONTRIBUTING.md), [governance notes](https://github.com/{login}/.github/blob/main/GOVERNANCE.md), [support guide](https://github.com/{login}/.github/blob/main/SUPPORT.md), and [security policy](https://github.com/{login}/.github/security/policy).
- Start in the README and local instructions of the exact repository being changed; this profile is an index, not a substitute for repository documentation.

### For AI agents

1. Read [`project-context.yaml`]({mirrors['github_project_context_url']}) for the canonical GitHub owner, Linear project, and reviewed routing context.
2. Read [`repository-relationships.json`](https://github.com/{login}/.github/blob/main/repository-relationships.json) before inferring dependencies, ownership, or repository selection.
3. Read the organization [`AGENTS.md`](https://github.com/{login}/.github/blob/main/AGENTS.md), [`ORG_CONTEXT.md`](https://github.com/{login}/.github/blob/main/ORG_CONTEXT.md), and every applicable repository-local `AGENTS.md`, `agents.md`, provider instruction, and path-specific instruction.
4. {route_text}
5. Keep credentials, private repository content, customer information, incident details, and security-sensitive topology out of public outputs.

## Canonical identity and authority

- GitHub organization: [`{login}`](https://github.com/{login})
- Immutable GitHub owner ID: `{github['account_id']}`
- Linear project: [`{project_name}`]({linear['project_url']})
- Immutable Linear project ID: `{linear['project_id']}`
- Linear team: `{linear['team_key']}` (`{linear['team_id']}`)
- Organization defaults and public policies: [`{login}/.github`](https://github.com/{login}/.github)
- Canonical registry: [`{generated['repository']}/{generated['path']}`]({generated['url']})

The reviewed central registry is authoritative for GitHub/Linear identity and routing. Repository-local instructions are authoritative for builds, tests, architecture, migrations, and implementation. Exact repository overrides take precedence over owner-level defaults. Missing, unmapped, ambiguous, or contradictory context must stop and be reported; it must not be invented.

## Operating principles

- Do not infer product scope, architecture, ownership, or repository relationships from names alone; use reviewed repository documentation and machine-readable context.
- Preserve data and state non-destructively. Do not use history rewrites, blanket resets, destructive cleanup, or wholesale side selection to make a change appear simple.
- Keep application code and infrastructure repositories separate. An `*-infra` repository does not belong under a monorepo `apps/` directory as a Git submodule.
- Link substantial work to Linear and a GitHub issue or pull request so humans and agents can recover intent.
- Resolve Git conflicts semantically: inspect the merge base, both sides, path-scoped history, and 3–10 relevant commits when available; read linked issues, pull requests, tests, schemas, migrations, architecture decisions, and relevant same-organization or external repositories. Never accept `ours`, `theirs`, current, or incoming wholesale without conceptual review.
- Preserve compatible intent, APIs, schemas, tests, documentation, security controls, and operational safeguards from every relevant side, then scan the complete worktree for unresolved conflict markers and run all affected validation.

{_semantic_conflict_markdown()}
## Public context boundary

This profile and the `.github` repository are intentionally public. They may contain public identifiers, links, policies, and operating guidance. They must not contain credentials, private customer or user data, private issue content, incident details, security-sensitive topology, or unpublished business information.
"""

    errors = validate_org_homepage_text(profile, expect_org=login)
    if errors:
        details = "; ".join(errors)
        raise RegistryError(f"generated organization homepage is invalid: {details}")
    return profile


def _repository_readme(context: Mapping[str, Any]) -> str:
    github = context["github"]
    return f"""# {github['login']} organization context

This special public `.github` repository is the discoverable organization anchor for humans and AI agents.

- `profile/README.md` is the visible organization profile.
- `project-context.yaml` is the generated GitHub owner ↔ Linear project mapping.
- `org-context-manifest.json` records deterministic SHA-256 hashes for every other managed file.
- `agents/org-context.agent.md` is the organization-level GitHub Copilot custom-agent profile.
- `.github/workflows/org-context-integrity.yml` verifies this mirror against its immutable central registry commit.
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
    project_name = _markdown_link_text(linear["project_name"])
    return f"""---
name: {safe_name}-org-context
description: Resolves {github['login']} repositories to the canonical Linear project without guessing
tools: ["read", "search"]
target: github-copilot
---

You are the organization-context resolver for GitHub owner `{github['login']}` (immutable account ID `{github['account_id']}`).

Map organization-level work to Linear project [{project_name}]({linear['project_url']}) (immutable project ID `{linear['project_id']}`) in team `{linear['team_key']}`. Exact repository overrides in the central registry take precedence over this owner-level mapping. {route_line}

Read repository-local `AGENTS.md`, lowercase `agents.md`, `.github/copilot-instructions.md`, and narrower path instructions before proposing implementation changes. Repository-local instructions control implementation details; the central registry controls GitHub/Linear identity and routing.

{_semantic_conflict_markdown()}
Fail closed when the owner, repository, or Linear project is missing or ambiguous. Never route by a mutable display name alone. Never expose credentials, private issue content, customer data, or hidden reasoning in public context.

Canonical registry: {generated['url']}
"""


def _integrity_workflow(context: Mapping[str, Any]) -> str:
    github = context["github"]
    generated = context["generated_from"]
    return f"""name: Organization context integrity

on:
  pull_request:
    paths:
      - .github/workflows/org-context-integrity.yml
      - README.md
      - agents/org-context.agent.md
      - org-context-manifest.json
      - profile/README.md
      - project-context.yaml
  push:
    branches: [main]
    paths:
      - .github/workflows/org-context-integrity.yml
      - README.md
      - agents/org-context.agent.md
      - org-context-manifest.json
      - profile/README.md
      - project-context.yaml
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{{{ github.workflow }}}}-${{{{ github.ref }}}}
  cancel-in-progress: true

jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Validate workflow syntax
        uses: docker://rhysd/actionlint@sha256:b1934ee5f1c509618f2508e6eb47ee0d3520686341fec936f3b79331f9315667
        with:
          args: .github/workflows/org-context-integrity.yml

      - name: Verify immutable central source and generated bundle
        shell: bash
        env:
          EXPECTED_OWNER: "{github['login']}"
          REGISTRY_REF: "{generated['ref']}"
          REGISTRY_REPOSITORY: "{generated['repository']}"
        run: |
          set -euo pipefail
          context_tmp="$(mktemp -d)"
          trap 'rm -rf "$context_tmp"' EXIT
          raw_base="https://raw.githubusercontent.com/$REGISTRY_REPOSITORY/$REGISTRY_REF"
          mkdir -p "$context_tmp/config" "$context_tmp/scripts"
          for path in \\
            config/org-project-registry.yaml \\
            scripts/render_org_project_context.py \\
            scripts/validate_org_homepage.py \\
            scripts/validate_org_project_registry.py \\
            scripts/verify_org_project_context.py; do
            curl --fail --silent --show-error --location \\
              --proto '=https' --tlsv1.2 \\
              --retry 3 --retry-all-errors \\
              --connect-timeout 10 --max-time 30 \\
              "$raw_base/$path" --output "$context_tmp/$path"
          done
          python3 -m py_compile "$context_tmp"/scripts/*.py
          python3 "$context_tmp/scripts/verify_org_project_context.py" \\
            --registry "$context_tmp/config/org-project-registry.yaml" \\
            --bundle-dir . \\
            --owner "$EXPECTED_OWNER" \\
            --registry-ref "$REGISTRY_REF"
"""


def _bundle_manifest(
    context: Mapping[str, Any], bundle: Mapping[str, str]
) -> str:
    manifest = {
        "schema_version": 1,
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
    mapping = resolve_owner(registry, owner)
    context = _project_context(registry, mapping, registry_ref)
    bundle = {
        "README.md": _repository_readme(context),
        "project-context.yaml": json.dumps(context, indent=2, ensure_ascii=False) + "\n",
        "profile/README.md": _profile_readme(context),
        "agents/org-context.agent.md": _custom_agent(context),
        ".github/workflows/org-context-integrity.yml": _integrity_workflow(context),
    }
    bundle["org-context-manifest.json"] = _bundle_manifest(context, bundle)
    return bundle


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
    parser.add_argument("--registry-ref", required=True)
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
