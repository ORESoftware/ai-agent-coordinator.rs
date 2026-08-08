"""Classify observations, maintain the idempotent ledger, and emit CLI work."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .common import (
    CLI_QUEUE_SCHEMA,
    DEFAULT_CLI_TASK_ID,
    FINDING_RANK,
    HTTPS_GITHUB_RE,
    LEDGER_SCHEMA,
    MAX_BATCH_SIZE,
    MAX_ITEMS,
    ORIGIN_ID_RE,
    RecoveryError,
    SUMMARY_SCHEMA,
    expect_object,
    ledger_key,
    parse_timestamp,
    sha256_value,
    validate_public_safety,
)
from .observation import validate_observation


def claimed_pr_number(url: str | None) -> int | None:
    if url is None:
        return None
    match = HTTPS_GITHUB_RE.fullmatch(url)
    if not match or match.group("kind") != "pull" or not match.group("value"):
        return None
    return int(match.group("value"))


def classify(item: Mapping[str, Any]) -> dict[str, Any]:
    findings: set[str] = set()
    target = item["target"]
    intent = item["intent"]
    local = item["local"]
    remote = item["remote"]
    claims = item["claims"]

    if target["artifact_kind"] == "none" or not intent["artifact_expected"]:
        return {
            "status": "excluded",
            "findings": [],
            "next_action": "none",
            "reason": "ordinary conversation or no tangible artifact was intended",
        }

    if not target["ownership_resolved"]:
        findings.add("ownership_ambiguous")
    if not remote["collected"]:
        findings.add("remote_evidence_incomplete")

    repo = remote["repository"]
    artifact = local["artifact"]
    if remote["collected"] and not repo["exists"]:
        findings.add("repository_missing")
    if artifact is not None and not local["git_repository"]:
        findings.add("artifact_only")
    if local["git_repository"] and not local["remote_present"]:
        findings.add("repository_has_no_remote")
    if local["dirty_paths"]:
        findings.add("changes_uncommitted")

    remote_branch_map = {branch["name"]: branch["sha"] for branch in remote["branches"]}
    remote_commit_shas = {commit["sha"] for commit in remote["commits"]}
    remote_commit_shas.update(remote_branch_map.values())
    if local["head_sha"] and repo["exists"] and local["head_sha"] not in remote_commit_shas:
        findings.add("commits_unpushed")

    intended_branch = intent["branch"]
    local_branches = set(local["branches"])
    if local["branch"]:
        local_branches.add(local["branch"])
    if intended_branch:
        if intended_branch not in local_branches and intended_branch not in remote_branch_map:
            findings.add("branch_not_created")
        elif intended_branch not in remote_branch_map:
            findings.add("branch_not_published")
        elif intent["pull_request_required"]:
            matches = [pr for pr in remote["pull_requests"] if pr["head"] == intended_branch]
            if not matches:
                findings.add("branch_without_pull_request")

    if claims["repository_url"] and (not repo["exists"] or claims["repository_url"] != repo["url"]):
        findings.add("claimed_repository_unverified")
    if claims["commit_sha"] and claims["commit_sha"] not in remote_commit_shas:
        findings.add("claimed_commit_unverified")
    if claims["branch"] and claims["branch"] not in remote_branch_map:
        findings.add("claimed_branch_unverified")
    claimed_pr = claimed_pr_number(claims["pull_request_url"])
    if claimed_pr is not None and claimed_pr not in {pr["number"] for pr in remote["pull_requests"]}:
        findings.add("claimed_pull_request_unverified")

    ordered = sorted(findings, key=lambda value: FINDING_RANK[value])
    if not ordered:
        return {
            "status": "complete",
            "findings": [],
            "next_action": "none",
            "reason": "repository, branch, commit, and pull-request evidence is complete",
        }

    fail_closed_findings = {
        "ownership_ambiguous",
        "remote_evidence_incomplete",
        "claimed_repository_unverified",
        "claimed_commit_unverified",
        "claimed_branch_unverified",
        "claimed_pull_request_unverified",
    }
    if findings & fail_closed_findings:
        return {
            "status": "blocked",
            "findings": ordered,
            "next_action": "manual_review",
            "reason": (
                "ownership, remote evidence, or a claimed GitHub object is incomplete; "
                "fail closed"
            ),
        }
    if "repository_missing" in findings:
        action = "cli_create_repository" if intent["allow_repository_creation"] else "manual_review"
        return {
            "status": "actionable" if action != "manual_review" else "blocked",
            "findings": ordered,
            "next_action": action,
            "reason": "the intended repository is absent",
        }
    if findings & {
        "artifact_only",
        "repository_has_no_remote",
        "changes_uncommitted",
        "commits_unpushed",
        "branch_not_created",
        "branch_not_published",
    }:
        return {
            "status": "actionable",
            "findings": ordered,
            "next_action": "cli_recover_local_artifact",
            "reason": "local Git state or artifact publication is incomplete",
        }
    if "branch_without_pull_request" in findings:
        return {
            "status": "actionable",
            "findings": ordered,
            "next_action": "open_draft_pull_request",
            "reason": "the reviewed branch is published but has no pull request",
        }
    return {
        "status": "blocked",
        "findings": ordered,
        "next_action": "manual_review",
        "reason": "a claimed GitHub object lacks current evidence",
    }


def evidence_links(item: Mapping[str, Any]) -> list[str]:
    links: set[str] = set()
    repo = item["remote"]["repository"]
    if repo["url"]:
        links.add(repo["url"])
    links.update(commit["url"] for commit in item["remote"]["commits"])
    links.update(pr["url"] for pr in item["remote"]["pull_requests"])
    return sorted(links)


def desired_visibility(item: Mapping[str, Any]) -> str:
    return item["target"]["visibility"] or "private"


def build_cli_item(entry: Mapping[str, Any], target_task_id: str) -> dict[str, Any] | None:
    if entry["classification"]["next_action"] not in {
        "cli_create_repository",
        "cli_recover_local_artifact",
    }:
        return None
    item = entry["observation"]
    artifact = item["local"]["artifact"]
    return {
        "ledger_key": entry["ledger_key"],
        "target_task_id": target_task_id,
        "owner": item["target"]["owner"],
        "repository": item["target"]["repository"],
        "visibility": desired_visibility(item),
        "base_branch": item["intent"]["base_branch"],
        "intended_branch": item["intent"]["branch"],
        "artifact": copy.deepcopy(artifact),
        "local_head_sha": item["local"]["head_sha"],
        "findings": list(entry["classification"]["findings"]),
        "required_sequence": [
            "verify current repository, branches, commits, and pull requests",
            "scan intended paths for credentials and private material",
            "reuse existing repository and branch when present",
            "create the repository only when absent and explicitly authorized",
            "stage only the intended paths",
            "commit without rewriting shared history",
            "push without force",
            "open or reuse one draft pull request",
            "record the verified repository URL, branch, commit SHA, and PR URL",
        ],
        "forbidden": [
            "reuse chat-pasted credentials",
            "force push",
            "broadly stage a mixed worktree",
            "directly write the default branch",
            "merge or bypass protections",
        ],
    }


def empty_ledger(now: str) -> dict[str, Any]:
    return {
        "schema_version": LEDGER_SCHEMA,
        "created_at": now,
        "updated_at": now,
        "last_batch": None,
        "entries": {},
        "summary": {},
    }


def validate_ledger(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema_version") != LEDGER_SCHEMA:
        raise RecoveryError(f"ledger schema_version must be {LEDGER_SCHEMA}")
    created_at = parse_timestamp(value.get("created_at"), "ledger.created_at")
    updated_at = parse_timestamp(value.get("updated_at"), "ledger.updated_at")
    entries = expect_object(value.get("entries", {}), "ledger.entries")
    if len(entries) > MAX_ITEMS:
        raise RecoveryError("ledger.entries exceeds the item limit")
    for key, entry in entries.items():
        if not isinstance(key, str) or not key:
            raise RecoveryError("ledger entry keys must be non-empty strings")
        expect_object(entry, f"ledger.entries[{key!r}]")
    normalized = copy.deepcopy(dict(value))
    normalized["created_at"] = created_at
    normalized["updated_at"] = updated_at
    validate_public_safety(normalized)
    return normalized


def summarize_entries(entries: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    statuses = Counter(entry["classification"]["status"] for entry in entries.values())
    findings = Counter(
        finding
        for entry in entries.values()
        for finding in entry["classification"]["findings"]
    )
    actions = Counter(entry["classification"]["next_action"] for entry in entries.values())
    return {
        "entries": len(entries),
        "status_counts": dict(sorted(statuses.items())),
        "finding_counts": dict(sorted(findings.items())),
        "next_action_counts": dict(sorted(actions.items())),
        "complete": statuses.get("complete", 0),
        "actionable": statuses.get("actionable", 0),
        "blocked": statuses.get("blocked", 0),
        "excluded": statuses.get("excluded", 0),
    }


def reconcile(
    observation: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    *,
    now: str,
    batch_size: int,
    target_task_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise RecoveryError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    if not ORIGIN_ID_RE.fullmatch(target_task_id):
        raise RecoveryError("target CLI task ID has an invalid shape")
    normalized = validate_observation(observation)
    ledger = empty_ledger(now) if previous is None else validate_ledger(previous)
    ledger = copy.deepcopy(ledger)
    items = normalized["items"][:batch_size]
    processed_keys: list[str] = []

    for item in items:
        key = ledger_key(item)
        digest = sha256_value(item)
        prior = ledger["entries"].get(key)
        classification = classify(item)
        if prior is None:
            first_seen = now
            attempts = 1
        else:
            first_seen = prior["first_seen"]
            attempts = int(prior.get("attempts", 0)) + (prior.get("observation_digest") != digest)
            if attempts < 1:
                attempts = 1
        entry = {
            "ledger_key": key,
            "first_seen": first_seen,
            "last_seen": now,
            "attempts": attempts,
            "observation_digest": digest,
            "evidence_digest": sha256_value(
                {
                    "remote": item["remote"],
                    "claims": item["claims"],
                    "classification": classification,
                }
            ),
            "observation": item,
            "classification": classification,
            "evidence_links": evidence_links(item),
        }
        ledger["entries"][key] = entry
        processed_keys.append(key)

    ledger["updated_at"] = now
    ledger["last_batch"] = {
        "id": normalized["batch"]["id"],
        "processed_at": now,
        "processed": len(items),
        "available": len(normalized["items"]),
        "source_complete": normalized["batch"]["complete"],
        "next_cursor": normalized["batch"]["next_cursor"],
        "processed_keys": processed_keys,
    }
    ledger["entries"] = dict(sorted(ledger["entries"].items()))
    ledger["summary"] = summarize_entries(ledger["entries"])

    queue_items = []
    for entry in ledger["entries"].values():
        cli_item = build_cli_item(entry, target_task_id)
        if cli_item is not None:
            queue_items.append(cli_item)
    queue = {
        "schema_version": CLI_QUEUE_SCHEMA,
        "generated_at": now,
        "target_task_id": target_task_id,
        "source_ledger_digest": sha256_value(ledger),
        "items": sorted(queue_items, key=lambda item: item["ledger_key"]),
    }
    queue["summary"] = {
        "items": len(queue["items"]),
        "create_repository": sum("repository_missing" in item["findings"] for item in queue["items"]),
        "recover_local": sum("repository_missing" not in item["findings"] for item in queue["items"]),
    }
    validate_public_safety(ledger)
    validate_public_safety(queue)
    return ledger, queue


def atomic_write_json(path: Path | str, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def summary_document(ledger: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_ledger(ledger)
    return {
        "schema_version": SUMMARY_SCHEMA,
        "updated_at": validated["updated_at"],
        "last_batch": validated.get("last_batch"),
        "summary": summarize_entries(validated["entries"]),
        "ledger_digest": sha256_value(validated),
    }
