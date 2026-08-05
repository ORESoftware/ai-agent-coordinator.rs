#!/usr/bin/env python3
"""Render a human- and agent-oriented organization profile from public context."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from validate_org_homepage import validate_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = ROOT / "templates" / "org-profile-readme.md"


class DuplicateKeyError(ValueError):
    """Raised when JSON input contains a duplicate object key."""


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_context(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"unable to read UTF-8 project context {path}: {exc}") from exc

    try:
        value = json.loads(text, object_pairs_hook=_object_without_duplicates)
    except (json.JSONDecodeError, DuplicateKeyError) as exc:
        raise ValueError(f"invalid project context JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("project context must be a JSON object")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_text(item, f"{label}[{index}]"))
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contains duplicate repositories")
    return result


def _selection_rule(runtime: dict[str, Any]) -> str:
    default = runtime.get("default_repository")
    if default is not None:
        default = _text(default, "runtime_route.default_repository")

    allowlist = _string_list(
        runtime.get("repository_allowlist", []),
        "runtime_route.repository_allowlist",
    )
    if default is not None and default not in allowlist:
        raise ValueError(
            "runtime_route.default_repository must appear in repository_allowlist"
        )

    if default is not None:
        rendered_allowlist = ", ".join(f"`{repository}`" for repository in allowlist)
        return (
            f"For reviewed routed work, the default repository is `{default}`. "
            f"The runtime allowlist is {rendered_allowlist}. Exact repository "
            "overrides take precedence. Ambiguous or unmapped work must stop "
            "rather than be guessed."
        )

    if allowlist:
        rendered_allowlist = ", ".join(f"`{repository}`" for repository in allowlist)
        return (
            f"No default repository is declared. The reviewed runtime allowlist is "
            f"{rendered_allowlist}; select one explicitly. Ambiguous or unmapped "
            "work must stop rather than be guessed."
        )

    return (
        "Resolve the exact repository explicitly. This organization has no reviewed "
        "default runtime repository, so ambiguous or unmapped work must stop rather "
        "than be guessed."
    )


def _operating_principles(values: list[str]) -> str:
    if not values:
        values = [
            "Preserve the organization's reviewed product, privacy, security, "
            "compatibility, data-integrity, and architecture invariants."
        ]
    return "\n".join(f"- {value.strip()}" for value in values)


def render_profile(
    context: dict[str, Any],
    template: str,
    *,
    display_name: str,
    summary: str,
    public_starting_points: list[str] | None = None,
    operating_principles: list[str] | None = None,
) -> str:
    github = _mapping(context.get("github"), "github")
    linear = _mapping(context.get("linear"), "linear")
    runtime = _mapping(context.get("runtime_route", {}), "runtime_route")

    login = _text(github.get("login"), "github.login")
    account_type = _text(github.get("account_type"), "github.account_type")
    if account_type != "Organization":
        raise ValueError("organization homepage rendering requires account_type=Organization")

    account_id = _positive_int(github.get("account_id"), "github.account_id")
    project_name = _text(linear.get("project_name"), "linear.project_name")
    project_url = _text(linear.get("project_url"), "linear.project_url")
    if not project_url.startswith("https://linear.app/"):
        raise ValueError("linear.project_url must use https://linear.app/")
    project_id = _text(linear.get("project_id"), "linear.project_id")
    team_key = _text(linear.get("team_key"), "linear.team_key")
    team_id = _text(linear.get("team_id"), "linear.team_id")

    if context.get("public_context_only") is not True:
        raise ValueError("project context must declare public_context_only=true")

    starting_points = public_starting_points or [
        "the organization's public repositories and pinned projects"
    ]
    if any(not isinstance(item, str) or not item.strip() for item in starting_points):
        raise ValueError("public starting points must be non-empty strings")

    replacements = {
        "ORG_DISPLAY_NAME": _text(display_name, "display_name"),
        "ORG_SUMMARY": _text(summary, "summary"),
        "PUBLIC_STARTING_POINTS": "; ".join(
            item.strip() for item in starting_points
        ),
        "LINEAR_PROJECT_URL": project_url,
        "ORG_LOGIN": login,
        "REPOSITORY_SELECTION_RULE": _selection_rule(runtime),
        "GITHUB_OWNER_ID": str(account_id),
        "LINEAR_PROJECT_NAME": project_name,
        "LINEAR_PROJECT_ID": project_id,
        "LINEAR_TEAM_KEY": team_key,
        "LINEAR_TEAM_ID": team_id,
        "OPERATING_PRINCIPLES": _operating_principles(
            operating_principles or []
        ),
    }

    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("template contains unknown or unrendered placeholders")
    if not rendered.endswith("\n"):
        rendered += "\n"

    errors = validate_text(rendered, expect_org=login)
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise ValueError(f"rendered profile failed validation:\n{details}")
    return rendered


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a validated public organization profile."
    )
    parser.add_argument("project_context", type=Path)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--public-starting-point", action="append", default=[])
    parser.add_argument("--operating-principle", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        context = load_context(args.project_context)
        template = args.template.read_text(encoding="utf-8")
        profile = render_profile(
            context,
            template,
            display_name=args.display_name,
            summary=args.summary,
            public_starting_points=args.public_starting_point,
            operating_principles=args.operating_principle,
        )
        write_atomic(args.output, profile)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"PASS: rendered and validated {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
