#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from ores_legal_bootstrap.core import (  # noqa: E402
    BootstrapError,
    EXPECTED_ORGANIZATION,
    EXPECTED_REPOSITORIES,
    load_manifest,
    manifest_digest,
    render_all,
    repository_spec,
    validate_manifest,
)
from ores_legal_bootstrap.publication import (  # noqa: E402
    MERGE_CONFIRMATION,
    apply_repository,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Sealed ores-legal repository fleet bootstrap")
    value.add_argument("--manifest", type=Path, default=None, help="Override the reviewed manifest path")
    subparsers = value.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Validate and print the sealed publication plan")
    plan.add_argument("--manifest-sha256", required=True)
    plan.add_argument("--result", type=Path, required=False)

    render = subparsers.add_parser("render", help="Render every repository without network access")
    render.add_argument("--manifest-sha256", required=True)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--result", type=Path, required=False)

    apply = subparsers.add_parser("apply", help="Create one repository, open its PR, and exact-head squash merge it")
    apply.add_argument("--manifest-sha256", required=True)
    apply.add_argument("--organization", required=True)
    apply.add_argument("--repository", required=True, choices=EXPECTED_REPOSITORIES)
    apply.add_argument("--confirmation", required=True)
    apply.add_argument("--result", type=Path, required=True)
    apply.add_argument("--api-url", default="https://api.github.com")
    return value


def _emit(value: dict, result_path: Path | None) -> None:
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if result_path is not None:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(text, encoding="utf-8")
    print(text, end="")


def _manifest(args: argparse.Namespace) -> dict:
    manifest = load_manifest(args.manifest) if args.manifest else load_manifest()
    validate_manifest(manifest)
    digest = manifest_digest(manifest)
    if args.manifest_sha256 != digest:
        raise BootstrapError("--manifest-sha256 does not match the reviewed manifest")
    return manifest


def main() -> int:
    args = parser().parse_args()
    try:
        manifest = _manifest(args)
        digest = manifest_digest(manifest)
        if args.command == "plan":
            plan = {
                "organization": EXPECTED_ORGANIZATION,
                "manifest_digest": digest,
                "linear_issue": manifest["linear_issue"],
                "merge_confirmation": MERGE_CONFIRMATION,
                "repositories": [
                    {
                        "name": name,
                        "kind": repository_spec(manifest, name)["kind"],
                        "visibility": repository_spec(manifest, name)["visibility"],
                        "publication_order": index + 1,
                    }
                    for index, name in enumerate(EXPECTED_REPOSITORIES)
                ],
            }
            _emit(plan, args.result)
            return 0
        if args.command == "render":
            args.output.mkdir(parents=True, exist_ok=True)
            _emit(render_all(manifest, args.output), args.result)
            return 0
        if args.command == "apply":
            result = apply_repository(
                manifest,
                args.repository,
                args.organization,
                args.confirmation,
                api_url=args.api_url,
            )
            _emit({"result": result.to_dict()}, args.result)
            return 0
        raise BootstrapError(f"unsupported command: {args.command}")
    except BootstrapError as error:
        print(f"bootstrap rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
