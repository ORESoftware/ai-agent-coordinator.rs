#!/usr/bin/env python3
"""Verify one organization relationship declaration against its project context."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

EXPECTED_REGISTRY_REPOSITORY = "ORESoftware/ai-agent-coordinator.rs"
EXPECTED_REGISTRY_PATH = "config/org-project-registry.yaml"
MANDATORY_DIRECTIVE = (
    "resolve any and all git conflicts semantically, will full context, even looking back "
    "3-10 commits in git log history for more context - never hastily pick sides in a conflict "
    "but merge things conceptually, using max context and complete conceptual awareness for a "
    "given github organization's repos and external org repos too"
)

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_PATTERNS = (
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("bearer credential", re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._-]{16,}")),
)

EXPECTED_TOP_LEVEL_KEYS = {
    "generated_from",
    "git_conflict_resolution",
    "github",
    "governance",
    "linear",
    "relationships",
    "repository_selection",
    "schema_version",
}
REQUIRED_CONTEXT_SCOPE = {
    "conflicted_repository",
    "same_github_organization_repositories",
    "relevant_external_github_organization_repositories",
    "linear_project_context",
    "pull_requests_issues_architecture_decisions_tests_and_docs",
}
REQUIRED_FORBIDDEN_SHORTCUTS = {
    "wholesale_ours",
    "wholesale_theirs",
    "wholesale_current",
    "wholesale_incoming",
    "discarding_one_side_without_conceptual_analysis",
}


class RelationshipValidationError(ValueError):
    """Raised for a malformed or inconsistent relationship mirror."""


def _load_json(path: Path) -> tuple[dict[str, Any], str]:
    if path.is_symlink():
        raise RelationshipValidationError(f"refusing symlink: {path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RelationshipValidationError(f"unable to read {path.name}: {exc}") from exc
    if not text.endswith("\n"):
        raise RelationshipValidationError(f"missing final newline: {path.name}")
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise RelationshipValidationError(f"possible {label} in {path.name}")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RelationshipValidationError(f"invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise RelationshipValidationError(f"expected JSON object in {path.name}")
    return value, text


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RelationshipValidationError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RelationshipValidationError(f"{label} must be a non-empty string")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RelationshipValidationError(f"{label} must be an array")
    return value


def _context_source(context: Mapping[str, Any]) -> Mapping[str, Any]:
    generated = context.get("generated_from")
    derived = context.get("derived_from")
    if generated is not None and derived is not None:
        raise RelationshipValidationError(
            "project-context.yaml cannot contain both generated_from and derived_from"
        )
    return _mapping(generated if generated is not None else derived, "project context source")


def _relationship_index(
    relationships: Sequence[Any], expected_source: str
) -> dict[str, list[Mapping[str, Any]]]:
    by_kind: dict[str, list[Mapping[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for offset, raw in enumerate(relationships):
        relationship = _mapping(raw, f"relationships[{offset}]")
        kind = _string(relationship.get("kind"), f"relationships[{offset}].kind")
        source = _mapping(relationship.get("source"), f"relationships[{offset}].source")
        target = _mapping(relationship.get("target"), f"relationships[{offset}].target")
        source_id = _string(source.get("id"), f"relationships[{offset}].source.id")
        target_id = _string(target.get("id"), f"relationships[{offset}].target.id")
        if source_id != expected_source or source.get("type") != "github_repository":
            raise RelationshipValidationError(
                f"relationship {kind!r} does not originate from {expected_source}"
            )
        key = (kind, source_id, target_id)
        if key in seen:
            raise RelationshipValidationError(f"duplicate relationship: {key}")
        seen.add(key)
        by_kind.setdefault(kind, []).append(relationship)
    return by_kind


def _require_single(
    index: Mapping[str, list[Mapping[str, Any]]], kind: str
) -> Mapping[str, Any]:
    matches = index.get(kind, [])
    if len(matches) != 1:
        raise RelationshipValidationError(
            f"expected exactly one {kind!r} relationship, found {len(matches)}"
        )
    return matches[0]


def _validate_conflict_policy(
    relationship_policy: Mapping[str, Any], context_policy: Mapping[str, Any]
) -> None:
    for label, policy in (
        ("repository relationship", relationship_policy),
        ("project context", context_policy),
    ):
        if policy.get("mode") != "semantic_conceptual_merge":
            raise RelationshipValidationError(f"{label} conflict mode is not semantic")
        if policy.get("directive_verbatim") != MANDATORY_DIRECTIVE:
            raise RelationshipValidationError(
                f"{label} does not preserve the mandatory directive verbatim"
            )
        history = _mapping(policy.get("history_lookback_commits"), f"{label} history")
        if history.get("minimum") != 3 or history.get("maximum") != 10:
            raise RelationshipValidationError(f"{label} must require a 3–10 commit window")
        for key in (
            "when_available",
            "inspect_both_sides",
            "inspect_merge_base",
            "path_scoped_history",
        ):
            if history.get(key) is not True:
                raise RelationshipValidationError(f"{label} history must set {key}=true")
        scope = set(_list(policy.get("context_scope"), f"{label} context_scope"))
        if not REQUIRED_CONTEXT_SCOPE.issubset(scope):
            raise RelationshipValidationError(f"{label} omits required context scope")
        shortcuts = set(
            _list(policy.get("forbidden_shortcuts"), f"{label} forbidden_shortcuts")
        )
        if not REQUIRED_FORBIDDEN_SHORTCUTS.issubset(shortcuts):
            raise RelationshipValidationError(f"{label} permits a forbidden shortcut")

    if relationship_policy.get("directive_verbatim") != context_policy.get(
        "directive_verbatim"
    ):
        raise RelationshipValidationError("conflict policy drift between relationship and context")


def validate(
    relationship_path: Path,
    context_path: Path,
    *,
    expected_owner: str,
    expected_registry_ref: str,
) -> dict[str, Any]:
    relationship_path = relationship_path.absolute()
    context_path = context_path.absolute()
    if not COMMIT_RE.fullmatch(expected_registry_ref):
        raise RelationshipValidationError(
            "expected registry ref must be an immutable lowercase commit SHA"
        )
    if not expected_owner or expected_owner.strip() != expected_owner:
        raise RelationshipValidationError("expected owner must be an exact non-empty login")
    relationships_doc, relationship_text = _load_json(relationship_path)
    context, _ = _load_json(context_path)

    if set(relationships_doc) != EXPECTED_TOP_LEVEL_KEYS:
        raise RelationshipValidationError("repository-relationships.json has unexpected keys")
    if relationships_doc.get("schema_version") != 1:
        raise RelationshipValidationError("unsupported relationship schema version")
    canonical = json.dumps(
        relationships_doc, indent=2, ensure_ascii=False, sort_keys=True
    ) + "\n"
    if relationship_text != canonical:
        raise RelationshipValidationError(
            "repository-relationships.json is not deterministic canonical JSON"
        )

    generated = _mapping(relationships_doc.get("generated_from"), "generated_from")
    registry_ref = _string(generated.get("ref"), "generated_from.ref")
    if not COMMIT_RE.fullmatch(registry_ref):
        raise RelationshipValidationError("generated_from.ref is not an immutable commit")
    if registry_ref != expected_registry_ref:
        raise RelationshipValidationError(
            f"relationship mirror is not pinned to expected registry ref {expected_registry_ref}"
        )
    if generated.get("ref_type") != "commit" or generated.get("immutable") is not True:
        raise RelationshipValidationError("generated source is not marked immutable")
    if generated.get("repository") != EXPECTED_REGISTRY_REPOSITORY:
        raise RelationshipValidationError("unexpected central registry repository")
    if generated.get("path") != EXPECTED_REGISTRY_PATH:
        raise RelationshipValidationError("unexpected central registry path")
    canonical_sha = _string(
        generated.get("canonical_sha256"), "generated_from.canonical_sha256"
    )
    if not SHA256_RE.fullmatch(canonical_sha):
        raise RelationshipValidationError("invalid central registry SHA-256")
    expected_url = (
        f"https://github.com/{EXPECTED_REGISTRY_REPOSITORY}/blob/"
        f"{registry_ref}/{EXPECTED_REGISTRY_PATH}"
    )
    if generated.get("url") != expected_url:
        raise RelationshipValidationError("generated source URL does not match immutable source")

    source = _context_source(context)
    if source.get("repository") != EXPECTED_REGISTRY_REPOSITORY:
        raise RelationshipValidationError("project context uses a different registry repository")
    if source.get("path") != EXPECTED_REGISTRY_PATH:
        raise RelationshipValidationError("project context uses a different registry path")
    if source.get("canonical_sha256") != canonical_sha:
        raise RelationshipValidationError("central registry digest drift between mirrors")

    github = _mapping(relationships_doc.get("github"), "github")
    context_github = _mapping(context.get("github"), "project context github")
    owner = _string(github.get("login"), "github.login")
    if owner != expected_owner:
        raise RelationshipValidationError(
            f"relationship owner {owner!r} does not match expected owner {expected_owner!r}"
        )
    account_id = github.get("account_id")
    if not isinstance(account_id, int) or account_id <= 0:
        raise RelationshipValidationError("github.account_id must be a positive integer")
    if github.get("account_type") != "Organization":
        raise RelationshipValidationError("relationship mirror must target an organization")
    for key in ("login", "account_id", "account_type"):
        if github.get(key) != context_github.get(key):
            raise RelationshipValidationError(f"GitHub identity drift for {key}")

    linear = _mapping(relationships_doc.get("linear"), "linear")
    context_linear = _mapping(context.get("linear"), "project context linear")
    for key in (
        "project_id",
        "project_name",
        "project_url",
        "workspace_slug",
        "team_id",
        "team_key",
    ):
        if linear.get(key) != context_linear.get(key):
            raise RelationshipValidationError(f"Linear identity drift for {key}")

    expected_repo = f"{owner}/.github"
    governance = _mapping(relationships_doc.get("governance"), "governance")
    if governance.get("repository") != expected_repo:
        raise RelationshipValidationError("governance repository does not match owner")
    if governance.get("repository_scope") != f"{owner}/*":
        raise RelationshipValidationError("governance repository scope does not match owner")
    if governance.get("automatic_agent_instruction_inheritance") is not False:
        raise RelationshipValidationError(
            "automatic agent instruction inheritance must remain explicitly false"
        )
    if governance.get("repository_local_instruction_mirror_required") is not True:
        raise RelationshipValidationError("repository-local instruction mirrors must be required")
    if governance.get("public_context_only") is not True:
        raise RelationshipValidationError("governance mirror must remain public-context-only")

    relationship_policy = _mapping(
        relationships_doc.get("git_conflict_resolution"), "git conflict policy"
    )
    context_policy = _mapping(
        context.get("git_conflict_resolution"), "project context git conflict policy"
    )
    _validate_conflict_policy(relationship_policy, context_policy)

    selection = _mapping(
        relationships_doc.get("repository_selection"), "repository_selection"
    )
    resolution = _mapping(context.get("resolution"), "project context resolution")
    if selection.get("policy") != resolution.get("repository_selection"):
        raise RelationshipValidationError("repository selection policy drift")
    if selection.get("on_unmapped") != resolution.get("on_unmapped"):
        raise RelationshipValidationError("unmapped handling drift")
    if selection.get("on_ambiguous") != resolution.get("on_ambiguous"):
        raise RelationshipValidationError("ambiguous handling drift")
    if selection.get("unregistered_dependencies") != "unknown_not_assumed":
        raise RelationshipValidationError("unregistered dependencies must remain unknown")

    runtime = context.get("runtime_route")
    if runtime is None:
        expected_default = None
        expected_allowlist: list[str] = []
    else:
        runtime_mapping = _mapping(runtime, "runtime_route")
        expected_default = runtime_mapping.get("default_repository")
        expected_allowlist = _list(
            runtime_mapping.get("repository_allowlist"),
            "runtime_route.repository_allowlist",
        )
    if selection.get("default_repository") != expected_default:
        raise RelationshipValidationError("default runtime repository drift")
    if selection.get("runtime_allowlist") != expected_allowlist:
        raise RelationshipValidationError("runtime allowlist drift")

    relationship_index = _relationship_index(
        _list(relationships_doc.get("relationships"), "relationships"), expected_repo
    )
    generated_relationship = _require_single(relationship_index, "generated_from")
    generated_target = _mapping(generated_relationship.get("target"), "generated target")
    expected_generated_id = (
        f"{EXPECTED_REGISTRY_REPOSITORY}:{EXPECTED_REGISTRY_PATH}@{registry_ref}"
    )
    if generated_target.get("id") != expected_generated_id or generated_target.get(
        "type"
    ) != "github_file":
        raise RelationshipValidationError("generated_from relationship target is incorrect")

    owner_relationship = _require_single(
        relationship_index, "governs_public_context_for"
    )
    owner_target = _mapping(owner_relationship.get("target"), "owner target")
    if (
        owner_target.get("id") != owner
        or owner_target.get("type") != "github_owner"
        or owner_target.get("account_id") != account_id
        or owner_target.get("account_type") != "Organization"
    ):
        raise RelationshipValidationError("governed owner relationship is incorrect")

    linear_relationship = _require_single(relationship_index, "mirrors_linear_project")
    linear_target = _mapping(linear_relationship.get("target"), "Linear target")
    if (
        linear_target.get("id") != linear.get("project_id")
        or linear_target.get("name") != linear.get("project_name")
        or linear_target.get("url") != linear.get("project_url")
        or linear_target.get("type") != "linear_project"
    ):
        raise RelationshipValidationError("Linear project relationship is incorrect")

    default_relationships = relationship_index.get("defaults_runtime_routing_to", [])
    permit_relationships = relationship_index.get("permits_runtime_routing_to", [])
    if expected_default is None:
        if default_relationships or permit_relationships:
            raise RelationshipValidationError("runtime relationships were invented")
    else:
        if len(default_relationships) != 1:
            raise RelationshipValidationError("expected one default runtime relationship")
        default_target = _mapping(
            default_relationships[0].get("target"), "default runtime target"
        )
        if default_target.get("id") != expected_default:
            raise RelationshipValidationError("default runtime relationship drift")
        permitted = {
            _mapping(item.get("target"), "permitted runtime target").get("id")
            for item in permit_relationships
        }
        if permitted != set(expected_allowlist):
            raise RelationshipValidationError("permitted runtime relationship drift")

    return {
        "status": "pass",
        "owner": owner,
        "github_account_id": account_id,
        "linear_project_id": linear.get("project_id"),
        "registry_ref": registry_ref,
        "relationship_count": len(_list(relationships_doc.get("relationships"), "relationships")),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--relationships",
        type=Path,
        default=Path("repository-relationships.json"),
    )
    parser.add_argument(
        "--project-context",
        type=Path,
        default=Path("project-context.yaml"),
    )
    parser.add_argument("--expected-owner", required=True)
    parser.add_argument("--expected-registry-ref", required=True)
    args = parser.parse_args(argv)
    try:
        result = validate(
            args.relationships,
            args.project_context,
            expected_owner=args.expected_owner,
            expected_registry_ref=args.expected_registry_ref,
        )
    except (OSError, RelationshipValidationError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
