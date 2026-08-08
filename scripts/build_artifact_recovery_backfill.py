#!/usr/bin/env python3
"""Render the first bounded, public-safe artifact-recovery observation batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

GENERATED_AT = "2026-08-08T04:16:00Z"
SEALED_ORIGIN = "file_000000003294820e97c272f46f9db586"
ZED_ORIGIN = "file_000000003448822f94ce8546e1467c71"
CANONICAL_ORIGIN = "file_000000004f70820eb68de1c6e743e057"
SLACK_ORIGIN = "file_00000000addc820ea645e9bd921d837a"
ZED_DIGEST = "70e7bcdfa3a8a3e15bcbf8bd635a240baca53c9b95a36f01f4aa312f66fd18ae"
CANONICAL_DIGEST = "588c453399c97d2b54e129c46c6818e070400c06fbe8115937528a1ee9f6f434"
SLACK_DIGEST = "f4966c9f6577d6cd557061fc878dd8fc9ccd0ab3233ec0c84e4703c78b8002cc"

SEALED = (
    ("hypesiege", "hypesiege-analytics.rs", "3eb8efba49bd4f932b7cc673c66b3788e3f458c1"),
    ("hypesiege", "hypesiege-publishing-worker.rs", "0278b9cc86e7ea3b11d33dd987be6689dc06aba0"),
    ("hypesiege", "hypesiege-scheduler.rs", "e8a739d9e658e9cef8f1dc938a412b923dbff57d"),
    ("StreemPilot", "streempilot-media-router.rs", "a3b01146f85ee61400b72ed3f333c76b4413a4fa"),
)
MISSING = (
    ("apostille-me", "apme-mcp-server.rs"), ("apostille-me", "apme-e2e"),
    ("embedded-alerts", "eal-mcp-server.rs"), ("embedded-alerts", "eal-e2e"),
    ("evento-globolo", "evgl-mcp-server.rs"), ("evento-globolo", "evgl-e2e"),
    ("hacker-house-medellin", "hhm-mcp-server.rs"), ("hacker-house-medellin", "hhm-e2e"),
)
RECOVERED = (
    {
        "owner": "canonical-cloud", "repository": "canonical-api-server.rs", "visibility": "public",
        "origin": CANONICAL_ORIGIN, "observed_at": "2026-08-06T04:25:33Z",
        "artifact": "canonical-api-server.rs.bundle", "digest": CANONICAL_DIGEST,
        "locator": f"library:{CANONICAL_ORIGIN}#repos/canonical-api-server.rs",
        "artifact_commit": "c29a9c202eed6f2d5760830a89826ef34c06e84b",
        "main": "d3ed973eeb32df2210ce5d53cd2855b42dd220a3", "pr": 10,
        "pr_head": "agent/quote-persistence-gemini", "pr_state": "merged",
    },
    {
        "owner": "canonical-cloud", "repository": "canonical-infra", "visibility": "private",
        "origin": CANONICAL_ORIGIN, "observed_at": "2026-08-06T04:26:07Z",
        "artifact": "canonical-infra.bundle", "digest": CANONICAL_DIGEST,
        "locator": f"library:{CANONICAL_ORIGIN}#repos/canonical-infra",
        "artifact_commit": "563ebdf5fde25e6e88366484cced356276786a98",
        "main": "03d37469a6ea5ee075a89c064ee60017ae4ebf23", "pr": 4,
        "pr_head": "fix/pin-reviewed-auth-edge", "pr_state": "merged",
    },
    {
        "owner": "canonical-cloud", "repository": "canonical-lib", "visibility": "public",
        "origin": CANONICAL_ORIGIN, "observed_at": "2026-08-06T02:34:04Z",
        "artifact": "canonical-lib.bundle", "digest": CANONICAL_DIGEST,
        "locator": f"library:{CANONICAL_ORIGIN}#repos/canonical-lib",
        "artifact_commit": "69e8268c73b1bd57aa9e5f1c05af54db7bef97f3",
        "main": "04fc0aeccf455282cd8c1eb537161be546d83d8a", "pr": 1,
        "pr_head": "feat/bootstrap-domain-package", "pr_state": "merged",
    },
    {
        "owner": "ORESoftware", "repository": "slack-ores-integrations", "visibility": "public",
        "origin": SLACK_ORIGIN, "observed_at": "2026-08-05T18:07:46Z",
        "artifact": "alex-main-agent-v0.3.0-converged.zip", "digest": SLACK_DIGEST,
        "locator": f"library:{SLACK_ORIGIN}", "artifact_commit": None,
        "main": "4319e30048a2eb59088afaf1d5f5b299d5746fdb", "pr": 1,
        "pr_head": "agent/den-1602-canonicalize-repository", "pr_state": "merged",
    },
    {
    "owner": "ORESoftware", "repository": "ai-agent-coordinator.rs", "visibility": "public",
    "origin": "file_00000000efc0820e870395d40af3be7f", "observed_at": "2026-08-08T04:00:00Z",
    "artifact": "DEN-602-github-admin-browser-hardening.patch",
    "digest": "8b649d9c1e0cffe9162d47d70313544d376e19842c39d191a7f1ae7dd842127f",
    "locator": "library:file_00000000efc0820e870395d40af3be7f", "kind": "file",
    "paths": ["DEN-602-github-admin-browser-hardening.patch"],
    "artifact_commit": "0fee30e469f99d2e5cefc66b1389298dc3c33e30",
    "main": "624c2bae2ac92f65a3a0e86a842fed83758d0972", "pr": 56,
    "pr_head": "agent/den-602-github-admin-browser-hardening", "pr_state": "merged",
},
)
FLUTTER = {
    "owner": "canonical-cloud", "repository": "canonical-flutter", "visibility": "private",
    "origin": CANONICAL_ORIGIN, "observed_at": "2026-08-06T05:16:47Z",
    "artifact": "canonical-flutter.bundle", "digest": CANONICAL_DIGEST,
    "locator": f"library:{CANONICAL_ORIGIN}#repos/canonical-flutter",
    "artifact_commit": "e8651fb73515ade7dd7f851652b3f71e7bc8911d",
    "main": "aac2fee423b318881b9cea4e5df9a3073e2a7470",
    "branch": "feat/authenticated-compliance-quote-stack",
    "branch_sha": "ae333dd1f507aa5f626e6980dd76bc266788be3e", "pr": 1,
}

OPEN_RECOVERIES = (
    {
        "owner": "zed-pkg", "repository": "zed-api-server.rs", "visibility": "public",
        "origin": "file_00000000810481f78c4cd3b1052881c9", "observed_at": "2026-08-08T04:09:00Z",
        "artifact": "DEN-99-dependency-resolution.patch",
        "digest": "c183d8e12225776537b03c9d3720984804f360f88a4793b3646abe4dd7ea12b2",
        "locator": "library:file_00000000810481f78c4cd3b1052881c9",
        "main": "9d019325d42508aec883cfe86f59ce1461063b9c",
        "branch": "agent/den-99-dependency-resolution-model",
        "branch_sha": "b80f728dfed8f6cb005846015300a3ee19e01678", "pr": 16,
        "note": "Recovered to a green current-main draft PR; retain product-owner review.",
    },
    {
        "owner": "fiducia-cloud", "repository": "fiducia-brain.rs", "visibility": "public",
        "origin": "file_00000000119081f7b244394be85b4569", "observed_at": "2026-08-08T04:15:00Z",
        "artifact": "DEN-569-composition-model.patch",
        "digest": "184c4e58cbf047ee81d2cd1923285036f615d8462ed491c52f2ba0366bed358d",
        "locator": "library:file_00000000119081f7b244394be85b4569",
        "main": "4ef54fc5431793623c868de2ac0f4887858885de",
        "branch": "agent/den-569-quint-composition-model",
        "branch_sha": "c90cf14db6609ab550444af64200e22c8ee19327", "pr": 26,
        "note": "Recovered to a green current-main draft PR without overlapping the merged DEN-1516 Rust model.",
    },
)


def origin(file_id: str, observed_at: str) -> dict[str, str]:
    return {"source": "file_library", "id": file_id, "id_kind": "file_id", "observed_at": observed_at}


def pr(owner: str, repository: str, number: int, head: str, state: str, draft: bool = False) -> dict[str, Any]:
    return {
        "number": number, "url": f"https://github.com/{owner}/{repository}/pull/{number}",
        "head": head, "base": "main", "state": state, "draft": draft,
    }


def remote(owner: str, repository: str, visibility: str, main: str, *, branch: str | None = None,
           branch_sha: str | None = None, pull_requests: Sequence[dict[str, Any]] = ()) -> dict[str, Any]:
    url = f"https://github.com/{owner}/{repository}"
    branches = [{"name": "main", "sha": main}]
    if branch and branch_sha:
        branches.append({"name": branch, "sha": branch_sha})
    return {
        "collected": True,
        "repository": {"exists": True, "visibility": visibility, "default_branch": "main", "url": url},
        "branches": branches,
        "commits": [{"sha": item["sha"], "url": f"{url}/commit/{item['sha']}"} for item in branches],
        "pull_requests": list(pull_requests),
    }


def sealed(owner: str, repository: str, sha: str) -> dict[str, Any]:
    url = f"https://github.com/{owner}/{repository}"
    return {
        "origin": origin(SEALED_ORIGIN, "2026-08-05T20:50:28Z"),
        "target": {"owner": owner, "repository": repository, "visibility": "private", "artifact_kind": "code", "ownership_resolved": True},
        "intent": {"artifact_expected": True, "base_branch": "main", "branch": "main", "pull_request_required": False, "allow_repository_creation": True},
        "local": {"artifact": {"kind": "git_bundle", "name": f"{repository}.sealed-source", "sha256": None,
            "locator": "github:ORESoftware/k8s-cluster#1069", "commit_sha": sha, "paths": []},
            "git_repository": True, "remote_present": True, "branch": "main", "branches": ["main"], "head_sha": sha, "dirty_paths": []},
        "remote": remote(owner, repository, "private", sha),
        "claims": {"repository_url": url, "commit_sha": sha, "branch": "main", "pull_request_url": None},
        "note": "Exact private default-branch publication is verified by merged bounded evidence.",
    }


def missing(owner: str, repository: str) -> dict[str, Any]:
    return {
        "origin": origin(ZED_ORIGIN, "2026-08-05T00:00:00Z"),
        "target": {"owner": owner, "repository": repository, "visibility": "public", "artifact_kind": "code", "ownership_resolved": True},
        "intent": {"artifact_expected": True, "base_branch": "main", "branch": f"agent/den-2797-bootstrap-{repository.replace('.', '-')}",
            "pull_request_required": True, "allow_repository_creation": True},
        "local": {"artifact": {"kind": "file", "name": "zed-fleet-reconcile.sh", "sha256": ZED_DIGEST,
            "locator": "library:file_000000009c1c822fbca21330abaa93d2", "commit_sha": None, "paths": ["zed-fleet-reconcile.sh"]},
            "git_repository": False, "remote_present": False, "branch": None, "branches": [], "head_sha": None, "dirty_paths": []},
        "remote": {"collected": True, "repository": {"exists": False, "visibility": None, "default_branch": None, "url": None},
            "branches": [], "commits": [], "pull_requests": []},
        "claims": {"repository_url": None, "commit_sha": None, "branch": None, "pull_request_url": None},
        "note": "Exact repository lookup returned not found; use the tested bounded local reconciler.",
    }


def recovered(value: dict[str, Any]) -> dict[str, Any]:
    owner, repository, main = value["owner"], value["repository"], value["main"]
    url = f"https://github.com/{owner}/{repository}"
    evidence = pr(owner, repository, value["pr"], value["pr_head"], value["pr_state"])
    kind = value.get("kind", "archive" if repository == "slack-ores-integrations" else "git_bundle")
    return {
        "origin": origin(value["origin"], value["observed_at"]),
        "target": {"owner": owner, "repository": repository, "visibility": value["visibility"], "artifact_kind": "code", "ownership_resolved": True},
        "intent": {"artifact_expected": True, "base_branch": "main", "branch": "main", "pull_request_required": False, "allow_repository_creation": True},
        "local": {"artifact": {"kind": kind, "name": value["artifact"], "sha256": value["digest"], "locator": value["locator"],
            "commit_sha": value["artifact_commit"], "paths": value.get("paths", [])}, "git_repository": True, "remote_present": True,
            "branch": "main", "branches": ["main"], "head_sha": main, "dirty_paths": []},
        "remote": remote(owner, repository, value["visibility"], main, pull_requests=(evidence,)),
        "claims": {"repository_url": url, "commit_sha": main, "branch": "main", "pull_request_url": evidence["url"]},
        "note": "A current semantic successor is already merged; do not open a duplicate recovery branch.",
    }


def flutter() -> dict[str, Any]:
    value = FLUTTER
    owner, repository, branch = value["owner"], value["repository"], value["branch"]
    url = f"https://github.com/{owner}/{repository}"
    evidence = pr(owner, repository, value["pr"], branch, "open", True)
    return {
        "origin": origin(value["origin"], value["observed_at"]),
        "target": {"owner": owner, "repository": repository, "visibility": value["visibility"], "artifact_kind": "code", "ownership_resolved": True},
        "intent": {"artifact_expected": True, "base_branch": "main", "branch": branch, "pull_request_required": True, "allow_repository_creation": True},
        "local": {"artifact": {"kind": "git_bundle", "name": value["artifact"], "sha256": value["digest"], "locator": value["locator"],
            "commit_sha": value["artifact_commit"], "paths": ["repos/canonical-flutter"]}, "git_repository": True, "remote_present": True,
            "branch": branch, "branches": [branch], "head_sha": value["branch_sha"], "dirty_paths": []},
        "remote": remote(owner, repository, value["visibility"], value["main"], branch=branch, branch_sha=value["branch_sha"], pull_requests=(evidence,)),
        "claims": {"repository_url": url, "commit_sha": value["branch_sha"], "branch": branch, "pull_request_url": evidence["url"]},
        "note": "The artifact is already on a later canonical-interface-aligned draft PR head; review remains open but delivery is recovered.",
    }



def open_recovery(value: dict[str, Any]) -> dict[str, Any]:
    owner, repository, branch = value["owner"], value["repository"], value["branch"]
    url = f"https://github.com/{owner}/{repository}"
    evidence = pr(owner, repository, value["pr"], branch, "open", True)
    return {
        "origin": origin(value["origin"], value["observed_at"]),
        "target": {"owner": owner, "repository": repository, "visibility": value["visibility"], "artifact_kind": "code", "ownership_resolved": True},
        "intent": {"artifact_expected": True, "base_branch": "main", "branch": branch, "pull_request_required": True, "allow_repository_creation": False},
        "local": {"artifact": {"kind": "file", "name": value["artifact"], "sha256": value["digest"], "locator": value["locator"],
            "commit_sha": value["branch_sha"], "paths": [value["artifact"]]}, "git_repository": True, "remote_present": True,
            "branch": branch, "branches": [branch], "head_sha": value["branch_sha"], "dirty_paths": []},
        "remote": remote(owner, repository, value["visibility"], value["main"], branch=branch, branch_sha=value["branch_sha"], pull_requests=(evidence,)),
        "claims": {"repository_url": url, "commit_sha": value["branch_sha"], "branch": branch, "pull_request_url": evidence["url"]},
        "note": value["note"],
    }


def build_fixture() -> dict[str, Any]:
    items = [sealed(*value) for value in SEALED]
    items.extend(missing(*value) for value in MISSING)
    items.extend(recovered(value) for value in RECOVERED)
    items.append(flutter())
    items.extend(open_recovery(value) for value in OPEN_RECOVERIES)
    return {
        "schema_version": "artifact_recovery_observation.v1", "generated_at": GENERATED_AT,
        "batch": {"id": "accessible-library-backfill-2026-08-08-wave-2", "complete": False,
            "next_cursor": "library-created-before:2026-08-01T23:41:07Z",
            "source_window": "accessible ChatGPT file-library artifacts and refreshed GitHub evidence through 2026-08-08"},
        "items": items,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    payload = json.dumps(build_fixture(), indent=2, sort_keys=True) + "\n"
    if args.output:
        path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
