"""Implementation module for bounded nightly organization maintenance."""

from .common import *
from .plan import *
from .workspace import *
from .result import *

def _gh_json(
    args: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: float = 120.0,
) -> Any:
    raw = run_command(["gh", *args], env=env, timeout=timeout).stdout
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MaintenanceError(f"gh returned invalid JSON for {' '.join(args[:3])}") from exc


def _ensure_label(repository: str, label: str, env: Mapping[str, str]) -> None:
    owner, name = repository.split("/", 1)
    encoded = quote(label, safe="")
    existing = run_command(
        ["gh", "api", f"repos/{owner}/{name}/labels/{encoded}"],
        env=env,
        check=False,
    )
    if existing.stdout:
        return
    result = run_command(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{owner}/{name}/labels",
            "-f",
            f"name={label}",
            "-f",
            "color=0E8A16",
            "-f",
            "description=Created by the bounded nightly organization maintenance workflow",
        ],
        env=env,
        check=False,
    )
    if result.stderr and "already_exists" not in result.stderr and "422" not in result.stderr:
        raise MaintenanceError(f"could not ensure label {label} on {repository}")


def _compose_pr_body(
    change: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    run_key: str,
) -> str:
    linear = _require_mapping(snapshot.get("linear"), "snapshot.linear")
    tests = change.get("tests", [])
    test_lines = []
    for test in tests:
        command = test.get("command") or "(unspecified)"
        test_lines.append(f"- `{command}` — **{test.get('outcome')}**: {test.get('evidence') or 'no extra evidence'}")
    if not test_lines:
        test_lines = ["- No test command was available; the PR remains draft."]
    issue_line = f"Refs {change['linear_issue']}" if change.get("linear_issue") else "No single Linear issue; selected from the mapped project backlog."
    source_line = ""
    source = change.get("source_pr")
    if source:
        source_line = (
            f"\nThis is a semantic replacement for `{source['repository']}#{source['number']}` "
            f"at `{source['head_sha'][:12]}`. The old branch is not force-pushed or blindly merged.\n"
        )
    review_line = (
        "This change touches a protected area and must receive human approval before merge."
        if change.get("requires_human_review")
        else "This low/medium-risk change may be merged on a later run only after all required checks pass."
    )
    return textwrap.dedent(
        f"""
        {change['body']}

        ## Nightly maintenance evidence

        - Run: `{run_key}`
        - Organization: `{snapshot['owner']}`
        - Linear project: [{linear.get('project_name')}]({linear.get('project_url')})
        - Tracking: {issue_line}
        - Risk: `{change.get('risk')}`
        - Files changed: `{len(change.get('changed_files', []))}`
        - Changed bytes: `{change.get('changed_bytes', 0)}`
        - Merge gate: {review_line}
        {source_line}
        ## Validation

        {chr(10).join(test_lines)}

        ## Conflict policy

        Compatible intent is reconstructed against the current default branch. This workflow never
        resolves conflicts with wholesale `ours`/`theirs`, force-pushes another author's branch, or
        bypasses required reviews and checks.

        <!-- nightly-org-maintenance:{run_key}:{change['repository']} -->
        """
    ).strip() + "\n"


def _push_branch(
    repository_path: Path,
    branch: str,
    commit_message: str,
    env: Mapping[str, str],
) -> None:
    run_command(["git", "checkout", "-B", branch], cwd=repository_path)
    run_command(["git", "add", "--all"], cwd=repository_path)
    run_command(
        ["git", "-c", "user.name=nightly-org-maintenance[bot]", "-c", "user.email=nightly-org-maintenance[bot]@users.noreply.github.com", "commit", "-F", "-"],
        cwd=repository_path,
        input_text=commit_message,
    )
    run_command(["gh", "auth", "setup-git"], env=env)
    run_command(
        ["git", "push", "--set-upstream", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=repository_path,
        env=env,
        timeout=180.0,
    )


def _status_checks_green(rollup: Any) -> bool:
    if not isinstance(rollup, list) or not rollup:
        return False
    for check in rollup:
        if not isinstance(check, dict):
            return False
        state = _clean_text(check.get("state"), limit=30).upper()
        if state:
            if state != "SUCCESS":
                return False
            continue
        conclusion = _clean_text(check.get("conclusion"), limit=30).upper()
        status = _clean_text(check.get("status"), limit=30).upper()
        if status and status != "COMPLETED":
            return False
        if conclusion not in ALLOWED_CHECK_CONCLUSIONS:
            return False
    return True


def _merge_candidate(
    candidate: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    repository = candidate["repository"]
    number = candidate["number"]
    if candidate["action"] != "merge_if_green":
        return {
            "repository": repository,
            "number": number,
            "action": candidate["action"],
            "status": "not_merged",
            "reason": "candidate was selected for repair or skip",
        }

    fields = (
        "number,url,state,isDraft,mergeable,mergeStateStatus,reviewDecision,"
        "statusCheckRollup,headRefOid,baseRefName,labels,files"
    )

    def fetch_view() -> dict[str, Any]:
        value = _gh_json(
            ["pr", "view", str(number), "--repo", repository, "--json", fields],
            env=env,
        )
        return _require_mapping(value, "GitHub pull request view")

    view = fetch_view()
    if view.get("state") in TERMINAL_PR_STATES:
        return {
            "repository": repository,
            "number": number,
            "status": "already_terminal",
            "state": view.get("state"),
            "url": view.get("url"),
        }
    labels = {
        _clean_text(item.get("name"), limit=100).casefold()
        for item in view.get("labels", [])
        if isinstance(item, dict)
    }
    required_label = _clean_text(policy.get("merge_label", "agent:nightly"), limit=100)
    if required_label.casefold() not in labels:
        return {
            "repository": repository,
            "number": number,
            "status": "blocked",
            "reason": f"missing required label {required_label}",
            "url": view.get("url"),
        }

    protected_patterns = [str(item) for item in policy.get("protected_paths", [])]

    def evaluate(current: Mapping[str, Any], *, require_clean: bool) -> tuple[list[str], bool]:
        reasons: list[str] = []
        if current.get("headRefOid") != candidate["head_sha"]:
            reasons.append("head SHA changed after planning")
        if current.get("mergeable") != "MERGEABLE":
            reasons.append(f"mergeable={current.get('mergeable')}")
        if require_clean and current.get("mergeStateStatus") != "CLEAN":
            reasons.append(f"mergeStateStatus={current.get('mergeStateStatus')}")
        if current.get("reviewDecision") == "CHANGES_REQUESTED":
            reasons.append("changes are requested")
        if not _status_checks_green(current.get("statusCheckRollup")):
            reasons.append("required checks are absent, pending, or non-green")
        files = [
            _clean_text(item.get("path"), limit=500)
            for item in current.get("files", [])
            if isinstance(item, dict)
        ]
        protected = any(_matches_any(path, protected_patterns) for path in files)
        if protected and current.get("reviewDecision") != "APPROVED":
            reasons.append("protected paths require an approved review")
        return reasons, protected

    preliminary_reasons, protected = evaluate(view, require_clean=False)
    if bool(view.get("isDraft")):
        if protected:
            preliminary_reasons.append("protected pull request is still draft")
        elif not preliminary_reasons:
            run_command(
                ["gh", "pr", "ready", str(number), "--repo", repository],
                env=env,
                timeout=120.0,
            )
            view = fetch_view()
            if bool(view.get("isDraft")):
                preliminary_reasons.append("GitHub did not confirm ready-for-review state")
    if preliminary_reasons:
        return {
            "repository": repository,
            "number": number,
            "status": "blocked",
            "reason": "; ".join(dict.fromkeys(preliminary_reasons)),
            "url": view.get("url"),
        }

    reasons, _ = evaluate(view, require_clean=True)
    if bool(view.get("isDraft")):
        reasons.append("pull request is draft")
    if reasons:
        return {
            "repository": repository,
            "number": number,
            "status": "blocked",
            "reason": "; ".join(dict.fromkeys(reasons)),
            "url": view.get("url"),
        }
    method = _clean_text(policy.get("merge_method", "squash"), limit=20)
    if method not in {"squash", "merge", "rebase"}:
        raise MaintenanceError("policy.merge_method must be squash, merge, or rebase")
    run_command(
        ["gh", "pr", "merge", str(number), "--repo", repository, f"--{method}", "--delete-branch"],
        env=env,
        timeout=180.0,
    )
    verified = _gh_json(
        ["pr", "view", str(number), "--repo", repository, "--json", "state,url,mergeCommit"],
        env=env,
    )
    if verified.get("state") != "MERGED":
        raise MaintenanceError(f"GitHub did not confirm merge for {repository}#{number}")
    return {
        "repository": repository,
        "number": number,
        "status": "merged",
        "url": verified.get("url"),
        "merge_commit": (verified.get("mergeCommit") or {}).get("oid"),
    }


def publish(
    *,
    result: Mapping[str, Any],
    plan: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    policy: Mapping[str, Any],
    workspace: Path,
    run_key: str,
    token_env: str,
) -> dict[str, Any]:
    env = _gh_environment(token_env)
    merge_label = _clean_text(policy.get("merge_label", "agent:nightly"), limit=100)
    created: list[dict[str, Any]] = []
    for change in result["changes"]:
        repository = change["repository"]
        repository_path = _safe_workspace_path(workspace, repository)
        _ensure_label(repository, merge_label, env)
        commit_message = change["commit_message"]
        if change.get("linear_issue") and change["linear_issue"] not in commit_message:
            commit_message = f"{commit_message}\n\nRefs {change['linear_issue']}"
        _push_branch(repository_path, change["branch"], commit_message, env)
        body = _compose_pr_body(change, snapshot, run_key)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as stream:
            stream.write(body)
            body_path = stream.name
        try:
            existing = run_command(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    repository,
                    "--head",
                    change["branch"],
                    "--state",
                    "open",
                    "--json",
                    "number,url,state",
                ],
                env=env,
            ).stdout
            existing_values = json.loads(existing or "[]")
            if existing_values:
                pr = existing_values[0]
            else:
                output = run_command(
                    [
                        "gh",
                        "pr",
                        "create",
                        "--repo",
                        repository,
                        "--base",
                        _repository_index(snapshot)[repository.casefold()]["default_branch"],
                        "--head",
                        change["branch"],
                        "--title",
                        change["title"],
                        "--body-file",
                        body_path,
                        "--draft",
                    ],
                    env=env,
                    timeout=180.0,
                ).stdout.strip()
                number_match = re.search(r"/pull/(\d+)(?:$|[/?#])", output)
                if not number_match:
                    raise MaintenanceError(f"could not parse created PR URL for {repository}")
                pr = _gh_json(
                    [
                        "pr",
                        "view",
                        number_match.group(1),
                        "--repo",
                        repository,
                        "--json",
                        "number,url,state,isDraft,headRefName,baseRefName,headRefOid",
                    ],
                    env=env,
                )
            run_command(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    f"repos/{repository}/issues/{pr['number']}/labels",
                    "--input",
                    "-",
                ],
                env=env,
                input_text=json.dumps({"labels": [merge_label]}),
            )
            expected_base = _repository_index(snapshot)[repository.casefold()]["default_branch"]
            verified_pr = _gh_json(
                [
                    "pr",
                    "view",
                    str(pr["number"]),
                    "--repo",
                    repository,
                    "--json",
                    "number,url,state,isDraft,headRefName,baseRefName,headRefOid,labels",
                ],
                env=env,
            )
            if (
                verified_pr.get("state") != "OPEN"
                or verified_pr.get("headRefName") != change["branch"]
                or verified_pr.get("baseRefName") != expected_base
                or not SHA_RE.fullmatch(_clean_text(verified_pr.get("headRefOid"), limit=40))
            ):
                raise MaintenanceError(f"GitHub did not verify the expected PR state for {repository}")
            created.append(
                {
                    "repository": repository,
                    "number": verified_pr.get("number"),
                    "url": verified_pr.get("url"),
                    "state": verified_pr.get("state"),
                    "draft": verified_pr.get("isDraft"),
                    "branch": change["branch"],
                    "head_sha": verified_pr.get("headRefOid"),
                }
            )
        finally:
            Path(body_path).unlink(missing_ok=True)

    merged = [
        _merge_candidate(candidate, policy=policy, env=env)
        for candidate in plan["merge_candidates"]
    ]
    return {
        "schema_version": "nightly_org_ledger.v1",
        "run_key": run_key,
        "owner": result["owner"],
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "created_pull_requests": created,
        "merge_results": merged,
        "summary": result.get("summary"),
    }


def command_publish(args: argparse.Namespace) -> dict[str, Any]:
    result = _require_mapping(load_json(args.result, label="validated result"), "result")
    plan = _require_mapping(load_json(args.plan, label="validated plan"), "plan")
    snapshot = _require_mapping(load_json(args.snapshot, label="snapshot"), "snapshot")
    policy = _require_mapping(load_json(args.policy, label="maintenance policy"), "policy")
    normalized_plan = validate_plan(plan, snapshot)
    normalized_result = validate_result(result, normalized_plan, snapshot, args.workspace)
    ledger = publish(
        result=normalized_result,
        plan=normalized_plan,
        snapshot=snapshot,
        policy=policy,
        workspace=args.workspace,
        run_key=args.run_key,
        token_env=args.github_token_env,
    )
    write_json(args.ledger, ledger)
    if args.step_summary:
        summary = Path(args.step_summary)
        with summary.open("a", encoding="utf-8") as stream:
            stream.write(f"## Nightly maintenance: `{ledger['owner']}`\n\n")
            stream.write(f"Run key: `{ledger['run_key']}`\n\n")
            stream.write("### New pull requests\n\n")
            for pr in ledger["created_pull_requests"]:
                stream.write(f"- [{pr['repository']}#{pr['number']}]({pr['url']}) — draft={pr['draft']}\n")
            stream.write("\n### Existing pull-request decisions\n\n")
            for item in ledger["merge_results"]:
                target = f"{item['repository']}#{item['number']}"
                if item.get("url"):
                    target = f"[{target}]({item['url']})"
                stream.write(f"- {target} — **{item['status']}**")
                if item.get("reason"):
                    stream.write(f": {item['reason']}")
                stream.write("\n")
    return {
        "status": "published",
        "owner": ledger["owner"],
        "created_pull_requests": len(ledger["created_pull_requests"]),
        "merged_pull_requests": sum(1 for item in ledger["merge_results"] if item["status"] == "merged"),
        "ledger": str(args.ledger),
    }

__all__ = [name for name in globals() if not name.startswith("__")]
