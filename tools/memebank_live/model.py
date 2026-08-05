from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

API = "https://api.github.com"
ORG = "memebank"
PROJECT_TITLE = "memebank-project"
TRACKING_TITLE = "[DEN-1005] Canonical MemeBank repository fleet publication"
TRACKING_ISSUES = ("DEN-1004", "DEN-1005", "DEN-1043", "DEN-319", "DEN-1011", "DEN-1018")
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
PUBLIC_VISIBILITY_EXCEPTIONS = {".github"}


class PublicationError(RuntimeError):
    pass


class ApiError(PublicationError):
    def __init__(self, method: str, path: str, status: int, message: str):
        clean = scrub(message)[:500]
        super().__init__(f"GitHub {method} {path} returned {status}: {clean}")
        self.status = status
        self.message = clean


def scrub(value: str) -> str:
    value = re.sub(r"gh[pousr]_[A-Za-z0-9]{20,}", "[REDACTED_GITHUB_TOKEN]", value)
    value = re.sub(r"github_pat_[A-Za-z0-9_]{20,}", "[REDACTED_GITHUB_TOKEN]", value)
    value = re.sub(r"Bearer\s+[A-Za-z0-9._-]{20,}", "Bearer [REDACTED]", value, flags=re.I)
    return "".join(ch for ch in value if ch == "\n" or ord(ch) >= 32)


def encoded(value: str) -> str:
    return urllib.parse.quote(value, safe="/")


class GitHub:
    def __init__(self, token: str):
        token = token.strip()
        if len(token) < 20 or any(ch.isspace() for ch in token):
            raise PublicationError("invalid GitHub token shape")
        self._token = token

    def close(self) -> None:
        self._token = ""

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        allow: tuple[int, ...] = (),
    ) -> tuple[int, Any]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "memebank-source-v2-live-publisher/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(6):
            request = urllib.request.Request(API + path, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = response.read()
                    return response.status, json.loads(raw) if raw else None
            except urllib.error.HTTPError as error:
                raw = error.read(8192)
                try:
                    message = str(json.loads(raw).get("message", "unknown GitHub error"))
                except Exception:
                    message = raw.decode(errors="replace")
                if error.code in allow:
                    return error.code, None
                if error.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(min(2 ** (attempt + 1), 30))
                    continue
                raise ApiError(method, path, error.code, message) from None
            except urllib.error.URLError as error:
                if attempt < 5:
                    time.sleep(min(2 ** (attempt + 1), 30))
                    continue
                raise PublicationError(f"GitHub transport failed: {scrub(str(error))}") from None
        raise AssertionError("unreachable")

    def get(self, path: str, allow: tuple[int, ...] = ()) -> tuple[int, Any]:
        return self.request("GET", path, allow=allow)

    def post(self, path: str, payload: Any) -> tuple[int, Any]:
        return self.request("POST", path, payload)

    def patch(self, path: str, payload: Any) -> tuple[int, Any]:
        return self.request("PATCH", path, payload)

    def put(self, path: str, payload: Any) -> tuple[int, Any]:
        return self.request("PUT", path, payload)


@dataclass(frozen=True)
class RepoRecord:
    name: str
    source_path: str
    expected_tree: str
    expected_head: str
    tracked_entries: int
    role: str
    description: str

    @property
    def full_name(self) -> str:
        return f"{ORG}/{self.name}"


@dataclass
class Project:
    number: int
    url: str
    title: str


def run(
    command: Sequence[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=None if env is None else dict(env),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        command_name = " ".join(command[:3])
        raise PublicationError(
            f"command failed ({command_name}): {scrub(result.stderr or result.stdout)[:1000]}"
        )
    return result


def load_records(manifest: Mapping[str, Any]) -> list[RepoRecord]:
    if manifest.get("organization") != ORG:
        raise PublicationError("source-v2 organization changed")
    if manifest.get("visibility") != "private":
        raise PublicationError("source-v2 canonical repositories must remain private")
    if manifest.get("default_branch") != "main":
        raise PublicationError("source-v2 default branch changed")
    if manifest.get("repository_order") != list(EXPECTED_REPOSITORIES):
        raise PublicationError("source-v2 repository order changed")
    records = manifest.get("repositories")
    if not isinstance(records, list) or len(records) != len(EXPECTED_REPOSITORIES):
        raise PublicationError("source-v2 repository records changed")
    by_name = {record.get("name"): record for record in records if isinstance(record, dict)}
    if set(by_name) != set(EXPECTED_REPOSITORIES):
        raise PublicationError("source-v2 repository identities changed")
    result = [
        RepoRecord(
            name=name,
            source_path=str(by_name[name]["source_path"]),
            expected_tree=str(by_name[name]["expected_tree"]),
            expected_head=str(by_name[name]["expected_head"]),
            tracked_entries=int(by_name[name]["tracked_entries"]),
            role=str(by_name[name]["role"]),
            description=str(by_name[name]["description"]),
        )
        for name in EXPECTED_REPOSITORIES
    ]
    if result[-1].name != "memebank-monorepo":
        raise PublicationError("monorepo must remain last")
    return result
