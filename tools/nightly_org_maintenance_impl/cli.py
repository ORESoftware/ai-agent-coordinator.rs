"""Implementation module for bounded nightly organization maintenance."""

from .common import *
from .matrix import *
from .snapshot import *
from .plan import *
from .workspace import *
from .result import *
from .publish import *
from .tracking import *


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bounded nightly GitHub organization maintenance orchestration."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    matrix = subparsers.add_parser("matrix", help="Build the scheduled organization matrix.")
    matrix.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    matrix.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    matrix.add_argument("--now")
    matrix.add_argument("--force", action="store_true")
    matrix.add_argument("--owners", action="append", default=[])
    matrix.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT"))
    matrix.set_defaults(handler=command_matrix)

    snapshot = subparsers.add_parser("snapshot", help="Fetch bounded GitHub and Linear context.")
    snapshot.add_argument("--owner", required=True)
    snapshot.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    snapshot.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.add_argument("--github-token-env", default="GH_TOKEN")
    snapshot.add_argument("--linear-token-env", default="LINEAR_API_TOKEN")
    snapshot.set_defaults(handler=command_snapshot)

    plan = subparsers.add_parser("validate-plan", help="Validate a structured Codex plan.")
    plan.add_argument("--plan", type=Path, required=True)
    plan.add_argument("--snapshot", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.set_defaults(handler=command_validate_plan)

    prepare = subparsers.add_parser("prepare-workspace", help="Clone the selected repositories.")
    prepare.add_argument("--plan", type=Path, required=True)
    prepare.add_argument("--snapshot", type=Path, required=True)
    prepare.add_argument("--workspace", type=Path, required=True)
    prepare.add_argument("--github-token-env", default="GH_TOKEN")
    prepare.set_defaults(handler=command_prepare_workspace)

    result = subparsers.add_parser("validate-result", help="Validate Codex changes and metadata.")
    result.add_argument("--result", type=Path, required=True)
    result.add_argument("--plan", type=Path, required=True)
    result.add_argument("--snapshot", type=Path, required=True)
    result.add_argument("--workspace", type=Path, required=True)
    result.add_argument("--output", type=Path, required=True)
    result.set_defaults(handler=command_validate_result)

    publish_parser = subparsers.add_parser(
        "publish", help="Push code branches, open PRs, and merge eligible PRs."
    )
    publish_parser.add_argument("--result", type=Path, required=True)
    publish_parser.add_argument("--plan", type=Path, required=True)
    publish_parser.add_argument("--snapshot", type=Path, required=True)
    publish_parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    publish_parser.add_argument("--workspace", type=Path, required=True)
    publish_parser.add_argument("--run-key", required=True)
    publish_parser.add_argument("--ledger", type=Path, required=True)
    publish_parser.add_argument("--github-token-env", default="GH_TOKEN")
    publish_parser.add_argument("--step-summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    publish_parser.set_defaults(handler=command_publish)

    tracking = subparsers.add_parser(
        "sync-tracking",
        help="Mirror published and merged PR evidence into Linear and GitHub Projects.",
    )
    tracking.add_argument("--result", type=Path, required=True)
    tracking.add_argument("--snapshot", type=Path, required=True)
    tracking.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    tracking.add_argument("--run-key", required=True)
    tracking.add_argument("--ledger", type=Path, required=True)
    tracking.add_argument("--github-token-env", default="GH_TOKEN")
    tracking.add_argument("--linear-token-env", default="LINEAR_API_TOKEN")
    tracking.add_argument("--step-summary", default=os.environ.get("GITHUB_STEP_SUMMARY"))
    tracking.set_defaults(handler=command_sync_tracking)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except MaintenanceError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2


__all__ = [name for name in globals() if not name.startswith("__")]
