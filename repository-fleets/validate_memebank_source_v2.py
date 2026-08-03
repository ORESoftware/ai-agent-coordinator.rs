#!/usr/bin/env python3
"""Validate and reconstruct the sealed MemeBank source-v2 carrier."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Any, Mapping, Sequence

MANIFEST = Path("repository-fleets/memebank-source-v2.json")
SOURCE_ROOT = "memebank-source-v2"
EXPECTED_ORG = "memebank"
EXPECTED_REPOSITORIES = (
    ".github",
    "mb-interfaces",
    "mb-clients",
    "mb-cli",
    "memebank-api-server.rs",
    "memebank-web-server.rs",
    "memebank-media-worker.rs",
    "memebank-flutter",
    "mb-infra",
    "memebank.github.io",
    "memebank-mcp-server.rs",
    "memebank-e2e",
    "memebank-monorepo",
)
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


class ValidationError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def command(arguments: Sequence[str], cwd: Path, env: Mapping[str, str] | None = None) -> str:
    result = subprocess.run(
        list(arguments), cwd=cwd, env=None if env is None else dict(env),
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    if result.returncode:
        raise ValidationError(f"command failed: {' '.join(arguments)}\n{result.stdout[-8000:]}")
    return result.stdout.strip()


def load_manifest(root: Path) -> dict[str, Any]:
    path = root / MANIFEST
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("source-v2 manifest is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ValidationError("source-v2 manifest must be an object")
    if value.get("schema_version") != 2:
        raise ValidationError("source-v2 schema must remain 2")
    if value.get("organization") != EXPECTED_ORG:
        raise ValidationError("source-v2 organization changed")
    if value.get("visibility") != "private":
        raise ValidationError("source-v2 visibility must remain private")
    if value.get("default_branch") != "main":
        raise ValidationError("source-v2 default branch must remain main")
    if value.get("source_root") != SOURCE_ROOT:
        raise ValidationError("source-v2 root changed")
    if value.get("repository_order") != list(EXPECTED_REPOSITORIES):
        raise ValidationError("source-v2 repository order changed")
    if value.get("forbidden_repositories") != ["memebank-infra"]:
        raise ValidationError("source-v2 forbidden repository policy changed")
    records = value.get("repositories")
    if not isinstance(records, list) or len(records) != len(EXPECTED_REPOSITORIES):
        raise ValidationError("source-v2 must contain exactly 13 repositories")
    names = [record.get("name") for record in records if isinstance(record, dict)]
    if names != list(EXPECTED_REPOSITORIES):
        raise ValidationError("source-v2 repository records changed")
    if sum(int(record["tracked_entries"]) for record in records) != value.get("tracked_entries"):
        raise ValidationError("source-v2 tracked-entry aggregate is inconsistent")
    links = value.get("monorepo", {}).get("gitlinks")
    if not isinstance(links, dict) or len(links) != 11:
        raise ValidationError("source-v2 monorepo must pin exactly 11 gitlinks")
    by_name = {record["name"]: record for record in records}
    for path, sha in links.items():
        child = Path(path).name
        if child not in by_name or sha != by_name[child]["expected_head"]:
            raise ValidationError(f"gitlink {path} does not pin its exact child")
    return value


def decode_payload(root: Path, manifest: Mapping[str, Any]) -> bytes:
    payload = manifest.get("payload")
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise ValidationError("source-v2 payload metadata is invalid")
    parts: list[bytes] = []
    for record in payload.get("chunks", []):
        if not isinstance(record, dict):
            raise ValidationError("source-v2 chunk record is invalid")
        relative = str(record.get("path", ""))
        path = root / relative
        resolved = path.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ValidationError("source-v2 chunk escapes repository root") from exc
        if not path.is_file() or path.is_symlink():
            raise ValidationError(f"source-v2 chunk is missing or unsafe: {relative}")
        raw = path.read_bytes()
        if len(raw) != record.get("bytes") or digest(raw) != record.get("sha256"):
            raise ValidationError(f"source-v2 chunk integrity failed: {relative}")
        parts.append(raw)
    encoded = b"".join(parts)
    if len(encoded) != payload.get("base64_bytes"):
        raise ValidationError("source-v2 encoded byte total changed")
    try:
        archive = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValidationError("source-v2 payload is not strict base64") from exc
    archive_record = manifest.get("archive")
    if not isinstance(archive_record, dict):
        raise ValidationError("source-v2 archive record is invalid")
    if len(archive) != archive_record.get("bytes") or digest(archive) != archive_record.get("sha256"):
        raise ValidationError("source-v2 archive integrity failed")
    return archive


def member_parts(name: str) -> tuple[str, ...]:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise ValidationError(f"unsafe archive path: {name!r}")
    parts = tuple(name.rstrip("/").split("/"))
    if not parts or parts[0] != SOURCE_ROOT or any(part in {"", ".", "..", ".git"} for part in parts):
        raise ValidationError(f"unsafe archive path: {name!r}")
    if PurePosixPath(name).is_absolute():
        raise ValidationError(f"absolute archive path: {name!r}")
    return parts


def extract_archive(archive: bytes, destination: Path, manifest: Mapping[str, Any]) -> Path:
    destination.mkdir(parents=True, exist_ok=False)
    seen: set[str] = set()
    files = 0
    total = 0
    try:
        opened = tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz")
    except tarfile.TarError as exc:
        raise ValidationError("source-v2 archive is not a tar.gz") from exc
    with opened:
        for member in opened.getmembers():
            parts = member_parts(member.name)
            key = "/".join(parts).casefold()
            if key in seen:
                raise ValidationError(f"duplicate/case-colliding archive path: {member.name}")
            seen.add(key)
            if not member.isdir() and not member.isreg():
                raise ValidationError(f"links and special files are forbidden: {member.name}")
            if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                raise ValidationError(f"unsafe archive member size: {member.name}")
            total += member.size
            if total > MAX_TOTAL_BYTES:
                raise ValidationError("source-v2 extraction exceeds safety bound")
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = opened.extractfile(member)
            if source is None:
                raise ValidationError(f"unreadable archive member: {member.name}")
            data = source.read(MAX_MEMBER_BYTES + 1)
            if len(data) != member.size:
                raise ValidationError(f"truncated archive member: {member.name}")
            with target.open("xb") as output:
                output.write(data)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
            files += 1
    if files != manifest.get("source_files"):
        raise ValidationError(f"source-v2 extracted {files} files, expected {manifest.get('source_files')}")
    source = destination / SOURCE_ROOT
    if {entry.name for entry in source.iterdir()} != set(EXPECTED_REPOSITORIES):
        raise ValidationError("source-v2 repository roots changed")
    return source


def initialize(source: Path, destination: Path, commit: Mapping[str, str], gitlinks: Mapping[str, str] | None = None) -> tuple[str, str, int]:
    shutil.copytree(source, destination)
    command(["git", "init", "-q", "-b", "main"], destination)
    command(["git", "config", "user.name", commit["author_name"]], destination)
    command(["git", "config", "user.email", commit["author_email"]], destination)
    command(["git", "add", "-A"], destination)
    for path, sha in sorted((gitlinks or {}).items()):
        command(["git", "update-index", "--add", "--cacheinfo", f"160000,{sha},{path}"], destination)
    tree = command(["git", "write-tree"], destination)
    env = os.environ.copy()
    env["GIT_AUTHOR_DATE"] = commit["date"]
    env["GIT_COMMITTER_DATE"] = commit["date"]
    command(["git", "commit", "-q", "-m", commit["message"]], destination, env)
    head = command(["git", "rev-parse", "HEAD"], destination)
    entries = len(command(["git", "ls-files", "-s"], destination).splitlines())
    return tree, head, entries


def validate_histories(source: Path, destination: Path, manifest: Mapping[str, Any]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    records = manifest["repositories"]
    links = manifest["monorepo"]["gitlinks"]
    for record in records:
        name = record["name"]
        tree, head, entries = initialize(
            source / record["source_path"],
            destination / name,
            manifest["commit"],
            links if name == manifest["monorepo"]["repository"] else None,
        )
        if (tree, head, entries) != (
            record["expected_tree"], record["expected_head"], record["tracked_entries"]
        ):
            raise ValidationError(
                f"{name}: reconstructed {(tree, head, entries)} != approved "
                f"{(record['expected_tree'], record['expected_head'], record['tracked_entries'])}"
            )


def main() -> int:
    repository = Path.cwd()
    try:
        manifest = load_manifest(repository)
        archive = decode_payload(repository, manifest)
        with tempfile.TemporaryDirectory(prefix="memebank-source-v2-") as temporary:
            work = Path(temporary)
            source = extract_archive(archive, work / "extract", manifest)
            validate_histories(source, work / "repositories", manifest)
        print(
            f"PASS source-v2 repositories={len(manifest['repositories'])} "
            f"files={manifest['source_files']} entries={manifest['tracked_entries']} "
            f"archive={manifest['archive']['sha256']}"
        )
        return 0
    except ValidationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
