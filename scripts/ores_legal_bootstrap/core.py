from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable, Mapping

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "repository-fleets" / "ores-legal.json"
EXPECTED_ORGANIZATION = "ores-legal"
EXPECTED_TEST_ORGANIZATION = "ores-legal-test"
EXPECTED_LINEAR_ISSUE = "DEN-319"
EXPECTED_FEATURE_BRANCH = "alexanderdmills/den-319-bootstrap-ores-legal-fleet"
EXPECTED_REPOSITORIES = (
    "ores-legal-interfaces",
    "ores-legal-lib-core",
    "ores-legal-pub-lib-core",
    "ores-legal-orm-core",
    "ores-legal-clients",
    "ores-legal-api-server.rs",
    "ores-legal-admin-api-server.rs",
    "ores-legal-web-server.rs",
    "ores-legal-admin-web-server.rs",
    "ores-legal-flutter",
    "ores-legal-desktop-app.rs",
    "ores-legal-lambdas",
    "ores-legal-infra",
    "ores-legal-docs",
    "ores-legal-mcp-server.rs",
    "ores-legal.github.io",
    "ores-legal-monorepo",
)
EXPECTED_PUBLIC_REPOSITORIES = frozenset(
    {
        "ores-legal-interfaces",
        "ores-legal-clients",
        "ores-legal-pub-lib-core",
        "ores-legal-docs",
        "ores-legal.github.io",
    }
)
EXPECTED_LANGUAGE_TARGETS = (
    "rust",
    "typescript",
    "javascript",
    "dart",
    "kotlin",
    "swift",
    "java",
    "csharp",
    "go",
    "python",
    "ruby",
    "php",
    "c",
    "cpp",
    "objective-c",
    "scala",
    "elixir",
    "gleam",
    "haskell",
    "clojure",
)
EXPECTED_KINDS = {
    "interfaces",
    "lib-core",
    "pub-lib-core",
    "orm-core",
    "clients",
    "api-server",
    "admin-api-server",
    "web-server",
    "admin-web-server",
    "flutter",
    "desktop-app",
    "lambdas",
    "infra",
    "docs",
    "mcp-server",
    "marketing-site",
    "monorepo",
}
EXPECTED_PARITY_PLATFORMS = (
    "DocuSign",
    "Dropbox Sign",
    "Adobe Acrobat Sign",
    "PandaDoc",
    "signNow",
    "Zoho Sign",
    "OneSpan Sign",
    "Yousign",
)
FLAGS2ENV_REVISION = "2310d349cb87dfe7eac88ac67677e029c3e13167"
TOPICS = (
    "electronic-signatures",
    "document-signing",
    "legaltech",
    "rust",
    "ores-legal",
)


class BootstrapError(RuntimeError):
    """Raised when a reviewed repository-fleet invariant is violated."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def sha256_hex(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise BootstrapError(f"manifest does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise BootstrapError(f"manifest is invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise BootstrapError("manifest must be a JSON object")
    return value


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(manifest))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BootstrapError(message)


def _is_plain_repository_name(name: Any) -> bool:
    return isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", name) is not None and ".." not in name


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    _require(manifest.get("schema_version") == 1, "manifest schema_version must be 1")
    _require(manifest.get("organization") == EXPECTED_ORGANIZATION, "organization scope must be ores-legal")
    _require(manifest.get("test_organization") == EXPECTED_TEST_ORGANIZATION, "test organization must be ores-legal-test")
    _require(manifest.get("linear_issue") == EXPECTED_LINEAR_ISSUE, "Linear issue must remain DEN-319")

    governance = manifest.get("governance")
    _require(isinstance(governance, Mapping), "governance must be an object")
    _require(governance.get("default_branch") == "main", "default branch must be main")
    _require(governance.get("feature_branch") == EXPECTED_FEATURE_BRANCH, "feature branch drifted")
    _require(governance.get("merge_method") == "squash", "bootstrap merge method must be squash")
    _require(governance.get("delete_branch_on_merge") is True, "bootstrap branches must be deleted after merge")
    _require(governance.get("default_visibility") == "private", "default visibility must remain private")
    public_values = governance.get("public_repositories")
    _require(isinstance(public_values, list), "public_repositories must be a list")
    _require(set(public_values) == EXPECTED_PUBLIC_REPOSITORIES, "public repository allowlist drifted")

    authorities = manifest.get("contract_authorities")
    _require(isinstance(authorities, Mapping), "contract_authorities must be an object")
    _require(authorities.get("typespec", {}).get("role") == "independent-peer-authority", "TypeSpec must remain an independent authority")
    json_schema = authorities.get("json_schema", {})
    _require(json_schema.get("role") == "independent-peer-authority", "JSON Schema must remain an independent authority")
    _require(json_schema.get("draft") == "2020-12", "JSON Schema must use Draft 2020-12")
    _require(authorities.get("protobuf", {}).get("role") == "wire-contract-authority", "Protobuf must remain the wire authority")

    languages = manifest.get("language_targets")
    _require(languages == list(EXPECTED_LANGUAGE_TARGETS), "language target matrix must remain the reviewed twenty-language list")

    storage = manifest.get("storage")
    _require(isinstance(storage, Mapping), "storage must be an object")
    _require(storage.get("neon", {}).get("role") == "primary-transactional-postgres", "Neon must remain primary transactional Postgres")
    _require(storage.get("neon", {}).get("tenant_column") == "tenant_id", "Neon tenant column must be tenant_id")
    supabase = storage.get("supabase", {})
    _require(supabase.get("organization") == "oresoftware", "Supabase must remain under the reviewed shared-org exception")
    _require(supabase.get("canonical_schema") == "ores_legal_canonical", "canonical Supabase schema drifted")
    _require(supabase.get("auth_schema") == "ores_legal_auth", "auth Supabase schema drifted")
    client_storage = storage.get("client_owned_location", {})
    _require(client_storage.get("required") is True, "client-owned storage replication must remain required")
    _require(client_storage.get("routing_key") == "tenant_storage_profile_id", "tenant storage routing key drifted")

    parity = manifest.get("parity_reference_platforms")
    _require(parity == list(EXPECTED_PARITY_PLATFORMS), "feature-parity reference set drifted")

    repositories = manifest.get("repositories")
    _require(isinstance(repositories, list), "repositories must be a list")
    names = tuple(row.get("name") for row in repositories if isinstance(row, Mapping))
    _require(names == EXPECTED_REPOSITORIES, "repository inventory or dependency order drifted")
    _require(names[-1] == "ores-legal-monorepo", "monorepo must publish last")
    for row in repositories:
        _require(isinstance(row, Mapping), "each repository record must be an object")
        name = row.get("name")
        _require(_is_plain_repository_name(name), f"unsafe repository name: {name!r}")
        _require(row.get("kind") in EXPECTED_KINDS, f"unsupported kind for {name}")
        expected_visibility = "public" if name in EXPECTED_PUBLIC_REPOSITORIES else "private"
        _require(row.get("visibility") == expected_visibility, f"visibility drift for {name}")
        description = row.get("description")
        _require(isinstance(description, str) and 20 <= len(description) <= 350, f"description is invalid for {name}")


def repository_spec(manifest: Mapping[str, Any], name: str) -> dict[str, Any]:
    validate_manifest(manifest)
    for row in manifest["repositories"]:
        if row["name"] == name:
            return dict(row)
    raise BootstrapError(f"repository is outside the reviewed fleet: {name}")


def _json(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _readme(manifest: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    kind = spec["kind"]
    responsibilities = {
        "interfaces": "Defines independent TypeSpec and JSON Schema authorities plus Protobuf/OpenAPI transport contracts.",
        "lib-core": "Owns internal signing-domain state, evidence hashing, RuntimeConfig, and server-safe workflow types.",
        "pub-lib-core": "Exposes the deliberately small public Rust integration surface without private persistence internals.",
        "orm-core": "Owns private Diesel/SeaORM models, migrations, tenant isolation, outbox delivery, and immutable evidence constraints.",
        "clients": "Publishes governed client/server SDK foundations for the reviewed twenty-language matrix.",
        "api-server": "Accepts customer and integration writes for templates, envelopes, ceremonies, signatures, and webhooks.",
        "admin-api-server": "Provides privileged platform/tenant administration behind explicit authorization boundaries.",
        "web-server": "Serves HTML-first signer and customer experiences with MASH, Leptos, and Dioxus component parity contracts.",
        "admin-web-server": "Serves tenant and platform administration experiences without mixing customer signing routes.",
        "flutter": "Delivers mobile web, iOS, Android, and desktop Flutter signing clients and shared field widgets.",
        "desktop-app": "Delivers a native Rust/Dioxus desktop signing client without a React dependency.",
        "lambdas": "Contains portable provider workers for webhooks, reminders, evidence sealing, and tenant replication.",
        "infra": "Contains top-level Neon and Supabase IaC plus reviewed tenant/runtime configuration boundaries.",
        "docs": "Records architecture, compliance boundaries, threats, feature-parity gaps, and counsel-review templates.",
        "mcp-server": "Exposes policy-bounded template, envelope, signing, status, and evidence tools over MCP.",
        "marketing-site": "Publishes the responsive Astro organization site and honest feature-parity roadmap.",
        "monorepo": "Composes sibling repositories as real apps/ git submodules after their reviewed main commits exist.",
    }
    visibility = spec["visibility"]
    return f"""# {spec['name']}

{spec['description']}

## Boundary

{responsibilities[kind]}

- Organization: `{manifest['organization']}`
- Linear work item: `{manifest['linear_issue']}`
- Visibility: `{visibility}`
- Default branch: `main`
- Canonical governance: <{manifest['governance']['agents']}>

## Security posture

Documents, signatures, initials, consent receipts, identity evidence, audit events,
webhook secrets, and tenant storage credentials are confidential. Never commit real
customer material or credentials. Signature images alone are not evidence: completion
must bind document hashes, signer identity references, consent, field values, UTC
timestamps, authentication method references, audit-event hashes, and storage receipts.

## Status

This is an initial, compile-checked platform foundation. It intentionally does **not**
claim completed commercial parity with DocuSign, Dropbox Sign, Adobe Acrobat Sign, or
other reference platforms. See the generated feature-parity matrix and issue roadmap
before making production or legal-compliance claims.
"""


def _agents(manifest: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return f"""# Repository agent instructions

These instructions apply to the entire `{spec['name']}` repository.

1. Follow the canonical portfolio policy at `{manifest['governance']['agents']}`.
2. Track this bootstrap under `{manifest['linear_issue']}` and keep issue IDs in branches and PR titles.
3. Preserve `main` as the default branch. Merge commits and rebases are not publication defaults.
4. TypeSpec and JSON Schema Draft 2020-12 are independent peer authorities; compare normalized business declarations before release.
5. Public/external contracts and private/internal contracts must stay visibly separated.
6. Every executable parses the repository-root `.cli-flags.toml` through `flags2env`; secrets remain in secret managers.
7. All persistent signing records include `tenant_id`; storage routing uses `tenant_storage_profile_id`.
8. Audit and evidence records are append-only. Corrections create new linked events; never rewrite history.
9. Generated folders require a generated-code README and are not hand-edited.
10. Run `python3 scripts/verify.py` plus the repository-native format, lint, test, and build commands before merge.
11. Do not claim jurisdictional compliance or production parity without recorded evidence and counsel/security approval.
12. Ignore `.ores/`, local environment files, caches, credentials, signatures, customer documents, and build output.
"""


def _gitignore() -> str:
    return """.ores/
.env
.env.*
!.env.example
*.pem
*.key
*.p12
*.pfx
*.mobileprovision
*.jks
*.keystore
*.signature
*.signed.pdf
.DS_Store
.idea/
.vscode/
target/
build/
dist/
.dart_tool/
.flutter-plugins
.flutter-plugins-dependencies
node_modules/
coverage/
__pycache__/
*.pyc
"""


def _repository_contract(manifest: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "organization": manifest["organization"],
        "repository": spec["name"],
        "kind": spec["kind"],
        "visibility": spec["visibility"],
        "linear_issue": manifest["linear_issue"],
        "default_branch": "main",
        "typespec_authority": "independent-peer-authority",
        "json_schema_authority": "independent-peer-authority",
        "json_schema_draft": "2020-12",
        "tenant_column": "tenant_id",
        "tenant_storage_routing_key": "tenant_storage_profile_id",
        "manifest_digest": manifest_digest(manifest),
    }


def _verifier(spec: Mapping[str, Any], required_files: Iterable[str], required_terms: Mapping[str, Iterable[str]]) -> str:
    paths = list(dict.fromkeys(["README.md", "AGENTS.md", "repository.contract.json", *required_files]))
    serial_terms = {path: list(terms) for path, terms in required_terms.items()}
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = {spec["name"]!r}
REQUIRED_PATHS = {paths!r}
REQUIRED_TERMS = {serial_terms!r}
BANNED_SECRET_FRAGMENTS = ("BEGIN PRIVATE KEY", "ghp_", "github_pat_", "sk_live_", "AKIA")


def verify() -> None:
    missing = [path for path in REQUIRED_PATHS if not (ROOT / path).exists()]
    if missing:
        raise SystemExit(f"{{REPOSITORY}} missing required paths: {{missing}}")
    contract = json.loads((ROOT / "repository.contract.json").read_text(encoding="utf-8"))
    if contract.get("repository") != REPOSITORY:
        raise SystemExit("repository.contract.json points to a different repository")
    if contract.get("organization") != "ores-legal":
        raise SystemExit("repository.contract.json points outside ores-legal")
    for path, terms in REQUIRED_TERMS.items():
        value = (ROOT / path).read_text(encoding="utf-8")
        for term in terms:
            if term not in value:
                raise SystemExit(f"{{path}} is missing required term {{term!r}}")
    left, middle, right = "<" * 7, "=" * 7, ">" * 7
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            value = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if left in value or middle in value or right in value:
            raise SystemExit(f"conflict marker in {{path.relative_to(ROOT)}}")
        for fragment in BANNED_SECRET_FRAGMENTS:
            if fragment in value:
                raise SystemExit(f"secret-like material in {{path.relative_to(ROOT)}}")
    print(f"verified {{REPOSITORY}}")


if __name__ == "__main__":
    verify()
'''


def _common_files(manifest: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, str]:
    return {
        ".gitignore": _gitignore(),
        "AGENTS.md": _agents(manifest, spec),
        "README.md": _readme(manifest, spec),
        "repository.contract.json": _json(_repository_contract(manifest, spec)),
    }


def _flags_contract(command: str, *, default_bind: str | None = None, extra: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    flags: dict[str, Any] = {
        "log-level": {
            "env": "RUST_LOG",
            "type": "string",
            "description": "Tracing filter; credentials and document bodies must not be logged.",
            "default": "info,hyper=warn,tower_http=info",
        },
        "public-base-url": {
            "env": "ORES_LEGAL_PUBLIC_BASE_URL",
            "type": "string",
            "description": "Externally visible base URL used in signing links and callback validation.",
            "default": "http://127.0.0.1:8080",
        },
        "json-logs": {
            "env": "ORES_LEGAL_JSON_LOGS",
            "type": "bool",
            "description": "Emit structured JSON logs.",
            "default": True,
        },
    }
    if default_bind is not None:
        flags["bind"] = {
            "env": "ORES_LEGAL_BIND",
            "type": "string",
            "description": "Socket address to bind.",
            "default": default_bind,
        }
    if extra:
        flags.update(extra)
    ordered = ["[meta]", 'schema = "https://flags2env.dev/schema/v1"', "", f"[commands.{command}]", f'description = "Run {command}"', ""]
    for name, settings in flags.items():
        ordered.extend(
            [
                f"[commands.{command}.flags.{name}]",
                f'env = "{settings["env"]}"',
                f'type = "{settings["type"]}"',
                f'description = "{settings["description"]}"',
                f"default = {json.dumps(settings['default'])}",
                "",
            ]
        )
    return "\n".join(ordered)


def _runtime_config_rs(command: str, *, has_bind: bool = True, extra: Iterable[str] = ()) -> str:
    fields = [
        "pub public_base_url: String,",
        "pub json_logs: bool,",
        "pub rust_log: String,",
    ]
    assignments = [
        'public_base_url: required("ORES_LEGAL_PUBLIC_BASE_URL")?,',
        'json_logs: boolean("ORES_LEGAL_JSON_LOGS", true)?,',
        'rust_log: required("RUST_LOG")?,',
    ]
    if has_bind:
        fields.insert(0, "pub bind: SocketAddr,")
        assignments.insert(0, 'bind: required("ORES_LEGAL_BIND")?.parse().map_err(|error| format!("invalid ORES_LEGAL_BIND: {error}"))?,')
    for name in extra:
        fields.append(f"pub {name}: String,")
        assignments.append(f'{name}: required("ORES_LEGAL_{name.upper()}")?,')
    socket_import = "use std::net::SocketAddr;\n" if has_bind else ""
    return f'''use std::env;
{socket_import}
#[derive(Clone, Debug)]
pub struct RuntimeConfig {{
    {chr(10).join(fields)}
}}

impl RuntimeConfig {{
    pub fn from_flags() -> Result<Self, String> {{
        Ok(Self {{
            {chr(10).join(assignments)}
        }})
    }}
}}

fn required(name: &str) -> Result<String, String> {{
    env::var(name).map_err(|_| format!("{{name}} was not populated by flags2env for {command}"))
}}

fn boolean(name: &str, default: bool) -> Result<bool, String> {{
    match env::var(name) {{
        Ok(value) => value.parse().map_err(|error| format!("invalid {{name}}: {{error}}")),
        Err(_) => Ok(default),
    }}
}}
'''


def _rust_cargo(name: str, dependencies: Mapping[str, str], *, features: Mapping[str, Iterable[str]] | None = None) -> str:
    lines = [
        "[package]",
        f'name = "{name}"',
        'version = "0.1.0"',
        'edition = "2021"',
        f'description = "Initial ores-legal foundation for {name}"',
        'license = "Apache-2.0"',
        "publish = false",
        "",
    ]
    if features:
        lines.extend(["[features]", 'default = []'])
        for key, values in features.items():
            lines.append(f'{key} = [{", ".join(json.dumps(value) for value in values)}]')
        lines.append("")
    lines.append("[dependencies]")
    for dependency, value in dependencies.items():
        lines.append(f"{dependency} = {value}")
    lines.extend(
        [
            "",
            "[lints.rust]",
            'unsafe_code = "forbid"',
            "",
            "[lints.clippy]",
            'all = "deny"',
            'pedantic = "warn"',
        ]
    )
    return "\n".join(lines) + "\n"


def _rust_bin_common(
    manifest: Mapping[str, Any],
    spec: Mapping[str, Any],
    command: str,
    dependencies: Mapping[str, str],
    *,
    main_rs: str,
    runtime_config: str,
    extra_files: Mapping[str, str] | None = None,
    flags_extra: Mapping[str, Mapping[str, Any]] | None = None,
    default_bind: str | None = "127.0.0.1:8080",
    features: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, str]:
    files = _common_files(manifest, spec)
    deps = {
        "flags2env": f'{{ git = "https://github.com/flags-2-env/flags-2-env", rev = "{FLAGS2ENV_REVISION}", package = "flags2env" }}',
        **dependencies,
    }
    files.update(
        {
            ".cli-flags.toml": _flags_contract(command, default_bind=default_bind, extra=flags_extra),
            "Cargo.toml": _rust_cargo(spec["name"].replace(".rs", "").replace(".", "-"), deps, features=features),
            "src/main.rs": main_rs,
            "src/runtime_config.rs": runtime_config,
        }
    )
    if extra_files:
        files.update(extra_files)
    required = [".cli-flags.toml", "Cargo.toml", "src/main.rs", "src/runtime_config.rs"]
    files["scripts/verify.py"] = _verifier(
        spec,
        required,
        {
            "Cargo.toml": ["flags2env", FLAGS2ENV_REVISION],
            "src/main.rs": ["parse_or_exit", "RuntimeConfig"],
            ".cli-flags.toml": [f"[commands.{command}]", "ORES_LEGAL_PUBLIC_BASE_URL"],
        },
    )
    return files


def seed_files(manifest: Mapping[str, Any], repository_name: str) -> dict[str, str]:
    validate_manifest(manifest)
    spec = repository_spec(manifest, repository_name)
    from . import scaffolds

    renderer = scaffolds.RENDERERS.get(spec["kind"])
    if renderer is None:
        raise BootstrapError(f"no renderer for {spec['kind']}")
    files = renderer(manifest, spec)
    for path, content in files.items():
        if Path(path).is_absolute() or ".." in Path(path).parts or "\\" in path:
            raise BootstrapError(f"unsafe generated path for {repository_name}: {path}")
        if not isinstance(content, str):
            raise BootstrapError(f"generated content must be text: {repository_name}/{path}")
    if "scripts/verify.py" not in files:
        raise BootstrapError(f"renderer omitted scripts/verify.py: {repository_name}")
    return dict(sorted(files.items()))


def render_repository(manifest: Mapping[str, Any], repository_name: str, destination: Path) -> dict[str, Any]:
    files = seed_files(manifest, repository_name)
    target = destination / repository_name
    if target.exists():
        shutil.rmtree(target)
    for relative, content in files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        if relative.startswith("scripts/") and relative.endswith(".py"):
            path.chmod(0o755)
    digest = sha256_hex(
        b"".join(
            relative.encode("utf-8") + b"\0" + content.encode("utf-8") + b"\0"
            for relative, content in files.items()
        )
    )
    return {"repository": repository_name, "files": len(files), "digest": digest}


def render_all(manifest: Mapping[str, Any], destination: Path) -> dict[str, Any]:
    validate_manifest(manifest)
    destination.mkdir(parents=True, exist_ok=True)
    records = [render_repository(manifest, name, destination) for name in EXPECTED_REPOSITORIES]
    ledger = {
        "organization": EXPECTED_ORGANIZATION,
        "manifest_digest": manifest_digest(manifest),
        "repositories": records,
        "files": sum(record["files"] for record in records),
        "tree_digest": sha256_hex(canonical_json_bytes(records)),
    }
    (destination / "render-ledger.json").write_bytes(canonical_json_bytes(ledger))
    return ledger


__all__ = [
    "BootstrapError",
    "EXPECTED_FEATURE_BRANCH",
    "EXPECTED_KINDS",
    "EXPECTED_LANGUAGE_TARGETS",
    "EXPECTED_LINEAR_ISSUE",
    "EXPECTED_ORGANIZATION",
    "EXPECTED_PARITY_PLATFORMS",
    "EXPECTED_PUBLIC_REPOSITORIES",
    "EXPECTED_REPOSITORIES",
    "FLAGS2ENV_REVISION",
    "MANIFEST_PATH",
    "TOPICS",
    "canonical_json_bytes",
    "load_manifest",
    "manifest_digest",
    "render_all",
    "render_repository",
    "repository_spec",
    "seed_files",
    "sha256_hex",
    "validate_manifest",
    "_common_files",
    "_flags_contract",
    "_json",
    "_runtime_config_rs",
    "_rust_bin_common",
    "_rust_cargo",
    "_verifier",
]
