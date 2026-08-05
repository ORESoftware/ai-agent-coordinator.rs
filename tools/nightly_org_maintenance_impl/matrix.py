"""Implementation module for bounded nightly organization maintenance."""

from .common import *

def build_matrix(
    registry: Mapping[str, Any],
    policy: Mapping[str, Any],
    *,
    owners: Sequence[str] | None = None,
) -> dict[str, Any]:
    by_owner = _mapping_by_owner(registry)
    disabled = _policy_set(policy, "disabled_owners")
    priority = [str(item) for item in policy.get("priority_owners", [])]
    priority_index = {owner.casefold(): index for index, owner in enumerate(priority)}
    requested = {owner.casefold() for owner in owners or []}
    missing = requested - {owner.casefold() for owner in by_owner}
    if missing:
        raise MaintenanceError(f"requested owners are absent from the registry: {sorted(missing)}")

    include: list[dict[str, Any]] = []
    for owner, item in by_owner.items():
        if item["_account_type"].casefold() != "organization":
            continue
        if owner.casefold() in disabled:
            continue
        if requested and owner.casefold() not in requested:
            continue
        app = _require_mapping(item.get("github_app"), f"{owner}.github_app")
        installation_id = app.get("installation_id")
        if not isinstance(installation_id, int) or installation_id <= 0:
            raise MaintenanceError(f"{owner} has no valid GitHub App installation")
        linear = _require_mapping(item.get("linear"), f"{owner}.linear")
        project_id = _clean_text(linear.get("project_id"), limit=100)
        project_name = _clean_text(linear.get("project_name"), limit=200)
        project_url = _clean_text(linear.get("project_url"), limit=500)
        if not project_id or not project_name or not project_url.startswith("https://linear.app/"):
            raise MaintenanceError(f"{owner} has incomplete Linear project context")
        route = item.get("runtime_route")
        default_repository = ""
        if isinstance(route, dict):
            candidate = _clean_text(route.get("default_repository"), limit=250)
            if candidate and REPOSITORY_RE.fullmatch(candidate):
                default_repository = candidate
        is_test_org = owner.casefold().endswith("-test")
        base_owner = owner[:-5] if is_test_org else owner
        paired_test_owner = f"{owner}-test" if not is_test_org else owner
        canonical_test_owner = next(
            (candidate for candidate in by_owner if candidate.casefold() == paired_test_owner.casefold()),
            "",
        )
        test_mapping_exists = bool(canonical_test_owner)
        include.append(
            {
                "owner": owner,
                "owner_key": owner.casefold(),
                "installation_id": installation_id,
                "linear_project_id": project_id,
                "linear_project_name": project_name,
                "linear_project_url": project_url,
                "default_repository": default_repository,
                "is_test_org": is_test_org,
                "base_owner": base_owner,
                "paired_test_owner": canonical_test_owner if test_mapping_exists else "",
            }
        )

    include.sort(
        key=lambda item: (
            priority_index.get(item["owner_key"], len(priority_index)),
            item["owner_key"],
        )
    )
    if not include:
        raise MaintenanceError("the selected registry contains no enabled organizations")
    maximum = int(policy.get("max_organizations_per_run", 100))
    if maximum < 1 or maximum > 256:
        raise MaintenanceError("policy.max_organizations_per_run must be between 1 and 256")
    if len(include) > maximum:
        raise MaintenanceError(
            f"registry selected {len(include)} organizations, exceeding policy limit {maximum}"
        )
    return {"include": include}


def _append_github_output(path: str | None, values: Mapping[str, str]) -> None:
    if not path:
        return
    output = Path(path)
    with output.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in value:
                marker = f"EOF_{hashlib.sha256(value.encode()).hexdigest()[:16]}"
                stream.write(f"{key}<<{marker}\n{value}\n{marker}\n")
            else:
                stream.write(f"{key}={value}\n")


def command_matrix(args: argparse.Namespace) -> dict[str, Any]:
    registry = _require_mapping(load_json(args.registry, label="organization registry"), "registry")
    policy = _require_mapping(load_json(args.policy, label="maintenance policy"), "policy")
    decision = schedule_decision(parse_instant(args.now), force=args.force)
    owners = [item for raw in args.owners for item in raw.split(",") if item]
    matrix = build_matrix(registry, policy, owners=owners or None) if decision.due else {"include": []}
    result = {
        "status": "due" if decision.due else "not_due",
        "due": decision.due,
        "run_key": decision.run_key,
        "scheduled_timezone": TIME_ZONE_NAME,
        "local_time": decision.local_time.isoformat(),
        "matrix": matrix,
        "organization_count": len(matrix["include"]),
    }
    rendered_matrix = json.dumps(matrix, separators=(",", ":"), sort_keys=True)
    _append_github_output(
        args.github_output,
        {
            "due": "true" if decision.due else "false",
            "run_key": decision.run_key,
            "matrix": rendered_matrix,
            "organization_count": str(len(matrix["include"])),
        },
    )
    return result

__all__ = [name for name in globals() if not name.startswith("__")]
