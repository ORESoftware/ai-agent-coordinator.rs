from __future__ import annotations

import json
from typing import Any

from .constants import EXPECTED_ORGANIZATION

def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def zpkg_manifest(manifest: dict[str, Any], repo: dict[str, Any]) -> str:
    lines = [
        "[package]",
        f"org = {_toml_quote(EXPECTED_ORGANIZATION)}",
        f"name = {_toml_quote(repo['name'])}",
        'version = "0.1.0"',
        f"description = {_toml_quote(repo['description'])}",
        'license = "MIT"',
        "",
        "[package.repository]",
        'vcs = "git"',
        f"url = {_toml_quote('https://github.com/hhaus-org/' + repo['name'])}",
        "",
        "[install]",
        'dir = ".vendor/.zed"',
    ]
    dependencies = repo["internal_dependencies"] + repo["backend_internal_dependencies"] + repo["external_dependencies"]
    if dependencies:
        lines.extend(["", "[dependencies]"])
        for dependency in dependencies:
            lines.append(f"{_toml_quote(dependency)} = \"^0.1.0\"")
    lines.extend(
        [
            "",
            "[publish]",
            "include_readme = true",
            'tag_format = "v{version}"',
            'exclude = [".env", ".env.*", ".vendor/.zed/**", "node_modules/**", "target/**", "build/**", "dist/**", "*.log"]',
            "",
            "[scripts]",
            'verify = "python3 scripts/verify.py"',
            "",
            "[targets.repository]",
            'dir = "."',
        ]
    )
    if repo["kind"] in {"clients", "interfaces"}:
        adapters = {
            "c": "none",
            "cpp": "none",
            "csharp": "none",
            "dart": "dart",
            "elixir": "none",
            "erlang": "none",
            "gleam": "none",
            "go": "go",
            "java": "java",
            "kotlin": "java",
            "php": "none",
            "python": "python",
            "ruby": "none",
            "rust": "rust",
            "swift": "none",
            "typescript": "node",
            "zig": "none",
        }
        target_names = {"go": "golang", "typescript": "nodejs"}
        target_root = "clients" if repo["kind"] == "clients" else "generated"
        for language in manifest["language_targets"]:
            target = target_names.get(language, language)
            lines.extend(
                [
                    "",
                    f"[targets.{target}]",
                    f"dir = {_toml_quote(target_root + '/' + language)}",
                    f"adapter = {_toml_quote(adapters[language])}",
                ]
            )
    return "\n".join(lines) + "\n"


def common_readme(manifest: dict[str, Any], repo: dict[str, Any], digest: str) -> str:
    deps = repo["internal_dependencies"] + repo["backend_internal_dependencies"] + repo["external_dependencies"]
    dependency_lines = "\n".join(f"- `{dependency}`" for dependency in deps)
    backend_note = (
        "This repository is backend-only. It must never be imported into browser, mobile, or untrusted edge bundles."
        if repo["backend_only"]
        else "This repository exposes no backend-only ORM implementation through public client bundles."
    )
    return f"""# {repo['name']}

{repo['description']}

## Contract

- Fleet manifest digest: `{digest}`.
- TypeSpec and JSON Schema Draft 2020-12 are independent peer authorities; generation stops unless normalized semantics agree exactly.
- Shared dependencies are resolved through `zed-pkg`; generated directories are derivative and must not be edited by hand.
- Authentication uses `shared-auth`; request middleware uses `ores-middleware`; trace/log context uses `ores-otel`.
- Rate limiting uses `ores-rate-limit` across Cloudflare edge, gateway/load balancer, service-local LRU, distributed Redis/coordinator, and durable security/billing enforcement.
- {backend_note}

## Dependencies

{dependency_lines}

## Verification

```bash
python3 scripts/verify.py
```

Repository creation and the initial merge are performed from a sealed, tested payload in `ORESoftware/ai-agent-coordinator.rs`. Subsequent work uses feature branches and pull requests under the canonical [`ORESoftware/my-ai/AGENTS.md`](https://github.com/ORESoftware/my-ai/blob/main/AGENTS.md) rules.
"""


def common_agents(repo: dict[str, Any]) -> str:
    backend_rule = (
        "- This repository is backend-only; do not export ORM entities, database credentials, or internal validators to clients.\n"
        if repo["backend_only"]
        else "- Do not import backend-only ORM implementation into browser, mobile, desktop-client, or edge bundles.\n"
    )
    return f"""# H/HAUS repository rules

Read and follow `https://github.com/ORESoftware/my-ai/blob/main/AGENTS.md` and its `original-agents.md` companion before non-trivial work.

- Work on a feature branch and land through a pull request; never rewrite shared history.
- Resolve conflicts semantically after reading both sides and relevant commit history.
- Use `zed-pkg` for cross-repository dependency orchestration.
- Keep TypeSpec and JSON Schema Draft 2020-12 as independent peer authorities and fail closed on semantic drift.
- Keep Diesel and SeaORM projections independent and fail closed on parity drift.
- Route server requests through `ores-middleware`, `ores-otel`, `shared-auth`, and the explicit `ores-rate-limit` policy.
{backend_rule}- Never commit credentials, `.env` files, tokens, private keys, generated secrets, or raw personal identifiers in telemetry.
- State machines and cache/failure policies require exhaustive tests; do not add wildcard fallthrough that hides a new state.
"""


def common_security() -> str:
    return """# Security

Report vulnerabilities privately to the H/HAUS maintainers. Do not open a public issue containing credentials, private applicant data, access tokens, signed-upload URLs, raw IP addresses, or exploit details.

Secrets are injected by approved secret managers only. Logs and traces must use request IDs and pseudonymous subjects; raw authentication credentials and direct personal identifiers are forbidden.
"""


def repository_contract(manifest: dict[str, Any], repo: dict[str, Any], digest: str) -> str:
    value = {
        "schema_version": 1,
        "organization": EXPECTED_ORGANIZATION,
        "repository": repo["name"],
        "kind": repo["kind"],
        "description": repo["description"],
        "manifest_digest": digest,
        "backend_only": repo["backend_only"],
        "internal_dependencies": repo["internal_dependencies"],
        "backend_internal_dependencies": repo["backend_internal_dependencies"],
        "external_dependencies": repo["external_dependencies"],
        "contract_authorities": manifest["contract_authorities"],
        "rate_limit_layers": manifest["required_rate_limit_layers"],
        "middleware_order": [
            "cloudflare-edge",
            "gateway-load-balancer",
            "ores-middleware-ingress",
            "ores-otel-request-context",
            "shared-auth",
            "ores-rate-limit-service-runtime-lru",
            "ores-rate-limit-distributed-redis-coordinator",
            "ores-rate-limit-durable-security-billing",
            "handler",
            "ores-middleware-response",
        ],
    }
    if repo["kind"] in {"interfaces", "clients"}:
        value["language_targets"] = manifest["language_targets"]
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def generic_verify_script() -> str:
    return r'''#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_LAYERS = [
    "cloudflare-edge",
    "gateway-load-balancer",
    "service-runtime-lru",
    "distributed-redis-coordinator",
    "durable-security-billing",
]
SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"lin_api_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]


def fail(message: str) -> None:
    raise SystemExit(message)


def main() -> None:
    contract = json.loads((ROOT / "repository.contract.json").read_text(encoding="utf-8"))
    if contract["organization"] != "hhaus-org":
        fail("wrong organization")
    if contract["rate_limit_layers"] != EXPECTED_LAYERS:
        fail("rate-limit layer order drifted")
    if contract["contract_authorities"]["typespec"]["role"] != "independent-peer-authority":
        fail("TypeSpec authority drifted")
    if contract["contract_authorities"]["json_schema"]["draft"] != "2020-12":
        fail("JSON Schema draft drifted")
    with (ROOT / ".zpkg.toml").open("rb") as handle:
        zpkg = tomllib.load(handle)
    if zpkg["package"]["org"] != "hhaus-org":
        fail("zed-pkg package organization drifted")
    if contract["kind"] in {"interfaces", "clients"}:
        targets = contract.get("language_targets", [])
        if len(targets) < 15 or len(targets) != len(set(targets)):
            fail("language target matrix is incomplete")
    if not contract["backend_only"]:
        direct = set(contract["internal_dependencies"])
        if "hhaus-orm-core" in direct:
            fail("backend-only ORM leaked into a public dependency surface")
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if re.search(r"(?m)^(?:<{7}|={7}|>{7})", text):
            fail(f"conflict marker in {path.relative_to(ROOT)}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                fail(f"credential-shaped content in {path.relative_to(ROOT)}")
    print(json.dumps({"repository": contract["repository"], "status": "ok"}, sort_keys=True))


if __name__ == "__main__":
    main()
'''
