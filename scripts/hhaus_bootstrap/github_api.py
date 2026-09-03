from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

from .constants import CONTROL_CHARS, EXPECTED_ORGANIZATION, BootstrapError
from .manifest import canonical_json_bytes

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


@dataclass(frozen=True)
class ApiResponse:
    status: int
    body: Any


class GitHubApi:
    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        if not token or any(char.isspace() for char in token):
            raise BootstrapError("repository administration token is missing or malformed")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com" or parsed.path not in {"", "/"}:
            raise BootstrapError("GitHub API base must be exactly https://api.github.com")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._opener = urllib.request.build_opener(NoRedirect())

    def _safe_error(self, value: str) -> str:
        bounded = CONTROL_CHARS.sub("?", value[:4096])
        return bounded.replace(self._token, "[REDACTED]")

    def request(self, method: str, path: str, payload: Any | None = None, accepted: Iterable[int] = (200,)) -> ApiResponse:
        if (
            not path.startswith("/")
            or path.startswith("//")
            or CONTROL_CHARS.search(path)
            or "#" in path
            or "\\" in path
        ):
            raise BootstrapError("invalid GitHub API path")
        body = None if payload is None else canonical_json_bytes(payload)
        request = urllib.request.Request(
            self._base_url + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "ai-agent-coordinator-hhaus-bootstrap",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise BootstrapError("GitHub response exceeded the 2 MB safety limit")
                parsed = json.loads(raw) if raw else None
                status = int(response.status)
        except urllib.error.HTTPError as error:
            raw = error.read(4097)
            text = raw[:4096].decode("utf-8", errors="replace")
            if error.code in accepted:
                try:
                    parsed = json.loads(text) if text else None
                except json.JSONDecodeError:
                    parsed = text
                return ApiResponse(error.code, parsed)
            raise BootstrapError(f"GitHub API {method} {path} failed with {error.code}: {self._safe_error(text)}") from None
        except urllib.error.URLError as error:
            raise BootstrapError(f"GitHub API transport failed: {self._safe_error(str(error.reason))}") from None
        if status not in set(accepted):
            raise BootstrapError(f"GitHub API {method} {path} returned unexpected status {status}")
        return ApiResponse(status, parsed)


def repository_path(name: str, suffix: str = "") -> str:
    quoted = urllib.parse.quote(name, safe="")
    return f"/repos/{EXPECTED_ORGANIZATION}/{quoted}{suffix}"


def verify_existing_repository(repo: dict[str, Any]) -> None:
    if repo.get("full_name") != f"{EXPECTED_ORGANIZATION}/{repo.get('name')}":
        raise BootstrapError("GitHub returned an unexpected repository identity")
    if repo.get("visibility") != "private" and repo.get("private") is not True:
        raise BootstrapError("existing repository visibility does not match the sealed private policy")


def ensure_repository(api: GitHubApi, repo_spec: dict[str, Any]) -> tuple[dict[str, Any], str]:
    name = repo_spec["name"]
    response = api.request("GET", repository_path(name), accepted=(200, 404))
    if response.status == 200:
        repository = response.body
        verify_existing_repository(repository)
        action = "existing"
    else:
        payload = {
            "name": name,
            "description": repo_spec["description"],
            "private": True,
            "auto_init": True,
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
        }
        created = api.request("POST", f"/orgs/{EXPECTED_ORGANIZATION}/repos", payload, accepted=(201, 422))
        if created.status == 422:
            raced = api.request("GET", repository_path(name), accepted=(200,))
            repository = raced.body
            action = "raced-existing"
        else:
            repository = created.body
            action = "created"
        verify_existing_repository(repository)
    patched = api.request(
        "PATCH",
        repository_path(name),
        {
            "description": repo_spec["description"],
            "private": True,
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
            "allow_squash_merge": True,
            "allow_merge_commit": False,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
        },
        accepted=(200,),
    ).body
    verify_existing_repository(patched)
    return patched, action


def get_ref(api: GitHubApi, name: str, branch: str, accepted: Iterable[int] = (200,)) -> ApiResponse:
    return api.request("GET", repository_path(name, f"/git/ref/heads/{urllib.parse.quote(branch, safe='') }"), accepted=accepted)


def main_has_expected_contract(api: GitHubApi, repo_name: str, digest: str) -> bool:
    path = repository_path(repo_name, "/contents/repository.contract.json?ref=main")
    response = api.request("GET", path, accepted=(200, 404))
    if response.status == 404:
        return False
    encoded = response.body.get("content", "").replace("\n", "")
    try:
        value = json.loads(base64.b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BootstrapError(f"existing repository contract is unreadable: {error}") from None
    return value.get("manifest_digest") == digest and value.get("repository") == repo_name
