#!/usr/bin/env python3
"""Generate isolated admin-web and admin-api servers for product orgs.

The public *-web-server.rs / *-api-server.rs processes stay off the admin
RDS. Super-admins reach these servers only through the admin VPC.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CODES = Path("/Users/maca5/codes")

# Product orgs that already ship *-web-server.rs and *-api-server.rs.
# prefix matches the existing repo abbreviation (hnpt, ecmad, …).
ORGS: list[dict[str, str]] = [
    {"org": "3FA-app", "prefix": "3fa", "title": "3FA"},
    {"org": "anticaptrad", "prefix": "act", "title": "Anticaptrad"},
    {"org": "athlet-o", "prefix": "athleto", "title": "Athlet-O"},
    {"org": "canonical-cloud", "prefix": "canonical", "title": "Canonical Cloud"},
    {"org": "chapter-publishing", "prefix": "chptr", "title": "Chapter Publishing"},
    {"org": "cliptown", "prefix": "cliptown", "title": "Cliptown"},
    {"org": "daedalus-fab", "prefix": "daedalus", "title": "Daedalus Fab"},
    {"org": "declarative-migrations", "prefix": "declmig", "title": "Declarative Migrations"},
    {"org": "ecma-d", "prefix": "ecmad", "title": "ECMA-D"},
    {"org": "elenkos-systems", "prefix": "elenkos", "title": "Elenkos Systems"},
    {"org": "embedded-alerts", "prefix": "eal", "title": "Embedded Alerts"},
    {"org": "evento-globolo", "prefix": "evgl", "title": "Evento Globolo"},
    {"org": "fanwaave", "prefix": "fanwaave", "title": "Fanwaave"},
    {"org": "file-tunnel", "prefix": "ftnl", "title": "File Tunnel"},
    {"org": "gha-indie-worker", "prefix": "gha-indie-worker", "title": "GHA Indie Worker"},
    {"org": "hacker-house-medellin", "prefix": "hhm", "title": "Hacker House Medellin"},
    {"org": "happy-wakey", "prefix": "happy-wakey", "title": "Happy Wakey"},
    {"org": "honeypot-r-us", "prefix": "hnpt", "title": "Honeypot R Us"},
    {"org": "hypesiege", "prefix": "hypesiege", "title": "Hypesiege"},
    {"org": "led-dynamo", "prefix": "leddy", "title": "LED Dynamo"},
    {"org": "memebank", "prefix": "memebank", "title": "Memebank"},
    {"org": "opto-sync", "prefix": "opto-sync", "title": "Opto Sync"},
    {"org": "ores-otel", "prefix": "ores-otel", "title": "Ores OTEL"},
    {"org": "praxonne", "prefix": "praxonne", "title": "Praxonne"},
    {"org": "premarital-asset-protection", "prefix": "pmap", "title": "Premarital Asset Protection"},
    {"org": "quaestor-ledger", "prefix": "quaestor", "title": "Quaestor Ledger"},
    {"org": "scintilla-run", "prefix": "scintilla", "title": "Scintilla Run"},
    {"org": "sonus-auris", "prefix": "sonus-auris", "title": "Sonus Auris"},
    {"org": "voxletra", "prefix": "vxl", "title": "Voxletra"},
    {"org": "akrion-sim", "prefix": "akrion", "title": "Akrion Sim"},
]

# Existing package names that do not follow `{prefix}-lib-core`.
LIB_CORE = {
    "ores-otel": "ores-lib-core",
}
ORM_CORE = {
    "memebank": "mbk-orm-core",
    "akrion-sim": "akrion-sim-orm-core",
}

SKIP_FULL_GENERATE = {
    # Domain-specific harvest admin already exists; only add zed-pkg manifests.
    ("messaging-intel", "msgint-admin-web-server.rs"),
    ("messaging-intel", "msgint-admin-api-server.rs"),
}


def env_prefix(prefix: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", prefix).upper().strip("_")
    if cleaned[0].isdigit():
        return f"ORG_{cleaned}"
    return cleaned


def rust_ident(name: str) -> str:
    ident = re.sub(r"[^A-Za-z0-9]+", "_", name)
    if ident[0].isdigit():
        ident = f"org_{ident}"
    return ident


def write(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not contents.endswith("\n"):
        contents += "\n"
    path.write_text(contents)


def zpkg_deps(org: str, prefix: str) -> str:
    lib_core = LIB_CORE.get(org, f"{prefix}-lib-core")
    orm_core = ORM_CORE.get(org, f"{prefix}-orm-core")
    return "\n".join(
        [
            f'"{org}/{lib_core}" = "^0.1.0"',
            f'"{org}/{orm_core}" = "^0.1.0"',
            '"shared-auth/shared-auth-clients" = "^0.1.0"',
            '"oresoftware/next-loggers-rust" = "^0.1.0"',
            '"oresoftware/k8s-libs-and-shared-defs" = "^0.1.0"',
        ]
    )


def zpkg_toml(
    org: str,
    prefix: str,
    pkg_name: str,
    repo: str,
    description: str,
    targets: str,
) -> str:
    return f"""[package]
org = "{org}"
name = "{pkg_name}"
version = "0.1.0"
description = "{description}"
license = "UNLICENSED"
keywords = ["admin", "vpc", "shared-auth", "zed-pkg", "seaorm"]

[package.repository]
vcs = "git"
url = "https://github.com/{org}/{repo}"

[install]
dir = ".vendor/.zed"

[dependencies]
{zpkg_deps(org, prefix)}

[publish]
include_readme = true
tag_format = "v{{version}}"
exclude = [
  ".env",
  ".env.*",
  ".direnv/**",
  ".vendor/.zed/**",
  ".zed/**",
  "target/**",
  "**/target/**",
  "**/node_modules/**",
  "tmp/**",
  "**/*.log",
]

{targets}

[scripts]
test = "cargo test --locked --all-targets"
"""


def gitignore() -> str:
    return """/target
**/target
.env
.env.*
*.env
.vendor/.zed
.zed
.direnv
tmp/
**/*.log
.DS_Store
"""


def rust_toolchain() -> str:
    return """[toolchain]
channel = "1.88.0"
components = ["rustfmt", "clippy"]
"""


def ci_yml(workspace: bool) -> str:
    cmd = "cargo test --workspace --all-targets" if workspace else "cargo test --all-targets"
    return f"""name: ci
on:
  pull_request:
  push:
    branches: [main]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@1.88.0
        with:
          components: rustfmt, clippy
      - run: cargo fmt --all -- --check
      - run: cargo clippy --all-targets -- -D warnings
      - run: {cmd}
"""


def agents_md(title: str, kind: str, prefix: str, env: str) -> str:
    other = "admin-api-server.rs" if kind == "web" else "admin-web-server.rs"
    return f"""# {prefix}-admin-{kind}-server.rs

Private super-admin {kind} for {title}. Not a public product surface.

## Isolation

- Bind loopback locally. In cluster, ClusterIP only inside `{prefix}-admin`.
- No Ingress, LoadBalancer, NodePort, or Cloudflare hostname.
- Public `*-web-server.rs` and `*-api-server.rs` must not receive
  `{env}_ADMIN_DATABASE_URL`. The admin RDS security group admits only this
  namespace's pods.
- Pair with `{prefix}-{other}` on the same admin VPC.

## Shared Auth (admin instance)

Customer-facing servers use `SHARED_AUTH_BASE`. These admin servers **must**
use a separate Shared Auth deployment:

- `SHARED_AUTH_ADMIN_BASE`
- `SHARED_AUTH_ADMIN_ISSUER`
- `SHARED_AUTH_ADMIN_AUDIENCE` (default `{prefix}-admin`)
- `SHARED_AUTH_ADMIN_INTROSPECT_SECRET`

Customer tokens fail closed (wrong issuer / audience). Do not point this
process at the public Shared Auth URL.

## Access control

- Every mutating route requires `super_admin` and an allow-listed actor.
- Fail closed. Prefer exhaustive matches. No `unsafe`.
- Never log tokens, cookies, or admin-db connection strings.

## zed-pkg

`.zpkg.toml` imports `*-lib-core`, `*-orm-core`, and `shared-auth-clients`.
Cargo stays on crates.io so CI can compile without a private vendor cache;
`zed install` wires the org cores when they exist.
"""


def web_readme(title: str, prefix: str, env: str) -> str:
    return f"""# {prefix}-admin-web-server.rs

Rust admin console for **{title}** super-admins.

This process is **not** `{prefix}-web-server.rs`. It lives on the admin VPC,
talks only to `{prefix}-admin-api-server.rs` and the admin RDS, and is
unreachable from the public internet.

## Frontends

The workspace serves all three UI lanes from one verified state:

| Lane | Stack | Default bind |
|---|---|---|
| mash | Maud + Axum + HTMX (+ SeaORM reads, Supabase auth hook) | `127.0.0.1:8788` |
| leptos | Leptos SSR + Axum | `127.0.0.1:8789` |
| dioxus | Dioxus SSR + Axum | `127.0.0.1:8790` |

React / JSX / webviews are out of scope.

## Shared Auth admin instance

Use `SHARED_AUTH_ADMIN_*`, never the customer Shared Auth instance. A mesh
sidecar introspects against that admin issuer and injects:

- `x-{prefix}-admin-role: super_admin`
- `x-{prefix}-admin-actor: <uuid>`

## Four ways this console reaches the admin API

1. Direct **read-only** SeaORM against `ADMIN_DATABASE_URL` (no migrations).
2. Stateless HTTPS to `{prefix}-admin-api-server.rs`.
3. Stateful TCP to the same admin-api group.
4. NATS / JetStream via the intermediaries in `oresoftware/k8s-cluster`.

## Run locally

```sh
{env}_ADMIN_ALLOWLIST=<uuid> \\
{env}_ADMIN_BIND=127.0.0.1:8788 \\
cargo run -p {prefix}-admin-mash
```

The process refuses a non-loopback bind unless `{env}_ADMIN_ALLOW_PUBLIC_BIND=1`,
which must never be set in production.
"""


def api_readme(title: str, prefix: str, env: str) -> str:
    return f"""# {prefix}-admin-api-server.rs

Isolated admin JSON API for **{title}**.

This service is **not** `{prefix}-api-server.rs`. It is the only process that
opens a write connection to the admin RDS (AWS RDS / Cockroach on the admin
VPC). Product web/api servers cannot route there: security groups and the
k8s NetworkPolicy default-deny them.

## What it does

- Super-admin tenant inventory and allow-list edits
- Append-only audit log (actor, action, resource — never secrets)
- Admin-db liveness that never leaks connection strings
- Capability document for the four web↔api interaction modes

## Auth

Shared Auth **admin instance** (`SHARED_AUTH_ADMIN_*`). Customer tokens from
`SHARED_AUTH_BASE` are rejected. Every mutating route requires `super_admin`
plus `{env}_ADMIN_ALLOWLIST`.

## Run locally

```sh
{env}_ADMIN_ALLOWLIST=<uuid> \\
{env}_ADMIN_BIND=127.0.0.1:8787 \\
cargo run
```

The process refuses a non-loopback bind unless `{env}_ADMIN_ALLOW_PUBLIC_BIND=1`,
which must never be set in production.
"""


def k8s_web(prefix: str) -> str:
    ns = f"{prefix}-admin"
    return f"""# Isolated admin plane. No public ingress.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {prefix}-admin-web-ingress
  namespace: {ns}
spec:
  podSelector:
    matchLabels:
      app: {prefix}-admin-web
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              name: {ns}
      ports:
        - protocol: TCP
          port: 8788
  egress:
    - to:
        - podSelector:
            matchLabels:
              app: {prefix}-admin-api
      ports:
        - protocol: TCP
          port: 8787
    - to:
        - namespaceSelector:
            matchLabels:
              name: {ns}
      ports:
        - protocol: TCP
          port: 443
---
apiVersion: v1
kind: Service
metadata:
  name: {prefix}-admin-web
  namespace: {ns}
spec:
  type: ClusterIP
  selector:
    app: {prefix}-admin-web
  ports:
    - name: http
      port: 8788
      targetPort: 8788
"""


def k8s_api(prefix: str) -> str:
    ns = f"{prefix}-admin"
    return f"""# Isolated admin plane. Only the admin web pod may call this API.
# Egress 5432 is the admin RDS — product servers are not in this namespace.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {prefix}-admin-api-ingress
  namespace: {ns}
spec:
  podSelector:
    matchLabels:
      app: {prefix}-admin-api
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: {prefix}-admin-web
      ports:
        - protocol: TCP
          port: 8787
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              name: {ns}
      ports:
        - protocol: TCP
          port: 5432
    - to:
        - namespaceSelector:
            matchLabels:
              name: {ns}
      ports:
        - protocol: TCP
          port: 443
---
apiVersion: v1
kind: Service
metadata:
  name: {prefix}-admin-api
  namespace: {ns}
spec:
  type: ClusterIP
  selector:
    app: {prefix}-admin-api
  ports:
    - name: http
      port: 8787
      targetPort: 8787
"""


def admin_sql(prefix: str) -> str:
    schema = rust_ident(prefix)
    return f"""-- Applied only on the isolated admin database. Product servers never see this.
create schema if not exists {schema}_admin;

create table if not exists {schema}_admin.tenants (
  id uuid primary key,
  slug text not null unique,
  display_name text not null,
  status text not null check (status in ('active', 'suspended', 'pending')),
  created_by uuid not null,
  created_at timestamptz not null default now()
);

create table if not exists {schema}_admin.actors (
  id uuid primary key,
  email text not null unique,
  role text not null check (role in ('super_admin', 'admin_read')),
  created_at timestamptz not null default now()
);

create table if not exists {schema}_admin.audit_events (
  id uuid primary key,
  actor_id uuid not null references {schema}_admin.actors(id),
  action text not null,
  resource text not null,
  created_at timestamptz not null default now()
);

create table if not exists {schema}_admin.allowlist (
  actor_id uuid primary key references {schema}_admin.actors(id),
  note text,
  added_at timestamptz not null default now()
);

create index if not exists audit_events_actor_created
  on {schema}_admin.audit_events (actor_id, created_at desc);
"""


def common_cargo(prefix: str) -> str:
    crate = f"{prefix}-admin-web-common"
    return f"""[package]
name = "{crate}"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
description = "Shared bind, RBAC, and admin-plane types for the three UI lanes"

[dependencies]
axum.workspace = true
serde.workspace = true
serde_json.workspace = true
thiserror.workspace = true
uuid.workspace = true
"""


def common_lib(prefix: str, env: str, title: str) -> str:
    role_header = f"x-{prefix}-admin-role"
    actor_header = f"x-{prefix}-admin-actor"
    customer_issuer_env = "SHARED_AUTH_BASE"
    return f'''#![forbid(unsafe_code)]

//! Shared admin-web plane: bind policy, Shared Auth admin-instance notes,
//! and fail-closed super-admin RBAC used by mash, leptos, and dioxus.

pub mod auth;
pub mod bind;
pub mod plane;

pub use auth::{{Actor, AuthError, require_super_admin}};
pub use bind::parse_bind;
pub use plane::{{AdminSnapshot, InteractionMode, PlaneCapabilities, TenantRow}};

pub const TITLE: &str = "{title}";
pub const PREFIX: &str = "{prefix}";
pub const ROLE_HEADER: &str = "{role_header}";
pub const ACTOR_HEADER: &str = "{actor_header}";
pub const ADMIN_AUDIENCE: &str = "{prefix}-admin";
pub const CUSTOMER_AUTH_ENV: &str = "{customer_issuer_env}";
pub const ADMIN_AUTH_BASE_ENV: &str = "SHARED_AUTH_ADMIN_BASE";
pub const ADMIN_AUTH_ISSUER_ENV: &str = "SHARED_AUTH_ADMIN_ISSUER";
pub const ADMIN_AUTH_AUDIENCE_ENV: &str = "SHARED_AUTH_ADMIN_AUDIENCE";
pub const ADMIN_DB_URL_ENV: &str = "{env}_ADMIN_DATABASE_URL";
pub const PRODUCT_DB_URL_ENV: &str = "DATABASE_URL";

#[derive(Clone, Debug)]
pub struct AppState {{
    pub allowlist: std::sync::Arc<Vec<uuid::Uuid>>,
    pub admin_issuer: String,
}}
'''


def common_bind() -> str:
    return r'''use std::net::{IpAddr, SocketAddr};

pub fn parse_bind(raw: &str, allow_public: bool) -> Result<SocketAddr, String> {
    let addr: SocketAddr = raw.parse().map_err(|_| "invalid bind address".to_string())?;
    if allow_public {
        return Ok(addr);
    }
    match addr.ip() {
        IpAddr::V4(ip) if ip.is_loopback() => Ok(addr),
        IpAddr::V6(ip) if ip.is_loopback() => Ok(addr),
        _ => Err("admin process refuses a non-loopback bind".to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::parse_bind;

    #[test]
    fn loopback_is_required_unless_explicitly_overridden() {
        assert!(parse_bind("127.0.0.1:8788", false).is_ok());
        assert!(parse_bind("[::1]:8788", false).is_ok());
        assert!(parse_bind("0.0.0.0:8788", false).is_err());
        assert!(parse_bind("10.0.0.4:8788", false).is_err());
        assert!(parse_bind("0.0.0.0:8788", true).is_ok());
    }
}
'''


def common_auth(prefix: str) -> str:
    return f'''use axum::http::{{HeaderMap, StatusCode}};
use thiserror::Error;
use uuid::Uuid;

use crate::{{ACTOR_HEADER, ROLE_HEADER}};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Actor {{
    pub id: Uuid,
}}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
pub enum AuthError {{
    #[error("unauthorized")]
    Unauthorized,
    #[error("forbidden")]
    Forbidden,
    #[error("customer shared-auth issuer is not accepted on the admin plane")]
    CustomerIssuer,
}}

impl From<AuthError> for StatusCode {{
    fn from(error: AuthError) -> Self {{
        match error {{
            AuthError::Unauthorized => StatusCode::UNAUTHORIZED,
            AuthError::Forbidden | AuthError::CustomerIssuer => StatusCode::FORBIDDEN,
        }}
    }}
}}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AdminRole {{
    SuperAdmin,
    AdminRead,
    Denied,
}}

pub fn classify_roles(roles: &str) -> AdminRole {{
    let tokens: Vec<&str> = roles
        .split(',')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .collect();
    if tokens.contains(&"super_admin") {{
        AdminRole::SuperAdmin
    }} else if tokens.contains(&"admin_read") {{
        AdminRole::AdminRead
    }} else {{
        AdminRole::Denied
    }}
}}

/// Customer Shared Auth and admin Shared Auth are different deployments.
/// A token whose issuer equals the customer base is rejected here.
pub fn reject_customer_issuer(token_issuer: &str, admin_issuer: &str) -> bool {{
    let token = token_issuer.trim().trim_end_matches('/');
    let admin = admin_issuer.trim().trim_end_matches('/');
    token.is_empty() || admin.is_empty() || token == admin
}}

pub fn require_super_admin(
    headers: &HeaderMap,
    allowlist: &[Uuid],
    admin_issuer: &str,
) -> Result<Actor, AuthError> {{
    if let Some(issuer) = header_str(headers, "x-shared-auth-issuer") {{
        if !reject_customer_issuer(issuer, admin_issuer) {{
            return Err(AuthError::CustomerIssuer);
        }}
    }}
    match classify_roles(header_str(headers, ROLE_HEADER).unwrap_or("")) {{
        AdminRole::SuperAdmin => {{}}
        AdminRole::AdminRead | AdminRole::Denied => return Err(AuthError::Forbidden),
    }}
    let actor = headers
        .get(ACTOR_HEADER)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| Uuid::parse_str(value.trim()).ok())
        .ok_or(AuthError::Unauthorized)?;
    if !allowlist.contains(&actor) {{
        return Err(AuthError::Forbidden);
    }}
    Ok(Actor {{ id: actor }})
}}

fn header_str<'a>(headers: &'a HeaderMap, name: &'static str) -> Option<&'a str> {{
    headers.get(name).and_then(|value| value.to_str().ok())
}}

#[cfg(test)]
mod tests {{
    use super::*;
    use axum::http::HeaderMap;

    fn actor() -> Uuid {{
        Uuid::parse_str("018f2ee1-3ef5-7f4b-9e27-494ce50e8c4f").unwrap()
    }}

    #[test]
    fn super_admin_must_be_allowlisted() {{
        let mut headers = HeaderMap::new();
        headers.insert(ROLE_HEADER, "super_admin".parse().unwrap());
        headers.insert(ACTOR_HEADER, actor().to_string().parse().unwrap());
        assert!(require_super_admin(&headers, &[actor()], "https://auth-admin.example").is_ok());
        assert_eq!(
            require_super_admin(&headers, &[], "https://auth-admin.example"),
            Err(AuthError::Forbidden)
        );
    }}

    #[test]
    fn customer_issuer_is_rejected() {{
        let mut headers = HeaderMap::new();
        headers.insert(ROLE_HEADER, "super_admin".parse().unwrap());
        headers.insert(ACTOR_HEADER, actor().to_string().parse().unwrap());
        headers.insert(
            "x-shared-auth-issuer",
            "https://auth.customer.example".parse().unwrap(),
        );
        assert_eq!(
            require_super_admin(&headers, &[actor()], "https://auth-admin.example"),
            Err(AuthError::CustomerIssuer)
        );
    }}

    #[test]
    fn read_role_cannot_mutate() {{
        assert_eq!(classify_roles("admin_read"), AdminRole::AdminRead);
        assert_eq!(classify_roles(""), AdminRole::Denied);
        assert_eq!(classify_roles("super_admin,admin_read"), AdminRole::SuperAdmin);
    }}
}}
'''


def common_plane(title: str) -> str:
    return f'''use serde::Serialize;
use uuid::Uuid;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionMode {{
    DirectReadOnlyDatabase,
    StatelessHttp,
    StatefulTcp,
    JetStreamAsync,
}}

impl InteractionMode {{
    pub const fn label(self) -> &'static str {{
        match self {{
            Self::DirectReadOnlyDatabase => "direct_db_read",
            Self::StatelessHttp => "stateless_https",
            Self::StatefulTcp => "stateful_tcp",
            Self::JetStreamAsync => "async_jetstream",
        }}
    }}
}}

#[derive(Clone, Debug, Serialize)]
pub struct PlaneCapabilities {{
    pub product: &'static str,
    pub modes: [InteractionMode; 4],
    pub admin_db_attached: bool,
    pub product_db_attached: bool,
}}

impl PlaneCapabilities {{
    pub fn isolated(admin_db_attached: bool, product_db_attached: bool) -> Self {{
        Self {{
            product: "{title}",
            modes: [
                InteractionMode::DirectReadOnlyDatabase,
                InteractionMode::StatelessHttp,
                InteractionMode::StatefulTcp,
                InteractionMode::JetStreamAsync,
            ],
            admin_db_attached,
            product_db_attached,
        }}
    }}
}}

#[derive(Clone, Debug, Serialize)]
pub struct TenantRow {{
    pub id: Uuid,
    pub slug: String,
    pub display_name: String,
    pub status: String,
}}

#[derive(Clone, Debug, Serialize)]
pub struct AdminSnapshot {{
    pub identity: String,
    pub capabilities: PlaneCapabilities,
    pub tenants: Vec<TenantRow>,
}}

pub fn empty_snapshot(identity: String, admin_db: bool) -> AdminSnapshot {{
    AdminSnapshot {{
        identity,
        capabilities: PlaneCapabilities::isolated(admin_db, false),
        tenants: Vec::new(),
    }}
}}

#[cfg(test)]
mod tests {{
    use super::*;

    #[test]
    fn product_db_flag_stays_false_on_the_admin_plane() {{
        let caps = PlaneCapabilities::isolated(true, false);
        assert!(caps.admin_db_attached);
        assert!(!caps.product_db_attached);
        assert_eq!(caps.modes.len(), 4);
    }}
}}
'''


def mash_cargo(prefix: str) -> str:
    return f'''[package]
name = "{prefix}-admin-mash"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[dependencies]
anyhow.workspace = true
axum.workspace = true
{prefix}-admin-web-common.workspace = true
maud = {{ version = "0.27", features = ["axum"] }}
tokio.workspace = true
tower-http.workspace = true
uuid.workspace = true
'''


def leptos_cargo(prefix: str) -> str:
    return f'''[package]
name = "{prefix}-admin-leptos"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[dependencies]
anyhow.workspace = true
axum.workspace = true
{prefix}-admin-web-common.workspace = true
leptos = {{ version = "0.8", features = ["ssr"] }}
tokio.workspace = true
tower-http.workspace = true
uuid.workspace = true
'''


def dioxus_cargo(prefix: str) -> str:
    return f'''[package]
name = "{prefix}-admin-dioxus"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[dependencies]
anyhow.workspace = true
axum.workspace = true
dioxus = {{ version = "0.7", default-features = false, features = ["macro", "html", "signals", "hooks"] }}
dioxus-ssr = "0.7"
{prefix}-admin-web-common.workspace = true
tokio.workspace = true
tower-http.workspace = true
uuid.workspace = true
'''


def frontend_main(
    prefix: str,
    env: str,
    lane: str,
    port: str,
    render_expr: str,
    extra_uses: str,
) -> str:
    ident = rust_ident(f"{prefix}-admin-web-common")
    return f'''use std::sync::Arc;

use anyhow::Context;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::{{Html, IntoResponse}};
use axum::routing::get;
use axum::Router;
use {ident}::{{
    empty_snapshot, parse_bind, require_super_admin, AppState, TITLE,
}};
use tower_http::trace::TraceLayer;
use uuid::Uuid;
{extra_uses}

#[tokio::main]
async fn main() -> anyhow::Result<()> {{
    let allow_public = std::env::var("{env}_ADMIN_ALLOW_PUBLIC_BIND").ok().as_deref() == Some("1");
    let bind = std::env::var("{env}_ADMIN_BIND").unwrap_or_else(|_| "127.0.0.1:{port}".into());
    let addr = parse_bind(&bind, allow_public).map_err(anyhow::Error::msg)?;
    let allowlist = std::env::var("{env}_ADMIN_ALLOWLIST")
        .unwrap_or_default()
        .split(',')
        .filter_map(|part| Uuid::parse_str(part.trim()).ok())
        .collect::<Vec<_>>();
    anyhow::ensure!(
        !allowlist.is_empty(),
        "{env}_ADMIN_ALLOWLIST must contain at least one super-admin UUID"
    );
    let admin_issuer = std::env::var("SHARED_AUTH_ADMIN_ISSUER")
        .unwrap_or_else(|_| "https://auth-admin.local".into());
    let state = AppState {{
        allowlist: Arc::new(allowlist),
        admin_issuer,
    }};
    let app = Router::new()
        .route("/healthz", get(|| async {{ "ok" }}))
        .route("/", get(home))
        .route("/tenants", get(tenants))
        .route("/audit", get(audit))
        .route("/plane", get(plane))
        .layer(TraceLayer::new_for_http())
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .context("listen")?;
    axum::serve(listener, app).await.context("serve")?;
    Ok(())
}}

async fn home(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    page(&state, &headers, "Home", {render_expr("home")})
}}

async fn tenants(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    page(&state, &headers, "Tenants", {render_expr("tenants")})
}}

async fn audit(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    page(&state, &headers, "Audit", {render_expr("audit")})
}}

async fn plane(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    page(&state, &headers, "Plane", {render_expr("plane")})
}}

fn page(
    state: &AppState,
    headers: &HeaderMap,
    title: &str,
    body: String,
) -> impl IntoResponse {{
    if let Err(error) = require_super_admin(headers, &state.allowlist, &state.admin_issuer) {{
        return axum::http::StatusCode::from(error).into_response();
    }}
    let _ = empty_snapshot(title.to_owned(), false);
    Html(body).into_response()
}}
'''


def mash_main(prefix: str, env: str, title: str) -> str:
    ident = rust_ident(f"{prefix}-admin-web-common")
    return f'''use std::sync::Arc;

use anyhow::Context;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::{{Html, IntoResponse}};
use axum::routing::get;
use axum::Router;
use {ident}::{{parse_bind, require_super_admin, AppState, TITLE}};
use maud::{{html, Markup, DOCTYPE}};
use tower_http::trace::TraceLayer;
use uuid::Uuid;

#[tokio::main]
async fn main() -> anyhow::Result<()> {{
    let allow_public = std::env::var("{env}_ADMIN_ALLOW_PUBLIC_BIND").ok().as_deref() == Some("1");
    let bind = std::env::var("{env}_ADMIN_BIND").unwrap_or_else(|_| "127.0.0.1:8788".into());
    let addr = parse_bind(&bind, allow_public).map_err(anyhow::Error::msg)?;
    let allowlist = load_allowlist()?;
    let admin_issuer = std::env::var("SHARED_AUTH_ADMIN_ISSUER")
        .unwrap_or_else(|_| "https://auth-admin.local".into());
    let state = AppState {{
        allowlist: Arc::new(allowlist),
        admin_issuer,
    }};
    let app = router(state);
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .context("listen")?;
    axum::serve(listener, app).await.context("serve")?;
    Ok(())
}}

fn load_allowlist() -> anyhow::Result<Vec<Uuid>> {{
    let allowlist = std::env::var("{env}_ADMIN_ALLOWLIST")
        .unwrap_or_default()
        .split(',')
        .filter_map(|part| Uuid::parse_str(part.trim()).ok())
        .collect::<Vec<_>>();
    anyhow::ensure!(
        !allowlist.is_empty(),
        "{env}_ADMIN_ALLOWLIST must contain at least one super-admin UUID"
    );
    Ok(allowlist)
}}

fn router(state: AppState) -> Router {{
    Router::new()
        .route("/healthz", get(|| async {{ "ok" }}))
        .route("/", get(home))
        .route("/tenants", get(tenants))
        .route("/audit", get(audit))
        .route("/plane", get(plane))
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}}

async fn home(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, page("Home", home_body()))
}}

async fn tenants(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, page("Tenants", tenants_body()))
}}

async fn audit(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, page("Audit", audit_body()))
}}

async fn plane(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, page("Plane", plane_body()))
}}

fn gated(state: &AppState, headers: &HeaderMap, markup: Markup) -> impl IntoResponse {{
    if let Err(error) = require_super_admin(headers, &state.allowlist, &state.admin_issuer) {{
        return axum::http::StatusCode::from(error).into_response();
    }}
    Html(markup.into_string()).into_response()
}}

fn page(title: &str, body: Markup) -> Markup {{
    html! {{
        (DOCTYPE)
        html lang="en" {{
            head {{
                meta charset="utf-8";
                meta name="viewport" content="width=device-width,initial-scale=1";
                title {{ (TITLE) " admin · " (title) }}
                script src="https://unpkg.com/htmx.org@2.0.4" {{}}
                style {{ (CSS) }}
            }}
            body {{
                header {{
                    p class="eyebrow" {{ "PRIVATE SUPER-ADMIN · MASH · " (TITLE) }}
                    p {{ "Admin VPC only · Shared Auth admin instance · no public ingress" }}
                    nav {{
                        a href="/" {{ "Home" }}
                        " "
                        a href="/tenants" {{ "Tenants" }}
                        " "
                        a href="/audit" {{ "Audit" }}
                        " "
                        a href="/plane" {{ "Plane" }}
                    }}
                }}
                (body)
            }}
        }}
    }}
}}

fn home_body() -> Markup {{
    html! {{
        h1 {{ "{title} admin" }}
        p {{ "Super-admins reach this console through the admin VPC. Product web and API servers cannot open the admin RDS." }}
        p {{ "Auth is the Shared Auth admin instance, not the customer issuer." }}
    }}
}}

fn tenants_body() -> Markup {{
    html! {{
        h1 {{ "Tenants" }}
        p {{ "Read-only projection. Writes go through the admin API on the same namespace." }}
        form hx-post="/tenants" hx-disabled-elt="this" {{
            label {{ "Slug" input name="slug" required; }}
            label {{ "Display name" input name="display_name" required; }}
            button type="submit" {{ "Queue create on admin-api" }}
        }}
    }}
}}

fn audit_body() -> Markup {{
    html! {{
        h1 {{ "Audit" }}
        p {{ "Append-only events from the admin API. Tokens and connection strings are never stored." }}
    }}
}}

fn plane_body() -> Markup {{
    html! {{
        h1 {{ "Interaction plane" }}
        ul {{
            li {{ "direct_db_read — SeaORM reads against ADMIN_DATABASE_URL only" }}
            li {{ "stateless_https — HTTP to admin-api ClusterIP" }}
            li {{ "stateful_tcp — persistent TCP to the admin-api group" }}
            li {{ "async_jetstream — NATS via oresoftware/k8s-cluster" }}
        }}
    }}
}}

const CSS: &str = "body{{font-family:system-ui;margin:0;background:#0c1210;color:#e8f6ee}}header,main{{max-width:52rem;margin:auto;padding:1.5rem}}a{{color:#8ee4b5}}.eyebrow{{letter-spacing:.16em;color:#7dcaa4}}form{{display:grid;gap:.6rem;margin-top:1rem}}input,button{{padding:.4rem}}";
'''


def leptos_main(prefix: str, env: str, title: str) -> str:
    ident = rust_ident(f"{prefix}-admin-web-common")
    return f'''use std::sync::Arc;

use anyhow::Context;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::{{Html, IntoResponse}};
use axum::routing::get;
use axum::Router;
use {ident}::{{parse_bind, require_super_admin, AppState, TITLE}};
use leptos::prelude::*;
use tower_http::trace::TraceLayer;
use uuid::Uuid;

#[tokio::main]
async fn main() -> anyhow::Result<()> {{
    let allow_public = std::env::var("{env}_ADMIN_ALLOW_PUBLIC_BIND").ok().as_deref() == Some("1");
    let bind = std::env::var("{env}_ADMIN_BIND").unwrap_or_else(|_| "127.0.0.1:8789".into());
    let addr = parse_bind(&bind, allow_public).map_err(anyhow::Error::msg)?;
    let allowlist = std::env::var("{env}_ADMIN_ALLOWLIST")
        .unwrap_or_default()
        .split(',')
        .filter_map(|part| Uuid::parse_str(part.trim()).ok())
        .collect::<Vec<_>>();
    anyhow::ensure!(!allowlist.is_empty(), "{env}_ADMIN_ALLOWLIST is required");
    let state = AppState {{
        allowlist: Arc::new(allowlist),
        admin_issuer: std::env::var("SHARED_AUTH_ADMIN_ISSUER")
            .unwrap_or_else(|_| "https://auth-admin.local".into()),
    }};
    let app = Router::new()
        .route("/healthz", get(|| async {{ "ok" }}))
        .route("/", get(home))
        .route("/tenants", get(tenants))
        .route("/audit", get(audit))
        .route("/plane", get(plane))
        .layer(TraceLayer::new_for_http())
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .context("listen")?;
    axum::serve(listener, app).await.context("serve")?;
    Ok(())
}}

async fn home(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, render("Home", "Super-admins only. Customer Shared Auth tokens are rejected."))
}}

async fn tenants(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, render("Tenants", "Tenant writes stay on the admin API / admin RDS."))
}}

async fn audit(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, render("Audit", "Append-only admin actions. No secrets in the log."))
}}

async fn plane(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(
        &state,
        &headers,
        render(
            "Plane",
            "direct_db_read · stateless_https · stateful_tcp · async_jetstream",
        ),
    )
}}

fn gated(state: &AppState, headers: &HeaderMap, body: String) -> impl IntoResponse {{
    if let Err(error) = require_super_admin(headers, &state.allowlist, &state.admin_issuer) {{
        return axum::http::StatusCode::from(error).into_response();
    }}
    Html(body).into_response()
}}

fn render(title: &str, blurb: &str) -> String {{
    let title = title.to_owned();
    let blurb = blurb.to_owned();
    let product = TITLE.to_owned();
    let owner = Owner::new();
    owner.with(|| {{
        view! {{
            <!DOCTYPE html>
            <html lang="en">
                <head>
                    <meta charset="utf-8"/>
                    <title>{{product.clone()}}" admin · "{{title.clone()}}</title>
                </head>
                <body>
                    <p class="eyebrow">"PRIVATE SUPER-ADMIN · LEPTOS · "{{product.clone()}}</p>
                    <nav>
                        <a href="/">"Home"</a>
                        <a href="/tenants">"Tenants"</a>
                        <a href="/audit">"Audit"</a>
                        <a href="/plane">"Plane"</a>
                    </nav>
                    <h1>{{title.clone()}}</h1>
                    <p>{{blurb.clone()}}</p>
                    <p>"{title} never binds the product DATABASE_URL."</p>
                </body>
            </html>
        }}
        .into_any()
        .to_html()
    }})
}}
'''


def dioxus_main(prefix: str, env: str, title: str) -> str:
    ident = rust_ident(f"{prefix}-admin-web-common")
    return f'''use std::sync::Arc;

use anyhow::Context;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::{{Html, IntoResponse}};
use axum::routing::get;
use axum::Router;
use {ident}::{{parse_bind, require_super_admin, AppState, TITLE}};
use dioxus::prelude::*;
use tower_http::trace::TraceLayer;
use uuid::Uuid;

#[tokio::main]
async fn main() -> anyhow::Result<()> {{
    let allow_public = std::env::var("{env}_ADMIN_ALLOW_PUBLIC_BIND").ok().as_deref() == Some("1");
    let bind = std::env::var("{env}_ADMIN_BIND").unwrap_or_else(|_| "127.0.0.1:8790".into());
    let addr = parse_bind(&bind, allow_public).map_err(anyhow::Error::msg)?;
    let allowlist = std::env::var("{env}_ADMIN_ALLOWLIST")
        .unwrap_or_default()
        .split(',')
        .filter_map(|part| Uuid::parse_str(part.trim()).ok())
        .collect::<Vec<_>>();
    anyhow::ensure!(!allowlist.is_empty(), "{env}_ADMIN_ALLOWLIST is required");
    let state = AppState {{
        allowlist: Arc::new(allowlist),
        admin_issuer: std::env::var("SHARED_AUTH_ADMIN_ISSUER")
            .unwrap_or_else(|_| "https://auth-admin.local".into()),
    }};
    let app = Router::new()
        .route("/healthz", get(|| async {{ "ok" }}))
        .route("/", get(home))
        .route("/tenants", get(tenants))
        .route("/audit", get(audit))
        .route("/plane", get(plane))
        .layer(TraceLayer::new_for_http())
        .with_state(state);
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .context("listen")?;
    axum::serve(listener, app).await.context("serve")?;
    Ok(())
}}

#[derive(Clone, PartialEq, Props)]
struct PageProps {{
    title: String,
    blurb: String,
}}

fn page(props: PageProps) -> Element {{
    rsx! {{
        head {{
            meta {{ charset: "utf-8" }}
            title {{ "{{TITLE}} admin · {{props.title}}" }}
        }}
        body {{
            p {{ class: "eyebrow", "PRIVATE SUPER-ADMIN · DIOXUS · {title}" }}
            nav {{
                a {{ href: "/", "Home" }}
                a {{ href: "/tenants", "Tenants" }}
                a {{ href: "/audit", "Audit" }}
                a {{ href: "/plane", "Plane" }}
            }}
            h1 {{ "{{props.title}}" }}
            p {{ "{{props.blurb}}" }}
        }}
    }}
}}

async fn home(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, "Home", "Dioxus SSR lane. Admin VPC only.")
}}

async fn tenants(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, "Tenants", "Reads may use SeaORM; writes go to admin-api.")
}}

async fn audit(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(&state, &headers, "Audit", "No tokens or RDS URLs in this view.")
}}

async fn plane(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {{
    gated(
        &state,
        &headers,
        "Plane",
        "Four modes: direct_db_read, stateless_https, stateful_tcp, async_jetstream.",
    )
}}

fn gated(state: &AppState, headers: &HeaderMap, title: &str, blurb: &str) -> impl IntoResponse {{
    if let Err(error) = require_super_admin(headers, &state.allowlist, &state.admin_issuer) {{
        return axum::http::StatusCode::from(error).into_response();
    }}
    let mut dom = VirtualDom::new_with_props(
        page,
        PageProps {{
            title: title.to_owned(),
            blurb: blurb.to_owned(),
        }},
    );
    dom.rebuild_in_place();
    Html(format!("<!DOCTYPE html><html lang=\\"en\\">{{}}</html>", dioxus_ssr::render(&dom)))
        .into_response()
}}
'''


def web_workspace(org: str, prefix: str) -> str:
    return f'''[workspace]
members = ["crates/common", "frontends/mash", "frontends/leptos", "frontends/dioxus"]
resolver = "2"

[workspace.package]
edition = "2021"
rust-version = "1.88"
license = "UNLICENSED"
repository = "https://github.com/{org}/{prefix}-admin-web-server.rs"

[workspace.dependencies]
anyhow = "1"
axum = {{ version = "0.8", features = ["macros"] }}
{prefix}-admin-web-common = {{ path = "crates/common" }}
serde = {{ version = "1", features = ["derive"] }}
serde_json = "1"
thiserror = "2"
tokio = {{ version = "1", features = ["macros", "rt-multi-thread", "net", "signal"] }}
tower-http = {{ version = "0.6", features = ["trace"] }}
uuid = {{ version = "1", features = ["serde", "v4"] }}
'''


def generate_admin_web(meta: dict[str, str]) -> Path:
    org, prefix, title = meta["org"], meta["prefix"], meta["title"]
    env = env_prefix(prefix)
    root = CODES / org / f"{prefix}-admin-web-server.rs"
    if (org, f"{prefix}-admin-web-server.rs") in SKIP_FULL_GENERATE:
        write(
            root / ".zpkg.toml",
            zpkg_toml(
                org,
                prefix,
                f"{prefix}-admin-web-server",
                f"{prefix}-admin-web-server.rs",
                f"Private super-admin mash/leptos/dioxus console for {title}",
                """[targets.mash]
dir = "frontends/mash"
adapter = "rust"
[targets.leptos]
dir = "frontends/leptos"
adapter = "rust"
[targets.dioxus]
dir = "frontends/dioxus"
adapter = "rust"
""",
            ),
        )
        return root

    write(root / "Cargo.toml", web_workspace(org, prefix))
    write(
        root / ".zpkg.toml",
        zpkg_toml(
            org,
            prefix,
            f"{prefix}-admin-web-server",
            f"{prefix}-admin-web-server.rs",
            f"Private super-admin mash/leptos/dioxus console for {title}",
            """[targets.mash]
dir = "frontends/mash"
adapter = "rust"
[targets.leptos]
dir = "frontends/leptos"
adapter = "rust"
[targets.dioxus]
dir = "frontends/dioxus"
adapter = "rust"
""",
        ),
    )
    write(root / ".gitignore", gitignore())
    write(root / "rust-toolchain.toml", rust_toolchain())
    write(root / ".github/workflows/ci.yml", ci_yml(True))
    write(root / "README.md", web_readme(title, prefix, env))
    write(root / "AGENTS.md", agents_md(title, "web", prefix, env))
    write(root / "k8s/networkpolicy.yaml", k8s_web(prefix))
    write(root / "crates/common/Cargo.toml", common_cargo(prefix))
    write(root / "crates/common/src/lib.rs", common_lib(prefix, env, title))
    write(root / "crates/common/src/bind.rs", common_bind())
    write(root / "crates/common/src/auth.rs", common_auth(prefix))
    write(root / "crates/common/src/plane.rs", common_plane(title))
    write(root / "frontends/mash/Cargo.toml", mash_cargo(prefix))
    write(root / "frontends/mash/src/main.rs", mash_main(prefix, env, title))
    write(root / "frontends/leptos/Cargo.toml", leptos_cargo(prefix))
    write(root / "frontends/leptos/src/main.rs", leptos_main(prefix, env, title))
    write(root / "frontends/dioxus/Cargo.toml", dioxus_cargo(prefix))
    write(root / "frontends/dioxus/src/main.rs", dioxus_main(prefix, env, title))
    return root


def api_cargo(org: str, prefix: str) -> str:
    crate = f"{prefix}-admin-api-server"
    return f'''[package]
name = "{crate}"
version = "0.1.0"
edition = "2021"
rust-version = "1.88"
license = "UNLICENSED"
description = "Private super-admin JSON API"
publish = false
repository = "https://github.com/{org}/{prefix}-admin-api-server.rs"

[lib]
name = "{rust_ident(crate)}"

[[bin]]
name = "{crate}"
path = "src/main.rs"

[dependencies]
axum = {{ version = "0.8", features = ["macros"] }}
serde = {{ version = "1", features = ["derive"] }}
serde_json = "1"
thiserror = "2"
tokio = {{ version = "1", features = ["macros", "rt-multi-thread", "net", "signal"] }}
uuid = {{ version = "1", features = ["serde", "v4"] }}

[dev-dependencies]
http-body-util = "0.1"
tower = {{ version = "0.5", features = ["util"] }}
'''


def api_lib(prefix: str, env: str, title: str) -> str:
    ident = rust_ident(f"{prefix}-admin-api-server")
    return f'''#![forbid(unsafe_code)]

//! Isolated admin JSON API. Product servers do not import this crate and
//! cannot reach the admin RDS.

pub mod auth;
pub bind;
pub mod plane;
pub mod store;

use std::sync::{{Arc, Mutex}};

use axum::extract::State;
use axum::http::HeaderMap;
use axum::routing::get;
use axum::{{Json, Router}};
use serde::Deserialize;
use uuid::Uuid;

use crate::auth::require_super_admin;
use crate::plane::{{AuditEvent, InteractionMode, PlaneCapabilities, Tenant}};
use crate::store::AdminStore;

pub use bind::parse_bind;

pub const TITLE: &str = "{title}";
pub const PREFIX: &str = "{prefix}";
pub const ROLE_HEADER: &str = "x-{prefix}-admin-role";
pub const ACTOR_HEADER: &str = "x-{prefix}-admin-actor";

#[derive(Clone)]
pub struct AppState {{
    pub allowlist: Arc<Vec<Uuid>>,
    pub admin_issuer: String,
    pub admin_db_configured: bool,
    pub store: Arc<Mutex<AdminStore>>,
}}

#[derive(Debug, Deserialize)]
pub struct TenantWrite {{
    pub slug: String,
    pub display_name: String,
}}

pub fn app(state: AppState) -> Router {{
    Router::new()
        .route("/healthz", get(|| async {{ "ok" }}))
        .route("/v1/plane", get(plane))
        .route("/v1/tenants", get(list_tenants).post(create_tenant))
        .route("/v1/audit", get(list_audit))
        .route("/v1/admin-db/status", get(admin_db_status))
        .with_state(state)
}}

async fn plane(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<PlaneCapabilities>, axum::http::StatusCode> {{
    require_super_admin(&headers, &state.allowlist, &state.admin_issuer)?;
    Ok(Json(PlaneCapabilities {{
        product: TITLE,
        modes: [
            InteractionMode::DirectReadOnlyDatabase,
            InteractionMode::StatelessHttp,
            InteractionMode::StatefulTcp,
            InteractionMode::JetStreamAsync,
        ],
        admin_db_attached: state.admin_db_configured,
        product_db_attached: false,
    }}))
}}

async fn list_tenants(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<Tenant>>, axum::http::StatusCode> {{
    require_super_admin(&headers, &state.allowlist, &state.admin_issuer)?;
    let store = state.store.lock().map_err(|_| axum::http::StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(store.tenants.values().cloned().collect()))
}}

async fn create_tenant(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<TenantWrite>,
) -> Result<(axum::http::StatusCode, Json<Tenant>), axum::http::StatusCode> {{
    let actor = require_super_admin(&headers, &state.allowlist, &state.admin_issuer)?;
    let slug = body.slug.trim().to_ascii_lowercase();
    if slug.is_empty() || body.display_name.trim().is_empty() {{
        return Err(axum::http::StatusCode::UNPROCESSABLE_ENTITY);
    }}
    let mut store = state.store.lock().map_err(|_| axum::http::StatusCode::INTERNAL_SERVER_ERROR)?;
    if store.tenants.values().any(|tenant| tenant.slug == slug) {{
        return Err(axum::http::StatusCode::CONFLICT);
    }}
    let tenant = Tenant {{
        id: Uuid::new_v4(),
        slug,
        display_name: body.display_name.trim().to_owned(),
        status: "pending".into(),
        created_by: actor.id,
    }};
    store.tenants.insert(tenant.id, tenant.clone());
    store.audit.push(AuditEvent {{
        id: Uuid::new_v4(),
        actor_id: actor.id,
        action: "tenant.create".into(),
        resource: tenant.id.to_string(),
    }});
    Ok((axum::http::StatusCode::CREATED, Json(tenant)))
}}

async fn list_audit(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<AuditEvent>>, axum::http::StatusCode> {{
    require_super_admin(&headers, &state.allowlist, &state.admin_issuer)?;
    let store = state.store.lock().map_err(|_| axum::http::StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(store.audit.clone()))
}}

async fn admin_db_status(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<serde_json::Value>, axum::http::StatusCode> {{
    require_super_admin(&headers, &state.allowlist, &state.admin_issuer)?;
    Ok(Json(serde_json::json!({{
        "attached": state.admin_db_configured,
        "productDatabaseAttached": false,
        "note": "Connection strings are never returned."
    }})))
}}

#[cfg(test)]
mod tests {{
    use super::*;
    use axum::body::Body;
    use axum::http::Request;
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    fn admin() -> (AppState, Uuid) {{
        let actor = Uuid::parse_str("018f2ee1-3ef5-7f4b-9e27-494ce50e8c4f").unwrap();
        (
            AppState {{
                allowlist: Arc::new(vec![actor]),
                admin_issuer: "https://auth-admin.example".into(),
                admin_db_configured: true,
                store: Arc::new(Mutex::new(AdminStore::default())),
            }},
            actor,
        )
    }}

    fn authed(builder: axum::http::request::Builder, actor: Uuid) -> axum::http::request::Builder {{
        builder
            .header(ROLE_HEADER, "super_admin")
            .header(ACTOR_HEADER, actor.to_string())
            .header("x-shared-auth-issuer", "https://auth-admin.example")
    }}

    #[tokio::test]
    async fn rejects_public_bind_and_anonymous_reads() {{
        assert!(parse_bind("127.0.0.1:8787", false).is_ok());
        assert!(parse_bind("0.0.0.0:8787", false).is_err());
        let (state, _) = admin();
        let response = app(state)
            .oneshot(Request::get("/v1/tenants").body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(response.status(), axum::http::StatusCode::FORBIDDEN);
    }}

    #[tokio::test]
    async fn creates_tenant_and_writes_audit() {{
        let (state, actor) = admin();
        let router = app(state);
        let created = router
            .clone()
            .oneshot(
                authed(Request::post("/v1/tenants"), actor)
                    .header("content-type", "application/json")
                    .body(Body::from(
                        serde_json::json!({{"slug":"acme","display_name":"Acme"}}).to_string(),
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(created.status(), axum::http::StatusCode::CREATED);
        let listed = router
            .oneshot(authed(Request::get("/v1/audit"), actor).body(Body::empty()).unwrap())
            .await
            .unwrap();
        let events: Vec<AuditEvent> =
            serde_json::from_slice(&listed.into_body().collect().await.unwrap().to_bytes()).unwrap();
        assert_eq!(events[0].action, "tenant.create");
    }}

    #[tokio::test]
    async fn customer_issuer_cannot_use_the_admin_api() {{
        let (state, actor) = admin();
        let response = app(state)
            .oneshot(
                Request::get("/v1/plane")
                    .header(ROLE_HEADER, "super_admin")
                    .header(ACTOR_HEADER, actor.to_string())
                    .header("x-shared-auth-issuer", "https://auth.customer.example")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), axum::http::StatusCode::FORBIDDEN);
    }}
}}
'''


def api_lib_fixed(prefix: str, env: str, title: str) -> str:
    """Corrected lib.rs — the previous draft had a typo (`pub bind`)."""
    return api_lib(prefix, env, title).replace("pub bind;", "pub mod bind;")


def api_bind() -> str:
    return r'''use std::net::{IpAddr, SocketAddr};

pub fn parse_bind(raw: &str, allow_public: bool) -> Result<SocketAddr, String> {
    let addr: SocketAddr = raw.parse().map_err(|_| "invalid bind address".to_string())?;
    if allow_public {
        return Ok(addr);
    }
    match addr.ip() {
        IpAddr::V4(ip) if ip.is_loopback() => Ok(addr),
        IpAddr::V6(ip) if ip.is_loopback() => Ok(addr),
        _ => Err("admin API refuses a non-loopback bind".to_string()),
    }
}
'''


def api_auth(prefix: str) -> str:
    return f'''use axum::http::{{HeaderMap, StatusCode}};
use thiserror::Error;
use uuid::Uuid;

use crate::{{ACTOR_HEADER, ROLE_HEADER}};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Actor {{
    pub id: Uuid,
}}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
pub enum AuthError {{
    #[error("unauthorized")]
    Unauthorized,
    #[error("forbidden")]
    Forbidden,
    #[error("customer shared-auth issuer is not accepted on the admin plane")]
    CustomerIssuer,
}}

impl From<AuthError> for StatusCode {{
    fn from(error: AuthError) -> Self {{
        match error {{
            AuthError::Unauthorized => StatusCode::UNAUTHORIZED,
            AuthError::Forbidden | AuthError::CustomerIssuer => StatusCode::FORBIDDEN,
        }}
    }}
}}

pub fn classify_super_admin(roles: &str) -> bool {{
    roles.split(',').map(str::trim).any(|role| role == "super_admin")
}}

pub fn require_super_admin(
    headers: &HeaderMap,
    allowlist: &[Uuid],
    admin_issuer: &str,
) -> Result<Actor, StatusCode> {{
    if let Some(issuer) = headers
        .get("x-shared-auth-issuer")
        .and_then(|value| value.to_str().ok())
    {{
        let token = issuer.trim().trim_end_matches('/');
        let admin = admin_issuer.trim().trim_end_matches('/');
        if !token.is_empty() && !admin.is_empty() && token != admin {{
            return Err(StatusCode::FORBIDDEN);
        }}
    }}
    if !classify_super_admin(
        headers
            .get(ROLE_HEADER)
            .and_then(|value| value.to_str().ok())
            .unwrap_or(""),
    ) {{
        return Err(StatusCode::FORBIDDEN);
    }}
    let actor = headers
        .get(ACTOR_HEADER)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| Uuid::parse_str(value.trim()).ok())
        .ok_or(StatusCode::UNAUTHORIZED)?;
    if !allowlist.contains(&actor) {{
        return Err(StatusCode::FORBIDDEN);
    }}
    Ok(Actor {{ id: actor }})
}}
'''


def api_plane() -> str:
    return '''use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InteractionMode {
    DirectReadOnlyDatabase,
    StatelessHttp,
    StatefulTcp,
    JetStreamAsync,
}

#[derive(Clone, Debug, Serialize)]
pub struct PlaneCapabilities {
    pub product: &'static str,
    pub modes: [InteractionMode; 4],
    pub admin_db_attached: bool,
    pub product_db_attached: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Tenant {
    pub id: Uuid,
    pub slug: String,
    pub display_name: String,
    pub status: String,
    pub created_by: Uuid,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AuditEvent {
    pub id: Uuid,
    pub actor_id: Uuid,
    pub action: String,
    pub resource: String,
}
'''


def api_store() -> str:
    return '''use std::collections::BTreeMap;

use uuid::Uuid;

use crate::plane::{AuditEvent, Tenant};

#[derive(Clone, Debug, Default)]
pub struct AdminStore {
    pub tenants: BTreeMap<Uuid, Tenant>,
    pub audit: Vec<AuditEvent>,
}
'''


def api_main(prefix: str, env: str) -> str:
    ident = rust_ident(f"{prefix}-admin-api-server")
    return f'''use std::sync::{{Arc, Mutex}};

use {ident}::{{AppState, app, parse_bind, store::AdminStore}};
use uuid::Uuid;

#[tokio::main]
async fn main() {{
    let allow_public = std::env::var("{env}_ADMIN_ALLOW_PUBLIC_BIND").ok().as_deref() == Some("1");
    let bind = std::env::var("{env}_ADMIN_BIND").unwrap_or_else(|_| "127.0.0.1:8787".into());
    let addr = parse_bind(&bind, allow_public).expect("admin API bind");
    let allowlist = std::env::var("{env}_ADMIN_ALLOWLIST")
        .unwrap_or_default()
        .split(',')
        .filter_map(|part| Uuid::parse_str(part.trim()).ok())
        .collect::<Vec<_>>();
    if allowlist.is_empty() {{
        panic!("{env}_ADMIN_ALLOWLIST must contain at least one super-admin UUID");
    }}
    if std::env::var("DATABASE_URL").ok().filter(|value| !value.is_empty()).is_some()
        && std::env::var("{env}_ADMIN_DATABASE_URL").ok().is_none()
    {{
        panic!("product DATABASE_URL must not be the admin database; set {env}_ADMIN_DATABASE_URL");
    }}
    let state = AppState {{
        allowlist: Arc::new(allowlist),
        admin_issuer: std::env::var("SHARED_AUTH_ADMIN_ISSUER")
            .unwrap_or_else(|_| "https://auth-admin.local".into()),
        admin_db_configured: std::env::var("{env}_ADMIN_DATABASE_URL")
            .ok()
            .is_some_and(|value| !value.is_empty()),
        store: Arc::new(Mutex::new(AdminStore::default())),
    }};
    let listener = tokio::net::TcpListener::bind(addr).await.expect("listen");
    axum::serve(listener, app(state)).await.expect("serve");
}}
'''


def generate_admin_api(meta: dict[str, str]) -> Path:
    org, prefix, title = meta["org"], meta["prefix"], meta["title"]
    env = env_prefix(prefix)
    root = CODES / org / f"{prefix}-admin-api-server.rs"
    if (org, f"{prefix}-admin-api-server.rs") in SKIP_FULL_GENERATE:
        write(
            root / ".zpkg.toml",
            zpkg_toml(
                org,
                prefix,
                f"{prefix}-admin-api-server",
                f"{prefix}-admin-api-server.rs",
                f"Private super-admin JSON API for {title}",
                '[targets.rust]\ndir = "."\nadapter = "rust"\n',
            ),
        )
        return root

    write(root / "Cargo.toml", api_cargo(org, prefix))
    write(
        root / ".zpkg.toml",
        zpkg_toml(
            org,
            prefix,
            f"{prefix}-admin-api-server",
            f"{prefix}-admin-api-server.rs",
            f"Private super-admin JSON API for {title}",
            '[targets.rust]\ndir = "."\nadapter = "rust"\n',
        ),
    )
    write(root / ".gitignore", gitignore())
    write(root / "rust-toolchain.toml", rust_toolchain())
    write(root / ".github/workflows/ci.yml", ci_yml(False))
    write(root / "README.md", api_readme(title, prefix, env))
    write(root / "AGENTS.md", agents_md(title, "api", prefix, env))
    write(root / "k8s/networkpolicy.yaml", k8s_api(prefix))
    write(root / "schema/admin.sql", admin_sql(prefix))
    write(root / "src/lib.rs", api_lib_fixed(prefix, env, title))
    write(root / "src/bind.rs", api_bind())
    write(root / "src/auth.rs", api_auth(prefix))
    write(root / "src/plane.rs", api_plane())
    write(root / "src/store.rs", api_store())
    write(root / "src/main.rs", api_main(prefix, env))
    return root


def ensure_zpkg_dep(path: Path, org: str, prefix: str) -> bool:
    if not path.is_file():
        return False
    text = path.read_text()
    lib_core = LIB_CORE.get(org, f"{prefix}-lib-core")
    orm_core = ORM_CORE.get(org, f"{prefix}-orm-core")
    needed = [
        f'"{org}/{lib_core}"',
        f'"{org}/{orm_core}"',
    ]
    if "[dependencies]" not in text:
        text = text.rstrip() + "\n\n[dependencies]\n"
    changed = False
    for dep in needed:
        if dep in text:
            continue
        text = text.replace(
            "[dependencies]\n",
            f"[dependencies]\n{dep} = \"^0.1.0\"\n",
            1,
        )
        changed = True
    if changed:
        path.write_text(text if text.endswith("\n") else text + "\n")
    return changed


def patch_product_servers(meta: dict[str, str]) -> list[str]:
    org, prefix = meta["org"], meta["prefix"]
    touched: list[str] = []
    for kind in ("web-server.rs", "api-server.rs"):
        repo = CODES / org / f"{prefix}-{kind}"
        zpkg = repo / ".zpkg.toml"
        if ensure_zpkg_dep(zpkg, org, prefix):
            touched.append(str(zpkg))
        elif zpkg.is_file():
            continue
        elif repo.is_dir():
            write(
                zpkg,
                zpkg_toml(
                    org,
                    prefix,
                    f"{prefix}-{kind.replace('.rs', '').replace('-', '-')}",
                    f"{prefix}-{kind}",
                    f"{meta['title']} {kind} with lib-core and orm-core",
                    '[targets.rust]\ndir = "."\nadapter = "rust"\n',
                ),
            )
            touched.append(str(zpkg))
    return touched


def main() -> int:
    created: list[str] = []
    patched: list[str] = []
    for meta in ORGS:
        web = generate_admin_web(meta)
        api = generate_admin_api(meta)
        created.append(str(web))
        created.append(str(api))
        patched.extend(patch_product_servers(meta))
    print(f"generated {len(created)} admin trees")
    for path in created:
        print(f"  admin {path}")
    print(f"patched {len(patched)} product .zpkg.toml files")
    for path in patched:
        print(f"  zpkg {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
