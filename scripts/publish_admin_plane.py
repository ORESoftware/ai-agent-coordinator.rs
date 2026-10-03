#!/usr/bin/env python3
"""Create GitHub repos, feature branches, and draft PRs for the admin plane."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CODES = Path("/Users/maca5/codes")
BRANCH = "feat/admin-vpc-plane"
ZPKG_BRANCH = "feat/zed-pkg-lib-orm-core"
MSGINT_BRANCH = "feat/admin-zpkg-cores"

ORGS = [
    ("3FA-app", "3fa"),
    ("anticaptrad", "act"),
    ("athlet-o", "athleto"),
    ("canonical-cloud", "canonical"),
    ("chapter-publishing", "chptr"),
    ("cliptown", "cliptown"),
    ("daedalus-fab", "daedalus"),
    ("declarative-migrations", "declmig"),
    ("ecma-d", "ecmad"),
    ("elenkos-systems", "elenkos"),
    ("embedded-alerts", "eal"),
    ("evento-globolo", "evgl"),
    ("fanwaave", "fanwaave"),
    ("file-tunnel", "ftnl"),
    ("gha-indie-worker", "gha-indie-worker"),
    ("hacker-house-medellin", "hhm"),
    ("happy-wakey", "happy-wakey"),
    ("honeypot-r-us", "hnpt"),
    ("hypesiege", "hypesiege"),
    ("led-dynamo", "leddy"),
    ("memebank", "memebank"),
    ("opto-sync", "opto-sync"),
    ("ores-otel", "ores-otel"),
    ("praxonne", "praxonne"),
    ("premarital-asset-protection", "pmap"),
    ("quaestor-ledger", "quaestor"),
    ("scintilla-run", "scintilla"),
    ("sonus-auris", "sonus-auris"),
    ("voxletra", "vxl"),
    ("akrion-sim", "akrion"),
]

WEB_PATHS = [
    ".zpkg.toml",
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    ".gitignore",
    "README.md",
    "AGENTS.md",
    ".github/workflows/ci.yml",
    "k8s/networkpolicy.yaml",
    "crates",
    "frontends",
]

API_PATHS = [
    ".zpkg.toml",
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    ".gitignore",
    "README.md",
    "AGENTS.md",
    ".github/workflows/ci.yml",
    "k8s/networkpolicy.yaml",
    "schema/admin.sql",
    "src",
]


def run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    if token:
        env["GH_TOKEN"] = token
        env["GITHUB_TOKEN"] = token
        # Inherit token for git HTTPS without a global git-config change.
        n = int(env.get("GIT_CONFIG_COUNT") or "0")
        env["GIT_CONFIG_COUNT"] = str(n + 1)
        env[f"GIT_CONFIG_KEY_{n}"] = "url.https://x-access-token:" + token + "@github.com/.insteadOf"
        env[f"GIT_CONFIG_VALUE_{n}"] = "https://github.com/"
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
        env=env,
        timeout=120,
    )


def existing_paths(root: Path, names: list[str]) -> list[str]:
    return [name for name in names if (root / name).exists()]


def ensure_repo(root: Path, org: str, name: str, description: str) -> None:
    if not (root / ".git").exists():
        run(["git", "init", "-b", "main"], cwd=root)
    remotes = run(["git", "remote"], cwd=root, check=False).stdout.split()
    if "origin" not in remotes:
        created = run(
            [
                "gh",
                "repo",
                "create",
                f"{org}/{name}",
                "--private",
                "--description",
                description,
                "--add-readme",
            ],
            cwd=root,
            check=False,
        )
        if created.returncode != 0 and "already exists" not in (created.stderr + created.stdout):
            # create can fail if the repo exists; still add the remote
            pass
        url = f"https://github.com/{org}/{name}.git"
        run(["git", "remote", "add", "origin", url], cwd=root, check=False)


def checkout_branch(root: Path, branch: str) -> None:
    run(["git", "fetch", "origin"], cwd=root, check=False)
    # New local trees already hold generated files. Branch from HEAD so an
    # origin/main README from `gh repo create --add-readme` cannot clobber them.
    current = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, check=False)
    if current.returncode == 0 and current.stdout.strip() == branch:
        return
    created = run(["git", "checkout", "-B", branch], cwd=root, check=False)
    if created.returncode != 0:
        run(["git", "checkout", "-B", branch], cwd=root)


def commit_paths(root: Path, paths: list[str], message: str) -> bool:
    existing = existing_paths(root, paths)
    if not existing:
        return False
    run(["git", "add", "--"] + existing, cwd=root)
    staged = run(["git", "diff", "--cached", "--name-only"], cwd=root).stdout.strip()
    if not staged:
        return False
    run(
        ["git", "commit", "-m", message],
        cwd=root,
    )
    return True


def merge_origin_base(root: Path) -> None:
    run(["git", "fetch", "origin"], cwd=root, check=False)
    for base in ("origin/main", "origin/master"):
        exists = run(["git", "rev-parse", "--verify", base], cwd=root, check=False)
        if exists.returncode != 0:
            continue
        merged = run(
            [
                "git",
                "merge",
                base,
                "--allow-unrelated-histories",
                "-m",
                "Merge GitHub bootstrap README into the admin plane.",
            ],
            cwd=root,
            check=False,
        )
        if merged.returncode != 0:
            # Prefer the generated tree when the only conflict is README.
            run(["git", "checkout", "--ours", "--", "README.md"], cwd=root, check=False)
            run(["git", "add", "--", "README.md"], cwd=root, check=False)
            run(
                ["git", "commit", "-m", "Merge GitHub bootstrap README into the admin plane."],
                cwd=root,
                check=False,
            )
        return


def push_and_pr(root: Path, branch: str, title: str, body: str) -> str:
    merge_origin_base(root)
    run(["git", "push", "-u", "origin", branch], cwd=root)
    existing = run(
        ["gh", "pr", "list", "--head", branch, "--json", "url", "--jq", ".[0].url"],
        cwd=root,
        check=False,
    )
    url = existing.stdout.strip()
    if url:
        return url
    created = run(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=root,
        check=False,
    )
    return (created.stdout or created.stderr).strip()


def publish_admin(org: str, prefix: str, kind: str) -> str:
    name = f"{prefix}-admin-{kind}-server.rs"
    root = CODES / org / name
    if not root.is_dir():
        return f"missing {root}"
    description = (
        "Private super-admin mash/leptos/dioxus console on the admin VPC"
        if kind == "web"
        else "Private super-admin JSON API with isolated admin-RDS access"
    )
    ensure_repo(root, org, name, description)
    checkout_branch(root, BRANCH)
    paths = WEB_PATHS if kind == "web" else API_PATHS
    commit_paths(
        root,
        paths,
        f"Add isolated {kind} admin plane with zed-pkg cores and Shared Auth admin instance.",
    )
    return push_and_pr(
        root,
        BRANCH,
        f"Add {name} admin VPC plane",
        f"""## Summary
- Flesh out `{name}` so super-admins can reach the admin RDS from a private VPC.
- Import `*-lib-core` and `*-orm-core` through `.zpkg.toml` (zed-pkg).
- Use the Shared Auth **admin** instance (`SHARED_AUTH_ADMIN_*`), not the customer issuer.
- Bind loopback / ClusterIP only. Product web/api servers stay off this database.

## Test plan
- [ ] `cargo test --all-targets` (API) or `cargo test -p {prefix}-admin-web-common` (web)
- [ ] Confirm a non-loopback bind is refused
- [ ] Confirm a customer Shared Auth issuer is rejected
""",
    )


def publish_product_zpkg(org: str, prefix: str, kind: str) -> str | None:
    name = f"{prefix}-{kind}"
    root = CODES / org / name
    zpkg = root / ".zpkg.toml"
    if not zpkg.is_file() or not (root / ".git").exists():
        return None
    status = run(["git", "status", "--porcelain", "--", ".zpkg.toml"], cwd=root).stdout.strip()
    if not status:
        return None
    run(["git", "fetch", "origin"], cwd=root, check=False)
    checkout_branch(root, ZPKG_BRANCH)
    if not commit_paths(
        root,
        [".zpkg.toml"],
        "Import lib-core and orm-core through zed-pkg on the product server.",
    ):
        return None
    return push_and_pr(
        root,
        ZPKG_BRANCH,
        f"Import lib-core and orm-core in {name}",
        f"""## Summary
- Add zed-pkg dependencies on `{prefix}-lib-core` and `{prefix}-orm-core` so the product {kind} shares the same ORM boundary as the admin plane.

## Test plan
- [ ] `zed install` resolves the new cores when those packages exist
""",
    )


def publish_msgint_zpkg() -> list[str]:
    urls: list[str] = []
    for name in ("msgint-admin-web-server.rs", "msgint-admin-api-server.rs"):
        root = CODES / "messaging-intel" / name
        if not (root / ".zpkg.toml").is_file():
            continue
        run(["git", "fetch", "origin"], cwd=root, check=False)
        checkout_branch(root, MSGINT_BRANCH)
        if commit_paths(
            root,
            [".zpkg.toml"],
            "Declare lib-core, orm-core, and Shared Auth admin deps in zed-pkg.",
        ):
            urls.append(
                push_and_pr(
                    root,
                    MSGINT_BRANCH,
                    f"Add zed-pkg cores to {name}",
                    """## Summary
- Import `msgint-lib-core`, `msgint-orm-core`, and `shared-auth-clients` through `.zpkg.toml`.
- Admin servers continue to use the Shared Auth admin instance, not the customer issuer.

## Test plan
- [ ] Existing `cargo test` still passes
""",
                )
            )
    return urls


def main() -> int:
    results: list[str] = []
    for org, prefix in ORGS:
        for kind in ("web", "api"):
            try:
                print(f"publishing {org}/{prefix}-admin-{kind}-server.rs", flush=True)
                results.append(f"{org}/{prefix}-admin-{kind}: {publish_admin(org, prefix, kind)}")
            except subprocess.CalledProcessError as error:
                results.append(
                    f"{org}/{prefix}-admin-{kind}: FAILED {error.stderr or error.stdout}"
                )
            except subprocess.TimeoutExpired as error:
                results.append(f"{org}/{prefix}-admin-{kind}: TIMEOUT {error.cmd}")
        for kind in ("web-server.rs", "api-server.rs"):
            try:
                url = publish_product_zpkg(org, prefix, kind)
                if url:
                    results.append(f"{org}/{prefix}-{kind} zpkg: {url}")
            except subprocess.CalledProcessError as error:
                results.append(f"{org}/{prefix}-{kind} zpkg: FAILED {error.stderr or error.stdout}")
            except subprocess.TimeoutExpired as error:
                results.append(f"{org}/{prefix}-{kind} zpkg: TIMEOUT {error.cmd}")
    try:
        results.extend(publish_msgint_zpkg())
    except subprocess.CalledProcessError as error:
        results.append(f"msgint zpkg: FAILED {error.stderr or error.stdout}")
    print("\n".join(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
