#!/usr/bin/env python3
"""Validate the lowercase hierarchical agents.md contract without mutating files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

REQUIRED_SENTENCE = "avoid git rebase in favor of git merge."
POINTER_TEXT = (
    "Canonical instructions: `../agents.md`. Also load every ancestor "
    "`agents.md` from filesystem root to `$PWD`, in root-to-leaf order."
)
POINTER_PATHS = (
    Path(".claude/CLAUDE.md"),
    Path(".gemini/GEMINI.md"),
    Path(".openai/AGENTS.md"),
)
CONFLICT_PREFIXES = (b"<<<<<<<", b"=======", b">>>>>>>")
NEGATIVE_REBASE_TERMS = (
    "avoid",
    "do not",
    "don't",
    "never",
    "must not",
    "prohibit",
    "forbid",
    "instead of",
    "rather than",
)
POSITIVE_REBASE_PATTERNS = (
    "prefer rebase",
    "prefer rebasing",
    "use rebase",
    "use git rebase",
    "should rebase",
    "may rebase",
    "can rebase",
    "allow rebase",
    "rebase is preferred",
    "rebasing is preferred",
)
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")


class DiscoveryError(RuntimeError):
    """Instruction discovery could not be completed safely."""


@dataclass(frozen=True)
class ValidationReport:
    repo_root: str
    start_dir: str
    canonical_sha256: str | None
    headings: tuple[str, ...]
    instruction_files: tuple[str, ...]
    scanned_files: int
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "repo_root": self.repo_root,
            "start_dir": self.start_dir,
            "canonical_sha256": self.canonical_sha256,
            "headings": list(self.headings),
            "instruction_files": list(self.instruction_files),
            "scanned_files": self.scanned_files,
            "errors": list(self.errors),
        }


def _resolve(path: Path, *, label: str) -> Path:
    try:
        return path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DiscoveryError(f"{label} cannot be resolved safely: {path}: {exc}") from exc


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DiscoveryError(f"instruction file is unreadable UTF-8: {path}: {exc}") from exc


def _inode_key(path: Path) -> tuple[int, int]:
    try:
        stat_result = path.stat()
    except OSError as exc:
        raise DiscoveryError(f"cannot stat instruction path {path}: {exc}") from exc
    return (stat_result.st_dev, stat_result.st_ino)


def discover_instruction_files(start_dir: Path) -> tuple[Path, ...]:
    """Walk resolved ancestors root-to-leaf and return readable lowercase agents.md files."""

    resolved = _resolve(start_dir, label="start directory")
    if resolved.is_file():
        resolved = resolved.parent
    if not resolved.is_dir():
        raise DiscoveryError(f"start path is not a directory: {resolved}")

    ancestors: list[Path] = []
    seen_directories: set[tuple[int, int]] = set()
    current = resolved
    while True:
        key = _inode_key(current)
        if key in seen_directories:
            raise DiscoveryError(f"ancestor cycle detected at {current}")
        seen_directories.add(key)
        ancestors.append(current)
        if current.parent == current:
            break
        current = _resolve(current.parent, label="ancestor directory")

    found: list[Path] = []
    seen_instructions: set[tuple[int, int]] = set()
    for directory in reversed(ancestors):
        candidate = directory / "agents.md"
        if not candidate.exists() and not candidate.is_symlink():
            continue
        resolved_candidate = _resolve(candidate, label="agents.md")
        if not resolved_candidate.is_file():
            raise DiscoveryError(f"agents.md is not a regular file: {candidate}")
        key = _inode_key(resolved_candidate)
        if key in seen_instructions:
            continue
        _read_text(resolved_candidate)
        seen_instructions.add(key)
        found.append(resolved_candidate)

    return tuple(found)


def _validate_rebase_guidance(text: str, errors: list[str]) -> None:
    if REQUIRED_SENTENCE not in text:
        errors.append(f"agents.md must contain the exact sentence: {REQUIRED_SENTENCE}")

    for line_number, line in enumerate(text.splitlines(), start=1):
        lowered = line.casefold()
        if "rebase" not in lowered and "rebasing" not in lowered:
            continue
        if REQUIRED_SENTENCE in line:
            continue
        has_positive = any(pattern in lowered for pattern in POSITIVE_REBASE_PATTERNS)
        has_negative = any(term in lowered for term in NEGATIVE_REBASE_TERMS)
        if has_positive or not has_negative:
            errors.append(
                "agents.md contains contradictory or ambiguous rebase guidance "
                f"at line {line_number}: {line.strip()}"
            )


def _validate_pointer(
    repo_root: Path,
    canonical: Path,
    relative_path: Path,
    errors: list[str],
) -> None:
    path = repo_root / relative_path
    if not path.exists() and not path.is_symlink():
        errors.append(f"missing tool pointer: {relative_path.as_posix()}")
        return

    if path.is_symlink():
        try:
            target = os.readlink(path)
        except OSError as exc:
            errors.append(f"cannot read tool pointer symlink {relative_path.as_posix()}: {exc}")
            return
        if target != "../agents.md":
            errors.append(
                f"{relative_path.as_posix()} symlink must target only ../agents.md; "
                f"found {target!r}"
            )
            return
        try:
            resolved = _resolve(path, label=relative_path.as_posix())
        except DiscoveryError as exc:
            errors.append(str(exc))
            return
        if resolved != canonical:
            errors.append(
                f"{relative_path.as_posix()} does not resolve to the canonical root agents.md"
            )
        return

    try:
        content = _read_text(path).strip()
    except DiscoveryError as exc:
        errors.append(str(exc))
        return
    if content != POINTER_TEXT:
        errors.append(
            f"{relative_path.as_posix()} must contain only the canonical ../agents.md pointer"
        )


def _iter_worktree_files(repo_root: Path) -> Iterable[Path]:
    for directory, dirnames, filenames in os.walk(repo_root, topdown=True, followlinks=False):
        dirnames[:] = [
            name
            for name in dirnames
            if name != ".git" and not (Path(directory) / name).is_symlink()
        ]
        for filename in filenames:
            path = Path(directory) / filename
            if path.is_symlink():
                continue
            yield path


def scan_conflict_markers(repo_root: Path) -> tuple[int, list[str]]:
    errors: list[str] = []
    scanned = 0
    for path in _iter_worktree_files(repo_root):
        scanned += 1
        try:
            with path.open("rb") as handle:
                prefix = handle.read(4096)
                if b"\0" in prefix:
                    continue
                handle.seek(0)
                for line_number, line in enumerate(handle, start=1):
                    if line.startswith(CONFLICT_PREFIXES):
                        errors.append(
                            "unresolved conflict marker at "
                            f"{path.relative_to(repo_root).as_posix()}:{line_number}"
                        )
        except OSError as exc:
            errors.append(
                f"cannot scan worktree file "
                f"{path.relative_to(repo_root).as_posix()}: {exc}"
            )
    return scanned, errors


def validate_repository(repo_root: Path, start_dir: Path | None = None) -> ValidationReport:
    errors: list[str] = []
    headings: tuple[str, ...] = ()
    canonical_sha256: str | None = None
    instruction_files: tuple[str, ...] = ()
    scanned_files = 0

    try:
        root = _resolve(repo_root, label="repository root")
    except DiscoveryError as exc:
        return ValidationReport(
            repo_root=str(repo_root),
            start_dir=str(start_dir or repo_root),
            canonical_sha256=None,
            headings=(),
            instruction_files=(),
            scanned_files=0,
            errors=(str(exc),),
        )

    start = start_dir or root
    try:
        resolved_start = _resolve(start, label="start directory")
    except DiscoveryError as exc:
        resolved_start = Path(start)
        errors.append(str(exc))

    canonical = root / "agents.md"
    canonical_text: str | None = None
    if not canonical.exists() and not canonical.is_symlink():
        errors.append("missing canonical lowercase root agents.md")
    elif canonical.is_symlink():
        errors.append("canonical root agents.md must be a regular file, not a symlink")
    elif not canonical.is_file():
        errors.append("canonical root agents.md is not a regular file")
    else:
        try:
            canonical_text = _read_text(canonical)
        except DiscoveryError as exc:
            errors.append(str(exc))

    if canonical_text is not None:
        canonical_sha256 = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
        headings = tuple(
            match.group(1)
            for line in canonical_text.splitlines()
            if (match := HEADING_RE.match(line))
        )
        _validate_rebase_guidance(canonical_text, errors)

    canonical_resolved: Path | None = None
    if canonical.exists() and not canonical.is_symlink():
        try:
            canonical_resolved = _resolve(canonical, label="canonical agents.md")
        except DiscoveryError as exc:
            errors.append(str(exc))

    if canonical_resolved is not None:
        for relative_path in POINTER_PATHS:
            _validate_pointer(root, canonical_resolved, relative_path, errors)

    try:
        discovered = discover_instruction_files(resolved_start)
        instruction_files = tuple(str(path) for path in discovered)
        if canonical_resolved is not None and canonical_resolved not in discovered:
            errors.append(
                "hierarchical discovery from the requested start directory did not include "
                "the canonical repository agents.md"
            )
    except DiscoveryError as exc:
        errors.append(str(exc))

    scanned_files, marker_errors = scan_conflict_markers(root)
    errors.extend(marker_errors)

    return ValidationReport(
        repo_root=str(root),
        start_dir=str(resolved_start),
        canonical_sha256=canonical_sha256,
        headings=headings,
        instruction_files=instruction_files,
        scanned_files=scanned_files,
        errors=tuple(dict.fromkeys(errors)),
    )


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate hierarchical lowercase agents.md instructions."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--start-dir", type=Path)
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a stable machine-readable report suitable for before/after comparison",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    report = validate_repository(args.repo_root, args.start_dir)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    elif report.valid:
        print(
            "agents.md contract valid: "
            f"{len(report.instruction_files)} instruction file(s), "
            f"{report.scanned_files} worktree file(s) scanned"
        )
    else:
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
