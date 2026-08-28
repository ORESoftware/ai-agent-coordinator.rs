#!/usr/bin/env python3
"""Installation-token-safe entrypoint for the Astro Pages fleet bootstrap."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


CONTROLLER = Path(__file__).with_name("bootstrap_astro_pages_portfolio_20260810.py")


def load_controller() -> ModuleType:
    spec = importlib.util.spec_from_file_location("astro_pages_portfolio_controller", CONTROLLER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load controller at {CONTROLLER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def render_hardened_pages_workflow() -> str:
    """Build Astro directly and publish the exact dist tree through Pages."""
    return '''name: Deploy Astro to GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    env:
      ASTRO_TELEMETRY_DISABLED: '1'
    steps:
      - name: Check out source
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false

      - name: Set up Node.js
        uses: actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7
        with:
          node-version: 24
          package-manager-cache: false

      - name: Install exact Astro dependencies
        run: npm install --no-audit --no-fund

      - name: Build static Astro site
        run: npm run build

      - name: Upload exact Pages artifact
        uses: actions/upload-pages-artifact@fc324d3547104276b827a68afc52ff2a11cc49c9 # v5
        with:
          path: ./dist

  deploy:
    needs: build
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e # v4
'''


def write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the allowlisted Astro Pages bootstrap with a GitHub App installation token."
    )
    parser.add_argument("--org", required=True, help="Exact allowlisted GitHub organization login")
    parser.add_argument("--result", required=True, type=Path, help="Path for the JSON result ledger")
    args = parser.parse_args()

    controller = load_controller()
    controller.render_pages_workflow = render_hardened_pages_workflow
    site = controller.select_site(args.org)
    token = os.environ.get("GH_TOKEN", "").strip()
    if len(token) < 20 or any(character.isspace() for character in token):
        raise SystemExit("GH_TOKEN is missing or malformed")

    github = controller.GitHub(token)
    token = ""
    try:
        # Installation tokens deliberately do not call GET /user. That endpoint
        # accepts user access tokens, not GitHub App installation access tokens.
        result = controller.bootstrap(github, site)
        result["authentication_mode"] = "github_app_installation"
        result["pages_workflow_mode"] = "direct_astro_build"
        write_result(args.result, result)
        print(
            json.dumps(
                {
                    "organization": result["organization"],
                    "repository": result["repository"],
                    "repository_created": result["repository_created"],
                    "source_action": result["source_action"],
                    "pages_url": result["pages_url"],
                    "workflow_conclusion": result["workflow_conclusion"],
                    "live_status": result["live"]["status"],
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        failure = {
            "schema_version": 1,
            "organization": site.org,
            "repository": site.full_name,
            "status": "failed",
            "authentication_mode": "github_app_installation",
            "pages_workflow_mode": "direct_astro_build",
            "error_type": type(error).__name__,
            "error": str(error)[:1000],
            "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        write_result(args.result, failure)
        print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        github.close()


if __name__ == "__main__":
    raise SystemExit(main())
