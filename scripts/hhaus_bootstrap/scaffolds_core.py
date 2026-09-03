from __future__ import annotations

import json

def lib_core_files() -> dict[str, str]:
    cargo = '''[package]\nname = "hhaus-lib-core"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\nlicense = "MIT"\ndescription = "Shared H/HAUS validation, identity, request-context, telemetry, and rate-limit primitives"\n\n[lints.rust]\nunsafe_code = "forbid"\n\n[lints.clippy]\nall = "deny"\npedantic = "deny"\nunwrap_used = "deny"\nexpect_used = "deny"\n'''
    rust = r'''#![forbid(unsafe_code)]

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Principal {
    pub subject: String,
    pub organization_id: String,
    pub user_id: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RateLimitLayer {
    CloudflareEdge,
    GatewayLoadBalancer,
    ServiceRuntimeLru,
    DistributedRedisCoordinator,
    DurableSecurityBilling,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FailureMode {
    Open,
    Closed,
    LocalOnly,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RateLimitSubject(String);

impl RateLimitSubject {
    /// Builds a tenant-scoped policy subject from a verified principal.
    ///
    /// # Errors
    ///
    /// Returns an error when the verified subject or organization identifier is blank.
    pub fn from_authenticated(principal: &Principal) -> Result<Self, &'static str> {
        if principal.subject.trim().is_empty() || principal.organization_id.trim().is_empty() {
            return Err("authenticated rate-limit identity is incomplete");
        }
        Ok(Self(format!("org:{}:sub:{}", principal.organization_id, principal.subject)))
    }

    /// Builds an anonymous policy subject from an HMAC-SHA-256 digest.
    ///
    /// # Errors
    ///
    /// Returns an error unless `digest` is exactly 64 hexadecimal characters.
    pub fn from_pseudonymous_edge_digest(digest: &str) -> Result<Self, &'static str> {
        if digest.len() != 64 || !digest.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Err("edge identity must be a 32-byte lowercase hexadecimal digest");
        }
        Ok(Self(format!("anon:{digest}")))
    }

    /// Returns the opaque subject value suitable for policy services.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RequestContext {
    pub request_id: String,
    pub traceparent: Option<String>,
    pub principal: Option<Principal>,
    pub rate_limit_subject: RateLimitSubject,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn authenticated_subject_is_tenant_scoped() {
        let principal = Principal {
            subject: "user-7".into(),
            organization_id: "hhaus-medellin".into(),
            user_id: "internal-user-9".into(),
        };
        let result = RateLimitSubject::from_authenticated(&principal);
        assert_eq!(
            result.as_ref().map(RateLimitSubject::as_str),
            Ok("org:hhaus-medellin:sub:user-7")
        );
    }

    #[test]
    fn anonymous_subject_rejects_raw_addresses() {
        assert!(RateLimitSubject::from_pseudonymous_edge_digest("192.0.2.1").is_err());
        assert!(RateLimitSubject::from_pseudonymous_edge_digest(&"a".repeat(64)).is_ok());
    }
}
'''
    ts = '''export type RateLimitLayer =\n  | "cloudflare-edge"\n  | "gateway-load-balancer"\n  | "service-runtime-lru"\n  | "distributed-redis-coordinator"\n  | "durable-security-billing";\n\nexport interface RequestContext {\n  readonly requestId: string;\n  readonly traceparent?: string;\n  readonly authenticatedSubject?: string;\n  readonly rateLimitSubject: string;\n}\n\nexport function assertPseudonymousSubject(value: string): string {\n  if (!/^anon:[0-9a-f]{64}$/.test(value)) {\n    throw new TypeError("anonymous subjects must be pseudonymous SHA-256 digests");\n  }\n  return value;\n}\n'''
    dart = '''enum RateLimitLayer {\n  cloudflareEdge,\n  gatewayLoadBalancer,\n  serviceRuntimeLru,\n  distributedRedisCoordinator,\n  durableSecurityBilling,\n}\n\nfinal class RequestContext {\n  const RequestContext({\n    required this.requestId,\n    required this.rateLimitSubject,\n    this.traceparent,\n  });\n\n  final String requestId;\n  final String rateLimitSubject;\n  final String? traceparent;\n}\n'''
    return {
        "Cargo.toml": cargo,
        "src/lib.rs": rust,
        "typescript/src/index.ts": ts,
        "dart/lib/hhaus_lib_core.dart": dart,
    }


def orm_core_files() -> dict[str, str]:
    cargo = '''[package]\nname = "hhaus-orm-core"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\nlicense = "MIT"\ndescription = "Backend-only H/HAUS Diesel and SeaORM projections"\n\n[features]\ndefault = []\ndiesel-adapter = ["dep:diesel"]\nseaorm-adapter = ["dep:sea-orm"]\n\n[dependencies]\ndiesel = { version = "2.2", default-features = false, features = ["postgres"], optional = true }\nsea-orm = { version = "1.1", default-features = false, features = ["macros", "with-json"], optional = true }\n\n[lints.rust]\nunsafe_code = "forbid"\n\n[lints.clippy]\nall = "deny"\npedantic = "deny"\nunwrap_used = "deny"\nexpect_used = "deny"\n'''
    lib = r'''#![forbid(unsafe_code)]

pub const DIESEL_MEMBERSHIP_FIELDS: &[&str] = &[
    "id",
    "organization_id",
    "user_id",
    "role",
    "created_at_epoch_ms",
];

pub const SEA_ORM_MEMBERSHIP_FIELDS: &[&str] = &[
    "id",
    "organization_id",
    "user_id",
    "role",
    "created_at_epoch_ms",
];

/// Verifies exact field-order parity between the Diesel and `SeaORM` projections.
///
/// # Errors
///
/// Returns an error when the independently declared projections diverge.
pub fn assert_projection_parity() -> Result<(), &'static str> {
    if DIESEL_MEMBERSHIP_FIELDS == SEA_ORM_MEMBERSHIP_FIELDS {
        Ok(())
    } else {
        Err("Diesel and SeaORM membership projections diverged")
    }
}

#[cfg(feature = "diesel-adapter")]
pub mod diesel_models;
#[cfg(feature = "seaorm-adapter")]
pub mod sea_orm_entities;

#[cfg(test)]
mod tests {
    #[test]
    fn orm_projections_match() {
        assert!(super::assert_projection_parity().is_ok());
    }
}
'''
    diesel = r'''#![forbid(unsafe_code)]

diesel::table! {
    hhaus_memberships (id) {
        id -> Text,
        organization_id -> Text,
        user_id -> Text,
        role -> Text,
        created_at_epoch_ms -> BigInt,
    }
}

#[derive(Clone, Debug, diesel::Queryable, diesel::Selectable, diesel::Insertable)]
#[diesel(table_name = hhaus_memberships)]
pub struct MembershipRow {
    pub id: String,
    pub organization_id: String,
    pub user_id: String,
    pub role: String,
    pub created_at_epoch_ms: i64,
}
'''
    sea = r'''#![forbid(unsafe_code)]

use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel)]
#[sea_orm(table_name = "hhaus_memberships")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub id: String,
    pub organization_id: String,
    pub user_id: String,
    pub role: String,
    pub created_at_epoch_ms: i64,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {}

impl ActiveModelBehavior for ActiveModel {}
'''
    return {
        "Cargo.toml": cargo,
        "src/lib.rs": lib,
        "src/diesel_models.rs": diesel,
        "src/sea_orm_entities.rs": sea,
        "orm-projection.json": json.dumps(
            {
                "schema_version": 1,
                "table": "hhaus_memberships",
                "diesel": ["id", "organization_id", "user_id", "role", "created_at_epoch_ms"],
                "seaorm": ["id", "organization_id", "user_id", "role", "created_at_epoch_ms"],
                "publication_gate": "exact-parity",
            },
            indent=2,
        )
        + "\n",
    }


def sync_files() -> dict[str, str]:
    cargo = '''[package]\nname = "hhaus-sync"\nversion = "0.1.0"\nedition = "2024"\nrust-version = "1.85"\nlicense = "MIT"\ndescription = "Offline-first H/HAUS synchronization state machine"\n\n[lints.rust]\nunsafe_code = "forbid"\n\n[lints.clippy]\nall = "deny"\npedantic = "deny"\nunwrap_used = "deny"\nexpect_used = "deny"\n'''
    rust = r'''#![forbid(unsafe_code)]

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SyncPhase {
    Idle,
    ReadingLocal,
    PullingRemote,
    Reconciling,
    PushingRemote,
    CommittingLocal,
    BackingOff,
    FailedClosed,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SyncEvent {
    Start,
    LocalRead,
    RemotePulled,
    Reconciled,
    RemotePushed,
    LocalCommitted,
    RetryableFailure,
    PermanentFailure,
    RetryElapsed,
}

/// Applies one explicit synchronization-state transition.
///
/// # Errors
///
/// Returns an error for every event that is invalid in the current phase.
#[allow(clippy::match_same_arms)]
pub fn transition(phase: SyncPhase, event: SyncEvent) -> Result<SyncPhase, &'static str> {
    use SyncEvent::{LocalCommitted, LocalRead, PermanentFailure, Reconciled, RemotePulled, RemotePushed, RetryElapsed, RetryableFailure, Start};
    use SyncPhase::{BackingOff, CommittingLocal, FailedClosed, Idle, PullingRemote, PushingRemote, ReadingLocal, Reconciling};
    match (phase, event) {
        (Idle, Start) => Ok(ReadingLocal),
        (ReadingLocal, LocalRead) => Ok(PullingRemote),
        (PullingRemote, RemotePulled) => Ok(Reconciling),
        (Reconciling, Reconciled) => Ok(PushingRemote),
        (PushingRemote, RemotePushed) => Ok(CommittingLocal),
        (CommittingLocal, LocalCommitted) => Ok(Idle),
        (ReadingLocal | PullingRemote | Reconciling | PushingRemote | CommittingLocal, RetryableFailure) => Ok(BackingOff),
        (BackingOff, RetryElapsed) => Ok(ReadingLocal),
        (ReadingLocal | PullingRemote | Reconciling | PushingRemote | CommittingLocal | BackingOff, PermanentFailure) => Ok(FailedClosed),
        (Idle | FailedClosed, LocalRead | RemotePulled | Reconciled | RemotePushed | LocalCommitted | RetryableFailure | PermanentFailure | RetryElapsed)
        | (ReadingLocal | PullingRemote | Reconciling | PushingRemote | CommittingLocal | BackingOff | FailedClosed, Start)
        | (ReadingLocal, RemotePulled | Reconciled | RemotePushed | LocalCommitted | RetryElapsed)
        | (PullingRemote, LocalRead | Reconciled | RemotePushed | LocalCommitted | RetryElapsed)
        | (Reconciling, LocalRead | RemotePulled | RemotePushed | LocalCommitted | RetryElapsed)
        | (PushingRemote, LocalRead | RemotePulled | Reconciled | LocalCommitted | RetryElapsed)
        | (CommittingLocal, LocalRead | RemotePulled | Reconciled | RemotePushed | RetryElapsed)
        | (BackingOff, LocalRead | RemotePulled | Reconciled | RemotePushed | LocalCommitted | RetryableFailure) => Err("invalid sync transition"),
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Store {
    IndexedDb,
    Sqlite,
    Postgres,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn happy_path_returns_to_idle() {
        let events = [
            SyncEvent::Start,
            SyncEvent::LocalRead,
            SyncEvent::RemotePulled,
            SyncEvent::Reconciled,
            SyncEvent::RemotePushed,
            SyncEvent::LocalCommitted,
        ];
        let mut result = Ok(SyncPhase::Idle);
        for event in events {
            result = result.and_then(|phase| transition(phase, event));
        }
        assert_eq!(result, Ok(SyncPhase::Idle));
    }

    #[test]
    fn permanent_failures_fail_closed() {
        assert_eq!(
            transition(SyncPhase::Reconciling, SyncEvent::PermanentFailure),
            Ok(SyncPhase::FailedClosed)
        );
    }
}
'''
    topology = {
        "schema_version": 1,
        "upstream": "opto-sync/opto-sync",
        "client_stores": ["indexeddb", "sqlite"],
        "server_store": "postgres",
        "orm_adapters": ["diesel", "seaorm"],
        "conflict_policy": "deterministic-version-vector-then-domain-merge",
        "rate_limit": {"connection": "gcra", "mutation_batch": "token-bucket", "failure_mode": "bounded"},
        "backend_adapter_dependency": "hhaus-orm-core",
    }
    return {
        "Cargo.toml": cargo,
        "src/lib.rs": rust,
        "sync-topology.json": json.dumps(topology, indent=2, sort_keys=True) + "\n",
    }
