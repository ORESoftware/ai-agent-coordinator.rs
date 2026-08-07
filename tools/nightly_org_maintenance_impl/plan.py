"""Implementation module for bounded nightly organization maintenance."""

from .common import *

def _repository_index(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    github = _require_mapping(snapshot.get("github"), "snapshot.github")
    repositories = _require_list(github.get("repositories"), "snapshot.github.repositories")
    result: dict[str, dict[str, Any]] = {}
    for repo in repositories:
        item = _require_mapping(repo, "snapshot repository")
        full_name = _clean_text(item.get("full_name"), limit=250)
        if not REPOSITORY_RE.fullmatch(full_name):
            raise MaintenanceError(f"snapshot contains invalid repository {full_name!r}")
        result[full_name.casefold()] = item
    return result


def _linear_issue_index(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    linear = _require_mapping(snapshot.get("linear"), "snapshot.linear")
    issues = _require_list(linear.get("issues"), "snapshot.linear.issues")
    result: dict[str, dict[str, Any]] = {}
    for issue in issues:
        item = _require_mapping(issue, "snapshot Linear issue")
        identifier = _clean_text(item.get("identifier"), limit=50)
        if identifier:
            result[identifier.casefold()] = item
    return result


def _open_pr_index(snapshot: Mapping[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for repository in _repository_index(snapshot).values():
        full_name = repository["full_name"]
        pulls = _require_list(repository.get("open_pull_requests"), "repository.open_pull_requests")
        for pull in pulls:
            item = _require_mapping(pull, "open pull request")
            number = item.get("number")
            if isinstance(number, int) and number > 0:
                result[(full_name.casefold(), number)] = item
    return result


def validate_plan(plan: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if plan.get("schema_version") != "nightly_org_plan.v1":
        raise MaintenanceError("plan.schema_version must be nightly_org_plan.v1")
    owner = _clean_text(plan.get("owner"), limit=100)
    if owner.casefold() != _clean_text(snapshot.get("owner"), limit=100).casefold():
        raise MaintenanceError("plan owner does not match snapshot owner")
    repository_index = _repository_index(snapshot)
    issue_index = _linear_issue_index(snapshot)
    pull_index = _open_pr_index(snapshot)
    policy = _require_mapping(snapshot.get("policy"), "snapshot.policy")
    pr_policy = _require_mapping(policy.get("new_pull_requests"), "snapshot.policy.new_pull_requests")
    minimum = int(pr_policy.get("minimum", 1))
    maximum = int(pr_policy.get("maximum", 3))
    tasks = _require_list(plan.get("new_pr_tasks"), "plan.new_pr_tasks")
    if not minimum <= len(tasks) <= maximum:
        raise MaintenanceError(f"plan must contain between {minimum} and {maximum} new PR tasks")
    normalized_tasks: list[dict[str, Any]] = []
    seen_repositories: set[str] = set()
    source_repairs: set[tuple[str, int]] = set()
    for index, raw in enumerate(tasks):
        task = _require_mapping(raw, f"plan.new_pr_tasks[{index}]")
        repository = _clean_text(task.get("repository"), limit=250)
        repo_key = repository.casefold()
        if repo_key not in repository_index:
            raise MaintenanceError(f"task repository is not in the snapshot: {repository}")
        if repo_key in seen_repositories:
            raise MaintenanceError("the plan may create at most one new PR per repository")
        seen_repositories.add(repo_key)
        title = _clean_text(task.get("title"), limit=120)
        goal = _clean_text(task.get("goal"), limit=4000)
        acceptance_raw = _require_list(task.get("acceptance"), f"task {index}.acceptance")
        acceptance = [_clean_text(item, limit=500) for item in acceptance_raw]
        if not 8 <= len(title) <= 120:
            raise MaintenanceError(f"task {index} title must contain 8-120 characters")
        if not 20 <= len(goal) <= 4000:
            raise MaintenanceError(f"task {index} goal must contain 20-4000 characters")
        if not 1 <= len(acceptance) <= 12 or any(not item for item in acceptance):
            raise MaintenanceError(f"task {index} must contain 1-12 acceptance criteria")
        linear_issue = _clean_text(task.get("linear_issue"), limit=50)
        if linear_issue:
            if not LINEAR_ISSUE_RE.fullmatch(linear_issue):
                raise MaintenanceError(f"task {index} has an invalid Linear issue identifier")
            if linear_issue.casefold() not in issue_index:
                raise MaintenanceError(f"task {index} references an issue outside the project snapshot")
        risk = _clean_text(task.get("risk"), limit=20).casefold()
        if risk not in ALLOWED_RISK_LEVELS:
            raise MaintenanceError(f"task {index} risk must be low or medium")
        protected_area = bool(task.get("protected_area"))
        source_pr = task.get("source_pr")
        normalized_source: dict[str, Any] | None = None
        if source_pr is not None:
            source = _require_mapping(source_pr, f"task {index}.source_pr")
            source_repository = _clean_text(source.get("repository"), limit=250)
            source_number = source.get("number")
            source_sha = _clean_text(source.get("head_sha"), limit=40)
            key = (source_repository.casefold(), source_number)
            if key not in pull_index or not SHA_RE.fullmatch(source_sha):
                raise MaintenanceError(f"task {index} source PR is absent or invalid")
            if pull_index[key].get("head_sha") != source_sha:
                raise MaintenanceError(f"task {index} source PR head SHA changed")
            normalized_source = {
                "repository": source_repository,
                "number": source_number,
                "head_sha": source_sha,
            }
            source_repairs.add(key)
        normalized_tasks.append(
            {
                "repository": repository,
                "title": title,
                "goal": goal,
                "acceptance": acceptance,
                "linear_issue": linear_issue,
                "risk": risk,
                "protected_area": protected_area,
                "source_pr": normalized_source,
            }
        )

    merges = _require_list(plan.get("merge_candidates", []), "plan.merge_candidates")
    max_merges = int(policy.get("maximum_existing_pr_merges", 3))
    if len(merges) > max_merges:
        raise MaintenanceError(f"plan exceeds the {max_merges}-merge candidate limit")
    normalized_merges: list[dict[str, Any]] = []
    seen_pulls: set[tuple[str, int]] = set()
    for index, raw in enumerate(merges):
        candidate = _require_mapping(raw, f"plan.merge_candidates[{index}]")
        repository = _clean_text(candidate.get("repository"), limit=250)
        number = candidate.get("number")
        head_sha = _clean_text(candidate.get("head_sha"), limit=40)
        action = _clean_text(candidate.get("action"), limit=50)
        rationale = _clean_text(candidate.get("rationale"), limit=1200)
        key = (repository.casefold(), number)
        if key in seen_pulls:
            raise MaintenanceError("plan contains a duplicate merge candidate")
        seen_pulls.add(key)
        if key not in pull_index:
            raise MaintenanceError(f"merge candidate {repository}#{number} is not open in the snapshot")
        if not SHA_RE.fullmatch(head_sha) or pull_index[key].get("head_sha") != head_sha:
            raise MaintenanceError(f"merge candidate {repository}#{number} head SHA changed")
        if action not in ALLOWED_PLAN_ACTIONS:
            raise MaintenanceError(f"merge candidate action {action!r} is invalid")
        if not rationale:
            raise MaintenanceError("every merge candidate requires a rationale")
        if action == "repair_with_replacement_pr" and key not in source_repairs:
            raise MaintenanceError(
                f"repair candidate {repository}#{number} requires a matching new_pr_task.source_pr"
            )
        normalized_merges.append(
            {
                "repository": repository,
                "number": number,
                "head_sha": head_sha,
                "action": action,
                "rationale": rationale,
            }
        )

    return {
        "schema_version": "nightly_org_plan.v1",
        "owner": owner,
        "summary": _clean_text(plan.get("summary"), limit=2000),
        "new_pr_tasks": normalized_tasks,
        "merge_candidates": normalized_merges,
    }


def command_validate_plan(args: argparse.Namespace) -> dict[str, Any]:
    plan = _require_mapping(load_json(args.plan, label="Codex plan"), "plan")
    snapshot = _require_mapping(load_json(args.snapshot, label="organization snapshot"), "snapshot")
    normalized = validate_plan(plan, snapshot)
    write_json(args.output, normalized)
    return {
        "status": "plan_valid",
        "owner": normalized["owner"],
        "new_pr_tasks": len(normalized["new_pr_tasks"]),
        "merge_candidates": len(normalized["merge_candidates"]),
        "output": str(args.output),
    }

__all__ = [name for name in globals() if not name.startswith("__")]
