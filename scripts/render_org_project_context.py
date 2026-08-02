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
        },
        "public_context_only": True,
    }


def _profile_readme(context: Mapping[str, Any]) -> str:
    github = context["github"]
    linear = context["linear"]
    generated = context["generated_from"]
    mirrors = context["mirrors"]
    route = context["runtime_route"]
    route_text = (
        f"The reviewed runtime entry defaults to "
        f"[`{route['default_repository']}`](https://github.com/{route['default_repository']}) "
        "within its explicit allowlist."
        if route
        else "No default repository is declared; agents must resolve the exact repository and fail closed on ambiguity."
    )
    project_name = _markdown_link_text(linear["project_name"])
    return f"""# {github['login']}

This organization is mapped to the Linear project [{project_name}]({linear['project_url']}).

## AI agent context

- GitHub owner ID: `{github['account_id']}`
- Linear project ID: `{linear['project_id']}`
- Linear team: `{linear['team_key']}` (`{linear['team_id']}`)
- Machine-readable context: [`project-context.yaml`]({mirrors['github_project_context_url']})
- Canonical registry: [`{generated['repository']}/{generated['path']}`]({generated['url']})

{route_text}

Repository-local `AGENTS.md`, `agents.md`, and tool instructions remain authoritative for build, test, and implementation details. The central registry remains authoritative for GitHub/Linear identity and routing. Unmapped or ambiguous work must be rejected rather than guessed.

This public repository contains identifiers, links, and public operating guidance only. Do not place credentials, private customer data, or private operational details here.
"""


def _repository_readme(context: Mapping[str, Any]) -> str:
    github = context["github"]
    return f"""# {github['login']} organization context

This special public `.github` repository is the discoverable organization anchor for humans and AI agents.

- `profile/README.md` is the visible organization profile.
- `project-context.yaml` is the generated GitHub owner ↔ Linear project mapping.
- `org-context-manifest.json` records deterministic SHA-256 hashes for every other managed file.
- `agents/org-context.agent.md` is the organization-level GitHub Copilot custom-agent profile.
- `.github/workflows/org-context-integrity.yml` verifies this mirror against its immutable central registry commit.

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
