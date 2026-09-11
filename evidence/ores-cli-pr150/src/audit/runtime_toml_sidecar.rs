use std::collections::BTreeSet;
use std::fs;
use std::io;
use std::net::IpAddr;
use std::path::{Component, Path};

use toml::Value;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONFIG: &str = ".ores-sidecar.toml";
const CONFIG_PROTOCOL: &str = "ores.sidecar-config.v1";
const ROOT_KEYS: &[&str] = &["protocol", "runtimeUpdates", "sidecars"];
const RUNTIME_UPDATE_KEYS: &[&str] = &["provider", "lruConfigPath", "role", "cache"];
const SIDECAR_KEYS: &[&str] = &[
    "name",
    "enabled",
    "bindIp",
    "bindPort",
    "loopbackOnly",
    "runtimeNamespace",
    "runtimeKeys",
];
const MAX_SIDECARS: usize = 64;
const MAX_RUNTIME_KEYS: usize = 128;
const MAX_PORTABLE_ID_BYTES: usize = 96;
const MAX_ENV_KEY_BYTES: usize = 128;
const MAX_REPOSITORY_PATH_BYTES: usize = 256;

/// Apply invariants shared by the independently authored Sidecar TypeSpec and
/// Draft 2020-12 JSON Schema, plus repository-boundary operational policy that
/// is intentionally narrower than owner/runtime semantics. Full schema
/// admission remains owner/TJSV work.
pub(super) fn augment_sidecar_runtime_toml_audit(
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

    let issues_before = report.issue_count();
    audit_closed_keys(root, ROOT_KEYS, "root", &mut report);

    if root.get("protocol").and_then(Value::as_str) != Some(CONFIG_PROTOCOL) {
        push_error(
            &mut report,
            "sidecar-protocol",
            "protocol must equal ores.sidecar-config.v1",
            "protocol",
        );
    }

    audit_runtime_updates(root.get("runtimeUpdates"), options, &mut report);
    audit_sidecars(root.get("sidecars"), &mut report);

    if report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "sidecar-domain-inspected",
                ".ores-sidecar.toml passed peer-authority and repository-boundary inspection",
            )
            .with_target(CONFIG),
        );
    }

    report.finalize()
}

fn audit_runtime_updates(
    value: Option<&Value>,
    options: &RepositoryAuditOptions,
    report: &mut CommandReport,
) {
    let Some(table) = value.and_then(Value::as_table) else {
        push_error(
            report,
            "sidecar-runtime-updates-shape",
            "runtimeUpdates must be a TOML table",
            "runtimeUpdates",
        );
        return;
    };

    audit_closed_keys(table, RUNTIME_UPDATE_KEYS, "runtimeUpdates", report);
    require_string_eq(
        table,
        "provider",
        "ores-redis-lru-cache",
        "sidecar-runtime-provider",
        "runtimeUpdates",
        report,
    );
    require_string_eq(
        table,
        "role",
        "server",
        "sidecar-runtime-role",
        "runtimeUpdates",
        report,
    );
    require_string_eq(
        table,
        "cache",
        "runtime-env",
        "sidecar-runtime-cache",
        "runtimeUpdates",
        report,
    );

    let target = "runtimeUpdates.lruConfigPath";
    match table.get("lruConfigPath").and_then(Value::as_str) {
        Some(path) if safe_repository_relative_path(path) => {
            let referenced = options.path.join(path);
            match fs::symlink_metadata(referenced) {
                Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => {}
                _ => push_error(
                    report,
                    "sidecar-lru-config-missing",
                    "lruConfigPath must resolve to a regular repository-owned file",
                    target,
                ),
            }
        }
        _ => push_error(
            report,
            "sidecar-lru-config-path",
            "lruConfigPath must be a bounded portable repository-relative path without traversal",
            target,
        ),
    }
}

fn audit_sidecars(value: Option<&Value>, report: &mut CommandReport) {
    let Some(sidecars) = value.and_then(Value::as_array) else {
        push_error(
            report,
            "sidecar-list-shape",
            "sidecars must be an array of TOML tables",
            "sidecars",
        );
        return;
    };

    if sidecars.is_empty() || sidecars.len() > MAX_SIDECARS {
        push_error(
            report,
            "sidecar-count",
            "sidecars must contain between 1 and 64 entries",
            "sidecars",
        );
    }

    let mut names = BTreeSet::new();
    for (index, sidecar) in sidecars.iter().enumerate() {
        let target = format!("sidecars[{index}]");
        let Some(table) = sidecar.as_table() else {
            push_error(
                report,
                "sidecar-entry-shape",
                "sidecar entries must be TOML tables",
                &target,
            );
            continue;
        };

        audit_closed_keys(table, SIDECAR_KEYS, &target, report);
        audit_portable_id(
            table,
            "name",
            &target,
            "sidecar-name",
            "sidecar name must be a bounded portable identity",
            report,
        );
        if let Some(name) = table.get("name").and_then(Value::as_str) {
            if valid_portable_id(name) && !names.insert(name.to_owned()) {
                push_error(
                    report,
                    "sidecar-name-duplicate",
                    "sidecar names must be unique",
                    &format!("{target}.name"),
                );
            }
        }

        require_bool(table, "enabled", &target, report);
        audit_bind_ip(table, &target, report);
        require_listener_port(table, "bindPort", &target, report);
        require_bool(table, "loopbackOnly", &target, report);
        audit_portable_id(
            table,
            "runtimeNamespace",
            &target,
            "sidecar-runtime-namespace",
            "runtimeNamespace must be a bounded portable identity",
            report,
        );
        audit_runtime_keys(table.get("runtimeKeys"), &target, report);
    }
}

fn audit_bind_ip(
    table: &toml::map::Map<String, Value>,
    target: &str,
    report: &mut CommandReport,
) {
    let Some(bind_ip) = table.get("bindIp").and_then(Value::as_str) else {
        push_error(
            report,
            "sidecar-bind-ip",
            "bindIp must be an IPv4 or IPv6 address",
            &format!("{target}.bindIp"),
        );
        return;
    };
    let Ok(address) = bind_ip.parse::<IpAddr>() else {
        push_error(
            report,
            "sidecar-bind-ip",
            "bindIp must be an IPv4 or IPv6 address",
            &format!("{target}.bindIp"),
        );
        return;
    };
    if table.get("loopbackOnly").and_then(Value::as_bool) == Some(true) && !address.is_loopback() {
        push_error(
            report,
            "sidecar-loopback-boundary",
            "loopbackOnly sidecars must bind a loopback address",
            &format!("{target}.bindIp"),
        );
    }
}

fn audit_runtime_keys(value: Option<&Value>, target: &str, report: &mut CommandReport) {
    let Some(keys) = value.and_then(Value::as_array) else {
        push_error(
            report,
            "sidecar-string-array-shape",
            "runtimeKeys must be an array of strings",
            &format!("{target}.runtimeKeys"),
        );
        return;
    };

    if keys.len() > MAX_RUNTIME_KEYS {
        push_error(
            report,
            "sidecar-runtime-key-count",
            "runtimeKeys may contain at most 128 entries",
            &format!("{target}.runtimeKeys"),
        );
    }

    let mut seen = BTreeSet::new();
    for (index, value) in keys.iter().enumerate() {
        let item_target = format!("{target}.runtimeKeys[{index}]");
        let Some(key) = value.as_str() else {
            push_error(
                report,
                "sidecar-runtime-key-shape",
                "runtimeKeys entries must be strings",
                &item_target,
            );
            continue;
        };
        if !valid_env_key(key) {
            push_error(
                report,
                "sidecar-runtime-key-shape",
                "runtimeKeys entries must be bounded uppercase environment names",
                &item_target,
            );
        }
        if sensitive_runtime_key(key) {
            push_error(
                report,
                "sidecar-runtime-key-secret",
                "credential-bearing environment keys may not be runtime mutable",
                &item_target,
            );
        }
        if !seen.insert(key.to_owned()) {
            push_error(
                report,
                "sidecar-runtime-key-duplicate",
                "runtimeKeys entries must be unique within a sidecar",
                &item_target,
            );
        }
    }
}

fn audit_closed_keys(
    table: &toml::map::Map<String, Value>,
    allowed: &[&str],
    target: &str,
    report: &mut CommandReport,
) {
    for key in table.keys() {
        if !allowed.contains(&key.as_str()) {
            push_error(
                report,
                "sidecar-unknown-field",
                "field is not declared by the peer-authority SidecarConfig shape",
                &format!("{target}.{key}"),
            );
        }
    }
}

fn require_string_eq(
    table: &toml::map::Map<String, Value>,
    field: &str,
    expected: &str,
    code: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_str) != Some(expected) {
        push_error(
            report,
            code,
            "string value does not match the admitted repository policy constant",
            &format!("{target}.{field}"),
        );
    }
}

fn audit_portable_id(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    code: &str,
    message: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_str)
        .is_some_and(valid_portable_id)
    {
        push_error(report, code, message, &format!("{target}.{field}"));
    }
}

fn require_bool(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_bool).is_none() {
        push_error(
            report,
            "sidecar-boolean-shape",
            "required field must be boolean",
            &format!("{target}.{field}"),
        );
    }
}

fn require_listener_port(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_integer)
        .is_some_and(|value| (1..=65_535).contains(&value))
    {
        push_error(
            report,
            "sidecar-listener-port",
            "bindPort must be an integer in the listener-port range 1..=65535",
            &format!("{target}.{field}"),
        );
    }
}

fn safe_repository_relative_path(value: &str) -> bool {
    if value.is_empty()
        || value.len() > MAX_REPOSITORY_PATH_BYTES
        || value.contains('\\')
        || value.contains(':')
    {
        return false;
    }
    let path = Path::new(value);
    !path.is_absolute()
        && path.components().all(|component| matches!(component, Component::Normal(_)))
}

fn valid_portable_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= MAX_PORTABLE_ID_BYTES
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
}

fn valid_env_key(value: &str) -> bool {
    let mut bytes = value.bytes();
    let Some(first) = bytes.next() else {
        return false;
    };
    value.len() <= MAX_ENV_KEY_BYTES
        && (first.is_ascii_uppercase() || first == b'_')
        && bytes.all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
}

fn sensitive_runtime_key(key: &str) -> bool {
    let parts: Vec<&str> = key.split('_').collect();
    if parts
        .iter()
        .any(|part| matches!(*part, "SECRET" | "PASSWORD" | "CREDENTIAL" | "CREDENTIALS"))
    {
        return true;
    }
    let has_pair = |left: &str, right: &str| {
        parts
            .windows(2)
            .any(|pair| pair.first() == Some(&left) && pair.get(1) == Some(&right))
    };
    has_pair("PRIVATE", "KEY")
        || has_pair("API", "KEY")
        || has_pair("DATABASE", "URL")
        || has_pair("AUTH", "TOKEN")
        || has_pair("ACCESS", "TOKEN")
        || has_pair("REFRESH", "TOKEN")
        || has_pair("SESSION", "TOKEN")
        || has_pair("BEARER", "TOKEN")
        || has_pair("SIGNING", "KEY")
        || has_pair("HMAC", "KEY")
}

fn push_error(report: &mut CommandReport, code: &str, message: &str, target: &str) {
    report.push(Finding::error(code, message).with_target(target.to_owned()));
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::augment_sidecar_runtime_toml_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    const OWNER_EXAMPLE: &str = r#"
protocol = "ores.sidecar-config.v1"

[runtimeUpdates]
provider = "ores-redis-lru-cache"
lruConfigPath = ".ores-lru.toml"
role = "server"
cache = "runtime-env"

[[sidecars]]
name = "api"
enabled = true
bindIp = "127.0.0.1"
bindPort = 7410
loopbackOnly = true
runtimeNamespace = "example-api"
runtimeKeys = ["REQUEST_TIMEOUT_MS"]

[[sidecars]]
name = "worker"
enabled = true
bindIp = "::1"
bindPort = 7420
loopbackOnly = true
runtimeNamespace = "example-worker"
runtimeKeys = ["WORKER_BATCH_SIZE", "TOKEN_BUCKET_POLICY"]
"#;

    fn audit(contents: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".ores-sidecar.toml"), contents).expect("write config");
        fs::write(root.path().join(".ores-lru.toml"), "protocol = \"test\"\n")
            .expect("write referenced lru config");
        augment_sidecar_runtime_toml_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    fn audit_without_lru(contents: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".ores-sidecar.toml"), contents).expect("write config");
        augment_sidecar_runtime_toml_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn owner_example_passes_peer_authority_and_operational_boundary_audit() {
        let report = audit(OWNER_EXAMPLE);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-domain-inspected")
        );
    }

    #[test]
    fn protocol_unknown_field_and_listener_port_fail_closed() {
        let report = audit(
            &OWNER_EXAMPLE
                .replace("ores.sidecar-config.v1", "ores.sidecar-config.v2")
                .replace(
                    "bindPort = 7410",
                    "bindPort = 0\nplaintextToken = \"nope\"",
                ),
        );
        assert!(report.findings.iter().any(|finding| finding.code == "sidecar-protocol"));
        assert!(report.findings.iter().any(|finding| finding.code == "sidecar-unknown-field"));
        assert!(report.findings.iter().any(|finding| finding.code == "sidecar-listener-port"));
    }

    #[test]
    fn runtime_update_policy_requires_exact_cache_and_repository_owned_lru_file() {
        let report = audit_without_lru(&OWNER_EXAMPLE.replace("cache = \"runtime-env\"", "cache = \"other\""));
        assert!(report.findings.iter().any(|finding| finding.code == "sidecar-runtime-cache"));
        assert!(report.findings.iter().any(|finding| finding.code == "sidecar-lru-config-missing"));
    }

    #[test]
    fn lru_config_path_rejects_parent_traversal_and_platform_specific_forms() {
        for unsafe_path in ["../.ores-lru.toml", "config\\.ores-lru.toml", "C:/tmp/.ores-lru.toml"] {
            let report = audit(&OWNER_EXAMPLE.replace(".ores-lru.toml", unsafe_path));
            assert!(
                report
                    .findings
                    .iter()
                    .any(|finding| finding.code == "sidecar-lru-config-path"),
                "path {unsafe_path}: {:#?}",
                report.findings
            );
        }
    }

    #[test]
    fn loopback_only_rejects_non_loopback_and_malformed_bind_addresses() {
        let non_loopback = audit(&OWNER_EXAMPLE.replacen("127.0.0.1", "0.0.0.0", 1));
        assert!(
            non_loopback
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-loopback-boundary")
        );

        let malformed = audit(&OWNER_EXAMPLE.replacen("127.0.0.1", "localhost", 1));
        assert!(
            malformed
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-bind-ip")
        );
    }

    #[test]
    fn duplicate_sidecar_names_and_runtime_keys_fail_closed() {
        let duplicate_name = audit(&OWNER_EXAMPLE.replace("name = \"worker\"", "name = \"api\""));
        assert!(
            duplicate_name
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-name-duplicate")
        );

        let duplicate_key = audit(&OWNER_EXAMPLE.replace(
            "runtimeKeys = [\"REQUEST_TIMEOUT_MS\"]",
            "runtimeKeys = [\"REQUEST_TIMEOUT_MS\", \"REQUEST_TIMEOUT_MS\"]",
        ));
        assert!(
            duplicate_key
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-runtime-key-duplicate")
        );
    }

    #[test]
    fn credential_runtime_keys_fail_closed_but_token_bucket_policy_is_not_a_secret() {
        let report = audit(&OWNER_EXAMPLE.replace(
            "runtimeKeys = [\"REQUEST_TIMEOUT_MS\"]",
            "runtimeKeys = [\"REQUEST_TIMEOUT_MS\", \"AUTH_TOKEN\", \"DATABASE_URL\"]",
        ));
        let secret_findings = report
            .findings
            .iter()
            .filter(|finding| finding.code == "sidecar-runtime-key-secret")
            .count();
        assert_eq!(secret_findings, 2, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .all(|finding| finding.target.as_deref() != Some("sidecars[1].runtimeKeys[1]"))
        );
    }
}
