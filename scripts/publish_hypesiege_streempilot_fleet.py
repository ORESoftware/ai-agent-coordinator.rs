#!/usr/bin/env python3
"""Safely create and push one sealed HypeSiege or StreemPilot repository.

Planning is the default. Live execution is intentionally one repository at a
time and requires an exact repository confirmation plus a short-lived GitHub
App installation token supplied only through the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
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
    if manifest.get("schema_version") != 1 or not isinstance(repositories, list):
        raise PublicationError("unsupported or malformed fleet manifest")
    if manifest.get("repository_count") != len(repositories):
        raise PublicationError("repository_count does not match repositories")
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
    if record.get("visibility") not in {"public", "private"}:
        raise PublicationError("repository visibility must be explicit")
    return record


def preflight_source(record: dict[str, Any], source_root: pathlib.Path) -> pathlib.Path:
    repo = source_root / record["org"] / record["name"]
    if not (repo / ".git").is_dir():
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
            raw = response.read()
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
        env = os.environ.copy()
        env[TOKEN_ENV] = token
        env["GIT_ASKPASS"] = str(askpass)
        env["GIT_ASKPASS_REQUIRE"] = "force"
        env["GIT_TERMINAL_PROMPT"] = "0"
        completed = subprocess.run(
            ["git", "push", "--set-upstream", "origin", "main"],
            cwd=repo,
            env=env,
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
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise PublicationError(
            f"{TOKEN_ENV} is required; use a short-lived least-privilege "
            "GitHub App installation token"
        )

    repo = preflight_source(record, args.source_root.resolve())
    current = ensure_repository(record, token)
    push_main(repo, token)

    print(
        json.dumps(
            {
                "published": record["full_name"],
                "repository_id": current.get("id"),
                "visibility": current.get("visibility"),
                "default_branch": current.get("default_branch"),
                "commit": record["commit"],
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
