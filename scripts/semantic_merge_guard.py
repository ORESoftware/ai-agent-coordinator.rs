#!/usr/bin/env python3
"""Preview a Git merge in an isolated worktree and fail closed on ambiguity.

The guard never updates a branch or creates a commit.  It records only Git object
identities, paths, counts, and validation findings; source contents are never
serialized into the review artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

EXIT_OK = 0
EXIT_REVIEW_REQUIRED = 2
EXIT_ERROR = 3
MAX_SCAN_BYTES = 2 * 1024 * 1024
CONFLICT_MARKER_RE = re.compile(r"^(?:<<<<<<<(?: .*)?|>>>>>>>?(?: .*)?|\|\|\|\|\|\|\|(?: .*)?)$")
TOML_TABLE_RE = re.compile(r"^\s*\[([^\[\]]+)\]\s*(?:#.*)?$")
RUST_FIELD_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?!:).+$"
)
RUST_MOD_RE = re.compile(r"^\s*(?:pub\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class GuardError(RuntimeError):
    """A fail-closed operational error."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def run(
    args: Sequence[str],
    *,
    cwd: Path,
    check: bool = True,
    input_bytes: bytes | None = None,
) -> CommandResult:
    proc = subprocess.run(
        list(args),
        cwd=cwd,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    result = CommandResult(proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        command = " ".join(args[:3])
        raise GuardError(f"command failed ({proc.returncode}): {command}")
    return result


def text(result: CommandResult) -> str:
    return result.stdout.decode("utf-8", errors="replace").strip()


def git(repo: Path, *args: str, check: bool = True) -> CommandResult:
    return run(("git", *args), cwd=repo, check=check)


def resolve_commit(repo: Path, ref: str) -> str:
    value = text(git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}"))
    if not FULL_SHA_RE.fullmatch(value):
        raise GuardError(f"Git did not resolve {ref!r} to a full commit identity")
    return value


def head_refs(repo: Path) -> dict[str, str]:
    raw = git(
        repo,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
        "refs/heads",
    ).stdout
    refs: dict[str, str] = {}
    for line in raw.splitlines():
        if not line:
            continue
        name, sha = line.split(b"\x00", 1)
        refs[name.decode("utf-8", errors="strict")] = sha.decode("ascii", errors="strict")
    return refs


def nul_paths(payload: bytes) -> list[str]:
    return [part.decode("utf-8", errors="surrogateescape") for part in payload.split(b"\x00") if part]


def changed_paths(worktree: Path) -> list[str]:
    payload = git(worktree, "diff", "--cached", "--name-only", "-z").stdout
    return sorted(nul_paths(payload))


def candidate_changed_paths(
    repo: Path,
    merge_base: str,
    base_sha: str,
    head_sha: str,
) -> list[str]:
    """Return the complete two-sided review surface, not only conflict paths."""
    paths: set[str] = set()
    for descendant in (base_sha, head_sha):
        payload = git(repo, "diff", "--name-only", "-z", merge_base, descendant).stdout
        paths.update(nul_paths(payload))
    return sorted(paths)


def history(repo: Path, sha: str, depth: int) -> list[dict[str, Any]]:
    # Deliberately omit author names, emails, and commit messages from the
    # persisted artifact. Exact SHAs and timestamps are sufficient pointers
    # for an authorized reviewer to inspect the complete context locally.
    fmt = "%H%x00%P%x00%aI%x1e"
    raw = git(repo, "log", f"--max-count={depth}", f"--format={fmt}", sha).stdout
    rows: list[dict[str, Any]] = []
    for record in raw.split(b"\x1e"):
        record = record.strip(b"\n")
        if not record:
            continue
        fields = record.split(b"\x00")
        if len(fields) != 3:
            raise GuardError("unexpected git log record shape")
        rows.append(
            {
                "sha": fields[0].decode("ascii"),
                "parents": [p for p in fields[1].decode("ascii").split() if p],
                "authored_at": fields[2].decode("ascii"),
            }
        )
    return rows


def conflict_stages(worktree: Path, conflict_paths: Iterable[str]) -> list[dict[str, Any]]:
    wanted = set(conflict_paths)
    raw = git(worktree, "ls-files", "-u", "-z").stdout
    grouped: dict[str, dict[str, Any]] = {}
    for entry in raw.split(b"\x00"):
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        mode, sha, stage = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if path not in wanted:
            continue
        item = grouped.setdefault(path, {"path": path, "stages": {}})
        size = int(text(git(worktree, "cat-file", "-s", sha)))
        item["stages"][stage] = {"mode": mode, "blob": sha, "bytes": size}
    return [grouped[path] for path in sorted(grouped)]


def read_small_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_SCAN_BYTES:
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in payload:
        return None
    return payload.decode("utf-8", errors="replace")


def duplicate_json_keys(source: str) -> list[str]:
    duplicates: list[str] = []

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                duplicates.append(key)
            value[key] = child
        return value

    json.loads(source, object_pairs_hook=pairs_hook)
    return sorted(set(duplicates))


def scan_rust(path: str, source: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    module_lines: dict[str, int] = {}
    fields_by_depth: list[dict[str, int]] = [dict()]
    depth = 0

    for line_number, original_line in enumerate(source.splitlines(), start=1):
        line = original_line.split("//", 1)[0]
        module = RUST_MOD_RE.match(line)
        if module:
            name = module.group(1)
            if name in module_lines:
                findings.append(
                    {
                        "kind": "duplicate_rust_module_declaration",
                        "path": path,
                        "name": name,
                        "lines": [module_lines[name], line_number],
                    }
                )
            else:
                module_lines[name] = line_number

        while len(fields_by_depth) <= depth:
            fields_by_depth.append({})
        field = RUST_FIELD_RE.match(line)
        if field:
            name = field.group(1)
            first = fields_by_depth[depth].get(name)
            if first is not None:
                findings.append(
                    {
                        "kind": "duplicate_rust_field_at_same_block_depth",
                        "path": path,
                        "name": name,
                        "lines": [first, line_number],
                    }
                )
            else:
                fields_by_depth[depth][name] = line_number

        # This lightweight structural scan is intentionally conservative. It
        # is a backstop, not a parser; compile/test gates remain mandatory.
        opens = line.count("{")
        closes = line.count("}")
        for _ in range(opens):
            depth += 1
            if len(fields_by_depth) <= depth:
                fields_by_depth.append({})
            else:
                fields_by_depth[depth] = {}
        for _ in range(closes):
            if depth > 0:
                fields_by_depth[depth] = {}
                depth -= 1
    return findings


def semantic_findings(worktree: Path, paths: Iterable[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for relative in paths:
        source = read_small_text(worktree / relative)
        if source is None:
            continue
        for line_number, line in enumerate(source.splitlines(), start=1):
            if CONFLICT_MARKER_RE.match(line):
                findings.append(
                    {"kind": "unresolved_conflict_marker", "path": relative, "line": line_number}
                )

        suffix = Path(relative).suffix.lower()
        if suffix == ".toml":
            tables: dict[str, int] = {}
            for line_number, line in enumerate(source.splitlines(), start=1):
                match = TOML_TABLE_RE.match(line)
                if not match:
                    continue
                table = match.group(1).strip()
                if table in tables:
                    findings.append(
                        {
                            "kind": "duplicate_toml_table",
                            "path": relative,
                            "name": table,
                            "lines": [tables[table], line_number],
                        }
                    )
                else:
                    tables[table] = line_number
        elif suffix == ".json":
            try:
                duplicates = duplicate_json_keys(source)
            except json.JSONDecodeError as exc:
                findings.append(
                    {
                        "kind": "invalid_json",
                        "path": relative,
                        "line": exc.lineno,
                        "column": exc.colno,
                    }
                )
            else:
                for name in duplicates:
                    findings.append({"kind": "duplicate_json_key", "path": relative, "name": name})
        elif suffix == ".rs":
            findings.extend(scan_rust(relative, source))
    return findings


def write_report(path: Path, report: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="local Git repository")
    parser.add_argument("--base", required=True, help="base commit or ref")
    parser.add_argument("--head", required=True, help="candidate commit or ref")
    parser.add_argument("--output", required=True, help="content-free JSON review artifact")
    parser.add_argument("--history-depth", type=int, default=10, choices=range(3, 11))
    return parser.parse_args(argv)


def execute(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    supplied_repo = Path(args.repo).resolve()
    root = Path(text(git(supplied_repo, "rev-parse", "--show-toplevel"))).resolve()
    original_head = resolve_commit(root, "HEAD")
    refs_before = head_refs(root)
    base_sha = resolve_commit(root, args.base)
    head_sha = resolve_commit(root, args.head)
    merge_base_result = git(root, "merge-base", base_sha, head_sha, check=False)
    if merge_base_result.returncode != 0:
        raise GuardError("base and head have no merge base")
    merge_base = text(merge_base_result)
    if not FULL_SHA_RE.fullmatch(merge_base):
        raise GuardError("Git returned an invalid merge-base identity")
    review_paths = candidate_changed_paths(root, merge_base, base_sha, head_sha)

    report: dict[str, Any] = {
        "schema_version": "ore.semantic-merge-review.v1",
        "status": "started",
        "safe_to_publish": False,
        "safe_to_open_review_pr": False,
        "base": {"ref": args.base, "sha": base_sha, "history": history(root, base_sha, args.history_depth)},
        "head": {"ref": args.head, "sha": head_sha, "history": history(root, head_sha, args.history_depth)},
        "merge_base": merge_base,
        "changed_paths": review_paths,
        "preview_changed_paths": [],
        "conflict_paths": [],
        "conflicts": [],
        "findings": [],
        "preview_tree": None,
        "invariants": {
            "default_branch_updated": False,
            "branch_ref_updated": False,
            "commit_created": False,
            "source_content_serialized": False,
            "force_push_used": False,
            "wholesale_side_selected": False,
        },
        "required_next_step": "manual review",
    }

    worktree_parent = Path(tempfile.mkdtemp(prefix="semantic-merge-guard-"))
    worktree = worktree_parent / "worktree"
    merge_started = False
    exit_code = EXIT_ERROR
    try:
        git(root, "worktree", "add", "--detach", str(worktree), base_sha)
        merge = git(
            worktree,
            "-c",
            "rerere.enabled=false",
            "-c",
            "merge.conflictStyle=diff3",
            "merge",
            "--no-commit",
            "--no-ff",
            "--no-rerere-autoupdate",
            head_sha,
            check=False,
        )
        merge_started = True
        if merge.returncode != 0:
            conflicts = nul_paths(
                git(worktree, "diff", "--name-only", "--diff-filter=U", "-z", check=False).stdout
            )
            if not conflicts:
                raise GuardError("merge failed without producing reviewable unmerged paths")
            report["status"] = "manual_resolution_required"
            report["conflict_paths"] = sorted(conflicts)
            report["conflicts"] = conflict_stages(worktree, report["conflict_paths"])
            report["required_next_step"] = (
                "Create a fresh feature branch from the current reviewed base; inspect the merge base, "
                "both stage blobs, 3-10 relevant commits on each side, APIs, schemas, migrations, tests, "
                "documentation, and related repositories; implement the conceptual union; then rerun this guard."
            )
            exit_code = EXIT_REVIEW_REQUIRED
        else:
            paths = changed_paths(worktree)
            report["preview_changed_paths"] = paths
            diff_check = git(worktree, "diff", "--cached", "--check", check=False)
            findings = semantic_findings(worktree, paths)
            if diff_check.returncode != 0:
                findings.append({"kind": "git_diff_check_failed"})
            report["findings"] = findings
            report["preview_tree"] = text(git(worktree, "write-tree"))
            if findings:
                report["status"] = "semantic_validation_failed"
                report["required_next_step"] = (
                    "Correct the suspicious clean-merge result in a fresh feature branch, run repository-specific "
                    "compile/test/documentation gates, and rerun this guard."
                )
                exit_code = EXIT_REVIEW_REQUIRED
            else:
                report["status"] = "clean_preview"
                report["safe_to_publish"] = False
                report["safe_to_open_review_pr"] = True
                report["required_next_step"] = (
                    "Publish this exact preview tree only as a feature-branch pull request, then require the "
                    "repository-specific build, test, review, and immutable-head merge gates."
                )
                exit_code = EXIT_OK
    finally:
        if worktree.exists() and merge_started:
            git(worktree, "merge", "--abort", check=False)
        if worktree.exists():
            git(root, "worktree", "remove", "--force", str(worktree), check=False)
        git(root, "worktree", "prune", check=False)
        shutil.rmtree(worktree_parent, ignore_errors=True)

    final_head = resolve_commit(root, "HEAD")
    refs_after = head_refs(root)
    if final_head != original_head or refs_after != refs_before:
        report["status"] = "source_repository_mutated"
        report["safe_to_publish"] = False
        report["invariants"]["branch_ref_updated"] = refs_after != refs_before
        report["required_next_step"] = "Stop automation and investigate unexpected source-repository mutation."
        exit_code = EXIT_ERROR

    # Re-resolve mutable refs after the preview to catch a concurrent update.
    for key, ref, expected in (("base", args.base, base_sha), ("head", args.head, head_sha)):
        if FULL_SHA_RE.fullmatch(ref):
            report[key]["ref_stable"] = True
            continue
        actual = resolve_commit(root, ref)
        report[key]["ref_stable"] = actual == expected
        if actual != expected:
            report["status"] = "input_ref_moved"
            report["safe_to_publish"] = False
            report["required_next_step"] = "Restart from freshly reviewed immutable base and head SHAs."
            exit_code = EXIT_ERROR
    return exit_code, report


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report: dict[str, Any]
    try:
        code, report = execute(args)
    except Exception as exc:  # fail closed; never include subprocess output/source data
        code = EXIT_ERROR
        report = {
            "schema_version": "ore.semantic-merge-review.v1",
            "status": "guard_error",
            "safe_to_publish": False,
            "safe_to_open_review_pr": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "invariants": {
                "default_branch_updated": False,
                "commit_created": False,
                "force_push_used": False,
                "source_content_serialized": False,
            },
        }
    write_report(Path(args.output), report)
    print(
        json.dumps(
            {
                "status": report.get("status"),
                "safe_to_publish": report.get("safe_to_publish"),
                "safe_to_open_review_pr": report.get("safe_to_open_review_pr"),
            }
        )
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
