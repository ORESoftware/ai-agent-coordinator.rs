from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .constants import EXPECTED_ORGANIZATION, EXPECTED_REPOSITORIES, BootstrapError
from .github_api import GitHubApi, ensure_repository, get_ref, main_has_expected_contract, repository_path
from .manifest import manifest_digest
from .render import seed_files, validate_seed_files

def publish_scaffold(api: GitHubApi, manifest: dict[str, Any], repo_spec: dict[str, Any], merge: bool) -> dict[str, Any]:
    repo_name = repo_spec["name"]
    digest = manifest_digest(manifest)
    repository, repository_action = ensure_repository(api, repo_spec)
    if main_has_expected_contract(api, repo_name, digest):
        return {
            "repository": f"{EXPECTED_ORGANIZATION}/{repo_name}",
            "repository_action": repository_action,
            "publication_action": "already-current",
            "default_branch": repository.get("default_branch", "main"),
        }

    files = seed_files(manifest, repo_name)
    validate_seed_files(manifest, repo_name, files)
    main_ref = get_ref(api, repo_name, "main").body
    main_sha = main_ref.get("object", {}).get("sha")
    if not isinstance(main_sha, str) or len(main_sha) != 40:
        raise BootstrapError("main ref did not contain a bounded commit SHA")
    main_commit = api.request("GET", repository_path(repo_name, f"/git/commits/{main_sha}"), accepted=(200,)).body
    base_tree = main_commit.get("tree", {}).get("sha")
    if not isinstance(base_tree, str) or len(base_tree) != 40:
        raise BootstrapError("main commit did not contain a bounded tree SHA")

    tree_entries = []
    for path, content in files.items():
        blob = api.request(
            "POST",
            repository_path(repo_name, "/git/blobs"),
            {"content": content, "encoding": "utf-8"},
            accepted=(201,),
        ).body
        sha = blob.get("sha")
        if not isinstance(sha, str) or len(sha) != 40:
            raise BootstrapError(f"GitHub did not return a blob SHA for {path}")
        tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": sha})

    tree = api.request(
        "POST",
        repository_path(repo_name, "/git/trees"),
        {"base_tree": base_tree, "tree": tree_entries},
        accepted=(201,),
    ).body
    tree_sha = tree.get("sha")
    if not isinstance(tree_sha, str) or len(tree_sha) != 40:
        raise BootstrapError("GitHub did not return a tree SHA")
    commit = api.request(
        "POST",
        repository_path(repo_name, "/git/commits"),
        {
            "message": "feat: bootstrap the standard H/HAUS repository foundation",
            "tree": tree_sha,
            "parents": [main_sha],
        },
        accepted=(201,),
    ).body
    commit_sha = commit.get("sha")
    if not isinstance(commit_sha, str) or len(commit_sha) != 40:
        raise BootstrapError("GitHub did not return a commit SHA")

    branch = manifest["governance"]["feature_branch"]
    branch_ref = get_ref(api, repo_name, branch, accepted=(200, 404))
    if branch_ref.status == 404:
        api.request(
            "POST",
            repository_path(repo_name, "/git/refs"),
            {"ref": f"refs/heads/{branch}", "sha": commit_sha},
            accepted=(201,),
        )
    else:
        current_sha = branch_ref.body.get("object", {}).get("sha")
        if current_sha != main_sha:
            raise BootstrapError(
                f"existing bootstrap branch for {repo_name} is not at main; manual semantic review is required"
            )
        api.request(
            "PATCH",
            repository_path(repo_name, f"/git/refs/heads/{urllib.parse.quote(branch, safe='')}"),
            {"sha": commit_sha, "force": False},
            accepted=(200,),
        )

    head_query = urllib.parse.urlencode({"state": "open", "head": f"{EXPECTED_ORGANIZATION}:{branch}", "base": "main"})
    open_prs = api.request("GET", repository_path(repo_name, f"/pulls?{head_query}"), accepted=(200,)).body
    if open_prs:
        pull = open_prs[0]
    else:
        pull = api.request(
            "POST",
            repository_path(repo_name, "/pulls"),
            {
                "title": "Bootstrap the standard H/HAUS repository foundation",
                "head": branch,
                "base": "main",
                "body": (
                    "Sealed initial foundation generated from the H/HAUS fleet manifest. "
                    f"Manifest digest: `{digest}`. Validation ran in the coordinator before publication."
                ),
                "maintainer_can_modify": True,
            },
            accepted=(201,),
        ).body
    pr_number = pull.get("number")
    if not isinstance(pr_number, int):
        raise BootstrapError("GitHub did not return a pull request number")

    publication_action = "pull-request-open"
    merge_sha = None
    if merge:
        merged = api.request(
            "PUT",
            repository_path(repo_name, f"/pulls/{pr_number}/merge"),
            {
                "commit_title": "Bootstrap the standard H/HAUS repository foundation",
                "commit_message": f"Sealed fleet manifest {digest}",
                "sha": commit_sha,
                "merge_method": "squash",
            },
            accepted=(200, 405, 409),
        )
        if merged.status != 200 or not merged.body.get("merged"):
            raise BootstrapError(f"bootstrap PR {repo_name}#{pr_number} was not mergeable without history rewrite")
        merge_sha = merged.body.get("sha")
        publication_action = "pull-request-squash-merged"
        for _ in range(10):
            if main_has_expected_contract(api, repo_name, digest):
                break
            time.sleep(1)
        else:
            raise BootstrapError("merged repository contract was not observable on main")

    return {
        "repository": f"{EXPECTED_ORGANIZATION}/{repo_name}",
        "repository_action": repository_action,
        "publication_action": publication_action,
        "pull_request": pr_number,
        "head_sha": commit_sha,
        "merge_sha": merge_sha,
        "files": len(files),
        "manifest_digest": digest,
    }


def require_apply_authority(manifest: dict[str, Any]) -> tuple[str, str, bool]:
    if os.environ.get("HHAUS_REPOSITORY_ADMIN_ENABLED") != "true":
        raise BootstrapError("live repository administration is disabled")
    allowed = {value.strip() for value in os.environ.get("HHAUS_REPOSITORY_ADMIN_ALLOWED_ORGS", "").split(",") if value.strip()}
    if allowed != {EXPECTED_ORGANIZATION}:
        raise BootstrapError("allowed organization set must contain exactly hhaus-org")
    repo_name = os.environ.get("HHAUS_BOOTSTRAP_REPOSITORY", "")
    if repo_name not in EXPECTED_REPOSITORIES:
        raise BootstrapError("apply mode requires one exact repository from the sealed manifest")
    confirmation = os.environ.get("HHAUS_BOOTSTRAP_CONFIRM_REPOSITORY", "")
    if confirmation != f"{EXPECTED_ORGANIZATION}/{repo_name}":
        raise BootstrapError("repository confirmation does not match the exact target")
    digest = manifest_digest(manifest)
    if os.environ.get("HHAUS_BOOTSTRAP_CONFIRM_MANIFEST_SHA256") != digest:
        raise BootstrapError("manifest digest confirmation does not match")
    merge = os.environ.get("HHAUS_BOOTSTRAP_MERGE") == "true"
    expected_merge = f"{EXPECTED_ORGANIZATION}/{repo_name}@{digest}"
    if merge and os.environ.get("HHAUS_BOOTSTRAP_CONFIRM_MERGE") != expected_merge:
        raise BootstrapError("merge confirmation does not match the exact repository and manifest")
    token = os.environ.get("HHAUS_REPOSITORY_ADMIN_TOKEN", "")
    return repo_name, token, merge


def write_result(value: dict[str, Any]) -> None:
    output = os.environ.get("HHAUS_BOOTSTRAP_RESULT_PATH")
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(json.dumps(value, sort_keys=True))
