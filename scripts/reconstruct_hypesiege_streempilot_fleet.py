#!/usr/bin/env python3
"""Reconstruct and deterministically seal the HypeSiege/StreemPilot fleet.

The large repository-content generator is stored as gzip/base64 parts so the
source payload can be moved without losing Git histories. This wrapper verifies
that payload, runs it into a caller-owned directory, then replaces timestamp-
dependent commits with a deterministic child-before-monorepo graph containing
real mode-160000 gitlinks.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from typing import Any

EXPECTED_GENERATOR_SHA256 = "a57b00961ee57ae09bf3bb2e2d09afbdd1ddbbbde832b027802f82a1fc5dfa84"
FIXED_DATE = "2026-07-31T00:00:00-04:00"
AUTHOR_NAME = "ORESoftware"
AUTHOR_EMAIL = "11139560+ORESoftware@users.noreply.github.com"
EXPECTED_REPOSITORIES = 32
EXPECTED_FILES = 888
EXPECTED_GITLINKS = 30


class ReconstructionError(RuntimeError):
    """The sealed source payload or generated Git graph violated its contract."""


def run(
    args: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:4096]
        raise ReconstructionError(f"{' '.join(args)} failed: {detail}")
    return completed.stdout


def decode_generator(payload_dir: pathlib.Path) -> str:
    parts = sorted(payload_dir.glob("generator.py.gz.b64.part-*"))
    if [path.name for path in parts] != [
        f"generator.py.gz.b64.part-{index:02d}" for index in range(6)
    ]:
        raise ReconstructionError("expected exactly generator payload parts 00 through 05")
    encoded = "".join(path.read_text(encoding="utf-8").strip() for path in parts)
    try:
        source = gzip.decompress(base64.b64decode(encoded, validate=True))
    except (ValueError, gzip.BadGzipFile) as error:
        raise ReconstructionError("generator payload is not valid base64/gzip") from error
    digest = hashlib.sha256(source).hexdigest()
    if digest != EXPECTED_GENERATOR_SHA256:
        raise ReconstructionError(
            f"generator checksum mismatch: {digest} != {EXPECTED_GENERATOR_SHA256}"
        )
    return source.decode("utf-8")


def git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": AUTHOR_NAME,
            "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
            "GIT_AUTHOR_DATE": FIXED_DATE,
            "GIT_COMMITTER_NAME": AUTHOR_NAME,
            "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
            "GIT_COMMITTER_DATE": FIXED_DATE,
            "TZ": "UTC",
        }
    )
    return environment


def amend_root_commit(repository: pathlib.Path) -> str:
    """Seal the complete reviewed index tree as one deterministic root commit."""
    branch = run(["git", "branch", "--show-current"], cwd=repository).strip()
    if branch != "main":
        raise ReconstructionError(f"{repository} must be on main before sealing")

    source_count = int(
        run(["git", "rev-list", "--count", "HEAD"], cwd=repository).strip()
    )
    if source_count < 1:
        raise ReconstructionError(f"{repository} has no source commit to seal")
    if run(["git", "diff", "--name-only"], cwd=repository):
        raise ReconstructionError(f"{repository} contains unstaged changes")
    if run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=repository,
    ):
        raise ReconstructionError(f"{repository} contains untracked files")
    run(["git", "diff", "--cached", "--check"], cwd=repository)

    message = run(["git", "log", "-1", "--format=%B"], cwd=repository).strip()
    if not message:
        message = f"Initialize {repository.name}"
    tree = run(["git", "write-tree"], cwd=repository).strip()
    if len(tree) != 40 or any(character not in "0123456789abcdef" for character in tree):
        raise ReconstructionError(f"{repository} produced an invalid tree identity")
    commit = run(
        ["git", "commit-tree", tree, "-m", message],
        cwd=repository,
        env=git_environment(),
    ).strip()
    if len(commit) != 40 or any(
        character not in "0123456789abcdef" for character in commit
    ):
        raise ReconstructionError(f"{repository} produced an invalid commit identity")

    run(["git", "update-ref", "refs/heads/main", commit], cwd=repository)
    if run(["git", "rev-list", "--count", "HEAD"], cwd=repository).strip() != "1":
        raise ReconstructionError(f"{repository} did not collapse to one root commit")
    if run(["git", "status", "--porcelain"], cwd=repository):
        raise ReconstructionError(f"{repository} became dirty while sealing")
    return commit


def write_gitmodules(
    repository: pathlib.Path,
    child_records: list[dict[str, Any]],
) -> None:
    content = "".join(
        f'[submodule "apps/{record["name"]}"]\n'
        f'\tpath = apps/{record["name"]}\n'
        f'\turl = {record["remote"]}\n'
        for record in child_records
    )
    (repository / ".gitmodules").write_text(content, encoding="utf-8")
    run(["git", "add", ".gitmodules"], cwd=repository)


def add_local_gitlinks(
    root: pathlib.Path,
    repository: pathlib.Path,
    child_records: list[dict[str, Any]],
) -> None:
    run(["git", "config", "advice.addEmbeddedRepo", "false"], cwd=repository)
    for record in child_records:
        source = root / record["org"] / record["name"]
        target = repository / "apps" / record["name"]
        if target.exists():
            shutil.rmtree(target)
        run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(source), str(target)],
            cwd=repository,
        )
        run(["git", "checkout", "--quiet", record["commit"]], cwd=target)
        run(["git", "add", f'apps/{record["name"]}'], cwd=repository)


def count_gitlinks(repository: pathlib.Path) -> int:
    output = run(["git", "ls-files", "--stage"], cwd=repository)
    return sum(line.startswith("160000 ") for line in output.splitlines())


def validate_record(root: pathlib.Path, record: dict[str, Any]) -> tuple[int, int]:
    repository = root / record["org"] / record["name"]
    if run(["git", "branch", "--show-current"], cwd=repository).strip() != "main":
        raise ReconstructionError(f"{record['full_name']} is not on main")
    if run(["git", "rev-parse", "HEAD"], cwd=repository).strip() != record["commit"]:
        raise ReconstructionError(f"{record['full_name']} commit drift")
    if run(["git", "remote", "get-url", "origin"], cwd=repository).strip() != record["remote"]:
        raise ReconstructionError(f"{record['full_name']} origin drift")
    if run(["git", "status", "--porcelain"], cwd=repository):
        raise ReconstructionError(f"{record['full_name']} working tree is dirty")
    run(["git", "diff", "--check", "HEAD"], cwd=repository)
    run(["git", "fsck", "--full", "--no-dangling"], cwd=repository)
    files = len(run(["git", "ls-files"], cwd=repository).splitlines())
    gitlinks = count_gitlinks(repository)
    if files != record["files"]:
        raise ReconstructionError(f"{record['full_name']} tracked-file count drift")
    return files, gitlinks


def reconstruct(payload_dir: pathlib.Path, output_root: pathlib.Path) -> dict[str, Any]:
    source = decode_generator(payload_dir)
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)

    expected_literal = "ROOT = Path('/mnt/data/hypesiege-streempilot-fleet')"
    replacement = f"ROOT = Path({str(output_root)!r})"
    if source.count(expected_literal) != 1:
        raise ReconstructionError("generator output-root contract changed")
    source = source.replace(expected_literal, replacement)

    archive_literal = "/mnt/data/hypesiege-streempilot-fleet.tar.gz"
    archive_replacement = str(output_root.parent / f"{output_root.name}.tar.gz")
    if source.count(archive_literal) != 1:
        raise ReconstructionError("generator archive-output contract changed")
    source = source.replace(archive_literal, archive_replacement)

    checksum_literal = "/mnt/data/hypesiege-streempilot-fleet.SHA256"
    checksum_replacement = str(output_root.parent / f"{output_root.name}.SHA256")
    if source.count(checksum_literal) != 1:
        raise ReconstructionError("generator checksum-output contract changed")
    source = source.replace(checksum_literal, checksum_replacement)

    with tempfile.TemporaryDirectory(prefix="hypesiege-streempilot-generator-") as temp:
        generator = pathlib.Path(temp) / "generator.py"
        generator.write_text(source, encoding="utf-8")
        run(["python3", "-m", "py_compile", str(generator)])
        run(["python3", str(generator)])

    stale_archive = output_root.parent / f"{output_root.name}.tar.gz"
    stale_checksum = output_root.parent / f"{output_root.name}.SHA256"
    stale_archive.unlink(missing_ok=True)
    stale_checksum.unlink(missing_ok=True)
    (output_root / "publish.py").unlink(missing_ok=True)

    generated = json.loads((output_root / "MANIFEST.json").read_text(encoding="utf-8"))
    records = generated.get("repositories")
    if not isinstance(records, list) or len(records) != EXPECTED_REPOSITORIES:
        raise ReconstructionError("generated repository count changed")
    by_name = {record["full_name"]: record for record in records}

    sealed: list[dict[str, Any]] = []
    for record in sorted(
        (item for item in records if item["kind"] != "monorepo"),
        key=lambda item: (item["org"], item["name"]),
    ):
        repository = output_root / record["org"] / record["name"]
        record = dict(record)
        record["commit"] = amend_root_commit(repository)
        record["files"] = len(run(["git", "ls-files"], cwd=repository).splitlines())
        sealed.append(record)
        by_name[record["full_name"]] = record

    for record in sorted(
        (item for item in records if item["kind"] == "monorepo"),
        key=lambda item: (item["org"], item["name"]),
    ):
        repository = output_root / record["org"] / record["name"]
        repository_order = [
            item["name"]
            for item in json.loads((repository / "repos.json").read_text(encoding="utf-8"))[
                "repositories"
            ]
        ]
        sealed_for_org = {
            item["name"]: item
            for item in sealed
            if item["org"] == record["org"] and item["kind"] != "monorepo"
        }
        if set(repository_order) != set(sealed_for_org):
            raise ReconstructionError(f"{record['full_name']} child ledger drift")
        child_records = [sealed_for_org[name] for name in repository_order]
        write_gitmodules(repository, child_records)
        add_local_gitlinks(output_root, repository, child_records)
        record = dict(record)
        record["commit"] = amend_root_commit(repository)
        record["files"] = len(run(["git", "ls-files"], cwd=repository).splitlines())
        sealed.append(record)
        by_name[record["full_name"]] = record

    sealed.sort(key=lambda item: (item["org"], item["kind"] == "monorepo", item["name"]))
    for record in sealed:
        repository = output_root / record["org"] / record["name"]
        record["gitlinks"] = count_gitlinks(repository)
    manifest = {
        "schema_version": 2,
        "generated_at": FIXED_DATE,
        "generator_sha256": EXPECTED_GENERATOR_SHA256,
        "default_branch": "main",
        "repository_count": len(sealed),
        "total_tracked_files": EXPECTED_FILES,
        "total_gitlinks": EXPECTED_GITLINKS,
        "organizations": {"hypesiege": 15, "streempilot": 17},
        "publication_status": "deterministic histories sealed; remote authorization required",
        "repositories": sealed,
    }
    (output_root / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (output_root / "README.md").write_text(
        "# HypeSiege and StreemPilot repository fleet\n\n"
        "This directory contains 32 deterministic independent Git histories. "
        "Use the reviewed coordinator publisher with the checked-in manifest; "
        "no credential or live publisher is embedded in this artifact.\n",
        encoding="utf-8",
    )

    total_files = 0
    total_gitlinks = 0
    validation = [
        "HypeSiege + StreemPilot deterministic fleet validation",
        "=" * 52,
    ]
    for record in sealed:
        files, gitlinks = validate_record(output_root, record)
        expected_gitlinks = 0
        if record["kind"] == "monorepo":
            expected_gitlinks = 14 if record["org"] == "hypesiege" else 16
        if gitlinks != expected_gitlinks:
            raise ReconstructionError(
                f"{record['full_name']} gitlinks {gitlinks} != {expected_gitlinks}"
            )
        total_files += files
        total_gitlinks += gitlinks
        validation.append(
            f"PASS {record['full_name']} {record['commit']} files={files} gitlinks={gitlinks}"
        )
    if total_files != EXPECTED_FILES or total_gitlinks != EXPECTED_GITLINKS:
        raise ReconstructionError(
            f"fleet totals files={total_files}, gitlinks={total_gitlinks}"
        )
    validation.extend(
        [
            f"total tracked files: {total_files}",
            f"total gitlinks: {total_gitlinks}",
            "PASS all repositories clean on main",
            "PASS exact GitHub origins configured",
            "PASS git fsck and diff checks for all histories",
            "PASS deterministic child-before-monorepo gitlink graph",
        ]
    )
    (output_root / "VALIDATION.log").write_text(
        "\n".join(validation) + "\n", encoding="utf-8"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--payload-dir",
        type=pathlib.Path,
        default=pathlib.Path("repository-fleets/hypesiege-streempilot"),
    )
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--manifest-out", type=pathlib.Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = reconstruct(args.payload_dir.resolve(), args.output_root.resolve())
    if args.manifest_out is not None:
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_out.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    print(
        json.dumps(
            {
                "root": str(args.output_root.resolve()),
                "repositories": manifest["repository_count"],
                "files": sum(record["files"] for record in manifest["repositories"]),
                "gitlinks": EXPECTED_GITLINKS,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ReconstructionError as error:
        raise SystemExit(f"reconstruction refused: {error}") from error
