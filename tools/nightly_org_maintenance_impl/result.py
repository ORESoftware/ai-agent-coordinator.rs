"""Implementation module for bounded nightly organization maintenance."""

from .common import *
from .plan import *
from .workspace import *

def _git_changed_files(repository_path: Path) -> list[str]:
    result = run_command(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=repository_path
    ).stdout
    entries = result.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        if len(entry) < 4:
            raise MaintenanceError("git status returned an invalid entry")
        status_code = entry[:2]
        path = entry[3:]
        if "R" in status_code or "C" in status_code:
            if index >= len(entries) or not entries[index]:
                raise MaintenanceError("git status rename entry was incomplete")
            path = entries[index]
            index += 1
        if path.startswith("/") or ".." in Path(path).parts:
            raise MaintenanceError("git status returned an unsafe path")
        paths.append(path)
    return sorted(set(paths))


def _scan_changed_content(repository_path: Path, changed_files: Sequence[str]) -> None:
    for relative in changed_files:
        path = repository_path / relative
        if path.is_symlink():
            target = path.resolve()
            if repository_path.resolve() not in target.parents:
                raise MaintenanceError(f"changed symlink escapes repository: {relative}")
        if not path.exists() or not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise MaintenanceError(f"could not inspect changed file {relative}") from exc
        if len(raw) > 4 * 1024 * 1024:
            continue
        text = raw.decode("utf-8", errors="ignore")
        if CONFLICT_MARKER_RE.search(text):
            raise MaintenanceError(f"conflict markers remain in {relative}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise MaintenanceError(f"credential-shaped content found in {relative}")


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def validate_result(
    result: Mapping[str, Any],
    plan: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    workspace: Path,
) -> dict[str, Any]:
    if result.get("schema_version") != "nightly_org_result.v1":
        raise MaintenanceError("result.schema_version must be nightly_org_result.v1")
    owner = _clean_text(result.get("owner"), limit=100)
    if owner.casefold() != plan["owner"].casefold():
        raise MaintenanceError("result owner does not match plan owner")
    tasks_by_repository = {
        task["repository"].casefold(): task for task in plan["new_pr_tasks"]
    }
    changes = _require_list(result.get("changes"), "result.changes")
    if len(changes) != len(plan["new_pr_tasks"]):
        raise MaintenanceError("result must account for every planned new PR task exactly once")
    policy = _require_mapping(snapshot.get("policy"), "snapshot.policy")
    protected_patterns = [str(item) for item in policy.get("protected_paths", [])]
    max_changed_files = int(policy.get("max_changed_files_per_pr", 40))
    max_changed_bytes = int(policy.get("max_changed_bytes_per_pr", 512_000))
    normalized_changes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(changes):
        change = _require_mapping(raw, f"result.changes[{index}]")
        repository = _clean_text(change.get("repository"), limit=250)
        key = repository.casefold()
        if key in seen or key not in tasks_by_repository:
            raise MaintenanceError(f"result change repository is duplicate or unplanned: {repository}")
        seen.add(key)
        branch = _clean_text(change.get("branch"), limit=200)
        if not BRANCH_RE.fullmatch(branch):
            raise MaintenanceError(f"result branch is invalid: {branch}")
        title = _clean_text(change.get("title"), limit=120)
        body = _clean_text(change.get("body"), limit=12000)
        commit_message = _clean_text(change.get("commit_message"), limit=200)
        if not 8 <= len(title) <= 120 or len(body) < 40 or len(commit_message) < 8:
            raise MaintenanceError(f"result metadata is incomplete for {repository}")
        tests = _require_list(change.get("tests", []), f"result change {repository}.tests")
        normalized_tests: list[dict[str, str]] = []
        for test in tests[:20]:
            test_item = _require_mapping(test, "test result")
            command = _clean_text(test_item.get("command"), limit=500)
            outcome = _clean_text(test_item.get("outcome"), limit=50).casefold()
            evidence = _clean_text(test_item.get("evidence"), limit=1000)
            if outcome not in {"passed", "failed", "not_run", "blocked"}:
                raise MaintenanceError(f"invalid test outcome for {repository}")
            if outcome == "failed":
                raise MaintenanceError(
                    f"known failing validation blocks publication for {repository}: {command or 'unspecified test'}"
                )
            normalized_tests.append(
                {"command": command, "outcome": outcome, "evidence": evidence}
            )
        repository_path = _safe_workspace_path(workspace, repository)
        changed_files = _git_changed_files(repository_path)
        if not changed_files:
            raise MaintenanceError(f"Codex produced no changes for planned task {repository}")
        if len(changed_files) > max_changed_files:
            raise MaintenanceError(f"{repository} changed {len(changed_files)} files; limit is {max_changed_files}")
        tracked_patch = run_command(
            ["git", "diff", "--binary", "--no-ext-diff", "HEAD", "--"],
            cwd=repository_path,
        ).stdout.encode("utf-8")
        untracked_raw = run_command(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=repository_path,
        ).stdout
        untracked_bytes = 0
        for relative in (item for item in untracked_raw.split("\0") if item):
            path = repository_path / relative
            if path.exists() and path.is_file() and not path.is_symlink():
                untracked_bytes += path.stat().st_size
        changed_bytes = len(tracked_patch) + untracked_bytes
        if changed_bytes > max_changed_bytes:
            raise MaintenanceError(
                f"{repository} changed {changed_bytes} bytes; limit is {max_changed_bytes}"
            )
        _scan_changed_content(repository_path, changed_files)
        protected = any(_matches_any(path, protected_patterns) for path in changed_files)
        task = tasks_by_repository[key]
        if protected and not (bool(change.get("requires_human_review")) and task["protected_area"]):
            raise MaintenanceError(
                f"{repository} changed a protected path without an explicit human-review gate"
            )
        normalized_changes.append(
            {
                "repository": repository,
                "branch": branch,
                "title": title,
                "body": body,
                "commit_message": commit_message,
                "linear_issue": task["linear_issue"],
                "source_pr": task["source_pr"],
                "risk": task["risk"],
                "requires_human_review": bool(change.get("requires_human_review")) or protected,
                "changed_files": changed_files,
                "changed_bytes": changed_bytes,
                "tests": normalized_tests,
            }
        )
    return {
        "schema_version": "nightly_org_result.v1",
        "owner": owner,
        "summary": _clean_text(result.get("summary"), limit=3000),
        "changes": normalized_changes,
    }


def command_validate_result(args: argparse.Namespace) -> dict[str, Any]:
    result = _require_mapping(load_json(args.result, label="Codex implementation result"), "result")
    plan = _require_mapping(load_json(args.plan, label="validated plan"), "plan")
    snapshot = _require_mapping(load_json(args.snapshot, label="snapshot"), "snapshot")
    normalized_plan = validate_plan(plan, snapshot)
    normalized = validate_result(result, normalized_plan, snapshot, args.workspace)
    write_json(args.output, normalized)
    return {
        "status": "result_valid",
        "owner": normalized["owner"],
        "change_count": len(normalized["changes"]),
        "output": str(args.output),
    }

__all__ = [name for name in globals() if not name.startswith("__")]
