from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .model import (
    GitHub,
    ORG,
    PUBLIC_VISIBILITY_EXCEPTIONS,
    Project,
    PublicationError,
    RepoRecord,
    TRACKING_ISSUES,
    encoded,
    load_records,
    run,
)


def load_validator(repository: Path):
    path = repository / "repository-fleets" / "validate_memebank_source_v2.py"
    spec = importlib.util.spec_from_file_location("validate_memebank_source_v2_live", path)
    if spec is None or spec.loader is None:
        raise PublicationError("cannot load source-v2 validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def reconstruct(repository: Path, work: Path) -> tuple[Mapping[str, Any], list[RepoRecord], Path]:
    validator = load_validator(repository)
    manifest = validator.load_manifest(repository)
    records = load_records(manifest)
    archive = validator.decode_payload(repository, manifest)
    source = validator.extract_archive(archive, work / "extract", manifest)
    histories = work / "repositories"
    validator.validate_histories(source, histories, manifest)
    for record in records:
        actual = run(["git", "rev-parse", "HEAD"], histories / record.name).stdout.strip()
        if actual != record.expected_head:
            raise PublicationError(f"{record.name}: reconstructed head changed")
    return manifest, records, histories


def git_auth_environment(token_file: Path, work: Path) -> dict[str, str]:
    askpass = work / "git-askpass.sh"
    askpass.write_text(
        "#!/bin/sh\ncase \"$1\" in\n"
        "  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
        f"  *) cat '{token_file}' ;;\nesac\n",
        encoding="utf-8",
    )
    askpass.chmod(0o700)
    env = os.environ.copy()
    env.update(
        {
            "GIT_ASKPASS": str(askpass),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return env


def repo_path(record: RepoRecord) -> str:
    return f"/repos/{encoded(record.full_name)}"


def ensure_repository(gh: GitHub, record: RepoRecord) -> tuple[dict[str, Any], bool]:
    status, data = gh.get(repo_path(record), allow=(404,))
    created = False
    if status == 404:
        if record.name == ".github":
            raise PublicationError("memebank/.github disappeared; refusing to recreate governance")
        _, data = gh.post(
            f"/orgs/{ORG}/repos",
            {
                "name": record.name,
                "description": record.description,
                "private": True,
                "visibility": "private",
                "has_issues": True,
                "has_projects": True,
                "has_wiki": False,
                "auto_init": False,
                "allow_squash_merge": True,
                "allow_merge_commit": True,
                "allow_rebase_merge": False,
                "delete_branch_on_merge": True,
            },
        )
        created = True
    assert isinstance(data, dict)
    if record.name in PUBLIC_VISIBILITY_EXCEPTIONS:
        if data.get("visibility") != "public":
            raise PublicationError("memebank/.github must remain public for the organization profile")
    elif not data.get("private") or data.get("visibility") != "private":
        raise PublicationError(f"{record.full_name}: canonical repository must be private")
    gh.patch(
        repo_path(record),
        {
            "description": record.description,
            "has_issues": True,
            "has_projects": True,
            "has_wiki": False,
            "allow_squash_merge": True,
            "allow_merge_commit": True,
            "allow_rebase_merge": False,
            "delete_branch_on_merge": True,
        },
    )
    return data, created


def main_ref(gh: GitHub, record: RepoRecord) -> str | None:
    status, ref = gh.get(f"{repo_path(record)}/git/ref/heads/main", allow=(404, 409))
    return None if status != 200 else str(ref["object"]["sha"])


def commit_reachable(gh: GitHub, record: RepoRecord, sha: str) -> bool:
    status, _ = gh.get(f"{repo_path(record)}/git/commits/{sha}", allow=(404, 409, 422))
    return status == 200


def push_initial(
    gh: GitHub,
    record: RepoRecord,
    local: Path,
    git_env: Mapping[str, str],
) -> tuple[str, bool]:
    current = main_ref(gh, record)
    reachable = commit_reachable(gh, record, record.expected_head)
    if current is None:
        run(["git", "remote", "remove", "origin"], local, git_env, check=False)
        run(
            ["git", "remote", "add", "origin", f"https://github.com/{record.full_name}.git"],
            local,
            git_env,
        )
        run(["git", "push", "origin", "main:refs/heads/main"], local, git_env)
        current = main_ref(gh, record)
        gh.patch(repo_path(record), {"default_branch": "main"})
        if current != record.expected_head:
            raise PublicationError(
                f"{record.full_name}: initial main {current} != approved {record.expected_head}"
            )
        return current, True
    if not reachable:
        raise PublicationError(
            f"{record.full_name}: nonempty repository does not contain approved source-v2 head; refusing overwrite"
        )
    return current, False


def provenance_json(
    manifest: Mapping[str, Any],
    record: RepoRecord,
    repository_id: int,
    project: Project,
    run_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "organization": ORG,
        "repository": record.name,
        "repository_id": repository_id,
        "canonical_visibility": "public-profile-exception"
        if record.name in PUBLIC_VISIBILITY_EXCEPTIONS
        else "private",
        "source_manifest": "ORESoftware/ai-agent-coordinator.rs:repository-fleets/memebank-source-v2.json",
        "source_archive_sha256": manifest["archive"]["sha256"],
        "approved_source_tree": record.expected_tree,
        "approved_source_head": record.expected_head,
        "tracked_entries": record.tracked_entries,
        "role": record.role,
        "tracking_issues": list(TRACKING_ISSUES),
        "github_project": {"title": project.title, "number": project.number, "url": project.url},
        "publication_run_id": run_id,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }


def provenance_markdown(
    manifest: Mapping[str, Any],
    record: RepoRecord,
    project: Project,
    repository_url: str,
) -> str:
    issues = ", ".join(TRACKING_ISSUES)
    visibility = "public organization-profile exception" if record.name == ".github" else "private"
    return f"""# MemeBank canonical fleet publication

- Repository: `{record.full_name}`
- Repository URL: {repository_url}
- Role: `{record.role}`
- Visibility: `{visibility}`
- Approved source tree: `{record.expected_tree}`
- Approved source head: `{record.expected_head}`
- Source archive SHA-256: `{manifest['archive']['sha256']}`
- GitHub Project: [{project.title}]({project.url})
- Linear tracking: `{issues}`

## Authority and migration boundary

The sealed source-v2 carrier is maintained in `ORESoftware/ai-agent-coordinator.rs`.
The approved source commit was published without force-pushing or rewriting legacy
MemeBank repositories. Legacy `mbk-*`, `Memebank`, `playground`, and migration
repositories remain separate until a focused migration PR explicitly supersedes them.

`memebank/.github` is an additive semantic merge exception: its newer public
organization governance is preserved rather than replaced by the sealed source
snapshot. All other canonical repositories are private by default.

## Change policy

Resolve conflicts semantically using the merge base, relevant history, contracts,
tests, security controls, observability, and rollback intent. Never resolve a
substantive conflict by selecting an entire side wholesale.
"""


def clone_live(record: RepoRecord, destination: Path, git_env: Mapping[str, str]) -> None:
    run(
        [
            "git",
            "clone",
            "--quiet",
            "--single-branch",
            "--branch",
            "main",
            f"https://github.com/{record.full_name}.git",
            str(destination),
        ],
        env=git_env,
    )


def write_provenance(
    local: Path,
    manifest: Mapping[str, Any],
    record: RepoRecord,
    metadata: Mapping[str, Any],
    project: Project,
    run_id: str,
) -> bool:
    existing_json = local / "fleet-publication.json"
    existing_markdown = local / "docs" / "FLEET_PUBLICATION.md"
    if existing_json.is_file() and existing_markdown.is_file():
        try:
            existing = json.loads(existing_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if (
            existing.get("source_archive_sha256") == manifest["archive"]["sha256"]
            and existing.get("approved_source_head") == record.expected_head
            and existing.get("github_project", {}).get("number") == project.number
        ):
            return False
    docs = local / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "FLEET_PUBLICATION.md").write_text(
        provenance_markdown(manifest, record, project, str(metadata["html_url"])),
        encoding="utf-8",
    )
    existing_json.write_text(
        json.dumps(
            provenance_json(manifest, record, int(metadata["id"]), project, run_id),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return bool(run(["git", "status", "--porcelain"], local).stdout.strip())
