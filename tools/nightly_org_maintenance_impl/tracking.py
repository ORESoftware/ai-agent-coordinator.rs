"""Synchronize nightly GitHub delivery evidence to Linear and GitHub Projects."""

from .common import *
from .clients import LinearClient
from .workspace import _gh_environment
from .publish import _gh_json


LINEAR_ISSUE_COMMENTS_QUERY = """
query NightlyIssueComments($id: String!, $first: Int!) {
  issue(id: $id) {
    id
    comments(first: $first) {
      nodes { id body url }
    }
  }
}
"""

LINEAR_COMMENT_CREATE_MUTATION = """
mutation NightlyCommentCreate($issueId: String!, $body: String!) {
  commentCreate(input: {issueId: $issueId, body: $body}) {
    success
    comment { id url }
  }
}
"""


def _change_has_documentation(change: Mapping[str, Any]) -> bool:
    for raw in change.get("changed_files", []):
        path = str(raw).replace("\\", "/").strip("/")
        lowered = path.casefold()
        name = Path(path).name.casefold()
        if lowered.startswith(("docs/", "doc/")):
            return True
        if name.startswith(("readme", "contributing", "changelog", "security", "code_of_conduct")):
            return True
        if Path(name).suffix in {".md", ".mdx", ".rst", ".adoc"}:
            return True
    return False


def _normalized_projects(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("projects", [])
    if not isinstance(value, list):
        raise MaintenanceError("gh project list returned an invalid JSON shape")
    projects: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        number = raw.get("number")
        title = _clean_text(raw.get("title"), limit=200)
        url = _clean_text(raw.get("url"), limit=500)
        if isinstance(number, int) and number > 0 and title:
            projects.append({"number": number, "title": title, "url": url})
    return projects


def _select_project(owner: str, projects: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    normalized = [dict(item) for item in projects]
    exact_titles = {owner.casefold(), f"github.com/{owner}".casefold()}
    exact = [item for item in normalized if str(item.get("title", "")).casefold() in exact_titles]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise MaintenanceError(f"multiple exact GitHub Project matches exist for {owner}")
    if len(normalized) == 1:
        return normalized[0]
    if not normalized:
        return None
    raise MaintenanceError(
        f"GitHub Project resolution for {owner} is ambiguous; name one project {owner!r} "
        f"or 'github.com/{owner}'"
    )


def _resolve_or_create_project(
    owner: str,
    *,
    policy: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    listed = _gh_json(
        [
            "project",
            "list",
            "--owner",
            owner,
            "--limit",
            "100",
            "--format",
            "json",
            "--jq",
            "[.projects[] | {number,title,url}]",
        ],
        env=env,
    )
    projects = _normalized_projects(listed)
    selected = _select_project(owner, projects)
    if selected is not None:
        return selected
    if not bool(policy.get("create_missing_github_projects", True)):
        raise MaintenanceError(f"no open GitHub Project exists for {owner}")
    created = _gh_json(
        [
            "project",
            "create",
            "--owner",
            owner,
            "--title",
            f"github.com/{owner}",
            "--format",
            "json",
        ],
        env=env,
    )
    project = _require_mapping(created, "created GitHub Project")
    number = project.get("number")
    title = _clean_text(project.get("title"), limit=200)
    url = _clean_text(project.get("url"), limit=500)
    if not isinstance(number, int) or number <= 0 or not title:
        raise MaintenanceError(f"GitHub did not verify the created project for {owner}")
    return {"number": number, "title": title, "url": url, "created": True}


def _project_items(project: Mapping[str, Any], owner: str, env: Mapping[str, str]) -> list[dict[str, Any]]:
    value = _gh_json(
        [
            "project",
            "item-list",
            str(project["number"]),
            "--owner",
            owner,
            "--limit",
            "1000",
            "--format",
            "json",
            "--jq",
            "[.items[] | {id,content_url:(.content.url // \"\")} ]",
        ],
        env=env,
        timeout=180.0,
    )
    if isinstance(value, dict):
        value = value.get("items", [])
    if not isinstance(value, list):
        raise MaintenanceError("gh project item-list returned an invalid JSON shape")
    return [dict(item) for item in value if isinstance(item, dict)]


def _ensure_project_item(
    project: Mapping[str, Any],
    *,
    owner: str,
    url: str,
    env: Mapping[str, str],
) -> dict[str, Any]:
    existing = next(
        (
            item
            for item in _project_items(project, owner, env)
            if _clean_text(item.get("content_url"), limit=500) == url
        ),
        None,
    )
    if existing is not None:
        return {"status": "already_synced", "item_id": existing.get("id")}
    created = _gh_json(
        [
            "project",
            "item-add",
            str(project["number"]),
            "--owner",
            owner,
            "--url",
            url,
            "--format",
            "json",
        ],
        env=env,
        timeout=180.0,
    )
    item = _require_mapping(created, "created GitHub Project item")
    return {"status": "synced", "item_id": item.get("id")}


def _linear_issue(snapshot: Mapping[str, Any], identifier: str) -> dict[str, Any] | None:
    linear = _require_mapping(snapshot.get("linear"), "snapshot.linear")
    issues = _require_list(linear.get("issues", []), "snapshot.linear.issues")
    for raw in issues:
        if not isinstance(raw, dict):
            continue
        if _clean_text(raw.get("identifier"), limit=50).casefold() == identifier.casefold():
            return raw
    return None


def _sync_linear_comment(
    *,
    client: LinearClient,
    snapshot: Mapping[str, Any],
    identifier: str,
    body: str,
    marker: str,
) -> dict[str, Any]:
    issue = _linear_issue(snapshot, identifier)
    if issue is None:
        raise MaintenanceError(f"Linear issue {identifier} is absent from the sanitized project snapshot")
    issue_id = _clean_text(issue.get("id"), limit=100)
    if not issue_id:
        raise MaintenanceError(f"Linear issue {identifier} has no stable id")
    data = client.graphql(LINEAR_ISSUE_COMMENTS_QUERY, {"id": issue_id, "first": 100})
    issue_data = data.get("issue") if isinstance(data.get("issue"), dict) else None
    if issue_data is None:
        raise MaintenanceError(f"Linear issue {identifier} could not be re-read")
    comments = issue_data.get("comments") if isinstance(issue_data.get("comments"), dict) else {}
    nodes = comments.get("nodes") if isinstance(comments.get("nodes"), list) else []
    for raw in nodes:
        if isinstance(raw, dict) and marker in str(raw.get("body") or ""):
            return {
                "status": "already_synced",
                "issue": identifier,
                "comment_id": raw.get("id"),
                "comment_url": raw.get("url"),
            }
    created = client.graphql(
        LINEAR_COMMENT_CREATE_MUTATION,
        {"issueId": issue_id, "body": f"{body}\n\n{marker}"},
    )
    payload = created.get("commentCreate") if isinstance(created.get("commentCreate"), dict) else {}
    if payload.get("success") is not True:
        raise MaintenanceError(f"Linear did not confirm the tracking comment for {identifier}")
    comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
    return {
        "status": "synced",
        "issue": identifier,
        "comment_id": comment.get("id"),
        "comment_url": comment.get("url"),
    }


def sync_tracking(
    *,
    result: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    policy: Mapping[str, Any],
    ledger: Mapping[str, Any],
    run_key: str,
    github_token_env: str,
    linear_token_env: str,
) -> dict[str, Any]:
    owner = _clean_text(result.get("owner"), limit=100)
    env = _gh_environment(github_token_env)
    linear_token = os.environ.get(linear_token_env, "")
    linear = LinearClient(
        linear_token,
        api_url=os.environ.get("LINEAR_API_URL", DEFAULT_LINEAR_API),
        auth_scheme=os.environ.get("LINEAR_API_AUTH_SCHEME", "") or "api_key",
    )
    created_prs = _require_list(ledger.get("created_pull_requests", []), "ledger.created_pull_requests")
    merge_results = _require_list(ledger.get("merge_results", []), "ledger.merge_results")
    changes = {
        _clean_text(item.get("repository"), limit=250).casefold(): item
        for item in _require_list(result.get("changes", []), "result.changes")
        if isinstance(item, dict)
    }
    project: dict[str, Any] | None = None
    errors: list[str] = []
    entries: list[dict[str, Any]] = []
    needs_project = bool(created_prs) or any(
        isinstance(item, dict) and item.get("status") == "merged" and item.get("url")
        for item in merge_results
    )
    if needs_project:
        try:
            project = _resolve_or_create_project(owner, policy=policy, env=env)
        except MaintenanceError as exc:
            errors.append(str(exc))

    for raw_pr in created_prs:
        if not isinstance(raw_pr, dict):
            continue
        repository = _clean_text(raw_pr.get("repository"), limit=250)
        url = _clean_text(raw_pr.get("url"), limit=500)
        change = changes.get(repository.casefold())
        entry: dict[str, Any] = {
            "repository": repository,
            "pull_request": raw_pr.get("number"),
            "url": url,
            "documentation_changed": bool(change and _change_has_documentation(change)),
        }
        if project is None:
            entry["github_project"] = {"status": "blocked"}
        else:
            try:
                project_item = _ensure_project_item(project, owner=owner, url=url, env=env)
                entry["github_project"] = {
                    **project_item,
                    "number": project.get("number"),
                    "title": project.get("title"),
                    "url": project.get("url"),
                }
            except MaintenanceError as exc:
                entry["github_project"] = {"status": "blocked", "reason": str(exc)}
                errors.append(f"{repository}: {exc}")
        linear_issue = _clean_text(change.get("linear_issue") if change else "", limit=50)
        if linear_issue:
            marker = (
                f"<!-- nightly-org-maintenance:{run_key}:{repository}:"
                f"{raw_pr.get('number')} -->"
            )
            body = (
                f"Nightly maintenance wrote and pushed code in [{repository}#{raw_pr.get('number')}]({url}).\n\n"
                f"- Run: `{run_key}`\n"
                f"- Branch: `{raw_pr.get('branch')}`\n"
                f"- Head: `{raw_pr.get('head_sha')}`\n"
                f"- Documentation changed: `{entry['documentation_changed']}`"
            )
            try:
                entry["linear"] = _sync_linear_comment(
                    client=linear,
                    snapshot=snapshot,
                    identifier=linear_issue,
                    body=body,
                    marker=marker,
                )
            except MaintenanceError as exc:
                entry["linear"] = {"status": "blocked", "issue": linear_issue, "reason": str(exc)}
                errors.append(f"{repository}: {exc}")
        elif entry["documentation_changed"]:
            reason = "documentation changes require a mapped Linear issue"
            entry["linear"] = {"status": "blocked", "reason": reason}
            errors.append(f"{repository}: {reason}")
        else:
            entry["linear"] = {"status": "not_applicable", "reason": "no mapped Linear issue"}
        entries.append(entry)

    merged_entries: list[dict[str, Any]] = []
    for raw_merge in merge_results:
        if not isinstance(raw_merge, dict) or raw_merge.get("status") != "merged":
            continue
        url = _clean_text(raw_merge.get("url"), limit=500)
        merged_entry = {
            "repository": raw_merge.get("repository"),
            "pull_request": raw_merge.get("number"),
            "url": url,
        }
        if project is None:
            merged_entry["github_project"] = {"status": "blocked"}
        else:
            try:
                merged_entry["github_project"] = {
                    **_ensure_project_item(project, owner=owner, url=url, env=env),
                    "number": project.get("number"),
                    "title": project.get("title"),
                    "url": project.get("url"),
                }
            except MaintenanceError as exc:
                merged_entry["github_project"] = {"status": "blocked", "reason": str(exc)}
                errors.append(f"{raw_merge.get('repository')}#{raw_merge.get('number')}: {exc}")
        merged_entries.append(merged_entry)

    return {
        "schema_version": "nightly_org_tracking.v1",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "owner": owner,
        "project": project,
        "created_pull_requests": entries,
        "merged_pull_requests": merged_entries,
        "errors": sorted(set(errors)),
    }


def command_sync_tracking(args: argparse.Namespace) -> dict[str, Any]:
    result = _require_mapping(load_json(args.result, label="validated result"), "result")
    snapshot = _require_mapping(load_json(args.snapshot, label="snapshot"), "snapshot")
    policy = _require_mapping(load_json(args.policy, label="maintenance policy"), "policy")
    ledger = _require_mapping(load_json(args.ledger, label="publication ledger"), "ledger")
    tracking = sync_tracking(
        result=result,
        snapshot=snapshot,
        policy=policy,
        ledger=ledger,
        run_key=args.run_key,
        github_token_env=args.github_token_env,
        linear_token_env=args.linear_token_env,
    )
    updated_ledger = dict(ledger)
    updated_ledger["tracking_sync"] = tracking
    write_json(args.ledger, updated_ledger)
    if args.step_summary:
        with Path(args.step_summary).open("a", encoding="utf-8") as stream:
            stream.write("\n### Linear and GitHub Project synchronization\n\n")
            if tracking.get("project"):
                project = tracking["project"]
                stream.write(
                    f"- GitHub Project: [{project.get('title')}]({project.get('url')}) "
                    f"(number {project.get('number')})\n"
                )
            for entry in tracking["created_pull_requests"]:
                stream.write(
                    f"- `{entry['repository']}#{entry['pull_request']}` — "
                    f"project={entry['github_project']['status']}, "
                    f"linear={entry['linear']['status']}, "
                    f"docs={entry['documentation_changed']}\n"
                )
            for entry in tracking["merged_pull_requests"]:
                stream.write(
                    f"- merged `{entry['repository']}#{entry['pull_request']}` — "
                    f"project={entry['github_project']['status']}\n"
                )
    errors = tracking.get("errors", [])
    if errors and bool(policy.get("require_tracking_sync", True)):
        raise MaintenanceError("tracking synchronization failed: " + "; ".join(errors[:8]))
    return {
        "status": "tracking_synced" if not errors else "tracking_partial",
        "owner": tracking["owner"],
        "created_pull_requests": len(tracking["created_pull_requests"]),
        "merged_pull_requests": len(tracking["merged_pull_requests"]),
        "errors": len(errors),
        "ledger": str(args.ledger),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
