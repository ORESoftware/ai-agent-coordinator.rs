from __future__ import annotations

import json
import os
import time
import urllib.parse
from typing import Any, Iterable, Mapping

from .model import (
    ApiError,
    GitHub,
    ORG,
    PROJECT_TITLE,
    Project,
    PublicationError,
    RepoRecord,
    TRACKING_TITLE,
    run,
    scrub,
)
from .source import repo_path


def ensure_project(token: str) -> Project:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    result = run(
        ["gh", "project", "list", "--owner", ORG, "--limit", "100", "--format", "json"],
        env=env,
    )
    data = json.loads(result.stdout)
    projects = data if isinstance(data, list) else data.get("projects", [])
    for project in projects:
        if project.get("title") == PROJECT_TITLE and not project.get("closed", False):
            return Project(int(project["number"]), str(project["url"]), PROJECT_TITLE)
    created = run(
        ["gh", "project", "create", "--owner", ORG, "--title", PROJECT_TITLE, "--format", "json"],
        env=env,
    )
    project = json.loads(created.stdout)
    return Project(int(project["number"]), str(project["url"]), PROJECT_TITLE)


def add_project_item(token: str, project: Project, url: str) -> None:
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    result = run(
        [
            "gh",
            "project",
            "item-add",
            str(project.number),
            "--owner",
            ORG,
            "--url",
            url,
        ],
        env=env,
        check=False,
    )
    if result.returncode != 0 and "already" not in (result.stderr + result.stdout).lower():
        raise PublicationError(f"failed to add project item {url}: {scrub(result.stderr)[:500]}")


def ensure_tracking_issue(gh: GitHub, body: str) -> dict[str, Any]:
    _, issues = gh.get(f"/repos/{ORG}/.github/issues?state=all&per_page=100")
    for issue in issues:
        if "pull_request" not in issue and issue.get("title") == TRACKING_TITLE:
            _, updated = gh.patch(
                f"/repos/{ORG}/.github/issues/{issue['number']}",
                {"body": body, "state": "open"},
            )
            return updated
    _, issue = gh.post(
        f"/repos/{ORG}/.github/issues",
        {"title": TRACKING_TITLE, "body": body, "labels": []},
    )
    return issue


def issue_body(project: Project, results: Iterable[Mapping[str, Any]]) -> str:
    rows = []
    for result in results:
        pr = result.get("pull_request") or "—"
        rows.append(
            f"| `{result['repository']}` | `{result.get('visibility')}` | "
            f"`{result.get('initial_source_head') or 'semantic-existing'}` | {pr} | "
            f"`{result.get('state')}` |"
        )
    return "\n".join(
        [
            "## Canonical MemeBank fleet publication",
            "",
            f"GitHub Project: [{project.title}]({project.url})",
            "",
            "This issue tracks live evidence for DEN-1005, DEN-1043, DEN-319, DEN-1011, and DEN-1018.",
            "Legacy repositories are preserved; no rename, deletion, or force-push is part of this publication.",
            "",
            "| Repository | Visibility | Approved source head | Provenance PR | State |",
            "| --- | --- | --- | --- | --- |",
            *rows,
            "",
            "The public `.github` repository is an additive governance exception. All other canonical repositories are private.",
        ]
    ) + "\n"


def open_pull_request(
    gh: GitHub,
    record: RepoRecord,
    branch: str,
    head_sha: str,
) -> dict[str, Any]:
    base = repo_path(record)
    head = urllib.parse.quote(f"{ORG}:{branch}", safe=":")
    _, existing = gh.get(f"{base}/pulls?state=all&head={head}&per_page=20")
    if existing:
        return existing[0]
    _, pr = gh.post(
        f"{base}/pulls",
        {
            "title": f"docs(DEN-1005): record {record.name} fleet publication",
            "head": branch,
            "base": "main",
            "body": (
                f"Record canonical source-v2 publication evidence for `{record.full_name}`. "
                "This additive PR contains no product credential, customer data, or legacy-repository rewrite. "
                f"Exact head: `{head_sha}`."
            ),
            "maintainer_can_modify": True,
        },
    )
    return pr


def status_ready(gh: GitHub, record: RepoRecord, sha: str) -> tuple[bool, str]:
    _, checks = gh.get(f"{repo_path(record)}/commits/{sha}/check-runs?per_page=100")
    runs = checks.get("check_runs", []) if isinstance(checks, dict) else []
    if any(item.get("status") != "completed" for item in runs):
        return False, "pending"
    bad = {
        "failure",
        "timed_out",
        "cancelled",
        "action_required",
        "stale",
        "startup_failure",
    }
    if any(item.get("conclusion") in bad for item in runs):
        return False, "failed"
    _, combined = gh.get(f"{repo_path(record)}/commits/{sha}/status")
    state = combined.get("state") if isinstance(combined, dict) else None
    if isinstance(combined, dict) and int(combined.get("total_count", 0)) == 0:
        state = None
    if state in ("failure", "error"):
        return False, "failed"
    if state == "pending":
        return False, "pending"
    return True, "ready"


def merge_pull_request(
    gh: GitHub,
    record: RepoRecord,
    pr: Mapping[str, Any],
    expected_sha: str,
    wait_seconds: int,
) -> tuple[bool, str | None, str]:
    if pr.get("merged_at"):
        return True, pr.get("merge_commit_sha"), "already_merged"
    time.sleep(5)
    deadline = time.monotonic() + wait_seconds
    while True:
        _, current = gh.get(f"{repo_path(record)}/pulls/{pr['number']}")
        if current.get("merged_at"):
            return True, current.get("merge_commit_sha"), "already_merged"
        mergeable = current.get("mergeable")
        ready, state = status_ready(gh, record, expected_sha)
        if mergeable is False:
            return False, None, "merge_conflict"
        if ready and mergeable is not False:
            break
        if state == "failed":
            return False, None, "checks_failed"
        if time.monotonic() >= deadline:
            return False, None, "checks_pending_timeout"
        time.sleep(10)
    try:
        _, result = gh.put(
            f"{repo_path(record)}/pulls/{pr['number']}/merge",
            {
                "sha": expected_sha,
                "merge_method": "squash",
                "commit_title": f"docs(DEN-1005): record {record.name} fleet publication (#{pr['number']})",
                "commit_message": record.description,
            },
        )
        return (
            bool(result.get("merged")),
            result.get("sha"),
            "merged" if result.get("merged") else "not_merged",
        )
    except ApiError as error:
        if error.status in (405, 409, 422):
            return False, None, f"merge_blocked_{error.status}"
        raise
