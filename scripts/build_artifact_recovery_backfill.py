#!/usr/bin/env python3
"""Render the first bounded, public-safe artifact-recovery observation batch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

GENERATED_AT = "2026-08-07T18:55:00Z"
EVIDENCE_ORIGIN = "file_000000003294820e97c272f46f9db586"
ZED_ORIGIN = "file_000000003448822f94ce8546e1467c71"
CANONICAL_ORIGIN = "file_000000004f70820eb68de1c6e743e057"
SLACK_ORIGIN = "file_00000000addc820ea645e9bd921d837a"
ZED_DIGEST = "70e7bcdfa3a8a3e15bcbf8bd635a240baca53c9b95a36f01f4aa312f66fd18ae"
CANONICAL_DIGEST = "588c453399c97d2b54e129c46c6818e070400c06fbe8115937528a1ee9f6f434"
SLACK_DIGEST = "f4966c9f6577d6cd557061fc878dd8fc9ccd0ab3233ec0c84e4703c78b8002cc"

COMPLETE = (
    ("hypesiege", "hypesiege-analytics.rs", "3eb8efba49bd4f932b7cc673c66b3788e3f458c1"),
    ("hypesiege", "hypesiege-publishing-worker.rs", "0278b9cc86e7ea3b11d33dd987be6689dc06aba0"),
    ("hypesiege", "hypesiege-scheduler.rs", "e8a739d9e658e9cef8f1dc938a412b923dbff57d"),
    ("StreemPilot", "streempilot-media-router.rs", "a3b01146f85ee61400b72ed3f333c76b4413a4fa"),
)

MISSING = (
    ("apostille-me", "apme-mcp-server.rs"),
    ("apostille-me", "apme-e2e"),
    ("embedded-alerts", "eal-mcp-server.rs"),
    ("embedded-alerts", "eal-e2e"),
    ("evento-globolo", "evgl-mcp-server.rs"),
    ("evento-globolo", "evgl-e2e"),
    ("hacker-house-medellin", "hhm-mcp-server.rs"),
    ("hacker-house-medellin", "hhm-e2e"),
)

CANONICAL = (
    (
        "canonical-api-server.rs",
        "c29a9c202eed6f2d5760830a89826ef34c06e84b",
        "d3ed973eeb32df2210ce5d53cd2855b42dd220a3",
        "public",
    ),
    (
        "canonical-infra",
        "563ebdf5fde25e6e88366484cced356276786a98",
        "03d37469a6ea5ee075a89c064ee60017ae4ebf23",
        "private",
    ),
    (
        "canonical-lib",
        "69e8268c73b1bd57aa9e5f1c05af54db7bef97f3",
        "04fc0aeccf455282cd8c1eb537161be546d83d8a",
        "public",
    ),
    (
        "canonical-flutter",
        "e8651fb73515ade7dd7f851652b3f71e7bc8911d",
        "aac2fee423b318881b9cea4e5df9a3073e2a7470",
        "private",
    ),
)


def origin(file_id: str, observed_at: str) -> dict[str, Any]:
    return {
        "source": "file_library",
        "id": file_id,
        "id_kind": "file_id",
        "observed_at": observed_at,
    }


def remote_repository(owner: str, repository: str, sha: str, visibility: str) -> dict[str, Any]:
    url = f"https://github.com/{owner}/{repository}"
    return {
        "collected": True,
        "repository": {
            "exists": True,
            "visibility": visibility,
            "default_branch": "main",
            "url": url,
        },
        "branches": [{"name": "main", "sha": sha}],
        "commits": [{"sha": sha, "url": f"{url}/commit/{sha}"}],
        "pull_requests": [],
    }


def complete_item(owner: str, repository: str, sha: str) -> dict[str, Any]:
    url = f"https://github.com/{owner}/{repository}"
    return {
        "origin": origin(EVIDENCE_ORIGIN, "2026-08-05T20:50:28Z"),
        "target": {
            "owner": owner,
            "repository": repository,
            "visibility": "private",
            "artifact_kind": "code",
            "ownership_resolved": True,
        },
        "intent": {
            "artifact_expected": True,
            "base_branch": "main",
            "branch": "main",
            "pull_request_required": False,
            "allow_repository_creation": True,
        },
        "local": {
            "artifact": {
                "kind": "git_bundle",
                "name": f"{repository}.sealed-source",
                "sha256": None,
                "locator": "github:ORESoftware/k8s-cluster#1069",
                "commit_sha": sha,
                "paths": [],
            },
            "git_repository": True,
            "remote_present": True,
            "branch": "main",
            "branches": ["main"],
            "head_sha": sha,
            "dirty_paths": [],
        },
        "remote": remote_repository(owner, repository, sha, "private"),
        "claims": {
            "repository_url": url,
            "commit_sha": sha,
            "branch": "main",
            "pull_request_url": None,
        },
        "note": "Verified as an exact private default-branch landing by the merged bounded publication-evidence change.",
    }


def missing_item(owner: str, repository: str) -> dict[str, Any]:
    slug = repository.replace(".", "-")
    return {
        "origin": origin(ZED_ORIGIN, "2026-08-05T00:00:00Z"),
        "target": {
            "owner": owner,
            "repository": repository,
            "visibility": "public",
            "artifact_kind": "code",
            "ownership_resolved": True,
        },
        "intent": {
            "artifact_expected": True,
            "base_branch": "main",
            "branch": f"agent/den-2797-bootstrap-{slug}",
            "pull_request_required": True,
            "allow_repository_creation": True,
        },
        "local": {
            "artifact": {
                "kind": "file",
                "name": "zed-fleet-reconcile.sh",
                "sha256": ZED_DIGEST,
                "locator": "library:file_000000009c1c822fbca21330abaa93d2",
                "commit_sha": None,
                "paths": ["zed-fleet-reconcile.sh"],
            },
            "git_repository": False,
            "remote_present": False,
            "branch": None,
            "branches": [],
            "head_sha": None,
            "dirty_paths": [],
        },
        "remote": {
            "collected": True,
            "repository": {
                "exists": False,
                "visibility": None,
                "default_branch": None,
                "url": None,
            },
            "branches": [],
            "commits": [],
            "pull_requests": [],
        },
        "claims": {
            "repository_url": None,
            "commit_sha": None,
            "branch": None,
            "pull_request_url": None,
        },
        "note": "Exact repository lookup returned not found; the tested reconciler is the bounded local recovery artifact.",
    }


def canonical_item(repository: str, local_sha: str, remote_sha: str, visibility: str) -> dict[str, Any]:
    owner = "canonical-cloud"
    url = f"https://github.com/{owner}/{repository}"
    return {
        "origin": origin(CANONICAL_ORIGIN, "2026-08-06T02:47:24Z"),
        "target": {
            "owner": owner,
            "repository": repository,
            "visibility": visibility,
            "artifact_kind": "code",
            "ownership_resolved": True,
        },
        "intent": {
            "artifact_expected": True,
            "base_branch": "main",
            "branch": f"agent/den-2797-recover-{repository.replace('.', '-')}",
            "pull_request_required": True,
            "allow_repository_creation": True,
        },
        "local": {
            "artifact": {
                "kind": "git_bundle",
                "name": f"{repository}.bundle",
                "sha256": CANONICAL_DIGEST,
                "locator": f"library:{CANONICAL_ORIGIN}#repos/{repository}",
                "commit_sha": local_sha,
                "paths": [f"repos/{repository}"],
            },
            "git_repository": True,
            "remote_present": False,
            "branch": "main",
            "branches": ["main"],
            "head_sha": local_sha,
            "dirty_paths": [],
        },
        "remote": remote_repository(owner, repository, remote_sha, visibility),
        "claims": {
            "repository_url": url,
            "commit_sha": local_sha,
            "branch": None,
            "pull_request_url": None,
        },
        "note": "The repository exists, but the delivery-bundle commit is absent from current remote evidence and requires semantic recovery on a feature branch.",
    }


def slack_item() -> dict[str, Any]:
    owner = "ORESoftware"
    repository = "slack-ores-integrations"
    remote_sha = "4319e30048a2eb59088afaf1d5f5b299d5746fdb"
    return {
        "origin": origin(SLACK_ORIGIN, "2026-08-01T23:41:07Z"),
        "target": {
            "owner": owner,
            "repository": repository,
            "visibility": "public",
            "artifact_kind": "code",
            "ownership_resolved": True,
        },
        "intent": {
            "artifact_expected": True,
            "base_branch": "main",
            "branch": "agent/den-2797-recover-alex-main-agent",
            "pull_request_required": True,
            "allow_repository_creation": False,
        },
        "local": {
            "artifact": {
                "kind": "archive",
                "name": "alex-main-agent-v0.3.0-converged.zip",
                "sha256": SLACK_DIGEST,
                "locator": f"library:{SLACK_ORIGIN}",
                "commit_sha": None,
                "paths": [],
            },
            "git_repository": False,
            "remote_present": False,
            "branch": None,
            "branches": [],
            "head_sha": None,
            "dirty_paths": [],
        },
        "remote": remote_repository(owner, repository, remote_sha, "public"),
        "claims": {
            "repository_url": None,
            "commit_sha": None,
            "branch": None,
            "pull_request_url": None,
        },
        "note": "The validated release archive has a resolved existing owner repository but no branch or pull-request evidence for the archive contents.",
    }


def build_fixture() -> dict[str, Any]:
    items = [complete_item(*value) for value in COMPLETE]
    items.extend(missing_item(*value) for value in MISSING)
    items.extend(canonical_item(*value) for value in CANONICAL)
    items.append(slack_item())
    return {
        "schema_version": "artifact_recovery_observation.v1",
        "generated_at": GENERATED_AT,
        "batch": {
            "id": "initial-accessible-library-backfill-2026-08-07-a",
            "complete": False,
            "next_cursor": "library-created-before:2026-08-01T23:41:07Z",
            "source_window": "accessible ChatGPT file-library artifacts and verified GitHub state through 2026-08-07",
        },
        "items": items,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    payload = json.dumps(build_fixture(), indent=2, sort_keys=True) + "\n"
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
