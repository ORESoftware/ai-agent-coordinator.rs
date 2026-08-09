#!/usr/bin/env python3
"""Validate the public, test-only organization homepage canary registry.

The registry uses strict JSON syntax in a ``.yaml`` file. It is deliberately
separate from production owner routing: a test organization can describe an
acceptance relationship, but it can never become a runtime route by appearing in
this registry.
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

MAX_REGISTRY_BYTES = 256 * 1024
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 20_000
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
PROJECT_PATH_RE = re.compile(r"^/denman/project/[A-Za-z0-9._~-]+$")
FORBIDDEN_KEY_PARTS = ("token", "secret", "password", "credential")
FORBIDDEN_VALUE_MARKERS = (
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "bearer ",
    "lin_api_",
    "xoxb-",
    "xoxp-",
    "xoxa-",
    "xoxr-",
    "aws_secret_access_key",
    "-----begin private key-----",
)


class RegistryError(ValueError):
    """A fail-closed test-canary registry contract violation."""


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
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RegistryError(f"unable to read registry {path}: {exc}") from exc
    if not raw or len(raw) > MAX_REGISTRY_BYTES:
        raise RegistryError("registry size is outside the allowed range")
    if not raw.endswith(b"\n"):
        raise RegistryError("registry must end with a final newline")
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


def _array(value: Any, label: str) -> list[Any]:
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


def _uuid(value: Any, label: str) -> str:
    text = _text(value, label)
    if not UUID_RE.fullmatch(text):
        raise RegistryError(f"{label} must be a lowercase UUID")
    return text


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise RegistryError(f"{label} keys differ: missing={missing}, unknown={unknown}")


def _expected(value: Any, expected: Any, label: str) -> None:
    if value != expected:
        raise RegistryError(f"{label} must be {expected!r}")


def _https_url(value: Any, host: str, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = urlparse(text)
    except ValueError as exc:
        raise RegistryError(f"{label} is not a well-formed URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.netloc != host
        or not parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise RegistryError(f"{label} must be an https://{host}/... URL")
    return text


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
    value: Any,
    label: str,
    *,
    expected_name: str,
) -> tuple[str, str]:
    project = _object(value, label)
    _exact_keys(project, {"project_id", "project_name", "project_url"}, label)
    project_id = _uuid(project["project_id"], f"{label}.project_id")
    project_name = _text(project["project_name"], f"{label}.project_name")
    if project_name.casefold() != expected_name.casefold():
        raise RegistryError(f"{label}.project_name must identify {expected_name}")
    project_url = _https_url(
        project["project_url"], "linear.app", f"{label}.project_url"
    )
    if not PROJECT_PATH_RE.fullmatch(urlparse(project_url).path):
        raise RegistryError(
            f"{label}.project_url must identify a project in workspace denman"
        )
    return project_id, project_url


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
            "canaries",
        },
        "root",
    )
    _expected(root["schema_version"], 1, "schema_version")
    _expected(
        root["registry_id"],
        "github-linear-test-org-homepage-canaries",
        "registry_id",
    )
    observed_at = _text(root["observed_at"], "observed_at")
    try:
        parsed_date = date.fromisoformat(observed_at)
    except ValueError as exc:
        raise RegistryError("observed_at must use a real YYYY-MM-DD date") from exc
    if parsed_date.isoformat() != observed_at or not observed_at.startswith("20"):
        raise RegistryError("observed_at must use YYYY-MM-DD")

    authority = _object(root["authority"], "authority")
    _exact_keys(
        authority,
        {
            "source_of_truth",
            "registry_path",
            "expected_default_ref",
            "sync_direction",
            "drift_policy",
            "scope",
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
        "config/test-org-homepage-canaries.yaml",
        "authority.registry_path",
    )
    _expected(authority["expected_default_ref"], "main", "authority.expected_default_ref")
    _expected(
        authority["sync_direction"],
        "registry_to_test_org_github_mirrors",
        "authority.sync_direction",
    )
    _expected(authority["drift_policy"], "fail_closed", "authority.drift_policy")
    _expected(authority["scope"], "test_organizations_only", "authority.scope")
    _expected(
        authority["public_context_policy"],
        "identifiers_links_and_public_instructions_only",
        "authority.public_context_policy",
    )

    linear = _object(root["linear"], "linear")
    _exact_keys(
        linear,
        {"workspace_slug", "team_id", "team_key", "team_name"},
        "linear",
    )
    _expected(linear["workspace_slug"], "denman", "linear.workspace_slug")
    _uuid(linear["team_id"], "linear.team_id")
    _expected(linear["team_key"], "DEN", "linear.team_key")
    _expected(linear["team_name"], "Denman", "linear.team_name")

    resolution = _object(root["resolution"], "resolution")
    _exact_keys(
        resolution,
        {
            "owner_identity_key",
            "login_case_insensitive",
            "on_unmapped",
            "on_ambiguous",
            "runtime_routing",
            "production_registry_precedence",
        },
        "resolution",
    )
    _expected(
        resolution["owner_identity_key"],
        "github_account_id",
        "resolution.owner_identity_key",
    )
    _expected(
        resolution["login_case_insensitive"],
        True,
        "resolution.login_case_insensitive",
    )
    _expected(resolution["on_unmapped"], "reject", "resolution.on_unmapped")
    _expected(resolution["on_ambiguous"], "reject", "resolution.on_ambiguous")
    _expected(
        resolution["runtime_routing"],
        "forbidden",
        "resolution.runtime_routing",
    )
    _expected(
        resolution["production_registry_precedence"],
        True,
        "resolution.production_registry_precedence",
    )

    canaries = _array(root["canaries"], "canaries")
    if not canaries:
        raise RegistryError("canaries cannot be empty")

    logins: set[str] = set()
    account_ids: set[int] = set()
    installation_ids: set[int] = set()
    repositories: set[str] = set()
    github_project_urls: set[str] = set()
    linear_project_ids: set[str] = set()
    linear_project_urls: set[str] = set()
    parent_account_ids: set[int] = set()
    parent_linear_project_ids: set[str] = set()

    for index, raw_canary in enumerate(canaries):
        label = f"canaries[{index}]"
        canary = _object(raw_canary, label)
        _exact_keys(
            canary,
            {
                "github",
                "linear",
                "parent_production",
                "test_program",
                "runtime_route",
                "public_context_only",
            },
            label,
        )

        github = _object(canary["github"], f"{label}.github")
        _exact_keys(
            github,
            {
                "login",
                "account_id",
                "account_type",
                "installation_id",
                "repository",
                "github_project_url",
            },
            f"{label}.github",
        )
        login = _text(github["login"], f"{label}.github.login")
        if not LOGIN_RE.fullmatch(login) or not login.casefold().endswith("-test"):
            raise RegistryError(
                f"{label}.github.login must be a valid GitHub test organization login"
            )
        normalized_login = login.casefold()
        if normalized_login in logins:
            raise RegistryError(f"duplicate GitHub login: {login}")
        logins.add(normalized_login)

        account_id = _positive_int(github["account_id"], f"{label}.github.account_id")
        if account_id in account_ids:
            raise RegistryError(f"duplicate GitHub account ID: {account_id}")
        account_ids.add(account_id)
        _expected(
            github["account_type"],
            "Organization",
            f"{label}.github.account_type",
        )
        installation_id = _positive_int(
            github["installation_id"], f"{label}.github.installation_id"
        )
        if installation_id in installation_ids:
            raise RegistryError(f"duplicate GitHub installation ID: {installation_id}")
        installation_ids.add(installation_id)

        repository = _text(github["repository"], f"{label}.github.repository")
        if repository.casefold() != f"{login}/.github".casefold():
            raise RegistryError(
                f"{label}.github.repository must be the test organization's .github repository"
            )
        if repository.casefold() in repositories:
            raise RegistryError(f"duplicate GitHub repository: {repository}")
        repositories.add(repository.casefold())

        project_url = _https_url(
            github["github_project_url"],
            "github.com",
            f"{label}.github.github_project_url",
        )
        expected_project_path = rf"/orgs/{re.escape(login)}/projects/[1-9][0-9]*"
        if not re.fullmatch(
            expected_project_path,
            urlparse(project_url).path,
            flags=re.IGNORECASE,
        ):
            raise RegistryError(
                f"{label}.github.github_project_url must identify an organization project"
            )
        if project_url.casefold() in github_project_urls:
            raise RegistryError(f"duplicate GitHub project URL: {project_url}")
        github_project_urls.add(project_url.casefold())

        linear_project_id, linear_project_url = _validate_linear_project(
            canary["linear"],
            f"{label}.linear",
            expected_name=f"github.com/{login}",
        )
        if linear_project_id in linear_project_ids:
            raise RegistryError(f"duplicate Linear project ID: {linear_project_id}")
        if linear_project_url.casefold() in linear_project_urls:
            raise RegistryError(f"duplicate Linear project URL: {linear_project_url}")
        linear_project_ids.add(linear_project_id)
        linear_project_urls.add(linear_project_url.casefold())

        parent = _object(canary["parent_production"], f"{label}.parent_production")
        _exact_keys(
            parent,
            {
                "github_login",
                "github_account_id",
                "linear_project_id",
                "linear_project_name",
                "linear_project_url",
            },
            f"{label}.parent_production",
        )
        parent_login = _text(
            parent["github_login"], f"{label}.parent_production.github_login"
        )
        if (
            not LOGIN_RE.fullmatch(parent_login)
            or parent_login.casefold().endswith("-test")
            or parent_login.casefold() != login[:-5].casefold()
        ):
            raise RegistryError(
                f"{label}.parent_production.github_login must match the non-test parent"
            )
        parent_account_id = _positive_int(
            parent["github_account_id"],
            f"{label}.parent_production.github_account_id",
        )
        if parent_account_id == account_id:
            raise RegistryError(f"{label} test and production GitHub IDs must differ")
        if parent_account_id in parent_account_ids:
            raise RegistryError(
                f"duplicate parent production GitHub account ID: {parent_account_id}"
            )
        parent_account_ids.add(parent_account_id)
        parent_linear_project_id, _ = _validate_linear_project(
            {
                "project_id": parent["linear_project_id"],
                "project_name": parent["linear_project_name"],
                "project_url": parent["linear_project_url"],
            },
            f"{label}.parent_production.linear",
            expected_name=f"github.com/{parent_login}",
        )
        if parent_linear_project_id == linear_project_id:
            raise RegistryError(f"{label} test and production Linear IDs must differ")
        if parent_linear_project_id in parent_linear_project_ids:
            raise RegistryError(
                f"duplicate parent production Linear project ID: {parent_linear_project_id}"
            )
        parent_linear_project_ids.add(parent_linear_project_id)

        test_program = _object(canary["test_program"], f"{label}.test_program")
        _exact_keys(
            test_program,
            {"purpose", "scope", "profile_strategy"},
            f"{label}.test_program",
        )
        purpose = _text(test_program["purpose"], f"{label}.test_program.purpose")
        if len(purpose) < 40:
            raise RegistryError(f"{label}.test_program.purpose is too short")
        scope = _array(test_program["scope"], f"{label}.test_program.scope")
        if not scope or len(scope) > 20:
            raise RegistryError(f"{label}.test_program.scope has invalid length")
        normalized_scope: set[str] = set()
        for scope_index, raw_scope in enumerate(scope):
            item = _text(
                raw_scope,
                f"{label}.test_program.scope[{scope_index}]",
            )
            normalized_item = item.casefold()
            if normalized_item in normalized_scope:
                raise RegistryError(f"{label}.test_program.scope contains duplicates")
            normalized_scope.add(normalized_item)
        _expected(
            test_program["profile_strategy"],
            "preserve_specialized_acceptance_notes",
            f"{label}.test_program.profile_strategy",
        )

        if canary["runtime_route"] is not None:
            raise RegistryError(f"{label}.runtime_route must be null for test canaries")
        _expected(
            canary["public_context_only"],
            True,
            f"{label}.public_context_only",
        )

    _assert_public_safe(root)
    return {
        "canaries": len(canaries),
        "test_organizations": len(logins),
        "production_parents": len(parent_account_ids),
    }


def resolve_canary(registry: Mapping[str, Any], owner: str | int) -> dict[str, Any]:
    validate_registry(registry)
    matches: list[dict[str, Any]] = []
    for raw_canary in registry["canaries"]:
        canary = _object(raw_canary, "canary")
        github = _object(canary["github"], "canary.github")
        if isinstance(owner, int):
            if github["account_id"] == owner:
                matches.append(canary)
        elif github["login"].casefold() == str(owner).casefold():
            matches.append(canary)
    if not matches:
        raise RegistryError(f"unmapped test organization: {owner}")
    if len(matches) != 1:
        raise RegistryError(f"ambiguous test organization: {owner}")
    return matches[0]


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "registry",
        nargs="?",
        type=Path,
        default=Path("config/test-org-homepage-canaries.yaml"),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
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
                "registry_sha256": canonical_sha256(registry),
                **counts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
