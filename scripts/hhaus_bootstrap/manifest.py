from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .constants import (
    EXPECTED_BACKEND_INTERNAL_DEPENDENCIES,
    EXPECTED_BACKEND_ONLY,
    EXPECTED_EXTERNAL_DEPENDENCIES,
    EXPECTED_INTERNAL_DEPENDENCIES,
    EXPECTED_ORGANIZATION,
    EXPECTED_PLATFORM_DEPENDENCIES,
    EXPECTED_RATE_LIMIT_LAYERS,
    EXPECTED_REPOSITORIES,
    MANIFEST_PATH,
    BootstrapError,
)

def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def manifest_digest(manifest: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    with path.open("rb") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise BootstrapError("manifest root must be an object")
    return value


def _unique_strings(values: Any, field: str) -> list[str]:
    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
        raise BootstrapError(f"{field} must be a non-empty string array")
    if len(values) != len(set(values)):
        raise BootstrapError(f"{field} contains duplicates")
    return values


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 1:
        raise BootstrapError("unsupported manifest schema_version")
    if manifest.get("organization") != EXPECTED_ORGANIZATION:
        raise BootstrapError("manifest organization is not the exact H/HAUS organization")

    governance = manifest.get("governance")
    if not isinstance(governance, dict):
        raise BootstrapError("governance must be an object")
    if governance.get("visibility") != "private":
        raise BootstrapError("new H/HAUS repositories must default to private")
    if governance.get("default_branch") != "main":
        raise BootstrapError("main must remain the default branch")
    if governance.get("merge_method") != "squash":
        raise BootstrapError("bootstrap PRs must use squash merge")
    if governance.get("agents") != "https://github.com/ORESoftware/my-ai/blob/main/AGENTS.md":
        raise BootstrapError("the canonical AGENTS.md reference is missing")

    languages = _unique_strings(manifest.get("language_targets"), "language_targets")
    if len(languages) < 15:
        raise BootstrapError("at least 15 language targets are required")
    for required in ("rust", "typescript", "dart", "go", "gleam"):
        if required not in languages:
            raise BootstrapError(f"required language target is missing: {required}")

    layers = tuple(_unique_strings(manifest.get("required_rate_limit_layers"), "required_rate_limit_layers"))
    if layers != EXPECTED_RATE_LIMIT_LAYERS:
        raise BootstrapError("the five-layer rate-limit order must remain exact")

    authorities = manifest.get("contract_authorities")
    if not isinstance(authorities, dict):
        raise BootstrapError("contract_authorities must be an object")
    if authorities.get("typespec", {}).get("role") != "independent-peer-authority":
        raise BootstrapError("TypeSpec must remain an independent peer authority")
    if authorities.get("json_schema", {}).get("role") != "independent-peer-authority":
        raise BootstrapError("JSON Schema must remain an independent peer authority")
    if authorities.get("json_schema", {}).get("draft") != "2020-12":
        raise BootstrapError("JSON Schema Draft 2020-12 is required")

    platform = manifest.get("platform_dependencies")
    if not isinstance(platform, dict):
        raise BootstrapError("platform_dependencies must be an object")
    if platform != EXPECTED_PLATFORM_DEPENDENCIES:
        raise BootstrapError("platform dependency map drifted")

    repositories = manifest.get("repositories")
    if not isinstance(repositories, list):
        raise BootstrapError("repositories must be an array")
    names = [repo.get("name") for repo in repositories if isinstance(repo, dict)]
    if tuple(names) != EXPECTED_REPOSITORIES:
        raise BootstrapError("repository order or membership drifted")
    repo_by_name = {repo["name"]: repo for repo in repositories}

    graph: dict[str, list[str]] = {}
    for repo in repositories:
        if not isinstance(repo.get("description"), str) or not repo["description"].strip():
            raise BootstrapError(f"{repo['name']} has no description")
        direct = _unique_strings(repo.get("internal_dependencies"), f"{repo['name']}.internal_dependencies")
        backend = _unique_strings(
            repo.get("backend_internal_dependencies"),
            f"{repo['name']}.backend_internal_dependencies",
        )
        external = _unique_strings(repo.get("external_dependencies"), f"{repo['name']}.external_dependencies")
        if direct != EXPECTED_INTERNAL_DEPENDENCIES[repo["name"]]:
            raise BootstrapError(f"{repo['name']} internal dependency topology drifted")
        if backend != EXPECTED_BACKEND_INTERNAL_DEPENDENCIES[repo["name"]]:
            raise BootstrapError(f"{repo['name']} backend adapter topology drifted")
        if external != EXPECTED_EXTERNAL_DEPENDENCIES[repo["name"]]:
            raise BootstrapError(f"{repo['name']} external platform dependency topology drifted")
        if bool(repo.get("backend_only")) is not EXPECTED_BACKEND_ONLY[repo["name"]]:
            raise BootstrapError(f"{repo['name']} backend visibility boundary drifted")
        if "zed-pkg/zed-pkg" not in external:
            raise BootstrapError(f"{repo['name']} is not orchestrated through zed-pkg")
        for dependency in direct + backend:
            if dependency not in repo_by_name:
                raise BootstrapError(f"{repo['name']} references unknown repository {dependency}")
        if not repo.get("backend_only"):
            for dependency in direct:
                if repo_by_name[dependency].get("backend_only"):
                    raise BootstrapError(
                        f"{repo['name']} exposes backend-only dependency {dependency} on its public surface"
                    )
        if backend and repo["kind"] != "sync":
            raise BootstrapError("only the sync repository may expose a separately scoped backend adapter")
        graph[repo["name"]] = direct + backend

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise BootstrapError(f"repository dependency cycle includes {name}")
        if name in visited:
            return
        visiting.add(name)
        for dependency in graph[name]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)
