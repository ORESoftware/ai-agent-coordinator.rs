#!/usr/bin/env python3
"""Safely create and push one deterministic HypeSiege/StreemPilot repository.

Planning is network-free. Live execution is intentionally one repository at a
time and requires exact confirmation plus a short-lived, least-privilege GitHub
App installation token supplied only through the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "ai-agent-coordinator-hypesiege-streempilot-publisher"
TOKEN_ENV = "GITHUB_REPOSITORY_ADMIN_TOKEN"
ALLOWED_ORGS = frozenset({"hypesiege", "streempilot"})
EXPECTED_GENERATOR_SHA256 = "50629a57beca1ac85928cfae8fbebbca4f62a6455a7013016f92b1203dcbbd1f"
EXPECTED_REPOSITORIES = 32
EXPECTED_FILES = 888
EXPECTED_GITLINKS = 30


class PublicationError(RuntimeError):
    """Fail-closed publication error."""


def run(args: list[str], cwd: pathlib.Path) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:4096]
        raise PublicationError(f"{' '.join(args)} failed in {cwd}: {detail}")
    return completed.stdout


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    repositories = manifest.get("repositories")
    if manifest.get("schema_version") != 2 or not isinstance(repositories, list):
        raise PublicationError("unsupported or malformed fleet manifest")
    if manifest.get("generator_sha256") != EXPECTED_GENERATOR_SHA256:
        raise PublicationError("fleet manifest generator checksum changed")
    if manifest.get("repository_count") != EXPECTED_REPOSITORIES:
        raise PublicationError("fleet manifest repository count changed")
    if manifest.get("repository_count") != len(repositories):
        raise PublicationError("repository_count does not match repositories")
    if manifest.get("total_tracked_files") != EXPECTED_FILES:
        raise PublicationError("fleet manifest tracked-file total changed")
    if manifest.get("total_gitlinks") != EXPECTED_GITLINKS:
        raise PublicationError("fleet manifest gitlink total changed")
    if manifest.get("organizations") != {"hypesiege": 15, "streempilot": 17}:
        raise PublicationError("fleet manifest organization counts changed")
    full_names = [record.get("full_name") for record in repositories]
    if len(set(full_names)) != len(full_names):
        raise PublicationError("fleet manifest contains duplicate repositories")
    return manifest


def select_record(manifest: dict[str, Any], full_name: str) -> dict[str, Any]:
    matches = [
        record
        for record in manifest["repositories"]
        if record.get("full_name") == full_name
    ]
    if len(matches) != 1:
        raise PublicationError(f"manifest must contain exactly one {full_name!r} record")
    record = matches[0]
    org = record.get("org")
    name = record.get("name")
    if org not in ALLOWED_ORGS or full_name != f"{org}/{name}":
        raise PublicationError("repository is outside the approved fleet")
    if record.get("default_branch") != "main":
        raise PublicationError("repository default branch must be main")
    if record.get("visibility") not in {"public", "private"}:
        raise PublicationError("repository visibility must be explicit")
    if not re.fullmatch(r"[0-9a-f]{40}", str(record.get("commit", ""))):
        raise PublicationError("repository commit must be a full lowercase SHA")
    expected_gitlinks = 0
    if record.get("kind") == "monorepo":
        expected_gitlinks = 14 if org == "hypesiege" else 16
    if record.get("gitlinks") != expected_gitlinks:
        raise PublicationError("repository gitlink count violates the fleet topology")
    return record


def staged_gitlinks(repo: pathlib.Path) -> dict[str, str]:
    gitlinks: dict[str, str] = {}
    for line in run(["git", "ls-files", "--stage"], repo).splitlines():
        metadata, path = line.split("\t", 1)
        mode, object_id, _stage = metadata.split()
        if mode == "160000":
            gitlinks[path] = object_id
    return gitlinks


def gitmodule_paths(repo: pathlib.Path) -> set[str]:
    path = repo / ".gitmodules"
    if not path.is_file():
        return set()
    completed = subprocess.run(
        [
            "git",
            "config",
            "--file",
            ".gitmodules",
            "--get-regexp",
            r"^submodule\..*\.path$",
        ],
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode not in {0, 1}:
        raise PublicationError(f"failed to parse {path}: {completed.stderr[:4096]}")
    return {
        line.split(maxsplit=1)[1]
        for line in completed.stdout.splitlines()
        if line.strip()
    }


def preflight_source(record: dict[str, Any], source_root: pathlib.Path) -> pathlib.Path:
    repo = source_root / record["org"] / record["name"]
    if not (repo / ".git").exists():
        raise PublicationError(f"missing independent Git history: {repo}")

    checks = {
        "branch": run(["git", "branch", "--show-current"], repo).strip(),
        "head": run(["git", "rev-parse", "HEAD"], repo).strip(),
        "origin": run(["git", "remote", "get-url", "origin"], repo).strip(),
        "status": run(["git", "status", "--porcelain"], repo),
        "files": len(run(["git", "ls-files"], repo).splitlines()),
    }
    if checks["branch"] != "main":
        raise PublicationError(f"{record['full_name']} is not on main")
    if checks["head"] != record["commit"]:
        raise PublicationError(
            f"{record['full_name']} head mismatch: {checks['head']} != {record['commit']}"
        )
    if checks["origin"] != record["remote"]:
        raise PublicationError(f"{record['full_name']} origin mismatch")
    if checks["status"]:
        raise PublicationError(f"{record['full_name']} working tree is dirty")
    if checks["files"] != record["files"]:
        raise PublicationError(f"{record['full_name']} tracked-file count mismatch")

    gitlinks = staged_gitlinks(repo)
    if len(gitlinks) != record["gitlinks"]:
        raise PublicationError(f"{record['full_name']} gitlink count mismatch")
    if gitlinks:
        expected_paths = gitmodule_paths(repo)
        if expected_paths != set(gitlinks):
            raise PublicationError(f"{record['full_name']} .gitmodules/index mismatch")
        for path, expected_commit in gitlinks.items():
            checkout = repo / path
            if not (checkout / ".git").exists():
                raise PublicationError(f"unmaterialized submodule checkout: {path}")
            actual_commit = run(["git", "rev-parse", "HEAD"], checkout).strip()
            if actual_commit != expected_commit:
                raise PublicationError(
                    f"submodule checkout drift for {path}: {actual_commit} != {expected_commit}"
                )
            if run(["git", "status", "--porcelain"], checkout):
                raise PublicationError(f"submodule checkout is dirty: {path}")

    run(["git", "diff", "--check", "HEAD"], repo)
    run(["git", "fsck", "--full", "--no-dangling"], repo)
    return repo


def request_json(
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        API_BASE + path,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        },
    )
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise PublicationError("GitHub API response exceeded 256 KiB")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read(4096).decode("utf-8", errors="replace")
        if error.code == 404 and method == "GET":
            return error.code, None
        raise PublicationError(
            f"GitHub API {error.code} for {method} {path}: {raw}"
        ) from error
    except urllib.error.URLError as error:
        raise PublicationError(f"GitHub API unavailable for {method} {path}: {error}") from error


def ensure_repository(record: dict[str, Any], token: str) -> dict[str, Any]:
    status, current = request_json("GET", f"/repos/{record['full_name']}", token)
    if status == 404:
        _, current = request_json(
            "POST",
            f"/orgs/{record['org']}/repos",
            token,
            {
                "name": record["name"],
                "description": record["description"],
                "private": record["visibility"] == "private",
                "has_issues": True,
                "has_projects": False,
                "has_wiki": False,
                "auto_init": False,
                "allow_squash_merge": True,
                "allow_merge_commit": True,
                "allow_rebase_merge": False,
                "delete_branch_on_merge": True,
            },
        )
    if not isinstance(current, dict):
        raise PublicationError("GitHub did not return repository metadata")
    if current.get("full_name", "").casefold() != record["full_name"].casefold():
        raise PublicationError("GitHub returned an unexpected repository")
    if current.get("visibility") != record["visibility"]:
        raise PublicationError(
            f"visibility mismatch: {current.get('visibility')} != {record['visibility']}"
        )
    return current


def remote_main_commit(full_name: str, token: str) -> str | None:
    status, payload = request_json("GET", f"/repos/{full_name}/commits/main", token)
    if status == 404:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("sha"), str):
        raise PublicationError(f"GitHub returned invalid main-commit metadata for {full_name}")
    return payload["sha"]


def verify_monorepo_children(
    manifest: dict[str, Any],
    record: dict[str, Any],
    token: str,
) -> None:
    if record["kind"] != "monorepo":
        return
    children = [
        item
        for item in manifest["repositories"]
        if item["org"] == record["org"] and item["kind"] != "monorepo"
    ]
    for child in children:
        actual = remote_main_commit(child["full_name"], token)
        if actual != child["commit"]:
            raise PublicationError(
                f"cannot publish {record['full_name']}: child {child['full_name']} "
                f"remote main is {actual!r}, expected {child['commit']}"
            )


def push_main(repo: pathlib.Path, token: str) -> None:
    directory = pathlib.Path(tempfile.mkdtemp(prefix="fleet-git-askpass-"))
    try:
        directory.chmod(stat.S_IRWXU)
        askpass = directory / "askpass.sh"
        askpass.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  *Username*) printf "%s\\n" x-access-token ;;\n'
            f'  *) printf "%s\\n" "${{{TOKEN_ENV}}}" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        askpass.chmod(stat.S_IRWXU)
        environment = os.environ.copy()
        environment[TOKEN_ENV] = token
        environment["GIT_ASKPASS"] = str(askpass)
        environment["GIT_ASKPASS_REQUIRE"] = "force"
        environment["GIT_TERMINAL_PROMPT"] = "0"
        completed = subprocess.run(
            ["git", "push", "--porcelain", "--set-upstream", "origin", "main"],
            cwd=repo,
            env=environment,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[:4096]
            raise PublicationError(f"git push failed for {repo.name}: {detail}")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=pathlib.Path("repository-fleets/hypesiege-streempilot.json"),
    )
    parser.add_argument("--source-root", type=pathlib.Path)
    parser.add_argument("--repository", required=True, help="exact owner/name")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-repository")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    record = select_record(manifest, args.repository)

    print(
        json.dumps(
            {
                "mode": "execute" if args.execute else "plan",
                "repository": record["full_name"],
                "commit": record["commit"],
                "visibility": record["visibility"],
                "remote": record["remote"],
                "files": record["files"],
                "gitlinks": record["gitlinks"],
            },
            indent=2,
        )
    )

    if not args.execute:
        return 0
    if args.confirm_repository != record["full_name"]:
        raise PublicationError(
            "--confirm-repository must exactly equal the requested owner/name"
        )
    if args.source_root is None:
        raise PublicationError("--source-root is required in execute mode")
    repo = preflight_source(record, args.source_root.resolve())
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise PublicationError(
            f"{TOKEN_ENV} is required; use a short-lived least-privilege "
            "GitHub App installation token"
        )

    verify_monorepo_children(manifest, record, token)
    current = ensure_repository(record, token)
    push_main(repo, token)
    actual = remote_main_commit(record["full_name"], token)
    if actual != record["commit"]:
        raise PublicationError(
            f"remote verification failed for {record['full_name']}: "
            f"{actual!r} != {record['commit']}"
        )

    print(
        json.dumps(
            {
                "published": record["full_name"],
                "repository_id": current.get("id"),
                "visibility": current.get("visibility"),
                "default_branch": "main",
                "commit": actual,
                "verified": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublicationError as error:
        raise SystemExit(f"publication refused: {error}") from error
