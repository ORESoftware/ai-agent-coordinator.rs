#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from deep_test_fleet_templates import (
    BOOTSTRAP_OPERATION,
    Fleet,
    SuiteSpec,
    generate_repository_files,
    load_fleet,
    validate_all_templates,
    validate_generated_files,
)

API = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "test-org-deep-fleet-bootstrap/1"
FOUNDATION_BRANCH = "agent/deep-test-foundation-20260808"
CHECK_NAME = "verify"
MAX_ERROR_BYTES = 4096
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
TOKEN_PATTERN = re.compile(r"(?i)(?:gh[pousr]_[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._~+/=-]{16,})")


class BootstrapError(RuntimeError):
    pass


class ApiError(BootstrapError):
    def __init__(self, method: str, path: str, status: int, message: str) -> None:
        super().__init__(f"GitHub {method} {path} returned {status}: {message[:500]}")
        self.method = method
        self.path = path
        self.status = status
        self.message = message[:500]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def redact(value: str, token: str = "") -> str:
    result = value
    if token:
        result = result.replace(token, "[REDACTED]")
    return TOKEN_PATTERN.sub("[REDACTED]", result).replace("\x00", "?")[:1000]


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def git_blob_sha(content: str) -> str:
    raw = content.encode("utf-8")
    return hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()  # noqa: S324


def read_json_response(response: Any) -> Any:
    raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise BootstrapError(
            f"GitHub response exceeds the {MAX_RESPONSE_BYTES}-byte safety limit"
        )
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError as error:
        raise BootstrapError(f"GitHub returned malformed JSON: {error.msg}") from None


class GitHub:
    def __init__(self, token: str, api_base: str = API) -> None:
        if not token or any(character.isspace() for character in token):
            raise BootstrapError("GitHub token is missing or contains whitespace")
        parsed = urllib.parse.urlparse(api_base)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com":
            raise BootstrapError("GitHub API base must be exact https://api.github.com")
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.opener = urllib.request.build_opener(NoRedirect())
        self.remaining: int | None = None

    def request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        *,
        allow: Iterable[int] = (),
        timeout: int = 60,
    ) -> tuple[int, Any, dict[str, str]]:
        if not path.startswith("/") or "\n" in path or "\r" in path:
            raise BootstrapError(f"unsafe GitHub API path: {path!r}")
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        allowed = set(allow)
        for attempt in range(5):
            request = urllib.request.Request(
                self.api_base + path,
                data=body,
                headers=headers,
                method=method,
            )
            try:
                with self.opener.open(request, timeout=timeout) as response:
                    response_headers = dict(response.headers.items())
                    remaining = response.headers.get("X-RateLimit-Remaining")
                    if remaining and remaining.isdigit():
                        self.remaining = int(remaining)
                    parsed = read_json_response(response)
                    return response.status, parsed, response_headers
            except urllib.error.HTTPError as error:
                raw = error.read(MAX_ERROR_BYTES)
                try:
                    parsed_error = json.loads(raw)
                    message = str(parsed_error.get("message", "unknown error"))
                except Exception:
                    message = raw.decode("utf-8", errors="replace")
                if error.code in allowed:
                    return error.code, None, dict(error.headers.items())
                if error.code in {429, 500, 502, 503, 504} and attempt < 4:
                    retry_after = error.headers.get("Retry-After")
                    delay = int(retry_after) if retry_after and retry_after.isdigit() else min(2 ** (attempt + 1), 20)
                    time.sleep(delay)
                    continue
                raise ApiError(method, path, error.code, redact(message, self.token)) from None
            except urllib.error.URLError as error:
                if attempt < 4:
                    time.sleep(min(2 ** (attempt + 1), 20))
                    continue
                raise BootstrapError(f"GitHub transport failed: {redact(str(error), self.token)}") from None
        raise AssertionError("unreachable")

    def get(self, path: str, *, allow: Iterable[int] = ()) -> tuple[int, Any, dict[str, str]]:
        return self.request("GET", path, allow=allow)

    def post(self, path: str, payload: Any, *, allow: Iterable[int] = ()) -> tuple[int, Any, dict[str, str]]:
        return self.request("POST", path, payload, allow=allow)

    def patch(self, path: str, payload: Any, *, allow: Iterable[int] = ()) -> tuple[int, Any, dict[str, str]]:
        return self.request("PATCH", path, payload, allow=allow)

    def put(self, path: str, payload: Any, *, allow: Iterable[int] = ()) -> tuple[int, Any, dict[str, str]]:
        return self.request("PUT", path, payload, allow=allow)


@dataclass(frozen=True)
class Target:
    organization: str
    spec: SuiteSpec

    @property
    def repository(self) -> str:
        return f"{self.organization}/{self.spec.name}"


@dataclass
class Result:
    repository: str
    suite: str
    state: str = "planned"
    repository_url: str | None = None
    created: bool = False
    default_branch: str | None = None
    branch: str | None = None
    head_sha: str | None = None
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    check_state: str | None = None
    merged: bool = False
    default_branch_sha: str | None = None
    error: str | None = None


@dataclass
class RunLedger:
    schema_version: int = 1
    operation: str = BOOTSTRAP_OPERATION
    started_at_epoch: int = field(default_factory=lambda: int(time.time()))
    finished_at_epoch: int | None = None
    preflight_complete: bool = False
    repositories: list[Result] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "repositories_total": len(self.repositories),
            "repositories_created": sum(1 for item in self.repositories if item.created),
            "repositories_existing": sum(1 for item in self.repositories if not item.created and item.state != "planned"),
            "pull_requests_created_or_reused": sum(1 for item in self.repositories if item.pull_request_number is not None),
            "pull_requests_merged": sum(1 for item in self.repositories if item.merged),
            "pull_requests_open": sum(1 for item in self.repositories if item.pull_request_number and not item.merged),
            "already_initialized": sum(1 for item in self.repositories if item.state == "already_initialized"),
            "verified_on_default_branch": sum(
                1 for item in self.repositories if item.state in {"already_initialized", "merged_verified"}
            ),
            "failures": len(self.failures),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "started_at_epoch": self.started_at_epoch,
            "finished_at_epoch": self.finished_at_epoch,
            "preflight_complete": self.preflight_complete,
            "summary": self.summary(),
            "repositories": [asdict(item) for item in self.repositories],
            "failures": self.failures,
        }

    def write(self, path: Path) -> None:
        self.finished_at_epoch = int(time.time())
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)


@dataclass(frozen=True)
class ExistingRepository:
    target: Target
    data: dict[str, Any] | None

    @property
    def exists(self) -> bool:
        return self.data is not None


def repository_path(target: Target) -> str:
    return f"/repos/{quote(target.organization)}/{quote(target.spec.name)}"


def exact_repository(data: dict[str, Any], target: Target) -> None:
    if str(data.get("full_name", "")).lower() != target.repository.lower():
        raise BootstrapError(f"repository identity mismatch for {target.repository}")
    owner = data.get("owner") or {}
    if str(owner.get("login", "")).lower() != target.organization.lower():
        raise BootstrapError(f"repository owner mismatch for {target.repository}")
    if data.get("private") is not False or data.get("visibility") != "public":
        raise BootstrapError(f"visibility mismatch for {target.repository}; expected public")
    if data.get("archived") is True or data.get("disabled") is True:
        raise BootstrapError(f"repository is archived or disabled: {target.repository}")


def preflight(github: GitHub, fleet: Fleet, targets: list[Target]) -> dict[str, ExistingRepository]:
    _, user, _ = github.get("/user")
    if user.get("login") != fleet.owner_login or int(user.get("id", -1)) != fleet.owner_id:
        raise BootstrapError(
            f"authenticated GitHub identity drift: expected {fleet.owner_login}/{fleet.owner_id}"
        )

    for organization in fleet.organizations:
        _, org, _ = github.get(f"/orgs/{quote(organization)}")
        if str(org.get("login", "")).lower() != organization.lower():
            raise BootstrapError(f"organization identity mismatch: {organization}")
        _, membership, _ = github.get(f"/user/memberships/orgs/{quote(organization)}")
        if membership.get("state") != "active" or membership.get("role") != "admin":
            raise BootstrapError(
                f"authenticated identity lacks active owner/admin membership: {organization}"
            )

    inventory: dict[str, ExistingRepository] = {}
    for target in targets:
        status, data, _ = github.get(repository_path(target), allow=(404,))
        if status == 404:
            inventory[target.repository] = ExistingRepository(target, None)
            continue
        exact_repository(data, target)
        inventory[target.repository] = ExistingRepository(target, data)
    return inventory


def create_repository(github: GitHub, target: Target) -> dict[str, Any]:
    payload = {
        "name": target.spec.name,
        "description": target.spec.description,
        "private": False,
        "auto_init": True,
        "has_issues": True,
        "has_projects": False,
        "has_wiki": False,
        "has_discussions": False,
        "allow_squash_merge": True,
        "allow_merge_commit": True,
        "allow_rebase_merge": False,
        "delete_branch_on_merge": True,
    }
    _, data, _ = github.post(f"/orgs/{quote(target.organization)}/repos", payload)
    exact_repository(data, target)
    return data


def configure_repository(github: GitHub, target: Target) -> dict[str, Any]:
    _, data, _ = github.patch(
        repository_path(target),
        {
            "description": target.spec.description,
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
            "has_discussions": False,
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
        },
    )
    exact_repository(data, target)
    return data


def get_ref(github: GitHub, target: Target, branch: str, *, allow_missing: bool = False) -> dict[str, Any] | None:
    status, data, _ = github.get(
        f"{repository_path(target)}/git/ref/heads/{quote(branch)}",
        allow=(404,) if allow_missing else (),
    )
    return None if status == 404 else data


def wait_for_ref(github: GitHub, target: Target, branch: str, attempts: int = 30) -> dict[str, Any]:
    for attempt in range(attempts):
        data = get_ref(github, target, branch, allow_missing=True)
        if data is not None:
            return data
        time.sleep(min(1 + attempt // 10, 3))
    raise BootstrapError(f"timed out waiting for {target.repository} branch {branch}")


def ensure_main_default(github: GitHub, target: Target, repository: dict[str, Any], created: bool) -> dict[str, Any]:
    default_branch = str(repository.get("default_branch") or "")
    if not default_branch:
        raise BootstrapError(f"repository default branch missing: {target.repository}")
    wait_for_ref(github, target, default_branch)
    if default_branch == "main":
        return repository
    if not created:
        # Existing repositories retain their reviewed default branch; new target names are expected to be absent.
        return repository
    source = wait_for_ref(github, target, default_branch)
    main = get_ref(github, target, "main", allow_missing=True)
    if main is None:
        github.post(
            f"{repository_path(target)}/git/refs",
            {"ref": "refs/heads/main", "sha": source["object"]["sha"]},
        )
    _, updated, _ = github.patch(repository_path(target), {"default_branch": "main"})
    return updated


def commit_tree(github: GitHub, target: Target, commit_sha: str) -> tuple[str, dict[str, str]]:
    _, commit, _ = github.get(f"{repository_path(target)}/git/commits/{quote(commit_sha)}")
    tree_sha = commit["tree"]["sha"]
    _, tree, _ = github.get(
        f"{repository_path(target)}/git/trees/{quote(tree_sha)}?recursive=1"
    )
    if tree.get("truncated"):
        raise BootstrapError(f"repository tree is truncated: {target.repository}")
    paths = {
        item["path"]: item["sha"]
        for item in tree.get("tree", [])
        if item.get("type") == "blob" and isinstance(item.get("path"), str)
    }
    return tree_sha, paths


def expected_shas(files: dict[str, str]) -> dict[str, str]:
    return {path: git_blob_sha(content) for path, content in files.items()}


def verify_tree_exact(paths: dict[str, str], files: dict[str, str]) -> tuple[bool, list[str], list[str]]:
    expected = expected_shas(files)
    missing = sorted(path for path in expected if path not in paths)
    mismatched = sorted(path for path, sha in expected.items() if path in paths and paths[path] != sha)
    return not missing and not mismatched, missing, mismatched


def branch_or_foundation_commit(
    github: GitHub,
    target: Target,
    files: dict[str, str],
    default_branch: str,
    *,
    created: bool,
    tracking_issue: str,
) -> tuple[str, str, bool]:
    base_ref = wait_for_ref(github, target, default_branch)
    base_sha = base_ref["object"]["sha"]
    base_tree_sha, base_paths = commit_tree(github, target, base_sha)
    exact, missing, mismatched = verify_tree_exact(base_paths, files)
    if exact:
        return base_sha, default_branch, True

    allowed_mismatch = {"README.md"} if created else set()
    unsafe = sorted(set(mismatched) - allowed_mismatch)
    if unsafe:
        raise BootstrapError(
            f"managed-path conflict on {target.repository}: {', '.join(unsafe)}; semantic reconciliation required"
        )
    # Existing exact managed files plus missing additions are safe; unrelated paths remain untouched.
    branch_ref = get_ref(github, target, FOUNDATION_BRANCH, allow_missing=True)
    if branch_ref is not None:
        branch_sha = branch_ref["object"]["sha"]
        _, branch_paths = commit_tree(github, target, branch_sha)
        branch_exact, branch_missing, branch_mismatched = verify_tree_exact(branch_paths, files)
        if not branch_exact:
            raise BootstrapError(
                f"existing foundation branch drift on {target.repository}: "
                f"missing={branch_missing} mismatched={branch_mismatched}"
            )
        return branch_sha, FOUNDATION_BRANCH, False

    tree_entries = [
        {"path": path, "mode": "100644", "type": "blob", "content": content}
        for path, content in sorted(files.items())
    ]
    _, tree, _ = github.post(
        f"{repository_path(target)}/git/trees",
        {"base_tree": base_tree_sha, "tree": tree_entries},
    )
    _, commit, _ = github.post(
        f"{repository_path(target)}/git/commits",
        {
            "message": f"test: add {target.spec.suite} deep-test foundation\n\nTracking: {tracking_issue}",
            "tree": tree["sha"],
            "parents": [base_sha],
        },
    )
    branch_sha = commit["sha"]
    status, _, _ = github.post(
        f"{repository_path(target)}/git/refs",
        {"ref": f"refs/heads/{FOUNDATION_BRANCH}", "sha": branch_sha},
        allow=(422,),
    )
    if status == 422:
        raced = get_ref(github, target, FOUNDATION_BRANCH)
        if raced["object"]["sha"] != branch_sha:
            _, raced_paths = commit_tree(github, target, raced["object"]["sha"])
            raced_exact, _, _ = verify_tree_exact(raced_paths, files)
            if not raced_exact:
                raise BootstrapError(f"foundation branch race produced divergent content: {target.repository}")
            branch_sha = raced["object"]["sha"]
    return branch_sha, FOUNDATION_BRANCH, False


def find_or_create_pull_request(
    github: GitHub,
    target: Target,
    default_branch: str,
    head_sha: str,
    tracking_issue: str,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "state": "all",
            "head": f"{target.organization}:{FOUNDATION_BRANCH}",
            "base": default_branch,
            "per_page": "100",
        }
    )
    _, pulls, _ = github.get(f"{repository_path(target)}/pulls?{query}")
    if pulls:
        pull = sorted(pulls, key=lambda item: int(item["number"]))[-1]
        if pull.get("merged_at"):
            return pull
        if pull.get("state") != "open":
            raise BootstrapError(f"foundation pull request was closed without merge: {target.repository}")
        if pull.get("head", {}).get("sha") != head_sha:
            raise BootstrapError(f"foundation pull request head drift: {target.repository}")
        return pull
    body = (
        f"## Deep test foundation\n\n"
        f"Adds the `{target.spec.suite}` deep-testing foundation for `{target.repository}`. "
        "The source is deterministic, dependency-light, and was validated before publication.\n\n"
        "### Coverage\n\n"
        f"- {target.spec.description}\n"
        "- pinned, read-only GitHub Actions;\n"
        "- repository contract and credential/conflict-marker scans;\n"
        "- semantic conflict instructions requiring merge-base and relevant history inspection.\n\n"
        f"Tracking: {tracking_issue}\n\n"
        "This exact head may merge only after its `verify` check succeeds."
    )
    _, pull, _ = github.post(
        f"{repository_path(target)}/pulls",
        {
            "title": f"test: add {target.spec.suite} deep-test foundation",
            "head": FOUNDATION_BRANCH,
            "base": default_branch,
            "body": body,
            "draft": False,
            "maintainer_can_modify": True,
        },
    )
    if pull.get("head", {}).get("sha") != head_sha:
        raise BootstrapError(f"created pull request head mismatch: {target.repository}")
    return pull


def check_state(github: GitHub, target: Target, head_sha: str) -> tuple[str, str]:
    _, payload, _ = github.get(
        f"{repository_path(target)}/commits/{quote(head_sha)}/check-runs?per_page=100"
    )
    checks = [item for item in payload.get("check_runs", []) if item.get("name") == CHECK_NAME]
    if not checks:
        return "pending", "verify check has not appeared"
    checks.sort(key=lambda item: str(item.get("started_at") or item.get("created_at") or ""))
    check = checks[-1]
    if check.get("status") != "completed":
        return "pending", str(check.get("status") or "queued")
    conclusion = str(check.get("conclusion") or "unknown")
    if conclusion in {"success", "neutral", "skipped"}:
        return "success", conclusion
    return "failure", conclusion


def wait_for_all_checks(
    github: GitHub,
    targets_by_repository: dict[str, Target],
    results: list[Result],
    timeout_seconds: int,
) -> None:
    pending = {
        item.repository: item
        for item in results
        if item.pull_request_number is not None and not item.merged and item.head_sha is not None
    }
    deadline = time.monotonic() + timeout_seconds
    round_number = 0
    while pending and time.monotonic() < deadline:
        round_number += 1
        completed: list[str] = []
        for repository, result in list(pending.items()):
            state, detail = check_state(github, targets_by_repository[repository], result.head_sha or "")
            result.check_state = f"{state}:{detail}"
            if state in {"success", "failure"}:
                completed.append(repository)
        for repository in completed:
            pending.pop(repository, None)
        if pending:
            print(
                f"check poll {round_number}: pending={len(pending)} completed={len(results)-len(pending)}",
                flush=True,
            )
            time.sleep(15)
    for result in pending.values():
        result.check_state = "timeout:verify check did not complete before deadline"


def merge_and_verify(
    github: GitHub,
    target: Target,
    result: Result,
    files: dict[str, str],
) -> None:
    if not result.pull_request_number or not result.head_sha:
        raise BootstrapError(f"missing pull request state: {target.repository}")
    if not result.check_state or not result.check_state.startswith("success:"):
        raise BootstrapError(
            f"exact-head verify check is not successful for {target.repository}: {result.check_state}"
        )
    status, payload, _ = github.put(
        f"{repository_path(target)}/pulls/{result.pull_request_number}/merge",
        {
            "commit_title": f"test: add {target.spec.suite} deep-test foundation",
            "commit_message": f"Tracking: {BOOTSTRAP_OPERATION}",
            "sha": result.head_sha,
            "merge_method": "squash",
        },
        allow=(405, 409),
    )
    if status in {405, 409} or not payload or payload.get("merged") is not True:
        message = payload.get("message") if isinstance(payload, dict) else f"HTTP {status}"
        raise BootstrapError(f"pull request did not merge for {target.repository}: {message}")
    result.merged = True
    default_ref = wait_for_ref(github, target, result.default_branch or "main")
    default_sha = default_ref["object"]["sha"]
    _, paths = commit_tree(github, target, default_sha)
    exact, missing, mismatched = verify_tree_exact(paths, files)
    if not exact:
        raise BootstrapError(
            f"post-merge tree verification failed for {target.repository}: "
            f"missing={missing} mismatched={mismatched}"
        )
    result.default_branch_sha = default_sha
    result.state = "merged_verified"


def execute(
    github: GitHub,
    fleet: Fleet,
    ledger: RunLedger,
    *,
    merge: bool,
    check_timeout_seconds: int,
) -> int:
    targets = [Target(organization, spec) for organization in fleet.organizations for spec in fleet.repositories]
    results_by_repository = {
        target.repository: Result(repository=target.repository, suite=target.spec.suite)
        for target in targets
    }
    ledger.repositories = [results_by_repository[target.repository] for target in targets]
    targets_by_repository = {target.repository: target for target in targets}

    inventory = preflight(github, fleet, targets)
    ledger.preflight_complete = True
    print(
        f"preflight complete: organizations={len(fleet.organizations)} repositories={len(targets)} "
        f"missing={sum(1 for item in inventory.values() if not item.exists)}",
        flush=True,
    )

    generated_by_repository: dict[str, dict[str, str]] = {}
    for target in targets:
        generated_by_repository[target.repository] = generate_repository_files(
            target.organization, target.spec, fleet
        )

    for index, target in enumerate(targets, start=1):
        result = results_by_repository[target.repository]
        try:
            existing = inventory[target.repository]
            if existing.exists:
                repository = existing.data or {}
                result.created = False
            else:
                repository = create_repository(github, target)
                result.created = True
            repository = ensure_main_default(github, target, repository, result.created)
            repository = configure_repository(github, target)
            result.repository_url = f"https://github.com/{target.repository}"
            result.default_branch = str(repository.get("default_branch") or "main")
            files = generated_by_repository[target.repository]
            head_sha, branch, initialized = branch_or_foundation_commit(
                github,
                target,
                files,
                result.default_branch,
                created=result.created,
                tracking_issue=fleet.tracking_issue,
            )
            result.head_sha = head_sha
            result.branch = branch
            if initialized:
                result.state = "already_initialized"
                result.default_branch_sha = head_sha
            else:
                pull = find_or_create_pull_request(
                    github,
                    target,
                    result.default_branch,
                    head_sha,
                    fleet.tracking_issue,
                )
                result.pull_request_number = int(pull["number"])
                result.pull_request_url = str(pull.get("html_url") or "")
                if pull.get("merged_at"):
                    result.merged = True
                    result.state = "merged_pending_verify"
                else:
                    result.state = "pull_request_open"
            print(
                f"[{index}/{len(targets)}] {target.repository}: {result.state}",
                flush=True,
            )
        except Exception as error:  # continue to maximize bounded fleet progress
            result.state = "failed"
            result.error = redact(str(error), github.token)
            ledger.failures.append({"repository": target.repository, "error": result.error})
            print(f"[{index}/{len(targets)}] {target.repository}: FAILED {result.error}", flush=True)

    candidates = [
        item
        for item in ledger.repositories
        if item.state == "pull_request_open" and item.pull_request_number is not None
    ]
    if merge and candidates:
        wait_for_all_checks(github, targets_by_repository, candidates, check_timeout_seconds)
        for result in candidates:
            target = targets_by_repository[result.repository]
            try:
                merge_and_verify(
                    github,
                    target,
                    result,
                    generated_by_repository[result.repository],
                )
                print(f"merged and verified: {result.repository}", flush=True)
            except Exception as error:
                result.error = redact(str(error), github.token)
                if result.state != "failed":
                    result.state = "pull_request_blocked"
                ledger.failures.append({"repository": result.repository, "error": result.error})
                print(f"merge blocked: {result.repository}: {result.error}", flush=True)

    # Verify PRs that a prior idempotent run already merged.
    for result in ledger.repositories:
        if result.state == "merged_pending_verify":
            target = targets_by_repository[result.repository]
            try:
                default_ref = wait_for_ref(github, target, result.default_branch or "main")
                default_sha = default_ref["object"]["sha"]
                _, paths = commit_tree(github, target, default_sha)
                exact, missing, mismatched = verify_tree_exact(
                    paths, generated_by_repository[result.repository]
                )
                if not exact:
                    raise BootstrapError(
                        f"merged tree drift: missing={missing} mismatched={mismatched}"
                    )
                result.default_branch_sha = default_sha
                result.state = "merged_verified"
            except Exception as error:
                result.error = redact(str(error), github.token)
                result.state = "failed"
                ledger.failures.append({"repository": result.repository, "error": result.error})

    return 0 if not ledger.failures and ledger.summary()["verified_on_default_branch"] == fleet.total else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap deep-test repositories across exact *-test orgs")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--check-timeout-seconds", type=int, default=1200)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    fleet = load_fleet(args.manifest)
    validate_all_templates(fleet)
    if args.validate_only:
        print(
            f"validated deep-test fleet: organizations={len(fleet.organizations)} "
            f"repositories_per_org={len(fleet.repositories)} total={fleet.total}"
        )
        return 0
    if not args.token_file or not args.result:
        raise BootstrapError("live execution requires --token-file and --result")
    if args.check_timeout_seconds < 60 or args.check_timeout_seconds > 3600:
        raise BootstrapError("check timeout must be between 60 and 3600 seconds")
    token = args.token_file.read_text(encoding="utf-8").strip()
    ledger = RunLedger()
    try:
        github = GitHub(token)
        status = execute(
            github,
            fleet,
            ledger,
            merge=args.merge,
            check_timeout_seconds=args.check_timeout_seconds,
        )
    except Exception as error:
        message = redact(str(error), token)
        ledger.failures.append({"repository": "preflight", "error": message})
        print(f"fatal bootstrap error: {message}", file=sys.stderr)
        status = 1
    finally:
        ledger.write(args.result)
        token = ""
    return status


if __name__ == "__main__":
    raise SystemExit(main())
