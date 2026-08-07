#!/usr/bin/env python3
"""Validate the dependency-free GitHub owner to Linear project registry.

The registry uses JSON syntax in a ``.yaml`` file. JSON is a strict YAML 1.2
subset, which keeps the artifact readable by YAML tooling while allowing agents
and CI to validate it with the Python standard library only.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

MAX_REGISTRY_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 100_000
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ISSUE_RE = re.compile(r"^DEN-[1-9][0-9]*$")
CHANNEL_RE = re.compile(r"^[CDG][A-Z0-9]+$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
REPOSITORY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
FORBIDDEN_KEY_PARTS = ("token", "secret", "password", "credential")
FORBIDDEN_VALUE_MARKERS = (
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "bearer ",
    "linear_api_key",
    "lin_api_",
    "xoxb-",
    "xoxp-",
    "xoxa-",
    "xoxr-",
    "xoxs-",
    "aws_secret_access_key",
    "-----begin private key-----",
)


class RegistryError(ValueError):
    """A fail-closed registry contract violation."""


def _object_without_duplicates(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in key
        ):
            raise RegistryError("JSON object keys contain forbidden characters")
        if key in result:
            raise RegistryError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise RegistryError(f"non-finite JSON number is forbidden: {value}")


def _assert_bounded_json(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise RegistryError("registry contains too many JSON values")
        if depth > MAX_JSON_DEPTH:
            raise RegistryError("registry nesting is too deep")
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)


def load_registry(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_REGISTRY_BYTES:
        raise RegistryError("registry size is outside the allowed range")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RegistryError("registry must be UTF-8") from exc
    if text.startswith("\ufeff"):
        raise RegistryError("registry must not contain a UTF-8 byte-order mark")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise RegistryError(
            f"registry is not strict JSON-compatible YAML at line {exc.lineno}, "
            f"column {exc.colno}"
        ) from exc
    except RecursionError as exc:
        raise RegistryError("registry nesting is too deep") from exc
    _assert_bounded_json(value)
    if not isinstance(value, dict):
        raise RegistryError("registry root must be an object")
    return value


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RegistryError(f"{label} must be an array")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise RegistryError(f"{label} must be a non-empty trimmed string")
    if any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    ):
        raise RegistryError(f"{label} contains forbidden characters")
    return value


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RegistryError(f"{label} must be a positive integer")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise RegistryError(f"{label} keys differ: missing={missing}, unknown={unknown}")


def _expected(value: Any, expected: Any, label: str) -> None:
    if value != expected:
        raise RegistryError(f"{label} must be {expected!r}")


def _uuid(value: Any, label: str) -> str:
    text = _text(value, label)
    if not UUID_RE.fullmatch(text):
        raise RegistryError(f"{label} must be a lowercase UUID")
    return text


def _https_url(value: Any, host: str, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = urlparse(text)
        hostname = parsed.hostname
    except ValueError as exc:
        raise RegistryError(f"{label} is not a well-formed URL") from exc
    if (
        parsed.scheme != "https"
        or hostname != host
        or parsed.netloc != host
        or not parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RegistryError(f"{label} must be an https://{host}/... URL")
    return text


def _repository(value: Any, label: str) -> tuple[str, str]:
    text = _text(value, label)
    if text.count("/") != 1:
        raise RegistryError(f"{label} must use owner/repository form")
    owner, name = text.split("/", 1)
    if (
        not LOGIN_RE.fullmatch(owner)
        or not REPOSITORY_NAME_RE.fullmatch(name)
        or name in {".", ".."}
    ):
        raise RegistryError(f"{label} is not a valid repository identity")
    return owner, name


def _assert_public_safe(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = key.casefold()
            if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
                raise RegistryError(f"secret-like key is forbidden at {path}.{key}")
            _assert_public_safe(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_safe(child, f"{path}[{index}]")
    elif isinstance(value, str):
        lowered = value.casefold()
        if any(marker in lowered for marker in FORBIDDEN_VALUE_MARKERS):
            raise RegistryError(f"credential-like value is forbidden at {path}")


def _validate_linear_project(
    value: Any, label: str, workspace_slug: str
) -> tuple[str, str, str]:
    project = _object(value, label)
    _exact_keys(project, {"project_id", "project_name", "project_url"}, label)
    project_id = _uuid(project["project_id"], f"{label}.project_id")
    project_name = _text(project["project_name"], f"{label}.project_name")
    project_url = _https_url(
        project["project_url"], "linear.app", f"{label}.project_url"
    )
    parsed = urlparse(project_url)
    project_path = rf"/{re.escape(workspace_slug)}/project/[A-Za-z0-9._~-]+"
    if not re.fullmatch(project_path, parsed.path):
        raise RegistryError(
            f"{label}.project_url must identify a project in workspace {workspace_slug}"
        )
    return project_id, project_name, project_url


def validate_registry(registry: Mapping[str, Any]) -> dict[str, int]:
    root = _object(registry, "root")
    _exact_keys(
        root,
        {
            "schema_version",
            "registry_id",
            "observed_at",
            "authority",
            "linear",
            "resolution",
            "runtime_routing_registry",
            "mappings",
            "repository_overrides",
            "unmapped_installed_organizations",
        },
        "root",
    )
    _expected(root["schema_version"], 1, "schema_version")
    _expected(root["registry_id"], "github-linear-org-projects", "registry_id")
    observed_at = _text(root["observed_at"], "observed_at")
    try:
        parsed_observed_at = date.fromisoformat(observed_at)
    except ValueError as exc:
        raise RegistryError("observed_at must use a real YYYY-MM-DD date") from exc
    if parsed_observed_at.isoformat() != observed_at or not observed_at.startswith("20"):
        raise RegistryError("observed_at must use YYYY-MM-DD")

    authority = _object(root["authority"], "authority")
    _exact_keys(
        authority,
        {
            "source_of_truth",
            "registry_path",
            "expected_default_ref",
            "sync_direction",
            "github_org_context_path",
            "linear_document_title",
            "drift_policy",
            "public_context_policy",
        },
        "authority",
    )
    _expected(
        authority["source_of_truth"],
        "ORESoftware/ai-agent-coordinator.rs",
        "authority.source_of_truth",
    )
    _expected(
        authority["registry_path"],
        "config/org-project-registry.yaml",
        "authority.registry_path",
    )
    _expected(authority["expected_default_ref"], "main", "authority.expected_default_ref")
    _expected(
        authority["sync_direction"],
        "registry_to_github_and_linear_mirrors",
        "authority.sync_direction",
    )
    _expected(
        authority["github_org_context_path"],
        "project-context.yaml",
        "authority.github_org_context_path",
    )
    _expected(
        authority["linear_document_title"],
        "AI Agent Context",
        "authority.linear_document_title",
    )
    _expected(authority["drift_policy"], "fail_closed", "authority.drift_policy")
    _expected(
        authority["public_context_policy"],
        "identifiers_links_and_public_instructions_only",
        "authority.public_context_policy",
    )

    linear = _object(root["linear"], "linear")
    _exact_keys(linear, {"workspace_slug", "team_id", "team_key", "team_name"}, "linear")
    _expected(linear["workspace_slug"], "denman", "linear.workspace_slug")
    _uuid(linear["team_id"], "linear.team_id")
    _expected(linear["team_key"], "DEN", "linear.team_key")
    _expected(linear["team_name"], "Denman", "linear.team_name")

    resolution = _object(root["resolution"], "resolution")
    _exact_keys(
        resolution,
        {
            "owner_identity_key",
            "repository_override_precedence",
            "login_aliases_case_insensitive",
            "on_unmapped",
            "on_ambiguous",
            "repository_selection",
        },
        "resolution",
    )
    _expected(resolution["owner_identity_key"], "github_account_id", "resolution.owner_identity_key")
    _expected(resolution["repository_override_precedence"], True, "resolution.repository_override_precedence")
    _expected(resolution["login_aliases_case_insensitive"], True, "resolution.login_aliases_case_insensitive")
    _expected(resolution["on_unmapped"], "reject", "resolution.on_unmapped")
    _expected(resolution["on_ambiguous"], "reject", "resolution.on_ambiguous")
    _expected(
        resolution["repository_selection"],
        "explicit_or_runtime_allowlist",
        "resolution.repository_selection",
    )

    runtime = _object(root["runtime_routing_registry"], "runtime_routing_registry")
    _exact_keys(
        runtime,
        {
            "repository",
            "path",
            "pull_request",
            "ref",
            "commit",
            "file_blob_sha",
            "status",
            "linear_issue",
        },
        "runtime_routing_registry",
    )
    _expected(runtime["repository"], "ORESoftware/ai-agent-bridge.rs", "runtime_routing_registry.repository")
    _expected(runtime["path"], "config/alex-main-agent.channels.json", "runtime_routing_registry.path")
    _positive_int(runtime["pull_request"], "runtime_routing_registry.pull_request")
    _text(runtime["ref"], "runtime_routing_registry.ref")
    if not SHA_RE.fullmatch(_text(runtime["commit"], "runtime_routing_registry.commit")):
        raise RegistryError("runtime_routing_registry.commit must be a lowercase 40-character SHA")
    if not SHA_RE.fullmatch(_text(runtime["file_blob_sha"], "runtime_routing_registry.file_blob_sha")):
        raise RegistryError("runtime_routing_registry.file_blob_sha must be a lowercase 40-character SHA")
    if runtime["status"] not in {"pending_merge", "active"}:
        raise RegistryError("runtime_routing_registry.status is unsupported")
    if not ISSUE_RE.fullmatch(_text(runtime["linear_issue"], "runtime_routing_registry.linear_issue")):
        raise RegistryError("runtime_routing_registry.linear_issue is invalid")

    mappings = _list(root["mappings"], "mappings")
    if not mappings:
        raise RegistryError("mappings cannot be empty")
    logins: set[str] = set()
    account_ids: set[int] = set()
    installation_ids: set[int] = set()
    aliases: dict[str, str] = {}
    owner_project_ids: set[str] = set()
    project_urls: set[str] = set()
    runtime_repositories: set[str] = set()
    runtime_route_count = 0

    for index, raw_mapping in enumerate(mappings):
        label = f"mappings[{index}]"
        mapping = _object(raw_mapping, label)
        _exact_keys(mapping, {"github", "github_app", "linear", "runtime_route"}, label)

        github = _object(mapping["github"], f"{label}.github")
        _exact_keys(github, {"login", "account_id", "account_type", "aliases"}, f"{label}.github")
        login = _text(github["login"], f"{label}.github.login")
        if not LOGIN_RE.fullmatch(login):
            raise RegistryError(f"{label}.github.login is invalid")
        normalized_login = login.casefold()
        if normalized_login in logins:
            raise RegistryError(f"duplicate GitHub login: {login}")
        logins.add(normalized_login)
        account_id = _positive_int(github["account_id"], f"{label}.github.account_id")
        if account_id in account_ids:
            raise RegistryError(f"duplicate GitHub account ID: {account_id}")
        account_ids.add(account_id)
        if github["account_type"] not in {"Organization", "User"}:
            raise RegistryError(f"{label}.github.account_type is unsupported")

        mapping_aliases = _list(github["aliases"], f"{label}.github.aliases")
        normalized_aliases: set[str] = set()
        for alias_index, raw_alias in enumerate(mapping_aliases):
            alias = _text(raw_alias, f"{label}.github.aliases[{alias_index}]").casefold()
            if alias in normalized_aliases:
                raise RegistryError(f"duplicate alias inside {label}: {alias}")
            if alias in aliases:
                raise RegistryError(
                    f"alias {alias!r} maps to both {aliases[alias]} and {login}"
                )
            normalized_aliases.add(alias)
            aliases[alias] = login
        required_aliases = {normalized_login, f"github.com/{normalized_login}"}
        if not required_aliases.issubset(normalized_aliases):
            raise RegistryError(f"{label} is missing its canonical aliases")

        app = _object(mapping["github_app"], f"{label}.github_app")
        _exact_keys(app, {"installation_id", "observed_at"}, f"{label}.github_app")
        installation_id = _positive_int(app["installation_id"], f"{label}.github_app.installation_id")
        if installation_id in installation_ids:
            raise RegistryError(f"duplicate GitHub App installation ID: {installation_id}")
        installation_ids.add(installation_id)
        _expected(app["observed_at"], observed_at, f"{label}.github_app.observed_at")

        project_id, _, project_url = _validate_linear_project(
            mapping["linear"], f"{label}.linear", linear["workspace_slug"]
        )
        if project_id in owner_project_ids:
            raise RegistryError(f"duplicate owner-level Linear project ID: {project_id}")
        owner_project_ids.add(project_id)
        normalized_project_url = project_url.casefold()
        if normalized_project_url in project_urls:
            raise RegistryError(f"duplicate Linear project URL: {project_url}")
        project_urls.add(normalized_project_url)

        route = mapping["runtime_route"]
        if route is not None:
            runtime_route_count += 1
            route = _object(route, f"{label}.runtime_route")
            _exact_keys(
                route,
                {"slack_channel_id", "default_repository", "repository_allowlist", "source_linear_issue"},
                f"{label}.runtime_route",
            )
            if not CHANNEL_RE.fullmatch(_text(route["slack_channel_id"], f"{label}.runtime_route.slack_channel_id")):
                raise RegistryError(f"{label}.runtime_route.slack_channel_id is invalid")
            default_repository = _text(route["default_repository"], f"{label}.runtime_route.default_repository")
            allowlist = _list(route["repository_allowlist"], f"{label}.runtime_route.repository_allowlist")
            if not allowlist or default_repository not in allowlist:
                raise RegistryError(f"{label}.runtime_route default is not allowlisted")
            for repo_index, raw_repository in enumerate(allowlist):
                owner, _ = _repository(raw_repository, f"{label}.runtime_route.repository_allowlist[{repo_index}]")
                if owner.casefold() != normalized_login:
                    raise RegistryError(f"{label}.runtime_route escapes its GitHub owner")
                normalized_repository = raw_repository.casefold()
                if normalized_repository in runtime_repositories:
                    raise RegistryError(f"runtime repository is duplicated: {raw_repository}")
                runtime_repositories.add(normalized_repository)
            if not ISSUE_RE.fullmatch(_text(route["source_linear_issue"], f"{label}.runtime_route.source_linear_issue")):
                raise RegistryError(f"{label}.runtime_route.source_linear_issue is invalid")

    overrides = _list(root["repository_overrides"], "repository_overrides")
    override_repositories: set[str] = set()
    override_project_ids: set[str] = set()
    for index, raw_override in enumerate(overrides):
        label = f"repository_overrides[{index}]"
        override = _object(raw_override, label)
        _exact_keys(override, {"repository", "linear"}, label)
        owner, _ = _repository(override["repository"], f"{label}.repository")
        if owner.casefold() not in logins:
            raise RegistryError(f"{label} references an unmapped GitHub owner")
        normalized_repository = override["repository"].casefold()
        if normalized_repository in override_repositories:
            raise RegistryError(f"duplicate repository override: {override['repository']}")
        override_repositories.add(normalized_repository)
        project_id, _, project_url = _validate_linear_project(
            override["linear"], f"{label}.linear", linear["workspace_slug"]
        )
        if project_id in owner_project_ids or project_id in override_project_ids:
            raise RegistryError(f"duplicate Linear project identity: {project_id}")
        override_project_ids.add(project_id)
        normalized_project_url = project_url.casefold()
        if normalized_project_url in project_urls:
            raise RegistryError(f"duplicate Linear project URL: {project_url}")
        project_urls.add(normalized_project_url)

    unmapped = _list(root["unmapped_installed_organizations"], "unmapped_installed_organizations")
    unmapped_logins: set[str] = set()
    unmapped_ids: set[int] = set()
    for index, raw_gap in enumerate(unmapped):
        label = f"unmapped_installed_organizations[{index}]"
        gap = _object(raw_gap, label)
        _exact_keys(gap, {"github", "github_app", "reason", "linear_issue"}, label)
        github = _object(gap["github"], f"{label}.github")
        _exact_keys(github, {"login", "account_id", "account_type"}, f"{label}.github")
        login = _text(github["login"], f"{label}.github.login")
        normalized_login = login.casefold()
        if not LOGIN_RE.fullmatch(login) or normalized_login in logins or normalized_login in unmapped_logins:
            raise RegistryError(f"{label}.github.login collides or is invalid")
        unmapped_logins.add(normalized_login)
        account_id = _positive_int(github["account_id"], f"{label}.github.account_id")
        if account_id in account_ids or account_id in unmapped_ids:
            raise RegistryError(f"{label}.github.account_id collides")
        unmapped_ids.add(account_id)
        _expected(github["account_type"], "Organization", f"{label}.github.account_type")
        app = _object(gap["github_app"], f"{label}.github_app")
        _exact_keys(app, {"installation_id", "observed_at"}, f"{label}.github_app")
        installation_id = _positive_int(app["installation_id"], f"{label}.github_app.installation_id")
        if installation_id in installation_ids:
            raise RegistryError(f"{label}.github_app.installation_id collides")
        installation_ids.add(installation_id)
        _expected(app["observed_at"], observed_at, f"{label}.github_app.observed_at")
        if gap["reason"] not in {"product_identity_unresolved", "no_canonical_linear_project"}:
            raise RegistryError(f"{label}.reason is unsupported")
        if gap["linear_issue"] is not None and not ISSUE_RE.fullmatch(
            _text(gap["linear_issue"], f"{label}.linear_issue")
        ):
            raise RegistryError(f"{label}.linear_issue is invalid")
        if gap["reason"] == "product_identity_unresolved" and gap["linear_issue"] is None:
            raise RegistryError(f"{label} must identify its resolution issue")

    _assert_public_safe(root)
    return {
        "mappings": len(mappings),
        "repository_overrides": len(overrides),
        "runtime_routes": runtime_route_count,
        "unmapped": len(unmapped),
    }


def resolve_owner(registry: Mapping[str, Any], owner: str | int) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for raw_mapping in registry["mappings"]:
        mapping = _object(raw_mapping, "mapping")
        github = _object(mapping["github"], "mapping.github")
        if isinstance(owner, int) and github["account_id"] == owner:
            matches.append(mapping)
        elif isinstance(owner, str):
            needle = owner.strip().casefold()
            if needle in {alias.casefold() for alias in github["aliases"]}:
                matches.append(mapping)
    if len(matches) != 1:
        raise RegistryError(f"owner resolution returned {len(matches)} matches")
    return matches[0]


def resolve_project(
    registry: Mapping[str, Any], owner: str | int, repository: str | None = None
) -> dict[str, Any]:
    mapping = resolve_owner(registry, owner)
    if repository is not None:
        repository_owner, _ = _repository(repository, "repository")
        expected_owner = mapping["github"]["login"]
        if repository_owner.casefold() != expected_owner.casefold():
            raise RegistryError(
                "repository owner does not match the resolved GitHub owner"
            )
        normalized = repository.casefold()
        override_matches = [
            override
            for override in registry["repository_overrides"]
            if override["repository"].casefold() == normalized
        ]
        if len(override_matches) > 1:
            raise RegistryError("repository override resolution is ambiguous")
        if override_matches:
            return override_matches[0]["linear"]
    return mapping["linear"]


def resolve_runtime_repository(
    registry: Mapping[str, Any], owner: str | int, repository: str | None = None
) -> str:
    """Resolve only a reviewed runtime repository, never an arbitrary org repo."""

    mapping = resolve_owner(registry, owner)
    route = mapping["runtime_route"]
    if route is None:
        raise RegistryError("owner has no reviewed runtime route")
    candidate = route["default_repository"] if repository is None else repository
    repository_owner, _ = _repository(candidate, "repository")
    expected_owner = mapping["github"]["login"]
    if repository_owner.casefold() != expected_owner.casefold():
        raise RegistryError("runtime repository escapes the resolved GitHub owner")
    matches = [
        allowed
        for allowed in route["repository_allowlist"]
        if allowed.casefold() == candidate.casefold()
    ]
    if len(matches) != 1:
        raise RegistryError("runtime repository is not in the reviewed allowlist")
    return matches[0]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "registry",
        nargs="?",
        type=Path,
        default=Path("config/org-project-registry.yaml"),
    )
    args = parser.parse_args(argv)
    try:
        registry = load_registry(args.registry)
        counts = validate_registry(registry)
    except (OSError, RegistryError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "pass",
                "canonical_sha256": canonical_sha256(registry),
                **counts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
