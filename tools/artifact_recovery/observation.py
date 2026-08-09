"""Normalize bounded source observations and current GitHub evidence."""

from __future__ import annotations

from typing import Any, Mapping

from .common import (
    MAX_ITEMS,
    MAX_PATHS,
    OBSERVATION_SCHEMA,
    ORIGIN_ID_RE,
    RecoveryError,
    expect_bool,
    expect_list,
    expect_object,
    expect_string,
    ledger_key,
    normalize_artifact,
    normalize_branch,
    normalize_repo,
    normalize_sha,
    normalize_visibility,
    optional_string,
    parse_timestamp,
    validate_github_url,
    validate_public_safety,
)

def normalize_observation_item(raw: Any, index: int) -> dict[str, Any]:
    item = expect_object(raw, f"items[{index}]")
    allowed = {"origin", "target", "intent", "local", "remote", "claims", "note"}
    extra = set(item) - allowed
    if extra:
        raise RecoveryError(f"items[{index}] has unsupported keys: {sorted(extra)}")

    origin = expect_object(item.get("origin"), f"items[{index}].origin")
    source = expect_string(origin.get("source"), f"items[{index}].origin.source", 32)
    if source not in {"chatgpt", "claude", "file_library", "conversation", "task"}:
        raise RecoveryError(f"items[{index}].origin.source is unsupported")
    origin_id = expect_string(origin.get("id"), f"items[{index}].origin.id", 200)
    if not ORIGIN_ID_RE.fullmatch(origin_id):
        raise RecoveryError(f"items[{index}].origin.id has an invalid shape")
    origin_kind = expect_string(origin.get("id_kind"), f"items[{index}].origin.id_kind", 32)
    if origin_kind not in {"chat_id", "task_id", "file_id", "conversation_id", "derived_fingerprint"}:
        raise RecoveryError(f"items[{index}].origin.id_kind is unsupported")
    observed_at = parse_timestamp(origin.get("observed_at"), f"items[{index}].origin.observed_at")

    target = expect_object(item.get("target"), f"items[{index}].target")
    owner = expect_string(target.get("owner"), f"items[{index}].target.owner", 39)
    repository = expect_string(target.get("repository"), f"items[{index}].target.repository", 100)
    identity = normalize_repo(owner, repository)
    visibility = normalize_visibility(target.get("visibility"), f"items[{index}].target.visibility")
    artifact_kind = expect_string(target.get("artifact_kind"), f"items[{index}].target.artifact_kind", 32)
    if artifact_kind not in {"code", "documentation", "none"}:
        raise RecoveryError(f"items[{index}].target.artifact_kind is unsupported")
    ownership_resolved = target.get("ownership_resolved", True)
    ownership_resolved = expect_bool(ownership_resolved, f"items[{index}].target.ownership_resolved")

    intent = expect_object(item.get("intent", {}), f"items[{index}].intent")
    artifact_expected = expect_bool(intent.get("artifact_expected", artifact_kind != "none"), f"items[{index}].intent.artifact_expected")
    base_branch = normalize_branch(intent.get("base_branch", "main"), f"items[{index}].intent.base_branch")
    branch = normalize_branch(intent.get("branch"), f"items[{index}].intent.branch")
    pull_request_required = expect_bool(intent.get("pull_request_required", True), f"items[{index}].intent.pull_request_required")
    allow_repository_creation = expect_bool(intent.get("allow_repository_creation", True), f"items[{index}].intent.allow_repository_creation")

    local = expect_object(item.get("local", {}), f"items[{index}].local")
    artifact = normalize_artifact(local.get("artifact"), f"items[{index}].local.artifact")
    git_repository = expect_bool(local.get("git_repository", False), f"items[{index}].local.git_repository")
    remote_present = expect_bool(local.get("remote_present", False), f"items[{index}].local.remote_present")
    local_branch = normalize_branch(local.get("branch"), f"items[{index}].local.branch")
    local_head = normalize_sha(local.get("head_sha") or (artifact or {}).get("commit_sha"), f"items[{index}].local.head_sha")
    local_branches_raw = expect_list(local.get("branches", []), f"items[{index}].local.branches", MAX_PATHS)
    local_branches = [normalize_branch(value, f"items[{index}].local.branches") for value in local_branches_raw]
    dirty_paths_raw = expect_list(local.get("dirty_paths", []), f"items[{index}].local.dirty_paths", MAX_PATHS)
    dirty_paths = [expect_string(path, f"items[{index}].local.dirty_paths", 300) for path in dirty_paths_raw]

    remote = expect_object(item.get("remote", {}), f"items[{index}].remote")
    collected = expect_bool(remote.get("collected", False), f"items[{index}].remote.collected")
    repo_remote = expect_object(remote.get("repository", {}), f"items[{index}].remote.repository")
    repo_exists = expect_bool(repo_remote.get("exists", False), f"items[{index}].remote.repository.exists")
    remote_visibility = normalize_visibility(repo_remote.get("visibility"), f"items[{index}].remote.repository.visibility")
    default_branch = normalize_branch(repo_remote.get("default_branch"), f"items[{index}].remote.repository.default_branch")
    repository_url = optional_string(repo_remote.get("url"), f"items[{index}].remote.repository.url", 300)
    if repository_url is not None:
        validate_github_url(repository_url, f"items[{index}].remote.repository.url", identity)

    remote_branches: list[dict[str, str]] = []
    for branch_index, branch_value in enumerate(expect_list(remote.get("branches", []), f"items[{index}].remote.branches", MAX_PATHS)):
        branch_obj = expect_object(branch_value, f"items[{index}].remote.branches[{branch_index}]")
        name = normalize_branch(branch_obj.get("name"), f"items[{index}].remote.branches[{branch_index}].name")
        sha = normalize_sha(branch_obj.get("sha"), f"items[{index}].remote.branches[{branch_index}].sha")
        if name is None or sha is None:
            raise RecoveryError(f"items[{index}].remote.branches[{branch_index}] requires name and sha")
        remote_branches.append({"name": name, "sha": sha})

    remote_commits: list[dict[str, str]] = []
    for commit_index, commit_value in enumerate(expect_list(remote.get("commits", []), f"items[{index}].remote.commits", MAX_PATHS)):
        commit_obj = expect_object(commit_value, f"items[{index}].remote.commits[{commit_index}]")
        sha = normalize_sha(commit_obj.get("sha"), f"items[{index}].remote.commits[{commit_index}].sha")
        url = expect_string(commit_obj.get("url"), f"items[{index}].remote.commits[{commit_index}].url", 300)
        if sha is None:
            raise RecoveryError(f"items[{index}].remote.commits[{commit_index}] requires sha")
        _, kind, value = validate_github_url(url, f"items[{index}].remote.commits[{commit_index}].url", identity)
        if kind != "commit" or value != sha:
            raise RecoveryError(f"items[{index}].remote.commits[{commit_index}] URL/SHA mismatch")
        remote_commits.append({"sha": sha, "url": url})

    pull_requests: list[dict[str, Any]] = []
    for pr_index, pr_value in enumerate(expect_list(remote.get("pull_requests", []), f"items[{index}].remote.pull_requests", MAX_PATHS)):
        pr = expect_object(pr_value, f"items[{index}].remote.pull_requests[{pr_index}]")
        number = pr.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise RecoveryError(f"items[{index}].remote.pull_requests[{pr_index}].number must be positive")
        url = expect_string(pr.get("url"), f"items[{index}].remote.pull_requests[{pr_index}].url", 300)
        _, kind, value = validate_github_url(url, f"items[{index}].remote.pull_requests[{pr_index}].url", identity)
        if kind != "pull" or value != str(number):
            raise RecoveryError(f"items[{index}].remote.pull_requests[{pr_index}] URL/number mismatch")
        head = normalize_branch(pr.get("head"), f"items[{index}].remote.pull_requests[{pr_index}].head")
        base = normalize_branch(pr.get("base"), f"items[{index}].remote.pull_requests[{pr_index}].base")
        state = expect_string(pr.get("state"), f"items[{index}].remote.pull_requests[{pr_index}].state", 16)
        if state not in {"open", "closed", "merged"}:
            raise RecoveryError(f"items[{index}].remote.pull_requests[{pr_index}].state is unsupported")
        pull_requests.append(
            {
                "number": number,
                "url": url,
                "head": head,
                "base": base,
                "state": state,
                "draft": expect_bool(pr.get("draft", False), f"items[{index}].remote.pull_requests[{pr_index}].draft"),
            }
        )

    claims = expect_object(item.get("claims", {}), f"items[{index}].claims")
    claimed_repository_url = optional_string(claims.get("repository_url"), f"items[{index}].claims.repository_url", 300)
    if claimed_repository_url is not None:
        validate_github_url(claimed_repository_url, f"items[{index}].claims.repository_url", identity)
    claimed_commit = normalize_sha(claims.get("commit_sha"), f"items[{index}].claims.commit_sha")
    claimed_branch = normalize_branch(claims.get("branch"), f"items[{index}].claims.branch")
    claimed_pr = optional_string(claims.get("pull_request_url"), f"items[{index}].claims.pull_request_url", 300)
    if claimed_pr is not None:
        _, kind, value = validate_github_url(claimed_pr, f"items[{index}].claims.pull_request_url", identity)
        if kind != "pull" or not value or not value.isdigit() or int(value) < 1:
            raise RecoveryError(f"items[{index}].claims.pull_request_url must point to a positive PR")

    note = optional_string(item.get("note"), f"items[{index}].note", 500)

    normalized = {
        "origin": {
            "source": source,
            "id": origin_id,
            "id_kind": origin_kind,
            "observed_at": observed_at,
        },
        "target": {
            "owner": owner,
            "repository": repository,
            "identity": identity,
            "visibility": visibility,
            "artifact_kind": artifact_kind,
            "ownership_resolved": ownership_resolved,
        },
        "intent": {
            "artifact_expected": artifact_expected,
            "base_branch": base_branch,
            "branch": branch,
            "pull_request_required": pull_request_required,
            "allow_repository_creation": allow_repository_creation,
        },
        "local": {
            "artifact": artifact,
            "git_repository": git_repository,
            "remote_present": remote_present,
            "branch": local_branch,
            "branches": sorted({value for value in local_branches if value}),
            "head_sha": local_head,
            "dirty_paths": sorted(set(dirty_paths)),
        },
        "remote": {
            "collected": collected,
            "repository": {
                "exists": repo_exists,
                "visibility": remote_visibility,
                "default_branch": default_branch,
                "url": repository_url,
            },
            "branches": sorted(remote_branches, key=lambda value: (value["name"], value["sha"])),
            "commits": sorted(remote_commits, key=lambda value: value["sha"]),
            "pull_requests": sorted(pull_requests, key=lambda value: value["number"]),
        },
        "claims": {
            "repository_url": claimed_repository_url,
            "commit_sha": claimed_commit,
            "branch": claimed_branch,
            "pull_request_url": claimed_pr,
        },
        "note": note,
    }
    validate_public_safety(normalized)
    return normalized


def validate_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    schema = value.get("schema_version")
    if schema != OBSERVATION_SCHEMA:
        raise RecoveryError(f"schema_version must be {OBSERVATION_SCHEMA}")
    generated_at = parse_timestamp(value.get("generated_at"), "generated_at")
    batch = expect_object(value.get("batch"), "batch")
    batch_id = expect_string(batch.get("id"), "batch.id", 100)
    complete = expect_bool(batch.get("complete", False), "batch.complete")
    next_cursor = optional_string(batch.get("next_cursor"), "batch.next_cursor", 200)
    source_window = optional_string(batch.get("source_window"), "batch.source_window", 200)
    raw_items = expect_list(value.get("items"), "items", MAX_ITEMS)
    normalized_items = [normalize_observation_item(item, index) for index, item in enumerate(raw_items)]
    keys = [ledger_key(item) for item in normalized_items]
    if len(keys) != len(set(keys)):
        raise RecoveryError("items contain duplicate origin/owner/repository ledger keys")
    result = {
        "schema_version": OBSERVATION_SCHEMA,
        "generated_at": generated_at,
        "batch": {
            "id": batch_id,
            "complete": complete,
            "next_cursor": next_cursor,
            "source_window": source_window,
        },
        "items": sorted(normalized_items, key=ledger_key),
    }
    validate_public_safety(result)
    return result
