#!/usr/bin/env python3
"""Validate a public GitHub organization profile for human and agent use."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED_HEADINGS = (
    "## Start here",
    "### For people",
    "### For AI agents",
    "## Canonical identity and authority",
    "## Public context boundary",
)

REQUIRED_REFERENCES = (
    "project-context.yaml",
    "repository-relationships.json",
    "AGENTS.md",
    "ORG_CONTEXT.md",
    "CONTRIBUTING.md",
    "GOVERNANCE.md",
    "SUPPORT.md",
)

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")
CONFLICT_MARKER_RE = re.compile(
    r"(?m)^(?:<{7}|>{7}|\|{7})(?: .*)?$|^={7}$"
)
GITHUB_OWNER_ID_RE = re.compile(
    r"Immutable GitHub owner ID:\s*`([1-9][0-9]*)`",
    re.IGNORECASE,
)
LINEAR_PROJECT_ID_RE = re.compile(
    r"Immutable Linear project ID:\s*`([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})`",
    re.IGNORECASE,
)

SECRET_PATTERNS = (
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    (
        "GitHub fine-grained token",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "private key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "bearer credential",
        re.compile(r"(?i)authorization:\s*bearer\s+[A-Za-z0-9._-]{16,}"),
    ),
)


def _contains_any(lowered: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in lowered for phrase in phrases)


def _opening_context(text: str) -> str:
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        return ""

    body: list[str] = []
    for line in lines[1:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


def validate_text(text: str, *, expect_org: str | None = None) -> list[str]:
    errors: list[str] = []
    lowered = text.lower()

    if not text:
        return ["profile is empty"]
    if not text.endswith("\n"):
        errors.append("profile must end with a final newline")
    if "\x00" in text:
        errors.append("profile contains a NUL byte")
    if CONFLICT_MARKER_RE.search(text):
        errors.append("profile contains an unresolved Git conflict marker")
    if PLACEHOLDER_RE.search(text):
        errors.append("profile contains an unrendered template placeholder")

    for label, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            errors.append(f"profile contains a possible {label}")

    lines = text.splitlines()
    if not lines or not lines[0].startswith("# ") or len(lines[0][2:].strip()) < 2:
        errors.append("profile must begin with a descriptive level-one heading")

    opening = _opening_context(text)
    if len(re.sub(r"\s+", " ", opening)) < 120:
        errors.append(
            "profile needs a substantive plain-language mission or scope introduction"
        )

    for heading in REQUIRED_HEADINGS:
        if heading not in text:
            errors.append(f"profile is missing required heading: {heading}")

    for reference in REQUIRED_REFERENCES:
        if reference not in text:
            errors.append(f"profile is missing required reference: {reference}")

    if "linear.app/" not in lowered:
        errors.append("profile does not link to the canonical Linear project")
    if "/security/policy" not in lowered and "security.md" not in lowered:
        errors.append("profile does not link to a security reporting policy")

    if not GITHUB_OWNER_ID_RE.search(text):
        errors.append("profile does not publish an immutable numeric GitHub owner ID")
    if not LINEAR_PROJECT_ID_RE.search(text):
        errors.append("profile does not publish an immutable Linear project UUID")

    if "central registry" not in lowered or "authoritative" not in lowered:
        errors.append("profile does not state central-registry identity authority")
    if "repository-local" not in lowered or "implementation" not in lowered:
        errors.append("profile does not state repository-local implementation authority")

    if "ambiguous" not in lowered:
        errors.append("profile does not address ambiguous work")
    if not _contains_any(
        lowered,
        (
            "must stop",
            "stop and be reported",
            "stop rather than",
            "fail closed",
            "fail-closed",
        ),
    ):
        errors.append("profile does not fail closed on missing or ambiguous context")
    if not _contains_any(lowered, ("unmapped", "missing")):
        errors.append("profile does not address missing or unmapped context")

    if "merge base" not in lowered:
        errors.append("profile omits merge-base conflict analysis")
    if "both sides" not in lowered:
        errors.append("profile omits inspection of both conflict sides")
    if "3–10" not in text and "3-10" not in text:
        errors.append("profile omits the 3–10 relevant-commit history window")
    if "`ours`" not in text or "`theirs`" not in text:
        errors.append("profile does not reject wholesale ours/theirs resolution")
    if "external repositories" not in lowered:
        errors.append("profile omits relevant external-repository conflict context")

    for concept in ("credentials", "private", "incident", "topology"):
        if concept not in lowered:
            errors.append(f"public-context boundary omits {concept!r}")

    if "github issue" not in lowered or "linear" not in lowered:
        errors.append("profile does not preserve recoverable GitHub and Linear work context")

    if expect_org is not None:
        normalized = expect_org.strip().lower()
        if not normalized:
            errors.append("--expect-org must be a non-empty GitHub login")
        elif f"github.com/{normalized}" not in lowered:
            errors.append(
                f"profile does not contain the canonical GitHub URL for {expect_org.strip()}"
            )

    return errors


def validate_path(path: Path, *, expect_org: str | None = None) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"unable to read UTF-8 profile {path}: {exc}"]
    return validate_text(text, expect_org=expect_org)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a public organization profile for humans and AI agents."
    )
    parser.add_argument("profile", type=Path, help="path to profile/README.md")
    parser.add_argument(
        "--expect-org",
        help="expected GitHub organization login; matching is case-insensitive",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    errors = validate_path(args.profile, expect_org=args.expect_org)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"FAIL: {len(errors)} organization homepage error(s)", file=sys.stderr)
        return 1
    print(f"PASS: validated {args.profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
