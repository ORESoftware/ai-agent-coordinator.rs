#!/usr/bin/env python3
"""Fail-closed, one-repository-at-a-time bootstrap for the H/HAUS standard fleet."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from hhaus_bootstrap.contract import *  # noqa: F403, E402
from hhaus_bootstrap.publish import *  # noqa: F403
from hhaus_bootstrap.render import *  # noqa: F403


def run() -> int:
    manifest = load_manifest()
    validate_manifest(manifest)
    mode = os.environ.get("HHAUS_BOOTSTRAP_MODE", "plan")
    if mode == "plan":
        plans = []
        for name in EXPECTED_REPOSITORIES:
            files = seed_files(manifest, name)
            validate_seed_files(manifest, name, files)
            plans.append({"repository": f"{EXPECTED_ORGANIZATION}/{name}", "files": len(files)})
        write_result({"mode": "plan", "manifest_digest": manifest_digest(manifest), "repositories": plans})
        return 0
    if mode == "render":
        root_value = os.environ.get("HHAUS_BOOTSTRAP_RENDER_ROOT")
        if not root_value:
            raise BootstrapError("render mode requires HHAUS_BOOTSTRAP_RENDER_ROOT")
        root = Path(root_value).resolve()
        root.mkdir(parents=True, exist_ok=True)
        result = render_all(manifest, root)
        write_result({"mode": "render", **result})
        return 0
    if mode == "apply":
        repo_name, token, merge = require_apply_authority(manifest)
        api = GitHubApi(token)
        spec = next(repo for repo in manifest["repositories"] if repo["name"] == repo_name)
        result = publish_scaffold(api, manifest, spec, merge)
        write_result({"mode": "apply", **result})
        return 0
    raise BootstrapError("HHAUS_BOOTSTRAP_MODE must be plan, render, or apply")


def main() -> None:
    try:
        raise SystemExit(run())
    except BootstrapError as error:
        print(f"bootstrap failed: {error}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
