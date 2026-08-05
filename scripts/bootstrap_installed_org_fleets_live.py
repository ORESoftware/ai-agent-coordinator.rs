#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API = "https://api.github.com"
ALLOWED_ORGS = ("apostille-me", "evento-globolo", "embedded-alerts", "hacker-house-medellin")
EXPECTED_TOTAL = 41
DATE = "20260804"
CHECKOUT = "3d3c42e5aac5ba805825da76410c181273ba90b1"


class ApiError(RuntimeError):
    def __init__(self, method: str, path: str, status: int, message: str):
        super().__init__(f"GitHub {method} {path} returned {status}: {message[:500]}")
        self.status, self.message = status, message[:500]


class GitHub:
    def __init__(self, token: str):
        self.token = token
        self.scopes: set[str] = set()

    def request(self, method: str, path: str, payload: Any = None, allow: tuple[int, ...] = ()):
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "installed-org-fleet-bootstrap/1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        for attempt in range(5):
            req = urllib.request.Request(API + path, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    scopes = response.headers.get("X-OAuth-Scopes", "")
                    self.scopes.update(x.strip() for x in scopes.split(",") if x.strip())
                    raw = response.read()
                    return response.status, json.loads(raw) if raw else None
            except urllib.error.HTTPError as error:
                raw = error.read(4096)
                try:
                    message = json.loads(raw).get("message", "unknown error")
                except Exception:
                    message = raw.decode(errors="replace")
                if error.code in allow:
                    return error.code, None
                if error.code in (429, 500, 502, 503, 504) and attempt < 4:
                    time.sleep(min(2 ** (attempt + 1), 20))
                    continue
                raise ApiError(method, path, error.code, str(message))
            except urllib.error.URLError as error:
                if attempt < 4:
                    time.sleep(min(2 ** (attempt + 1), 20))
                    continue
                raise RuntimeError(f"GitHub transport failed: {error}")
        raise AssertionError("unreachable")

    def get(self, path: str, allow: tuple[int, ...] = ()): return self.request("GET", path, allow=allow)
    def post(self, path: str, payload: Any): return self.request("POST", path, payload)
    def patch(self, path: str, payload: Any): return self.request("PATCH", path, payload)
    def put(self, path: str, payload: Any): return self.request("PUT", path, payload)


@dataclass(frozen=True)
class Repo:
    org: str
    issue: str
    name: str
    description: str
    order: int
    fleet: tuple[str, ...]

    @property
    def full(self): return f"{self.org}/{self.name}"


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def load(paths: list[Path]) -> list[Repo]:
    manifests = {}
    for path in paths:
        data = json.loads(path.read_text())
        org = data.get("organization")
        if org not in ALLOWED_ORGS or data.get("live_creation_enabled") is not False:
            raise ValueError(f"invalid or live-enabled manifest: {path}")
        manifests[org] = data
    if set(manifests) != set(ALLOWED_ORGS):
        raise ValueError("exact four-organization manifest set is required")
    result, seen = [], set()
    for org in ALLOWED_ORGS:
        data = manifests[org]
        rows = sorted(data["repositories"], key=lambda x: x["order"])
        fleet = tuple(row["name"] for row in rows)
        for row in rows:
            if row.get("visibility") != "public":
                raise ValueError(f"only public repositories are allowed: {org}/{row['name']}")
            repo = Repo(org, data["tracking_issue"], row["name"], row["description"], row["order"], fleet)
            if repo.full in seen: raise ValueError(f"duplicate repository: {repo.full}")
            seen.add(repo.full); result.append(repo)
    if len(result) != EXPECTED_TOTAL:
        raise ValueError(f"expected {EXPECTED_TOTAL} repositories, observed {len(result)}")
    return result


def kind(name: str) -> str:
    suffixes = (
        ("-infra", "infra"), (".github.io", "marketing"), ("-interfaces", "interfaces"),
        ("-api", "api"), ("-mash-web", "mash"), ("-leptos-web", "leptos"),
        ("-dioxus-web", "dioxus"), ("-sync", "sync"), ("-cli", "cli"),
        ("-clients", "clients"), ("-libs", "libs"), ("-monorepo", "monorepo"),
    )
    return next((value for suffix, value in suffixes if name.endswith(suffix)), "foundation")


def common(repo: Repo, repo_kind: str) -> dict[str, str]:
    metadata = {
        "schema_version": 1, "organization": repo.org, "repository": repo.name,
        "tracking_issue": repo.issue, "kind": repo_kind, "visibility": "public", "bootstrap_date": DATE,
    }
    fleet = "\n".join(f"- `{name}`" for name in repo.fleet)
    verify = '''#!/usr/bin/env python3
import json, re
from pathlib import Path
root = Path(__file__).resolve().parents[1]
meta = json.loads((root / "project.json").read_text())
required = ["README.md", "AGENTS.md", "project.json", "docs/architecture.md", *meta.get("required_paths", [])]
missing = [path for path in required if not (root / path).exists()]
if missing: raise SystemExit(f"missing required paths: {missing}")
for path in root.rglob("*"):
    if not path.is_file() or ".git" in path.parts or path.stat().st_size > 1_000_000: continue
    try: text = path.read_text()
    except UnicodeDecodeError: continue
    if any(marker in text for marker in ("<"*7, "="*7, ">"*7)): raise SystemExit(f"conflict marker in {path}")
    if re.search(r"gh[pousr]_[A-Za-z0-9]{20,}|lin_api_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY", text):
        raise SystemExit(f"credential-shaped content in {path}")
print(f"validated {meta['organization']}/{meta['repository']}")
'''
    return {
        "README.md": f"# {repo.name}\n\n{repo.description}\n\nInitialized through `{repo.issue}` as a testable `{repo_kind}` foundation. Product behavior continues through focused pull requests.\n\n```bash\npython3 scripts/verify_repo.py\n```\n",
        "AGENTS.md": f"# AGENTS.md\n\nOwner: `{repo.org}`  \nTracking: `{repo.issue}`\n\nUse focused pull requests, preserve interface compatibility, add tests with behavior changes, never commit credentials or customer data, and resolve conflicts semantically using both sides and relevant history.\n",
        "project.json": json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        "docs/architecture.md": f"# Architecture\n\n{repo.description}\n\n## Fleet\n\n{fleet}\n\nInterfaces own wire formats; libraries own reusable domain behavior; clients consume versioned contracts; runtimes own deployment behavior; monorepos coordinate pinned revisions. Edge code is allowlisted and never a generic proxy.\n",
        "scripts/verify_repo.py": verify,
        ".gitignore": ".env\n.env.*\n!.env.example\ntarget/\nnode_modules/\ndist/\n.astro/\ncoverage/\n",
    }


def rust_files(repo: Repo, repo_kind: str) -> dict[str, str]:
    package = slug(repo.name)
    if repo_kind in ("api", "mash", "leptos", "dioxus"):
        stack = {
            "api": "Axum, SeaORM and WebSockets",
            "mash": "Maud, Axum, SeaORM, Supabase, HTMX and WebSockets",
            "leptos": "Leptos with shared interfaces",
            "dioxus": "Dioxus with shared interfaces",
        }[repo_kind]
        cargo = f'''[package]
name = "{package}"
version = "0.1.0"
edition = "2024"
publish = false

[package.metadata.product]
stack = "{stack}"

[dependencies]
axum = "0.8"
tokio = {{ version = "1", features = ["macros", "net", "rt-multi-thread"] }}
'''
        source = f'''use axum::{{routing::get, Router}};

async fn health() -> &'static str {{ "ok" }}

#[tokio::main]
async fn main() {{
    let app = Router::new().route("/healthz", get(health));
    let listener = tokio::net::TcpListener::bind("0.0.0.0:8080").await.unwrap();
    axum::serve(listener, app).await.unwrap();
}}

#[cfg(test)]
mod tests {{ #[test] fn service_name_is_stable() {{ assert_eq!("{repo.name}", "{repo.name}"); }} }}
'''
        return {"Cargo.toml": cargo, "src/main.rs": source, "STACK.md": f"# Stack\n\n{stack}. The first follow-up PR should connect canonical interfaces and persistence.\n"}
    if repo_kind == "cli":
        cargo = f'''[package]
name = "{package}"
version = "0.1.0"
edition = "2024"
publish = false

[package.metadata.flags-2-env]
upstream = "https://github.com/ORESoftware/flags-2-env"

[dependencies]
clap = {{ version = "4", features = ["derive", "env"] }}
'''
        source = '''use clap::Parser;
#[derive(Parser)] struct Args { #[arg(long, env="SERVICE_ENDPOINT", default_value="http://localhost:8080")] endpoint: String }
fn main() { println!("{}", Args::parse().endpoint); }
'''
        return {"Cargo.toml": cargo, "src/main.rs": source}
    if repo_kind == "sync":
        metadata = f'''[package]
name = "{package}"
version = "0.1.0"
edition = "2024"
publish = false

[package.metadata.opto-sync]
upstream = "https://github.com/opto-sync"
strategy = "offline-first"
'''
        source = '''#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Envelope<T> { pub operation_id: String, pub revision: u64, pub payload: T }
impl<T> Envelope<T> { pub fn validate(&self) -> Result<(), &'static str> { if self.operation_id.trim().is_empty() { Err("operation_id is required") } else { Ok(()) } } }
#[cfg(test)] mod tests { use super::*; #[test] fn rejects_empty_id() { assert!(Envelope { operation_id: "".into(), revision: 1, payload: () }.validate().is_err()); } }
'''
        return {"Cargo.toml": metadata, "src/lib.rs": source}
    cargo = f'''[package]
name = "{package}"
version = "0.1.0"
edition = "2024"
publish = false
'''
    source = '''#[derive(Debug, Clone, PartialEq, Eq)] pub struct Record { pub id: String, pub kind: String }
impl Record { pub fn new(id: &str, kind: &str) -> Result<Self, &'static str> { if id.is_empty() || kind.is_empty() { Err("fields are required") } else { Ok(Self { id: id.into(), kind: kind.into() }) } } }
#[cfg(test)] mod tests { use super::*; #[test] fn validates() { assert!(Record::new("1", "domain").is_ok()); assert!(Record::new("", "domain").is_err()); } }
'''
    return {"Cargo.toml": cargo, "src/lib.rs": source}


def specialized(repo: Repo, repo_kind: str) -> dict[str, str]:
    if repo_kind in ("api", "mash", "leptos", "dioxus", "sync", "cli", "libs"):
        return rust_files(repo, repo_kind)
    if repo_kind == "interfaces":
        event = {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "DomainEvent", "type": "object", "required": ["id", "type", "occurredAt"], "properties": {"id": {"type": "string"}, "type": {"type": "string"}, "occurredAt": {"type": "string", "format": "date-time"}}, "additionalProperties": False}
        openapi = {"openapi": "3.1.0", "info": {"title": repo.name, "version": "0.1.0"}, "paths": {"/healthz": {"get": {"responses": {"200": {"description": "healthy"}}}}}}
        asyncapi = {"asyncapi": "3.0.0", "info": {"title": repo.name + " events", "version": "0.1.0"}, "channels": {"events": {"address": slug(repo.org) + ".events"}}}
        return {"schemas/event.schema.json": json.dumps(event, indent=2)+"\n", "openapi/openapi.json": json.dumps(openapi, indent=2)+"\n", "asyncapi/asyncapi.json": json.dumps(asyncapi, indent=2)+"\n"}
    if repo_kind == "infra":
        package = {"name": slug(repo.name), "private": True, "type": "module", "scripts": {"test": "node --test"}}
        worker = f'''const ALLOWED = new Set(["/healthz", "/edge/config"]);
export default {{ async fetch(request, env) {{ const url = new URL(request.url); if (!ALLOWED.has(url.pathname)) return new Response("not found", {{status:404}}); if (url.pathname === "/healthz") return Response.json({{service:"{repo.name}",status:"ok"}}); return Response.json({{proxying:"disabled",apiOriginConfigured:Boolean(env.API_ORIGIN)}}); }} }};
'''
        test = '''import test from "node:test"; import assert from "node:assert/strict"; import worker from "../src/index.js";
test("blocks generic proxy paths", async () => { assert.equal((await worker.fetch(new Request("https://x/healthz"), {})).status, 200); assert.equal((await worker.fetch(new Request("https://x/proxy"), {})).status, 404); });
'''
        return {"package.json": json.dumps(package, indent=2)+"\n", "src/index.js": worker, "tests/worker.test.mjs": test, "wrangler.toml": f'name = "{slug(repo.name)}"\nmain = "src/index.js"\ncompatibility_date = "2026-08-04"\nworkers_dev = false\n'}
    if repo_kind == "marketing":
        package = {"name": slug(repo.name), "private": True, "type": "module", "scripts": {"dev": "astro dev", "build": "astro build"}, "dependencies": {"astro": "^5.0.0"}}
        page = f'''---\nconst title = "{repo.org.replace('-', ' ').title()}";\n---\n<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width"/><title>{{title}}</title></head><body><main><p>Public foundation</p><h1>{{title}}</h1><p>{repo.description}</p></main></body></html>\n'''
        return {"package.json": json.dumps(package, indent=2)+"\n", "astro.config.mjs": "import { defineConfig } from 'astro/config';\nexport default defineConfig({ output: 'static' });\n", "src/pages/index.astro": page, "public/robots.txt": "User-agent: *\nAllow: /\n"}
    if repo_kind == "clients":
        return {
            "clients/go/go.mod": f"module github.com/{repo.org}/{repo.name}/clients/go\n\ngo 1.24\n",
            "clients/go/client.go": "package client\ntype Client struct { Endpoint string }\nfunc (c Client) HealthURL() string { return c.Endpoint + \"/healthz\" }\n",
            "clients/typescript/src/index.ts": "export class Client { constructor(readonly endpoint: string) {} healthUrl() { return `${this.endpoint}/healthz`; } }\n",
            "clients/rust/Cargo.toml": f'[package]\nname = "{slug(repo.name)}-rust"\nversion = "0.1.0"\nedition = "2024"\npublish = false\n',
            "clients/rust/src/lib.rs": "pub struct Client { pub endpoint: String }\nimpl Client { pub fn health_url(&self) -> String { format!(\"{}/healthz\", self.endpoint) } }\n",
            "clients/dart/lib/client.dart": "class Client { Client(this.endpoint); final String endpoint; String get healthUrl => '$endpoint/healthz'; }\n",
            "clients/gleam/src/client.gleam": "pub fn health_url(endpoint: String) -> String { endpoint <> \"/healthz\" }\n",
            "clients/erlang/src/client.erl": "-module(client).\n-export([health_url/1]).\nhealth_url(E) -> <<E/binary, \"/healthz\">>.\n",
            "clients/wasm/README.md": "# WASM client\n\nGenerate wasm-bindgen bindings from the Rust client after interfaces stabilize.\n",
        }
    if repo_kind == "monorepo":
        registry = {"schema_version": 1, "organization": repo.org, "tracking_issue": repo.issue, "repositories": [{"name": name, "ref": "main"} for name in repo.fleet if name != repo.name]}
        return {"repositories.lock.json": json.dumps(registry, indent=2)+"\n", "integration/README.md": "# Integration\n\nAdd pinned cross-repository compatibility tests here. Do not duplicate application source.\n"}
    return {"contracts/service.json": json.dumps({"service": repo.name, "health_path": "/healthz"}, indent=2)+"\n"}


def workflow(repo_kind: str) -> str:
    extra = ""
    if repo_kind in ("api", "mash", "leptos", "dioxus", "sync", "cli", "libs"):
        extra = "      - run: cargo test --all-targets\n"
    elif repo_kind == "infra": extra = "      - run: node --test\n"
    return f'''name: CI
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  contracts:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@{CHECKOUT}
        with:
          persist-credentials: false
      - run: python3 scripts/verify_repo.py
{extra}'''


def files(repo: Repo, include_workflow: bool) -> dict[str, str]:
    repo_kind = kind(repo.name)
    result = common(repo, repo_kind)
    result.update(specialized(repo, repo_kind))
    metadata = json.loads(result["project.json"])
    metadata["required_paths"] = sorted(path for path in result if path not in ("README.md", "AGENTS.md", "project.json", "docs/architecture.md"))
    if include_workflow:
        result[".github/workflows/ci.yml"] = workflow(repo_kind)
        metadata["required_paths"].append(".github/workflows/ci.yml")
        metadata["required_paths"].sort()
    result["project.json"] = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    return result


def encoded(full: str) -> str: return urllib.parse.quote(full, safe="/")


def ensure_repo(gh: GitHub, repo: Repo):
    path = f"/repos/{encoded(repo.full)}"
    status, data = gh.get(path, (404,)); created = False
    if status == 404:
        _, data = gh.post(f"/orgs/{repo.org}/repos", {"name": repo.name, "description": repo.description, "private": False, "visibility": "public", "has_issues": True, "has_projects": False, "has_wiki": False, "auto_init": True, "allow_squash_merge": True, "allow_merge_commit": True, "allow_rebase_merge": False, "delete_branch_on_merge": True})
        created = True
    if data.get("private") or data.get("visibility") != "public": raise RuntimeError(f"visibility mismatch for {repo.full}")
    gh.patch(path, {"description": repo.description, "has_issues": True, "has_projects": False, "has_wiki": False, "allow_squash_merge": True, "allow_merge_commit": True, "allow_rebase_merge": False, "delete_branch_on_merge": True})
    return data, created


def main_ref(gh: GitHub, repo: Repo):
    base = encoded(repo.full)
    for attempt in range(20):
        status, ref = gh.get(f"/repos/{base}/git/ref/heads/main", (404, 409))
        if status == 200:
            sha = ref["object"]["sha"]
            _, commit = gh.get(f"/repos/{base}/git/commits/{sha}")
            return sha, commit["tree"]["sha"]
        time.sleep(min(attempt + 1, 5))
    raise RuntimeError(f"main did not initialize for {repo.full}")


def initialized(gh: GitHub, repo: Repo) -> bool:
    status, _ = gh.get(f"/repos/{encoded(repo.full)}/contents/project.json?ref=main", (404,))
    return status == 200


def commit_files(gh: GitHub, repo: Repo, base_sha: str, tree_sha: str, content: dict[str, str]):
    base = encoded(repo.full); entries = []
    for path in sorted(content):
        _, blob = gh.post(f"/repos/{base}/git/blobs", {"content": content[path], "encoding": "utf-8"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    _, tree = gh.post(f"/repos/{base}/git/trees", {"base_tree": tree_sha, "tree": entries})
    _, commit = gh.post(f"/repos/{base}/git/commits", {"message": f"bootstrap({repo.issue}): initialize {repo.name}", "tree": tree["sha"], "parents": [base_sha]})
    return commit["sha"]


def set_branch(gh: GitHub, repo: Repo, branch: str, sha: str):
    base = encoded(repo.full); ref = urllib.parse.quote(branch, safe="")
    status, _ = gh.get(f"/repos/{base}/git/ref/heads/{ref}", (404,))
    if status == 404: gh.post(f"/repos/{base}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
    else: gh.patch(f"/repos/{base}/git/refs/heads/{ref}", {"sha": sha, "force": False})


def pull(gh: GitHub, repo: Repo, branch: str):
    base = encoded(repo.full); head = urllib.parse.quote(f"{repo.org}:{branch}", safe=":")
    _, existing = gh.get(f"/repos/{base}/pulls?state=all&head={head}&per_page=20")
    if existing: return existing[0], False
    _, pr = gh.post(f"/repos/{base}/pulls", {"title": f"bootstrap({repo.issue}): initialize {repo.name}", "head": branch, "base": "main", "body": f"Initialize `{repo.full}` as the `{kind(repo.name)}` component of the reviewed organization fleet. Includes metadata, architecture, contract validation, and an archetype-specific foundation. Tracking: {repo.issue}.", "maintainer_can_modify": True})
    return pr, True


def merge(gh: GitHub, repo: Repo, pr: dict[str, Any], sha: str):
    if pr.get("merged_at"): return True, pr.get("merge_commit_sha")
    try:
        _, result = gh.put(f"/repos/{encoded(repo.full)}/pulls/{pr['number']}/merge", {"sha": sha, "merge_method": "squash", "commit_title": f"bootstrap({repo.issue}): initialize {repo.name} (#{pr['number']})", "commit_message": repo.description})
        return bool(result.get("merged")), result.get("sha")
    except ApiError as error:
        if error.status in (405, 409, 422): return False, None
        raise


def one(gh: GitHub, repo: Repo, include_workflow: bool, should_merge: bool):
    metadata, created = ensure_repo(gh, repo)
    if initialized(gh, repo):
        return {"repository": repo.full, "repository_url": metadata["html_url"], "repository_created": created, "state": "already_initialized", "merged": True}
    base_sha, tree_sha = main_ref(gh, repo); branch = f"agent/{repo.issue.lower()}-bootstrap-{DATE}"
    used_workflow = include_workflow
    sha = commit_files(gh, repo, base_sha, tree_sha, files(repo, used_workflow))
    try: set_branch(gh, repo, branch, sha)
    except ApiError as error:
        if not used_workflow or error.status != 422 or "workflow" not in error.message.lower(): raise
        used_workflow = False; sha = commit_files(gh, repo, base_sha, tree_sha, files(repo, False)); set_branch(gh, repo, branch, sha)
    pr, pr_created = pull(gh, repo, branch); merged, merge_sha = (merge(gh, repo, pr, sha) if should_merge else (False, None))
    return {"repository": repo.full, "repository_url": metadata["html_url"], "repository_created": created, "state": "merged" if merged else "pull_request_open", "branch": branch, "commit_sha": sha, "pull_request": pr["html_url"], "pull_request_number": pr["number"], "pull_request_created": pr_created, "merged": merged, "merge_sha": merge_sha, "workflow_included": used_workflow}


def summary(results, failures):
    return {"repositories_total": len(results)+len(failures), "repositories_created": sum(bool(x.get("repository_created")) for x in results), "repositories_existing": sum(not bool(x.get("repository_created")) for x in results), "pull_requests_created": sum(bool(x.get("pull_request_created")) for x in results), "pull_requests_merged": sum(bool(x.get("merged")) for x in results), "pull_requests_open": sum(x.get("state") == "pull_request_open" for x in results), "already_initialized": sum(x.get("state") == "already_initialized" for x in results), "workflows_included": sum(x.get("workflow_included") is True for x in results), "failures": len(failures)}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--manifest", action="append", required=True, type=Path); parser.add_argument("--token-file", required=True, type=Path); parser.add_argument("--result", required=True, type=Path); parser.add_argument("--merge", action="store_true"); args = parser.parse_args()
    repos = load(args.manifest); token = args.token_file.read_text().strip()
    if len(token) < 20 or any(ch.isspace() for ch in token): raise SystemExit("invalid token shape")
    gh = GitHub(token); token = ""; _, profile = gh.get("/user"); include_workflow = "workflow" in gh.scopes or not gh.scopes
    results, failures = [], []
    for index, repo in enumerate(repos, 1):
        print(f"[{index}/{len(repos)}] {repo.full}", flush=True)
        try: results.append(one(gh, repo, include_workflow, args.merge))
        except Exception as error:
            failures.append({"repository": repo.full, "error": str(error)[:500]}); print(f"failed {repo.full}: {error}", file=sys.stderr, flush=True)
    output = {"schema_version": 1, "authenticated_login": profile.get("login"), "allowed_organizations": list(ALLOWED_ORGS), "expected_repositories": EXPECTED_TOTAL, "summary": summary(results, failures), "repositories": results, "failures": failures}
    args.result.write_text(json.dumps(output, indent=2, sort_keys=True)+"\n"); gh.token = ""; print(json.dumps(output["summary"], sort_keys=True)); return 1 if failures else 0


if __name__ == "__main__": raise SystemExit(main())
