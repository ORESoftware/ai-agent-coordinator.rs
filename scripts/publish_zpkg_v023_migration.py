#!/usr/bin/env python3
"""Fail-closed publisher for the reviewed Zed v0.2.3 fleet migration plan."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.parse
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from bootstrap_test_org_deep_fleets_live import (
    BootstrapError,
    GitHub,
    git_blob_sha,
    quote,
    redact,
)

OPERATION = "zed-manifest-v023-fleet-migration-2026-08-14"
TRACKING_ISSUE = "DEN-3733"
BRANCH = "agent/den-3733-zpkg-v023-20260814"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
ACCEPTED_CHECK_CONCLUSIONS = {"success", "neutral", "skipped"}
FAILED_CHECK_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "stale",
    "startup_failure",
    "timed_out",
}


class MigrationError(BootstrapError):
    """The live repository state violates the reviewed migration plan."""


@dataclass(frozen=True)
class Mutation:
    repository: str
    default_branch: str
    private: bool
    fork: bool
    path: str
    source_blob: str
    proposed_blob: str
    proposal_path: Path
    phase: int
    recipes: tuple[str, ...]
    content: str


@dataclass(frozen=True)
class RepositoryPlan:
    full_name: str
    default_branch: str
    private: bool
    fork: bool
    phase: int
    mutations: tuple[Mutation, ...]

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]


@dataclass(frozen=True)
class ReconciledManifest:
    full_name: str
    default_branch: str
    private: bool
    fork: bool
    path: str
    snapshot_blob: str
    current_blob: str
    current_size: int
    current_sha256: str

    @property
    def owner(self) -> str:
        return self.full_name.split("/", 1)[0]

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]


@dataclass
class Preflight:
    plan: RepositoryPlan
    default_sha: str
    base_tree_sha: str
    paths: dict[str, str]
    state: str
    has_workflows: bool


@dataclass
class Result:
    repository: str
    phase: int
    state: str = "planned"
    default_branch: str | None = None
    original_default_sha: str | None = None
    branch: str | None = None
    head_sha: str | None = None
    pull_request_number: int | None = None
    pull_request_url: str | None = None
    check_state: str | None = None
    merged: bool = False
    final_default_sha: str | None = None
    error: str | None = None


@dataclass
class Ledger:
    schema_version: int = 1
    operation: str = OPERATION
    mode: str = "preflight"
    phase: int = 1
    plan_sha256: str = ""
    started_at_epoch: int = field(default_factory=lambda: int(time.time()))
    finished_at_epoch: int | None = None
    preflight_complete: bool = False
    preflight_repositories_total: int = 0
    results: list[Result] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "repositories_total": len(self.results),
            "already_migrated": sum(item.state == "already_migrated" for item in self.results),
            "source_verified": sum(item.state == "source_verified" for item in self.results),
            "pull_requests_open": sum(item.pull_request_number is not None and not item.merged for item in self.results),
            "pull_requests_merged": sum(item.merged for item in self.results),
            "verified_on_default_branch": sum(
                item.state in {"already_migrated", "merged_verified"} for item in self.results
            ),
            "failures": len(self.failures),
        }

    def write(self, path: Path) -> None:
        self.finished_at_epoch = int(time.time())
        payload = {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "mode": self.mode,
            "phase": self.phase,
            "plan_sha256": self.plan_sha256,
            "started_at_epoch": self.started_at_epoch,
            "finished_at_epoch": self.finished_at_epoch,
            "preflight_complete": self.preflight_complete,
            "preflight_repositories_total": self.preflight_repositories_total,
            "summary": self.summary(),
            "results": [asdict(item) for item in self.results],
            "failures": self.failures,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)


def plan_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_repository_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise MigrationError(f"unsafe repository path: {value!r}")
    return path.as_posix()


def load_reconciled(raw: dict[str, Any]) -> tuple[ReconciledManifest, ...]:
    reconciled: list[ReconciledManifest] = []
    seen: set[tuple[str, str]] = set()
    for item in raw.get("already_reconciled", []):
        repository = str(item.get("repository") or "")
        path = safe_repository_path(str(item.get("path") or ""))
        snapshot_blob = str(item.get("snapshot_blob") or "")
        current_blob = str(item.get("current_blob") or "")
        current_sha256 = str(item.get("current_sha256") or "")
        current_size = item.get("current_size")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repository):
            raise MigrationError(f"malformed reconciled repository: {repository}")
        if not SHA_PATTERN.fullmatch(snapshot_blob) or not SHA_PATTERN.fullmatch(current_blob):
            raise MigrationError(f"malformed reconciled blob identity: {repository}:{path}")
        if not re.fullmatch(r"[0-9a-f]{64}", current_sha256):
            raise MigrationError(f"malformed reconciled SHA-256: {repository}:{path}")
        if not isinstance(current_size, int) or current_size <= 0:
            raise MigrationError(f"malformed reconciled size: {repository}:{path}")
        if item.get("validated_interface_revision") != "8428bc574111fa148e590c8350c7855035ce2046":
            raise MigrationError(f"reconciled validation provenance drift: {repository}:{path}")
        key = (repository.lower(), path)
        if key in seen:
            raise MigrationError(f"duplicate reconciled manifest: {repository}:{path}")
        seen.add(key)
        reconciled.append(
            ReconciledManifest(
                full_name=repository,
                default_branch=str(item.get("default_branch") or ""),
                private=item.get("private") is True,
                fork=item.get("fork") is True,
                path=path,
                snapshot_blob=snapshot_blob,
                current_blob=current_blob,
                current_size=current_size,
                current_sha256=current_sha256,
            )
        )
    return tuple(sorted(reconciled, key=lambda item: (item.full_name.lower(), item.path)))


def repository_api(plan: RepositoryPlan | ReconciledManifest) -> str:
    return f"/repos/{quote(plan.owner)}/{quote(plan.name)}"


def verify_reconciled_blob(github: GitHub, item: ReconciledManifest) -> None:
    _, blob, _ = github.get(
        f"{repository_api(item)}/git/blobs/{quote(item.current_blob)}"
    )
    if (
        blob.get("sha") != item.current_blob
        or blob.get("size") != item.current_size
        or blob.get("encoding") != "base64"
    ):
        raise MigrationError(
            f"reconciled blob metadata drift: {item.full_name}:{item.path}"
        )
    encoded = "".join(str(blob.get("content") or "").split())
    if len(encoded) > ((item.current_size + 2) // 3) * 4:
        raise MigrationError(
            f"reconciled blob encoding is oversized: {item.full_name}:{item.path}"
        )
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise MigrationError(
            f"reconciled blob encoding is invalid: {item.full_name}:{item.path}"
        ) from error
    if (
        len(content) != item.current_size
        or hashlib.sha256(content).hexdigest() != item.current_sha256
    ):
        raise MigrationError(
            f"reconciled blob SHA-256 drift: {item.full_name}:{item.path}"
        )


def load_plan(path: Path) -> tuple[dict[str, Any], dict[str, RepositoryPlan]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or raw.get("operation") != OPERATION:
        raise MigrationError("unsupported migration plan")
    if raw.get("tracking_issue") != TRACKING_ISSUE:
        raise MigrationError("migration plan tracking issue drift")
    zed = raw.get("zed") or {}
    if zed.get("cli_tag") != "v0.2.3" or zed.get("interface_revision") != "8428bc574111fa148e590c8350c7855035ce2046":
        raise MigrationError("migration plan Zed provenance drift")
    proposals = {
        item["source_blob"]: item for item in raw.get("proposals", []) if isinstance(item, dict)
    }
    if len(proposals) != len(raw.get("proposals", [])):
        raise MigrationError("duplicate or malformed proposal records")
    grouped: dict[str, list[Mutation]] = {}
    identities: dict[str, tuple[str, bool, bool, int]] = {}
    seen_paths: set[tuple[str, str]] = set()
    for item in raw.get("mutations", []):
        repository = str(item.get("repository") or "")
        path_value = safe_repository_path(str(item.get("path") or ""))
        source_blob = str(item.get("source_blob") or "")
        proposed_blob = str(item.get("proposed_blob") or "")
        phase = item.get("phase")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repository):
            raise MigrationError(f"malformed repository: {repository}")
        if not SHA_PATTERN.fullmatch(source_blob) or not SHA_PATTERN.fullmatch(proposed_blob):
            raise MigrationError(f"malformed blob identity for {repository}:{path_value}")
        if phase not in {1, 2}:
            raise MigrationError(f"malformed phase for {repository}:{path_value}")
        proposal = proposals.get(source_blob)
        if proposal is None or proposal.get("proposed_blob") != proposed_blob:
            raise MigrationError(f"proposal lookup drift for {repository}:{path_value}")
        proposal_path = (path.parent / str(item.get("proposal") or "")).resolve()
        try:
            proposal_path.relative_to(path.parent.resolve())
        except ValueError as error:
            raise MigrationError("proposal path escapes the plan directory") from error
        content = proposal_path.read_text(encoding="utf-8")
        if git_blob_sha(content) != proposed_blob:
            raise MigrationError(f"proposal bytes drift for {repository}:{path_value}")
        key = (repository.lower(), path_value)
        if key in seen_paths:
            raise MigrationError(f"duplicate mutation path: {repository}:{path_value}")
        seen_paths.add(key)
        mutation = Mutation(
            repository=repository,
            default_branch=str(item.get("default_branch") or ""),
            private=item.get("private") is True,
            fork=item.get("fork") is True,
            path=path_value,
            source_blob=source_blob,
            proposed_blob=proposed_blob,
            proposal_path=proposal_path,
            phase=phase,
            recipes=tuple(item.get("recipes") or ()),
            content=content,
        )
        grouped.setdefault(repository.lower(), []).append(mutation)
        identity = (mutation.default_branch, mutation.private, mutation.fork, phase)
        previous = identities.setdefault(repository.lower(), identity)
        if previous != identity:
            raise MigrationError(f"repository plan fields disagree: {repository}")
    plans: dict[str, RepositoryPlan] = {}
    for key, mutations in grouped.items():
        first = mutations[0]
        plans[key] = RepositoryPlan(
            full_name=first.repository,
            default_branch=first.default_branch,
            private=first.private,
            fork=first.fork,
            phase=first.phase,
            mutations=tuple(sorted(mutations, key=lambda item: item.path)),
        )
    snapshot = raw.get("snapshot") or {}
    if snapshot.get("mutation_repositories") != len(plans) or snapshot.get("mutation_instances") != len(seen_paths):
        raise MigrationError("migration plan summary does not match its mutations")
    reconciled = load_reconciled(raw)
    reconciled_paths = {(item.full_name.lower(), item.path) for item in reconciled}
    if reconciled_paths & seen_paths:
        raise MigrationError("reconciled manifest must not also be a planned mutation")
    return raw, plans


def get_ref(
    github: GitHub,
    plan: RepositoryPlan | ReconciledManifest,
    branch: str,
    *,
    missing: bool = False,
) -> dict[str, Any] | None:
    status, payload, _ = github.get(
        f"{repository_api(plan)}/git/ref/heads/{quote(branch)}", allow=(404,) if missing else ()
    )
    return None if status == 404 else payload


def commit_tree(
    github: GitHub,
    plan: RepositoryPlan | ReconciledManifest,
    commit_sha: str,
) -> tuple[str, dict[str, str]]:
    _, commit, _ = github.get(f"{repository_api(plan)}/git/commits/{quote(commit_sha)}")
    tree_sha = str(commit["tree"]["sha"])
    _, tree, _ = github.get(f"{repository_api(plan)}/git/trees/{quote(tree_sha)}?recursive=1")
    if tree.get("truncated"):
        raise MigrationError(f"repository tree is truncated: {plan.full_name}")
    paths = {
        str(item["path"]): str(item["sha"])
        for item in tree.get("tree", [])
        if item.get("type") == "blob" and isinstance(item.get("path"), str)
    }
    return tree_sha, paths


def target_dirs_exist(mutation: Mutation, paths: dict[str, str]) -> None:
    manifest = tomllib.loads(mutation.content)
    parent = PurePosixPath(mutation.path).parent
    for name, target in manifest.get("targets", {}).items():
        directory = str(target.get("dir") or "")
        if directory == ".":
            prefix = "" if str(parent) == "." else parent.as_posix()
        else:
            prefix = (parent / directory).as_posix() if str(parent) != "." else PurePosixPath(directory).as_posix()
        if prefix and not any(path == prefix or path.startswith(prefix + "/") for path in paths):
            raise MigrationError(
                f"target `{name}` path `{prefix}` is absent from {mutation.repository}"
            )


def classify_paths(plan: RepositoryPlan, paths: dict[str, str]) -> str:
    states: set[str] = set()
    for mutation in plan.mutations:
        actual = paths.get(mutation.path)
        if actual == mutation.source_blob:
            states.add("source")
        elif actual == mutation.proposed_blob:
            states.add("proposed")
        elif actual is None:
            raise MigrationError(f"manifest disappeared: {plan.full_name}:{mutation.path}")
        else:
            raise MigrationError(
                f"unreviewed manifest blob {actual} at {plan.full_name}:{mutation.path}; "
                f"expected {mutation.source_blob} or {mutation.proposed_blob}"
            )
    if len(states) != 1:
        raise MigrationError(f"partial migration state requires reconciliation: {plan.full_name}")
    return states.pop()


def validate_owner(github: GitHub, owner: str, actor: dict[str, Any]) -> None:
    """Require each namespace to be either the actor or an administered org."""
    _, namespace, _ = github.get(f"/users/{quote(owner)}")
    if str(namespace.get("login") or "").lower() != owner.lower():
        raise MigrationError(f"owner identity drift: {owner}")
    namespace_type = namespace.get("type")
    if namespace_type == "User":
        if namespace.get("id") != actor.get("id") or owner.lower() != str(actor.get("login") or "").lower():
            raise MigrationError(f"foreign user namespace is not publishable: {owner}")
        return
    if namespace_type != "Organization":
        raise MigrationError(f"unsupported owner type for {owner}: {namespace_type!r}")
    _, org, _ = github.get(f"/orgs/{quote(owner)}")
    if org.get("id") != namespace.get("id") or str(org.get("login") or "").lower() != owner.lower():
        raise MigrationError(f"organization identity drift: {owner}")
    _, membership, _ = github.get(f"/user/memberships/orgs/{quote(owner)}")
    if membership.get("state") != "active" or membership.get("role") != "admin":
        raise MigrationError(f"active organization admin membership is required: {owner}")


def preflight_all(
    github: GitHub, raw_plan: dict[str, Any], plans: dict[str, RepositoryPlan]
) -> dict[str, Preflight]:
    _, actor, _ = github.get("/user")
    expected_actor = raw_plan.get("authenticated_actor") or {}
    if actor.get("login") != expected_actor.get("login") or actor.get("id") != expected_actor.get("id"):
        raise MigrationError("authenticated GitHub identity drift")
    reconciled = load_reconciled(raw_plan)
    owners = sorted(
        {plan.owner for plan in plans.values()} | {item.owner for item in reconciled},
        key=str.lower,
    )
    for owner in owners:
        validate_owner(github, owner, actor)
    output: dict[str, Preflight] = {}
    for index, plan in enumerate(sorted(plans.values(), key=lambda item: item.full_name.lower()), start=1):
        _, repository, _ = github.get(repository_api(plan))
        if str(repository.get("full_name") or "").lower() != plan.full_name.lower():
            raise MigrationError(f"repository identity drift: {plan.full_name}")
        if repository.get("archived") or repository.get("disabled"):
            raise MigrationError(f"repository is archived or disabled: {plan.full_name}")
        if repository.get("private") is not plan.private or repository.get("fork") is not plan.fork:
            raise MigrationError(f"repository privacy/fork drift: {plan.full_name}")
        if repository.get("default_branch") != plan.default_branch:
            raise MigrationError(f"default branch drift: {plan.full_name}")
        ref = get_ref(github, plan, plan.default_branch)
        default_sha = str(ref["object"]["sha"])
        tree_sha, paths = commit_tree(github, plan, default_sha)
        state = classify_paths(plan, paths)
        if state == "source":
            for mutation in plan.mutations:
                target_dirs_exist(mutation, paths)
        output[plan.full_name.lower()] = Preflight(
            plan=plan,
            default_sha=default_sha,
            base_tree_sha=tree_sha,
            paths=paths,
            state=state,
            has_workflows=any(path.startswith(".github/workflows/") for path in paths),
        )
        print(f"preflight [{index}/{len(plans)}] {plan.full_name}: {state}", flush=True)
    for index, item in enumerate(reconciled, start=1):
        _, repository, _ = github.get(repository_api(item))
        if str(repository.get("full_name") or "").lower() != item.full_name.lower():
            raise MigrationError(f"reconciled repository identity drift: {item.full_name}")
        if repository.get("archived") or repository.get("disabled"):
            raise MigrationError(f"reconciled repository is archived or disabled: {item.full_name}")
        if repository.get("private") is not item.private or repository.get("fork") is not item.fork:
            raise MigrationError(f"reconciled repository privacy/fork drift: {item.full_name}")
        if repository.get("default_branch") != item.default_branch:
            raise MigrationError(f"reconciled default branch drift: {item.full_name}")
        ref = get_ref(github, item, item.default_branch)
        default_sha = str(ref["object"]["sha"])
        _, paths = commit_tree(github, item, default_sha)
        actual = paths.get(item.path)
        if actual != item.current_blob:
            raise MigrationError(
                f"reconciled manifest drift at {item.full_name}:{item.path}; "
                f"expected {item.current_blob}, found {actual or 'missing'}"
            )
        verify_reconciled_blob(github, item)
        print(
            f"preflight reconciled [{index}/{len(reconciled)}] "
            f"{item.full_name}:{item.path}: {item.current_blob}",
            flush=True,
        )
    return output


def branch_commit(github: GitHub, item: Preflight) -> tuple[str, bool]:
    plan = item.plan
    if item.state == "proposed":
        return item.default_sha, True
    existing = get_ref(github, plan, BRANCH, missing=True)
    if existing is not None:
        head_sha = str(existing["object"]["sha"])
        _, paths = commit_tree(github, plan, head_sha)
        if classify_paths(plan, paths) != "proposed":
            raise MigrationError(f"existing migration branch drift: {plan.full_name}")
        return head_sha, False
    entries = [
        {"path": mutation.path, "mode": "100644", "type": "blob", "content": mutation.content}
        for mutation in plan.mutations
    ]
    _, tree, _ = github.post(
        f"{repository_api(plan)}/git/trees", {"base_tree": item.base_tree_sha, "tree": entries}
    )
    _, commit, _ = github.post(
        f"{repository_api(plan)}/git/commits",
        {
            "message": (
                "fix(DEN-3733): migrate Zed manifest to v0.2.3\n\n"
                "Exact-blob fleet migration; source generators land before generated repositories."
            ),
            "tree": tree["sha"],
            "parents": [item.default_sha],
        },
    )
    head_sha = str(commit["sha"])
    status, _, _ = github.post(
        f"{repository_api(plan)}/git/refs",
        {"ref": f"refs/heads/{BRANCH}", "sha": head_sha},
        allow=(422,),
    )
    if status == 422:
        raced = get_ref(github, plan, BRANCH)
        raced_sha = str(raced["object"]["sha"])
        _, paths = commit_tree(github, plan, raced_sha)
        if classify_paths(plan, paths) != "proposed":
            raise MigrationError(f"migration branch race produced divergent content: {plan.full_name}")
        head_sha = raced_sha
    return head_sha, False


def find_or_create_pull(github: GitHub, plan: RepositoryPlan, head_sha: str) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {"state": "all", "head": f"{plan.owner}:{BRANCH}", "base": plan.default_branch, "per_page": "100"}
    )
    _, pulls, _ = github.get(f"{repository_api(plan)}/pulls?{query}")
    if pulls:
        pull = sorted(pulls, key=lambda item: int(item["number"]))[-1]
        if pull.get("merged_at"):
            return pull
        if pull.get("state") != "open" or pull.get("head", {}).get("sha") != head_sha:
            raise MigrationError(f"existing migration pull request drift: {plan.full_name}")
        return pull
    paths = "\n".join(f"- `{mutation.path}`" for mutation in plan.mutations)
    recipes = sorted({recipe for mutation in plan.mutations for recipe in mutation.recipes})
    recipe_text = "\n".join(f"- `{recipe}`" for recipe in recipes)
    body = (
        "## Zed v0.2.3 manifest migration\n\n"
        "This changes only the reviewed manifest paths below, from exact inventoried Git blobs "
        "to proposals accepted by released zed-cli v0.2.3 and zed-interfaces "
        "`8428bc574111fa148e590c8350c7855035ce2046`.\n\n"
        f"### Paths\n\n{paths}\n\n### Semantic recipes\n\n{recipe_text}\n\n"
        "The publisher completed an all-repository preflight before creating any branch. It refuses "
        "moved blobs, partial migrations, repository identity/visibility drift, duplicate target roots, "
        "and missing target directories. No unrelated file is changed.\n\n"
        "Linear: DEN-3733\nParent: DEN-2056\n\n"
        "This draft must remain unmerged until its exact head has either successful repository checks "
        "or an explicitly reviewed central-only validation record."
    )
    _, pull, _ = github.post(
        f"{repository_api(plan)}/pulls",
        {
            "title": "fix(DEN-3733): migrate Zed manifest to v0.2.3",
            "head": BRANCH,
            "base": plan.default_branch,
            "body": body,
            "draft": True,
            "maintainer_can_modify": True,
        },
    )
    if pull.get("head", {}).get("sha") != head_sha:
        raise MigrationError(f"created pull request head mismatch: {plan.full_name}")
    return pull


def check_state(github: GitHub, plan: RepositoryPlan, head_sha: str) -> tuple[str, str]:
    _, payload, _ = github.get(f"{repository_api(plan)}/commits/{quote(head_sha)}/check-runs?per_page=100")
    checks = [
        item for item in payload.get("check_runs", []) if (item.get("app") or {}).get("slug") == "github-actions"
    ]
    if not checks:
        return "none", "no GitHub Actions checks"
    failed = [
        f"{item.get('name')}:{item.get('conclusion')}"
        for item in checks
        if item.get("status") == "completed" and item.get("conclusion") in FAILED_CHECK_CONCLUSIONS
    ]
    if failed:
        return "failure", ", ".join(sorted(failed))
    pending = [str(item.get("name")) for item in checks if item.get("status") != "completed"]
    if pending:
        return "pending", ", ".join(sorted(pending))
    unacceptable = [
        f"{item.get('name')}:{item.get('conclusion')}"
        for item in checks
        if item.get("conclusion") not in ACCEPTED_CHECK_CONCLUSIONS
    ]
    if unacceptable:
        return "failure", ", ".join(sorted(unacceptable))
    return "success", f"{len(checks)} GitHub Actions checks"


def mark_ready(github: GitHub, node_id: str) -> None:
    query = """mutation MarkReady($id: ID!) {
      markPullRequestReadyForReview(input: {pullRequestId: $id}) {
        pullRequest { isDraft }
      }
    }"""
    _, payload, _ = github.post("/graphql", {"query": query, "variables": {"id": node_id}})
    if payload.get("errors"):
        raise MigrationError(f"failed to mark pull request ready: {payload['errors']}")
    pull = (((payload.get("data") or {}).get("markPullRequestReadyForReview") or {}).get("pullRequest") or {})
    if pull.get("isDraft") is not False:
        raise MigrationError("pull request remained draft after ready-for-review mutation")


def merge_one(github: GitHub, item: Preflight, result: Result) -> None:
    plan = item.plan
    if not result.pull_request_number or not result.head_sha:
        raise MigrationError(f"missing pull request state: {plan.full_name}")
    _, pull, _ = github.get(f"{repository_api(plan)}/pulls/{result.pull_request_number}")
    if pull.get("head", {}).get("sha") != result.head_sha:
        raise MigrationError(f"pull request head advanced: {plan.full_name}")
    if pull.get("draft"):
        mark_ready(github, str(pull["node_id"]))
    current_ref = get_ref(github, plan, plan.default_branch)
    current_sha = str(current_ref["object"]["sha"])
    _, current_paths = commit_tree(github, plan, current_sha)
    state = classify_paths(plan, current_paths)
    if state == "proposed":
        result.state = "already_migrated"
        result.final_default_sha = current_sha
        return
    status, payload, _ = github.put(
        f"{repository_api(plan)}/pulls/{result.pull_request_number}/merge",
        {
            "commit_title": "fix(DEN-3733): migrate Zed manifest to v0.2.3",
            "commit_message": f"Tracking: {OPERATION}; DEN-3733",
            "sha": result.head_sha,
            "merge_method": "squash",
        },
        allow=(405, 409),
    )
    if status in {405, 409} or not payload or payload.get("merged") is not True:
        message = payload.get("message") if isinstance(payload, dict) else f"HTTP {status}"
        raise MigrationError(f"exact-head merge failed for {plan.full_name}: {message}")
    result.merged = True
    ref = get_ref(github, plan, plan.default_branch)
    final_sha = str(ref["object"]["sha"])
    _, final_paths = commit_tree(github, plan, final_sha)
    if classify_paths(plan, final_paths) != "proposed":
        raise MigrationError(f"post-merge manifest verification failed: {plan.full_name}")
    result.final_default_sha = final_sha
    result.state = "merged_verified"


def github_token(args: argparse.Namespace) -> str:
    if args.gh_auth:
        completed = subprocess.run(
            ["gh", "auth", "token"], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
        )
        if completed.returncode:
            raise MigrationError("unable to read the active GitHub CLI token")
        return completed.stdout.strip()
    value = os.environ.get("GITHUB_TOKEN", "")
    if not value:
        raise MigrationError("live execution requires --gh-auth or GITHUB_TOKEN")
    return value


def execute(args: argparse.Namespace) -> int:
    raw_plan, plans = load_plan(args.plan)
    digest = plan_digest(args.plan)
    if args.mode == "merge-reviewed" and args.reviewed_plan_sha256 != digest:
        raise MigrationError(f"reviewed plan digest mismatch; expected {digest}")
    ledger = Ledger(mode=args.mode, phase=args.phase, plan_sha256=digest)
    selected = [plan for plan in plans.values() if plan.phase == args.phase]
    ledger.results = [
        Result(repository=plan.full_name, phase=plan.phase, default_branch=plan.default_branch)
        for plan in sorted(selected, key=lambda item: item.full_name.lower())
    ]
    results = {item.repository.lower(): item for item in ledger.results}
    github = GitHub(github_token(args))
    try:
        preflights = preflight_all(github, raw_plan, plans)
        ledger.preflight_complete = True
        ledger.preflight_repositories_total = len(preflights) + len(load_reconciled(raw_plan))
        for plan in selected:
            item = preflights[plan.full_name.lower()]
            result = results[plan.full_name.lower()]
            result.original_default_sha = item.default_sha
            result.state = "already_migrated" if item.state == "proposed" else "source_verified"
        if args.mode == "preflight":
            return 0
        for index, plan in enumerate(sorted(selected, key=lambda item: item.full_name.lower()), start=1):
            item = preflights[plan.full_name.lower()]
            result = results[plan.full_name.lower()]
            if item.state == "proposed":
                continue
            head_sha, already = branch_commit(github, item)
            result.head_sha = head_sha
            result.branch = plan.default_branch if already else BRANCH
            if already:
                result.state = "already_migrated"
                continue
            pull = find_or_create_pull(github, plan, head_sha)
            result.pull_request_number = int(pull["number"])
            result.pull_request_url = str(pull.get("html_url") or "")
            result.state = "pull_request_open"
            print(f"pull [{index}/{len(selected)}] {plan.full_name}: {result.pull_request_url}", flush=True)
        if args.mode == "create-pulls":
            return 0
        deadline = time.monotonic() + args.check_timeout_seconds
        first_poll = time.monotonic()
        pending = {key: result for key, result in results.items() if result.state == "pull_request_open"}
        while pending and time.monotonic() < deadline:
            finished: list[str] = []
            for key, result in pending.items():
                item = preflights[key]
                state, detail = check_state(github, item.plan, result.head_sha or "")
                if state == "none" and not item.has_workflows and time.monotonic() - first_poll >= args.no_check_grace_seconds:
                    state, detail = "central-only", "repository has no GitHub Actions workflows"
                result.check_state = f"{state}:{detail}"
                if state in {"success", "central-only", "failure"}:
                    finished.append(key)
            for key in finished:
                pending.pop(key, None)
            if pending:
                print(f"check poll: pending={len(pending)} complete={len(results)-len(pending)}", flush=True)
                time.sleep(15)
        for key, result in pending.items():
            result.check_state = "timeout:checks did not complete"
        blocked = [
            result for result in results.values() if result.state == "pull_request_open" and not (
                result.check_state and (result.check_state.startswith("success:") or result.check_state.startswith("central-only:"))
            )
        ]
        if blocked:
            for result in blocked:
                result.error = f"merge gate blocked: {result.check_state}"
                ledger.failures.append({"repository": result.repository, "error": result.error})
            return 1
        for result in results.values():
            if result.state != "pull_request_open":
                continue
            try:
                merge_one(github, preflights[result.repository.lower()], result)
                print(f"merged and verified: {result.repository}", flush=True)
            except Exception as error:  # continue for maximal bounded evidence
                result.error = redact(str(error), github.token)
                ledger.failures.append({"repository": result.repository, "error": result.error})
                print(f"merge blocked: {result.repository}: {result.error}", flush=True)
        return 0 if not ledger.failures else 1
    except Exception as error:
        ledger.failures.append(
            {"repository": "preflight-or-publisher", "error": redact(str(error), github.token)}
        )
        raise
    finally:
        ledger.write(args.result)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--phase", type=int, choices=(1, 2), required=True)
    parser.add_argument("--mode", choices=("preflight", "create-pulls", "merge-reviewed"), default="preflight")
    parser.add_argument("--reviewed-plan-sha256")
    parser.add_argument("--check-timeout-seconds", type=int, default=1800)
    parser.add_argument("--no-check-grace-seconds", type=int, default=90)
    parser.add_argument("--gh-auth", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check_timeout_seconds < 60 or args.no_check_grace_seconds < 30:
        raise MigrationError("check timeouts are below the reviewed minimum")
    return execute(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError, MigrationError) as error:
        print(f"fatal migration error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
