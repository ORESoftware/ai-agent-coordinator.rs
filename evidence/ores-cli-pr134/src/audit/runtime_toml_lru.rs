use std::fs;
use std::io;

use toml::Value;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONFIG: &str = ".ores-lru.toml";
const PROTOCOL: &str = "ores.lru-config.v1";
const ROOT_KEYS: &[&str] = &[
    "protocol",
    "namespace",
    "roles",
    "flags2env",
    "env",
    "redis",
    "defaults",
    "roleOverrides",
    "caches",
];
const ROLES: &[&str] = &["client", "server"];
const SYNC_MODES: &[&str] = &["local_only", "read_only", "write_through", "bidirectional"];
const OVERFLOW_MODES: &[&str] = &["evict_lru", "reject_and_reconcile"];
const ENV_KINDS: &[&str] = &["string", "bool", "integer", "double", "json", "url"];

/// Apply only `.ores-lru.toml` invariants expressed by both the owner's
/// TypeSpec and independently authored Draft 2020-12 JSON Schema authorities.
/// Full admission remains owner/TJSV work.
pub(super) fn augment_lru_runtime_toml_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    let path = options.path.join(CONFIG);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return report.finalize(),
        Err(_) => return report.finalize(),
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return report.finalize();
    }
    let Ok(text) = fs::read_to_string(path) else {
        return report.finalize();
    };
    let Ok(document) = toml::from_str::<Value>(&text) else {
        return report.finalize();
    };
    let Some(root) = document.as_table() else {
        return report.finalize();
    };

    let before = report.issue_count();
    closed_keys(root, ROOT_KEYS, "root", &mut report);
    require_exact_string(root, "protocol", PROTOCOL, "lru-protocol", &mut report);
    require_string(root, "namespace", "root.namespace", &mut report);
    audit_roles(root.get("roles"), "roles", &mut report);
    audit_flags2env(root.get("flags2env"), &mut report);
    audit_env(root.get("env"), &mut report);
    audit_redis(root.get("redis"), &mut report);
    audit_defaults(root.get("defaults"), &mut report);
    audit_role_overrides(root.get("roleOverrides"), &mut report);
    audit_caches(root.get("caches"), &mut report);

    if report.issue_count() == before {
        report.push(
            Finding::info(
                "lru-domain-inspected",
                ".ores-lru.toml passed the bounded peer-authority invariant audit",
            )
            .with_target(CONFIG),
        );
    }
    report.finalize()
}

fn audit_roles(value: Option<&Value>, target: &str, report: &mut CommandReport) {
    let Some(values) = value.and_then(Value::as_array) else {
        error(report, "lru-roles-shape", "roles must be an array", target);
        return;
    };
    for (index, value) in values.iter().enumerate() {
        if !value.as_str().is_some_and(|role| ROLES.contains(&role)) {
            error(
                report,
                "lru-role-enum",
                "role is outside the peer-authority enum",
                &format!("{target}[{index}]"),
            );
        }
    }
}

fn audit_flags2env(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        error(
            report,
            "lru-flags2env-shape",
            "flags2env must be a table",
            "flags2env",
        );
        return;
    };
    closed_keys(
        table,
        &["contract", "requireAudit", "precedence"],
        "flags2env",
        report,
    );
    require_string(table, "contract", "flags2env.contract", report);
    require_bool(table, "requireAudit", "flags2env.requireAudit", report);
    require_exact_string(
        table,
        "precedence",
        "argv-over-env",
        "lru-flags2env-precedence",
        report,
    );
}

fn audit_env(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(entries) = value.as_array() else {
        error(report, "lru-env-shape", "env must be an array", "env");
        return;
    };
    for (index, entry) in entries.iter().enumerate() {
        let target = format!("env[{index}]");
        let Some(table) = entry.as_table() else {
            error(
                report,
                "lru-env-entry-shape",
                "env entry must be a table",
                &target,
            );
            continue;
        };
        closed_keys(
            table,
            &[
                "name",
                "key",
                "kind",
                "required",
                "secret",
                "default",
                "description",
            ],
            &target,
            report,
        );
        require_string(table, "name", &format!("{target}.name"), report);
        require_string(table, "key", &format!("{target}.key"), report);
        require_enum(table, "kind", ENV_KINDS, &target, report);
        require_bool(table, "required", &format!("{target}.required"), report);
        require_bool(table, "secret", &format!("{target}.secret"), report);
        for field in ["default", "description"] {
            if table.contains_key(field) {
                require_string(table, field, &format!("{target}.{field}"), report);
            }
        }
    }
}

fn audit_redis(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        error(report, "lru-redis-shape", "redis must be a table", "redis");
        return;
    };
    closed_keys(
        table,
        &[
            "urlEnv",
            "keyPrefix",
            "pubsubChannel",
            "reconcileIntervalMs",
            "reconnectMinMs",
            "reconnectMaxMs",
        ],
        "redis",
        report,
    );
    for field in ["urlEnv", "keyPrefix", "pubsubChannel"] {
        require_string(table, field, &format!("redis.{field}"), report);
    }
    for field in ["reconcileIntervalMs", "reconnectMinMs", "reconnectMaxMs"] {
        require_integer(table, field, &format!("redis.{field}"), report);
    }
}

fn audit_defaults(value: Option<&Value>, report: &mut CommandReport) {
    let Some(table) = value.and_then(Value::as_table) else {
        error(
            report,
            "lru-defaults-shape",
            "defaults must be a table",
            "defaults",
        );
        return;
    };
    closed_keys(
        table,
        &["capacity", "syncMode", "overflowMode", "failOpenOnStartup"],
        "defaults",
        report,
    );
    require_integer(table, "capacity", "defaults.capacity", report);
    require_enum(table, "syncMode", SYNC_MODES, "defaults", report);
    require_enum(table, "overflowMode", OVERFLOW_MODES, "defaults", report);
    require_bool(
        table,
        "failOpenOnStartup",
        "defaults.failOpenOnStartup",
        report,
    );
}

fn audit_role_overrides(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(entries) = value.as_array() else {
        error(
            report,
            "lru-role-overrides-shape",
            "roleOverrides must be an array",
            "roleOverrides",
        );
        return;
    };
    for (index, entry) in entries.iter().enumerate() {
        audit_role_like(entry, &format!("roleOverrides[{index}]"), false, report);
    }
}

fn audit_caches(value: Option<&Value>, report: &mut CommandReport) {
    let Some(entries) = value.and_then(Value::as_array) else {
        error(
            report,
            "lru-caches-shape",
            "caches must be an array",
            "caches",
        );
        return;
    };
    for (index, entry) in entries.iter().enumerate() {
        audit_role_like(entry, &format!("caches[{index}]"), true, report);
    }
}

fn audit_role_like(value: &Value, target: &str, require_name: bool, report: &mut CommandReport) {
    let Some(table) = value.as_table() else {
        error(report, "lru-entry-shape", "entry must be a table", target);
        return;
    };
    let allowed = if require_name {
        &[
            "name",
            "role",
            "capacity",
            "syncMode",
            "overflowMode",
            "failOpenOnStartup",
        ][..]
    } else {
        &[
            "role",
            "capacity",
            "syncMode",
            "overflowMode",
            "failOpenOnStartup",
        ][..]
    };
    closed_keys(table, allowed, target, report);
    if require_name {
        require_string(table, "name", &format!("{target}.name"), report);
    }
    require_enum(table, "role", ROLES, target, report);
    if table.contains_key("capacity") {
        require_integer(table, "capacity", &format!("{target}.capacity"), report);
    }
    if table.contains_key("syncMode") {
        require_enum(table, "syncMode", SYNC_MODES, target, report);
    }
    if table.contains_key("overflowMode") {
        require_enum(table, "overflowMode", OVERFLOW_MODES, target, report);
    }
    if table.contains_key("failOpenOnStartup") {
        require_bool(
            table,
            "failOpenOnStartup",
            &format!("{target}.failOpenOnStartup"),
            report,
        );
    }
}

fn closed_keys(
    table: &toml::map::Map<String, Value>,
    allowed: &[&str],
    target: &str,
    report: &mut CommandReport,
) {
    for key in table.keys() {
        if !allowed.contains(&key.as_str()) {
            error(
                report,
                "lru-unknown-field",
                "field is not declared by the peer-authority LRU v1 shape",
                &format!("{target}.{key}"),
            );
        }
    }
}

fn require_exact_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    expected: &str,
    code: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_str) != Some(expected) {
        error(
            report,
            code,
            "string does not match the peer-authority constant",
            field,
        );
    }
}

fn require_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_str).is_none() {
        error(report, "lru-string-shape", "field must be a string", target);
    }
}

fn require_integer(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_integer).is_none() {
        error(
            report,
            "lru-integer-shape",
            "field must be an integer",
            target,
        );
    }
}

fn require_bool(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_bool).is_none() {
        error(report, "lru-boolean-shape", "field must be boolean", target);
    }
}

fn require_enum(
    table: &toml::map::Map<String, Value>,
    field: &str,
    allowed: &[&str],
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_str)
        .is_some_and(|value| allowed.contains(&value))
    {
        error(
            report,
            "lru-enum",
            "value is outside the peer-authority enum",
            &format!("{target}.{field}"),
        );
    }
}

fn error(report: &mut CommandReport, code: &str, message: &str, target: &str) {
    report.push(Finding::error(code, message).with_target(target));
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::augment_lru_runtime_toml_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    fn audit(config: &str) -> CommandReport {
        let root = tempdir().expect("tempdir");
        fs::write(root.path().join(".ores-lru.toml"), config).expect("config");
        augment_lru_runtime_toml_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo").finalize(),
        )
    }

    const VALID: &str = r#"
protocol = "ores.lru-config.v1"
namespace = "runtime"
roles = ["server"]

[flags2env]
contract = ".cli-flags.toml"
requireAudit = true
precedence = "argv-over-env"

[[env]]
name = "redis_url"
key = "REDIS_URL"
kind = "url"
required = true
secret = true
description = "Redis URL"

[redis]
urlEnv = "REDIS_URL"
keyPrefix = "runtime:"
pubsubChannel = "runtime-events"
reconcileIntervalMs = 30000
reconnectMinMs = 250
reconnectMaxMs = 10000

[defaults]
capacity = 1024
syncMode = "bidirectional"
overflowMode = "evict_lru"
failOpenOnStartup = false

[[caches]]
name = "runtime-env"
role = "server"
"#;

    #[test]
    fn accepts_peer_authority_shape() {
        let report = audit(VALID);
        assert_eq!(report.exit_code(), 0);
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.code == "lru-domain-inspected")
        );
    }

    #[test]
    fn rejects_unknown_field() {
        let report = audit(&VALID.replace(
            "namespace = \"runtime\"",
            "namespace = \"runtime\"\nunknown = true",
        ));
        assert_eq!(report.exit_code(), 2);
        assert!(
            report
                .findings
                .iter()
                .any(|f| f.code == "lru-unknown-field")
        );
    }

    #[test]
    fn rejects_protocol_and_enum_drift() {
        let report = audit(
            &VALID
                .replace("ores.lru-config.v1", "ores.lru-config.v2")
                .replace("bidirectional", "eventual"),
        );
        assert_eq!(report.exit_code(), 2);
        assert!(report.findings.iter().any(|f| f.code == "lru-protocol"));
        assert!(report.findings.iter().any(|f| f.code == "lru-enum"));
    }

    #[test]
    fn rejects_missing_required_cache_role() {
        let report = audit(&VALID.replace("role = \"server\"\n", ""));
        assert_eq!(report.exit_code(), 2);
        assert!(report.findings.iter().any(|f| f.code == "lru-enum"));
    }
}
