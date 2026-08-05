from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .model import GitHub, ORG, Project, PublicationError, RepoRecord, run, scrub
from .source import (
    clone_live,
    commit_reachable,
    ensure_repository,
    git_auth_environment,
    main_ref,
    push_initial,
    reconstruct,
    write_provenance,
)
from .tracking import (
    add_project_item,
    ensure_project,
    ensure_tracking_issue,
    issue_body,
    merge_pull_request,
    open_pull_request,
)


def publish_provenance_pr(
    gh: GitHub,
    manifest: Mapping[str, Any],
    record: RepoRecord,
    metadata: Mapping[str, Any],
    source_local: Path,
    git_env: Mapping[str, str],
    project: Project,
    run_id: str,
    work: Path,
    wait_seconds: int,
) -> dict[str, Any]:
    local = work / f"pr-{record.name}"
    if record.name == ".github":
        clone_live(record, local, git_env)
    else:
        shutil.copytree(source_local, local)
        run(["git", "remote", "remove", "origin"], local, git_env, check=False)
        run(
            ["git", "remote", "add", "origin", f"https://github.com/{record.full_name}.git"],
            local,
            git_env,
        )
        run(["git", "checkout", "main"], local, git_env)
    run(["git", "config", "user.name", "github-actions[bot]"], local)
    run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        local,
    )
    branch = re.sub(
        r"[^A-Za-z0-9._/-]+",
        "-",
        f"agent/den-1005-fleet-publication-{run_id}-{record.name.replace('.', '-')}",
    )[:200]
    run(["git", "checkout", "-b", branch], local, git_env)
    if not write_provenance(local, manifest, record, metadata, project, run_id):
        return {
            "pull_request": None,
            "pull_request_number": None,
            "state": "provenance_already_current",
            "merged": True,
            "merge_sha": main_ref(gh, record),
        }
    run(["git", "add", "docs/FLEET_PUBLICATION.md", "fleet-publication.json"], local)
    run(
        ["git", "commit", "-m", f"docs(DEN-1005): record {record.name} fleet publication"],
        local,
        git_env,
    )
    head_sha = run(["git", "rev-parse", "HEAD"], local).stdout.strip()
    run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], local, git_env)
    pr = open_pull_request(gh, record, branch, head_sha)
    merged, merge_sha, state = merge_pull_request(
        gh, record, pr, head_sha, wait_seconds
    )
    return {
        "branch": branch,
        "commit_sha": head_sha,
        "pull_request": pr["html_url"],
        "pull_request_number": pr["number"],
        "state": state,
        "merged": merged,
        "merge_sha": merge_sha,
    }


def validate_monorepo_targets(
    gh: GitHub,
    manifest: Mapping[str, Any],
    records: Sequence[RepoRecord],
) -> None:
    links = manifest["monorepo"]["gitlinks"]
    by_name = {record.name: record for record in records}
    for path, sha in sorted(links.items()):
        record = by_name.get(Path(path).name)
        if record is None or sha != record.expected_head:
            raise PublicationError(f"monorepo gitlink changed: {path}")
        if not commit_reachable(gh, record, sha):
            raise PublicationError(
                f"monorepo gitlink target is not reachable: {record.full_name}@{sha}"
            )


def result_summary(
    results: Sequence[Mapping[str, Any]],
    failures: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    return {
        "repositories_total": len(results) + len(failures),
        "repositories_created": sum(bool(item.get("repository_created")) for item in results),
        "initial_source_pushes": sum(bool(item.get("initial_source_pushed")) for item in results),
        "provenance_pull_requests": sum(bool(item.get("pull_request")) for item in results),
        "provenance_pull_requests_merged": sum(bool(item.get("merged")) for item in results),
        "failures": len(failures),
    }


def record_failure(
    failures: list[dict[str, str]],
    record: RepoRecord,
    error: Exception | str,
) -> None:
    failures.append({"repository": record.full_name, "error": scrub(str(error))[:500]})


def run_publication(
    repository: Path,
    token_file: Path,
    output: Path,
    run_id: str,
    check_wait_seconds: int,
) -> int:
    output.mkdir(parents=True, exist_ok=True)
    token = token_file.read_text(encoding="utf-8").strip()
    gh = GitHub(token)
    failures: list[dict[str, str]] = []
    results: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="memebank-live-publication-") as temporary:
            work = Path(temporary)
            manifest, records, histories = reconstruct(repository, work)
            git_env = git_auth_environment(token_file, work)
            project = ensure_project(token)
            tracking_issue = ensure_tracking_issue(
                gh,
                "Canonical MemeBank fleet publication is running. Final evidence will replace this body.\n",
            )
            add_project_item(token, project, tracking_issue["html_url"])

            ordered = [
                item
                for item in records
                if item.name not in {".github", "memebank-monorepo"}
            ]
            ordered.append(
                next(item for item in records if item.name == "memebank-monorepo")
            )
            for record in ordered:
                print(f"publishing {record.full_name}", flush=True)
                try:
                    metadata, created = ensure_repository(gh, record)
                    if record.name == "memebank-monorepo":
                        validate_monorepo_targets(gh, manifest, records)
                    _, pushed = push_initial(
                        gh,
                        record,
                        histories / record.name,
                        git_env,
                    )
                    pr_result = publish_provenance_pr(
                        gh,
                        manifest,
                        record,
                        metadata,
                        histories / record.name,
                        git_env,
                        project,
                        run_id,
                        work,
                        check_wait_seconds,
                    )
                    result = {
                        "repository": record.full_name,
                        "repository_id": metadata["id"],
                        "repository_url": metadata["html_url"],
                        "visibility": metadata["visibility"],
                        "repository_created": created,
                        "initial_source_head": record.expected_head,
                        "initial_source_tree": record.expected_tree,
                        "initial_source_pushed": pushed,
                        **pr_result,
                    }
                    results.append(result)
                    if result.get("pull_request") and not result.get("merged"):
                        record_failure(
                            failures,
                            record,
                            f"provenance PR not merged: {result.get('state')}",
                        )
                    if result.get("pull_request"):
                        add_project_item(token, project, result["pull_request"])
                except Exception as error:
                    record_failure(failures, record, error)
                    print(
                        f"failed {record.full_name}: {scrub(str(error))}",
                        file=sys.stderr,
                    )

            governance = next(item for item in records if item.name == ".github")
            try:
                metadata, created = ensure_repository(gh, governance)
                pr_result = publish_provenance_pr(
                    gh,
                    manifest,
                    governance,
                    metadata,
                    histories / governance.name,
                    git_env,
                    project,
                    run_id,
                    work,
                    check_wait_seconds,
                )
                result = {
                    "repository": governance.full_name,
                    "repository_id": metadata["id"],
                    "repository_url": metadata["html_url"],
                    "visibility": metadata["visibility"],
                    "repository_created": created,
                    "initial_source_head": None,
                    "initial_source_tree": governance.expected_tree,
                    "initial_source_pushed": False,
                    "semantic_merge": "preserve_live_public_governance_add_publication_docs",
                    **pr_result,
                }
                results.insert(0, result)
                if result.get("pull_request") and not result.get("merged"):
                    record_failure(
                        failures,
                        governance,
                        f"provenance PR not merged: {result.get('state')}",
                    )
                if result.get("pull_request"):
                    add_project_item(token, project, result["pull_request"])
            except Exception as error:
                record_failure(failures, governance, error)

            _, tracking_issue = gh.patch(
                f"/repos/{ORG}/.github/issues/{tracking_issue['number']}",
                {"body": issue_body(project, results)},
            )
            ledger = {
                "schema_version": 1,
                "organization": ORG,
                "run_id": run_id,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "source_archive_sha256": manifest["archive"]["sha256"],
                "project": {
                    "title": project.title,
                    "number": project.number,
                    "url": project.url,
                },
                "tracking_issue": tracking_issue["html_url"],
                "summary": result_summary(results, failures),
                "repositories": results,
                "failures": failures,
            }
            (output / "memebank-publication-ledger.json").write_text(
                json.dumps(ledger, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (output / "memebank-publication-ledger.md").write_text(
                issue_body(project, results)
                + "\n## Summary\n\n```json\n"
                + json.dumps(ledger["summary"], indent=2, sort_keys=True)
                + "\n```\n",
                encoding="utf-8",
            )
            print(json.dumps(ledger["summary"], sort_keys=True), flush=True)
    finally:
        gh.close()
        token = ""
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--check-wait-seconds", type=int, default=600)
    args = parser.parse_args()
    return run_publication(
        args.repository.resolve(),
        args.token_file.resolve(),
        args.output_directory.resolve(),
        args.run_id,
        args.check_wait_seconds,
    )
