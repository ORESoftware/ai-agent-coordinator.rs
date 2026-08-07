#!/usr/bin/env python3
"""Fail-closed publisher for one deterministic HypeSiege/StreemPilot repository.

Plan mode is credential-free and network-free. Execute mode requires one exact
repository confirmation, an independently reconstructed source tree, and a
short-lived least-privilege GitHub App installation token supplied only through
the environment.
"""

from __future__ import annotations

import argparse
import hashlib
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
from collections.abc import Iterable
from typing import Any

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "ai-agent-coordinator-hypesiege-streempilot-publisher"
TOKEN_ENV = "GITHUB_REPOSITORY_ADMIN_TOKEN"
ALLOWED_ORGS = frozenset({"hypesiege", "streempilot"})
EXPECTED_GENERATOR_SHA256 = "a57b00961ee57ae09bf3bb2e2d09afbdd1ddbbbde832b027802f82a1fc5dfa84"
EXPECTED_GENERATED_AT = "2026-07-31T00:00:00-04:00"
EXPECTED_PUBLICATION_STATUS = "deterministic histories sealed; remote authorization required"
EXPECTED_REPOSITORIES = 32
EXPECTED_FILES = 888
EXPECTED_GITLINKS = 30
EXPECTED_ORGANIZATIONS = {"hypesiege": 15, "streempilot": 17}
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_API_REQUEST_BYTES = 64 * 1024
MAX_API_RESPONSE_BYTES = 256 * 1024
MAX_ERROR_DETAIL_BYTES = 4096
MAX_REPORT_BYTES = 1024 * 1024

MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "generated_at",
        "generator_sha256",
        "default_branch",
        "repository_count",
        "total_tracked_files",
        "total_gitlinks",
        "organizations",
        "publication_status",
        "repositories",
    }
)
RECORD_KEYS = frozenset(
    {
        "org",
        "name",
        "full_name",
        "kind",
        "commit",
        "files",
        "remote",
        "description",
        "visibility",
        "default_branch",
        "gitlinks",
    }
)
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
KIND_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_]{20,512}$")
API_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$")
SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]+\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]+\b"),
)


class PublicationError(RuntimeError):
    """The requested publication violated a reviewed invariant."""


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward authorization to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def sanitize_detail(value: object, *, token: str | None = None) -> str:
    detail = str(value)
    if token:
        detail = detail.replace(token, "[REDACTED]")
    for pattern in SECRET_PATTERNS:
        detail = pattern.sub("[REDACTED]", detail)
    detail = "".join(
        character if character in "\n\t" or ord(character) >= 32 else "?"
        for character in detail
    )
    return detail.strip()[:MAX_ERROR_DETAIL_BYTES]


def run(args: list[str], cwd: pathlib.Path) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = sanitize_detail(completed.stderr or completed.stdout)
        raise PublicationError(f"{' '.join(args)} failed in {cwd}: {detail}")
    return completed.stdout


def reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def require_exact_keys(value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise PublicationError(
            f"{label} keys changed; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def read_json_document(path: pathlib.Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise PublicationError("fleet manifest must be a regular non-symlink file")
        size = path.stat().st_size
        if size <= 0 or size > MAX_MANIFEST_BYTES:
            raise PublicationError("fleet manifest size is outside the approved bound")
        raw = path.read_bytes()
        if len(raw) != size:
            raise PublicationError("fleet manifest changed while being read")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
    except PublicationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublicationError(f"cannot read fleet manifest: {sanitize_detail(error)}") from error
    if not isinstance(payload, dict):
        raise PublicationError("fleet manifest root must be an object")
    return payload


def validate_record(record: object, index: int) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise PublicationError(f"repository record {index} must be an object")
    require_exact_keys(record, RECORD_KEYS, f"repository record {index}")

    org = record["org"]
    name = record["name"]
    full_name = record["full_name"]
    kind = record["kind"]
    commit = record["commit"]
    files = record["files"]
    gitlinks = record["gitlinks"]
    description = record["description"]

    if not isinstance(org, str) or org not in ALLOWED_ORGS:
        raise PublicationError(f"repository record {index} uses an unapproved organization")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise PublicationError(f"repository record {index} has an invalid name")
    if full_name != f"{org}/{name}":
        raise PublicationError(f"repository record {index} full_name drifted")
    if not isinstance(kind, str) or not KIND_RE.fullmatch(kind):
        raise PublicationError(f"repository record {index} has an invalid kind")
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise PublicationError(f"repository record {index} has an invalid commit")
    if type(files) is not int or not 1 <= files <= 10_000:
        raise PublicationError(f"repository record {index} has an invalid file count")
    if type(gitlinks) is not int or gitlinks < 0:
        raise PublicationError(f"repository record {index} has an invalid gitlink count")
    if record["default_branch"] != "main":
        raise PublicationError(f"repository record {index} default branch must be main")
    if record["visibility"] != "public":
        raise PublicationError(f"repository record {index} must remain public")
    if record["remote"] != f"https://github.com/{full_name}.git":
        raise PublicationError(f"repository record {index} remote is not canonical HTTPS")
    if (
        not isinstance(description, str)
        or description != description.strip()
        or not description
        or len(description) > 350
        or any(ord(character) < 32 for character in description)
    ):
        raise PublicationError(f"repository record {index} has an invalid description")

    is_monorepo = kind == "monorepo"
    if is_monorepo != (name == f"{org}-monorepo"):
        raise PublicationError(f"repository record {index} monorepo identity drifted")
    expected_gitlinks = 14 if org == "hypesiege" else 16
    if gitlinks != (expected_gitlinks if is_monorepo else 0):
        raise PublicationError(f"repository record {index} gitlink topology drifted")
    return record


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    manifest = read_json_document(path)
    require_exact_keys(manifest, MANIFEST_KEYS, "fleet manifest")
    repositories = manifest["repositories"]
    if manifest["schema_version"] != 2 or not isinstance(repositories, list):
        raise PublicationError("unsupported or malformed fleet manifest")
    exact_values = {
        "generated_at": EXPECTED_GENERATED_AT,
        "generator_sha256": EXPECTED_GENERATOR_SHA256,
        "default_branch": "main",
        "repository_count": EXPECTED_REPOSITORIES,
        "total_tracked_files": EXPECTED_FILES,
        "total_gitlinks": EXPECTED_GITLINKS,
        "organizations": EXPECTED_ORGANIZATIONS,
        "publication_status": EXPECTED_PUBLICATION_STATUS,
    }
    for key, expected in exact_values.items():
        if manifest[key] != expected:
            raise PublicationError(f"fleet manifest {key} changed")
    if len(repositories) != EXPECTED_REPOSITORIES:
        raise PublicationError("repository_count does not match repositories")

    validated = [validate_record(record, index) for index, record in enumerate(repositories)]
    full_names = [record["full_name"] for record in validated]
    if len(set(full_names)) != len(full_names):
        raise PublicationError("fleet manifest contains duplicate repositories")
    expected_order = sorted(
        validated,
        key=lambda record: (record["org"], record["kind"] == "monorepo", record["name"]),
    )
    if validated != expected_order:
        raise PublicationError("fleet manifest order changed")
    actual_orgs = {
        org: sum(record["org"] == org for record in validated)
        for org in sorted(ALLOWED_ORGS)
    }
    if actual_orgs != EXPECTED_ORGANIZATIONS:
        raise PublicationError("repository records do not match organization totals")
    if sum(record["files"] for record in validated) != EXPECTED_FILES:
        raise PublicationError("repository records do not match tracked-file total")
    if sum(record["gitlinks"] for record in validated) != EXPECTED_GITLINKS:
        raise PublicationError("repository records do not match gitlink total")
    for org in sorted(ALLOWED_ORGS):
        if [record for record in validated if record["org"] == org][-1]["name"] != f"{org}-monorepo":
            raise PublicationError(f"{org} monorepo must publish last")
    return manifest


def select_record(manifest: dict[str, Any], full_name: str) -> dict[str, Any]:
    matches = [record for record in manifest["repositories"] if record["full_name"] == full_name]
    if len(matches) != 1:
        raise PublicationError(f"manifest must contain exactly one {full_name!r} record")
    return matches[0]


def staged_gitlinks(repo: pathlib.Path) -> dict[str, str]:
    gitlinks: dict[str, str] = {}
    for line in run(["git", "ls-files", "--stage"], repo).splitlines():
        try:
            metadata, path = line.split("\t", 1)
            mode, object_id, _stage = metadata.split()
        except ValueError as error:
            raise PublicationError(f"malformed Git index entry in {repo}") from error
        if mode == "160000":
            if not COMMIT_RE.fullmatch(object_id):
                raise PublicationError(f"invalid gitlink object in {repo}: {path}")
            gitlinks[path] = object_id
    return gitlinks


def gitmodule_paths(repo: pathlib.Path) -> set[str]:
    path = repo / ".gitmodules"
    if not path.is_file():
        return set()
    completed = subprocess.run(
        ["git", "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if completed.returncode not in {0, 1}:
        raise PublicationError(f"failed to parse {path}: {sanitize_detail(completed.stderr)}")
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
        "status": run(["git", "status", "--porcelain=v1", "--untracked-files=all"], repo),
        "files": len(run(["git", "ls-files"], repo).splitlines()),
        "commits": int(run(["git", "rev-list", "--count", "HEAD"], repo).strip()),
    }
    if checks["branch"] != "main":
        raise PublicationError(f"{record['full_name']} is not on main")
    if checks["head"] != record["commit"]:
        raise PublicationError(f"{record['full_name']} head mismatch")
    if checks["origin"] != record["remote"]:
        raise PublicationError(f"{record['full_name']} origin mismatch")
    if checks["status"]:
        raise PublicationError(f"{record['full_name']} working tree is dirty")
    if checks["files"] != record["files"]:
        raise PublicationError(f"{record['full_name']} tracked-file count mismatch")
    if checks["commits"] != 1:
        raise PublicationError(f"{record['full_name']} must be one deterministic root commit")

    gitlinks = staged_gitlinks(repo)
    if len(gitlinks) != record["gitlinks"]:
        raise PublicationError(f"{record['full_name']} gitlink count mismatch")
    if gitlinks:
        if gitmodule_paths(repo) != set(gitlinks):
            raise PublicationError(f"{record['full_name']} .gitmodules/index mismatch")
        for path, expected_commit in gitlinks.items():
            checkout = repo / path
            if not (checkout / ".git").exists():
                raise PublicationError(f"unmaterialized submodule checkout: {path}")
            if run(["git", "rev-parse", "HEAD"], checkout).strip() != expected_commit:
                raise PublicationError(f"submodule checkout drift for {path}")
            if run(["git", "status", "--porcelain=v1", "--untracked-files=all"], checkout):
                raise PublicationError(f"submodule checkout is dirty: {path}")
    run(["git", "diff", "--check", "HEAD"], repo)
    run(["git", "fsck", "--full", "--no-dangling"], repo)
    return repo


def validate_token(token: str | None) -> str:
    if token is None or not TOKEN_RE.fullmatch(token):
        raise PublicationError(
            f"{TOKEN_ENV} must be a non-whitespace GitHub token between 20 and 512 characters"
        )
    return token


def validate_api_path(path: str) -> None:
    if not API_PATH_RE.fullmatch(path) or ".." in path or "//" in path:
        raise PublicationError("refusing an invalid GitHub API path")


def request_json(
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
    *,
    opener: Any | None = None,
) -> tuple[int, Any | None]:
    method = method.upper()
    if method not in {"GET", "POST", "PATCH"}:
        raise PublicationError(f"unsupported GitHub API method: {method}")
    validate_api_path(path)
    data = None
    if body is not None:
        if not isinstance(body, dict):
            raise PublicationError("GitHub API request body must be an object")
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_API_REQUEST_BYTES:
            raise PublicationError("GitHub API request exceeded 64 KiB")
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
    client = opener or urllib.request.build_opener(NoRedirectHandler())
    try:
        with client.open(request, timeout=30) as response:
            raw = response.read(MAX_API_RESPONSE_BYTES + 1)
            if len(raw) > MAX_API_RESPONSE_BYTES:
                raise PublicationError("GitHub API response exceeded 256 KiB")
            if not raw:
                return response.status, None
            if response.headers.get_content_type() not in {
                "application/json",
                "application/problem+json",
            }:
                raise PublicationError("GitHub API returned an unexpected content type")
            try:
                payload = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
            except (UnicodeError, json.JSONDecodeError) as error:
                raise PublicationError("GitHub API returned malformed JSON") from error
            return response.status, payload
    except urllib.error.HTTPError as error:
        raw = error.read(MAX_ERROR_DETAIL_BYTES).decode("utf-8", errors="replace")
        if error.code == 404 and method == "GET":
            return 404, None
        detail = sanitize_detail(raw or error.reason, token=token)
        raise PublicationError(f"GitHub API {error.code} for {method} {path}: {detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        raise PublicationError(
            f"GitHub API unavailable for {method} {path}: "
            f"{sanitize_detail(reason, token=token)}"
        ) from error


def verify_repository_metadata(record: dict[str, Any], current: object) -> dict[str, Any]:
    if not isinstance(current, dict):
        raise PublicationError("GitHub did not return repository metadata")
    if type(current.get("id")) is not int or current["id"] <= 0:
        raise PublicationError("GitHub returned an invalid repository id")
    full_name = current.get("full_name")
    if not isinstance(full_name, str) or full_name.casefold() != record["full_name"].casefold():
        raise PublicationError("GitHub returned an unexpected repository")
    owner = current.get("owner")
    if not isinstance(owner, dict) or not isinstance(owner.get("login"), str):
        raise PublicationError("GitHub returned malformed repository owner metadata")
    if owner["login"].casefold() != record["org"].casefold():
        raise PublicationError("GitHub returned an unexpected repository owner")
    if current.get("visibility") != "public" or current.get("private") is not False:
        raise PublicationError("repository visibility must remain public")
    if current.get("fork") is not False:
        raise PublicationError("publisher refuses fork or malformed repositories")
    if current.get("archived") is not False or current.get("disabled") is not False:
        raise PublicationError("publisher refuses archived, disabled, or malformed repositories")
    return current


def ensure_repository(record: dict[str, Any], token: str) -> tuple[dict[str, Any], bool]:
    status, current = request_json("GET", f"/repos/{record['full_name']}", token)
    created = False
    if status == 404:
        status, current = request_json(
            "POST",
            f"/orgs/{record['org']}/repos",
            token,
            {
                "name": record["name"],
                "description": record["description"],
                "private": False,
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
        if status != 201:
            raise PublicationError(f"GitHub repository creation returned HTTP {status}")
        created = True
    elif status != 200:
        raise PublicationError(f"GitHub repository lookup returned HTTP {status}")
    return verify_repository_metadata(record, current), created


def remote_main_commit(full_name: str, token: str) -> str | None:
    status, payload = request_json("GET", f"/repos/{full_name}/commits/main", token)
    if status == 404:
        return None
    if status != 200 or not isinstance(payload, dict):
        raise PublicationError(f"GitHub returned invalid main metadata for {full_name}")
    sha = payload.get("sha")
    if not isinstance(sha, str) or not COMMIT_RE.fullmatch(sha):
        raise PublicationError(f"GitHub returned invalid main commit for {full_name}")
    return sha


def remote_branch_names(full_name: str, token: str) -> list[str]:
    status, payload = request_json("GET", f"/repos/{full_name}/branches", token)
    if status == 404:
        return []
    if status != 200 or not isinstance(payload, list):
        raise PublicationError(f"GitHub returned invalid branch metadata for {full_name}")
    names: list[str] = []
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise PublicationError(f"GitHub returned malformed branch metadata for {full_name}")
        names.append(item["name"])
    return names


def apply_repository_settings(record: dict[str, Any], token: str) -> dict[str, Any]:
    status, current = request_json(
        "PATCH",
        f"/repos/{record['full_name']}",
        token,
        {
            "description": record["description"],
            "default_branch": "main",
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
        },
    )
    if status != 200:
        raise PublicationError(f"GitHub repository settings returned HTTP {status}")
    current = verify_repository_metadata(record, current)
    if current.get("default_branch") != "main":
        raise PublicationError("GitHub did not retain main as the default branch")
    if current.get("allow_rebase_merge") is not False:
        raise PublicationError("GitHub did not disable rebase merging")
    if current.get("delete_branch_on_merge") is not True:
        raise PublicationError("GitHub did not enable merged-branch deletion")
    return current


def verify_monorepo_children(
    manifest: dict[str, Any], record: dict[str, Any], token: str
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
        environment.update(
            {
                TOKEN_ENV: token,
                "GIT_ASKPASS": str(askpass),
                "GIT_ASKPASS_REQUIRE": "force",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_SSH_COMMAND": "false",
                "HOME": str(directory),
                "XDG_CONFIG_HOME": str(directory),
            }
        )
        completed = subprocess.run(
            [
                "git",
                "-c",
                "credential.helper=",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "http.followRedirects=false",
                "push",
                "--porcelain",
                "--set-upstream",
                "origin",
                "HEAD:refs/heads/main",
            ],
            cwd=repo,
            env=environment,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        if completed.returncode != 0:
            detail = sanitize_detail(completed.stderr or completed.stdout, token=token)
            raise PublicationError(f"git push failed for {repo.name}: {detail}")
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def publish_repository(
    manifest: dict[str, Any],
    record: dict[str, Any],
    repo: pathlib.Path,
    token: str,
) -> dict[str, Any]:
    verify_monorepo_children(manifest, record, token)
    current, created = ensure_repository(record, token)
    actual = remote_main_commit(record["full_name"], token)
    if actual == record["commit"]:
        current = apply_repository_settings(record, token)
        return {
            "state": "already_verified",
            "published": record["full_name"],
            "repository_id": current["id"],
            "visibility": current["visibility"],
            "default_branch": current["default_branch"],
            "commit": actual,
            "created": created,
            "pushed": False,
            "verified": True,
        }
    if actual is not None:
        raise PublicationError(
            f"refusing divergent remote main for {record['full_name']}: "
            f"{actual} != {record['commit']}"
        )
    branches = remote_branch_names(record["full_name"], token)
    if branches:
        raise PublicationError(f"refusing repository with non-main history: {branches[:10]}")
    push_main(repo, token)
    actual = remote_main_commit(record["full_name"], token)
    if actual != record["commit"]:
        raise PublicationError(
            f"remote verification failed for {record['full_name']}: "
            f"{actual!r} != {record['commit']}"
        )
    current = apply_repository_settings(record, token)
    return {
        "state": "published",
        "published": record["full_name"],
        "repository_id": current["id"],
        "visibility": current["visibility"],
        "default_branch": current["default_branch"],
        "commit": actual,
        "created": created,
        "pushed": True,
        "verified": True,
    }


def manifest_digest(path: pathlib.Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise PublicationError("fleet manifest digest source must be a regular file")
    data = path.read_bytes()
    if not data or len(data) > MAX_MANIFEST_BYTES:
        raise PublicationError("fleet manifest digest source is outside the approved bound")
    return hashlib.sha256(data).hexdigest()


def write_json_atomic(path: pathlib.Path, payload: dict[str, Any]) -> None:
    if path.exists() and path.is_symlink():
        raise PublicationError("refusing to replace a symlink report path")
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise PublicationError("publication report exceeded 1 MiB")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = pathlib.Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
    parser.add_argument("--report-out", type=pathlib.Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    record = select_record(manifest, args.repository)
    plan = {
        "mode": "execute" if args.execute else "plan",
        "repository": record["full_name"],
        "commit": record["commit"],
        "visibility": record["visibility"],
        "remote": record["remote"],
        "files": record["files"],
        "gitlinks": record["gitlinks"],
    }
    print(json.dumps(plan, indent=2))
    if not args.execute:
        if args.report_out:
            write_json_atomic(
                args.report_out,
                {**plan, "manifest_sha256": manifest_digest(args.manifest), "network_mutation": False},
            )
        return 0
    if args.confirm_repository != record["full_name"]:
        raise PublicationError("--confirm-repository must exactly equal the requested owner/name")
    if args.source_root is None:
        raise PublicationError("--source-root is required in execute mode")
    repo = preflight_source(record, args.source_root.resolve())
    token = validate_token(os.environ.get(TOKEN_ENV))
    result = publish_repository(manifest, record, repo, token)
    result["manifest_sha256"] = manifest_digest(args.manifest)
    print(json.dumps(result, indent=2))
    if args.report_out:
        write_json_atomic(args.report_out, result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublicationError as error:
        raise SystemExit(f"publication refused: {sanitize_detail(error)}") from None
