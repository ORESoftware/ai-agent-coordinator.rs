use std::fs;
use std::io;

use toml::Value;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONFIG: &str = ".ores-chat.toml";
const ROOT_KEYS: &[&str] = &[
    "version",
    "mode",
    "strict",
    "flags2env",
    "env",
    "client",
    "server",
];
const FLAGS2ENV_KEYS: &[&str] = &["contract", "require_audit", "precedence"];
const ENV_KEYS: &[&str] = &[
    "name",
    "key",
    "kind",
    "required",
    "secret",
    "default",
    "description",
];
const CLIENT_KEYS: &[&str] = &[
    "enabled",
    "api_base_url_binding",
    "websocket_url_binding",
    "tenant_id_binding",
    "auth_token_binding",
];
const SERVER_KEYS: &[&str] = &[
    "enabled",
    "bind_addr_binding",
    "database_url_binding",
    "shared_auth_issuer_binding",
    "nats_url_binding",
    "retention_days_binding",
    "presence_ttl_seconds_binding",
];
const MODES: &[&str] = &["client", "server", "hybrid"];
const ENV_KINDS: &[&str] = &["string", "bool", "integer", "double", "json", "url"];

/// Validate only `.ores-chat.toml` invariants stated by both peer authorities
/// in `ores-chat/ores-chat-interfaces`. Cross-field runtime policy, secret
/// admission, binding resolution, and TJSV parity remain owner responsibilities.
pub(super) fn augment_chat_runtime_toml_audit(
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
    require_integer_eq(root, "version", 1, "chat-version", "root", &mut report);
    require_enum(root, "mode", MODES, "chat-mode", "root", &mut report);
    require_bool(root, "strict", "root", &mut report);
    audit_flags2env(root.get("flags2env"), &mut report);
    audit_env(root.get("env"), &mut report);
    audit_role_table(root.get("client"), CLIENT_KEYS, "client", &mut report);
    audit_role_table(root.get("server"), SERVER_KEYS, "server", &mut report);

    if report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "chat-domain-inspected",
                ".ores-chat.toml passed the bounded peer-authority invariant audit",
            )
            .with_target(CONFIG),
        );
    }

    report.finalize()
}

fn audit_flags2env(value: Option<&Value>, report: &mut CommandReport) {
    let Some(table) = value.and_then(Value::as_table) else {
        push_error(
            report,
            "chat-flags2env-shape",
            "flags2env must be a TOML table",
            "flags2env",
        );
        return;
    };
    audit_closed_keys(table, FLAGS2ENV_KEYS, "flags2env", report);
    require_bounded_string(table, "contract", 1, 256, "flags2env", report);
    require_bool(table, "require_audit", "flags2env", report);
    require_string_eq(
        table,
        "precedence",
        "argv-over-env",
        "chat-flags2env-precedence",
        "flags2env",
        report,
    );
}

fn audit_env(value: Option<&Value>, report: &mut CommandReport) {
    let Some(entries) = value.and_then(Value::as_array) else {
        push_error(
            report,
            "chat-env-list-shape",
            "env must be an array of TOML tables",
            "env",
        );
        return;
    };

    for (index, entry) in entries.iter().enumerate() {
        let target = format!("env[{index}]");
        let Some(table) = entry.as_table() else {
            push_error(
                report,
                "chat-env-entry-shape",
                "env entries must be TOML tables",
                &target,
            );
            continue;
        };
        audit_closed_keys(table, ENV_KEYS, &target, report);
        require_pattern_string(
            table,
            "name",
            1,
            64,
            is_logical_name,
            "chat-env-name",
            &target,
            report,
        );
        require_pattern_string(
            table,
            "key",
            1,
            128,
            is_env_key,
            "chat-env-key",
            &target,
            report,
        );
        require_enum(table, "kind", ENV_KINDS, "chat-env-kind", &target, report);
        require_bool(table, "required", &target, report);
        require_bool(table, "secret", &target, report);
        optional_bounded_string(table, "default", 0, 8192, &target, report);
        optional_bounded_string(table, "description", 0, 512, &target, report);
    }
}

fn audit_role_table(
    value: Option<&Value>,
    allowed: &[&str],
    target: &str,
    report: &mut CommandReport,
) {
    let Some(value) = value else {
        return;
    };
    let Some(table) = value.as_table() else {
        push_error(
            report,
            "chat-role-shape",
            "role configuration must be a TOML table",
            target,
        );
        return;
    };
    audit_closed_keys(table, allowed, target, report);
    require_bool(table, "enabled", target, report);
    for key in allowed.iter().copied().filter(|key| *key != "enabled") {
        optional_bounded_string(table, key, 1, 64, target, report);
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
                "chat-unknown-field",
                "field is not declared by the ORES Chat peer-authority shape",
                &format!("{target}.{key}"),
            );
        }
    }
}

fn require_integer_eq(
    table: &toml::map::Map<String, Value>,
    field: &str,
    expected: i64,
    code: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_integer) != Some(expected) {
        push_error(
            report,
            code,
            "integer value does not match the peer-authority constant",
            &format!("{target}.{field}"),
        );
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
            "string value does not match the peer-authority constant",
            &format!("{target}.{field}"),
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
        push_error(
            report,
            "chat-boolean-shape",
            "required field must be boolean",
            &format!("{target}.{field}"),
        );
    }
}

fn require_enum(
    table: &toml::map::Map<String, Value>,
    field: &str,
    allowed: &[&str],
    code: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_str)
        .is_some_and(|value| allowed.contains(&value))
    {
        push_error(
            report,
            code,
            "string value is outside the peer-authority enum",
            &format!("{target}.{field}"),
        );
    }
}

fn require_bounded_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    min: usize,
    max: usize,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_str)
        .is_some_and(|value| value.len() >= min && value.len() <= max)
    {
        push_error(
            report,
            "chat-string-bounds",
            "required string is outside peer-authority length bounds",
            &format!("{target}.{field}"),
        );
    }
}

fn require_pattern_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    min: usize,
    max: usize,
    predicate: fn(&str) -> bool,
    code: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_str)
        .is_some_and(|value| value.len() >= min && value.len() <= max && predicate(value))
    {
        push_error(
            report,
            code,
            "string does not satisfy the shared peer-authority pattern/bounds",
            &format!("{target}.{field}"),
        );
    }
}

fn optional_bounded_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    min: usize,
    max: usize,
    target: &str,
    report: &mut CommandReport,
) {
    if table.contains_key(field)
        && !table
            .get(field)
            .and_then(Value::as_str)
            .is_some_and(|value| value.len() >= min && value.len() <= max)
    {
        push_error(
            report,
            "chat-optional-string-bounds",
            "optional field must be a string within peer-authority length bounds",
            &format!("{target}.{field}"),
        );
    }
}

fn is_logical_name(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some('a'..='z'))
        && chars.all(|character| {
            character.is_ascii_lowercase() || character.is_ascii_digit() || character == '_'
        })
}

fn is_env_key(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some('A'..='Z') | Some('_'))
        && chars.all(|character| {
            character.is_ascii_uppercase() || character.is_ascii_digit() || character == '_'
        })
}

fn push_error(report: &mut CommandReport, code: &str, message: &str, target: &str) {
    report.push(Finding::error(code, message).with_target(target.to_owned()));
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::augment_chat_runtime_toml_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    const SERVER_SHAPED: &str = r#"
version = 1
mode = "server"
strict = true

[flags2env]
contract = ".cli-flags.toml"
require_audit = true
precedence = "argv-over-env"

[[env]]
name = "bind_addr"
key = "ORES_CHAT_BIND"
kind = "string"
required = false
secret = false
default = "127.0.0.1:8080"
description = "Customer API listen address."

[[env]]
name = "shared_auth_issuer"
key = "SHARED_AUTH_ISSUER"
kind = "url"
required = true
secret = false

[server]
enabled = true
bind_addr_binding = "bind_addr"
shared_auth_issuer_binding = "shared_auth_issuer"
"#;

    fn audit(contents: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".ores-chat.toml"), contents).expect("write config");
        augment_chat_runtime_toml_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn server_shaped_config_passes() {
        let report = audit(SERVER_SHAPED);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "chat-domain-inspected")
        );
    }

    #[test]
    fn client_and_hybrid_surface_passes() {
        let report = audit(
            &SERVER_SHAPED
                .replace("mode = \"server\"", "mode = \"hybrid\"")
                .replace(
                    "[server]",
                    "[client]\nenabled = true\napi_base_url_binding = \"shared_auth_issuer\"\nwebsocket_url_binding = \"shared_auth_issuer\"\n\n[server]",
                ),
        );
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
    }

    #[test]
    fn version_mode_precedence_kind_and_unknown_fields_fail_closed() {
        let report = audit(
            &SERVER_SHAPED
                .replace("version = 1", "version = 2\nextra = true")
                .replace("mode = \"server\"", "mode = \"peer\"")
                .replace("argv-over-env", "env-over-argv")
                .replace("kind = \"url\"", "kind = \"bytes\""),
        );
        for code in [
            "chat-version",
            "chat-unknown-field",
            "chat-mode",
            "chat-flags2env-precedence",
            "chat-env-kind",
        ] {
            assert!(
                report.findings.iter().any(|finding| finding.code == code),
                "missing {code}: {:#?}",
                report.findings
            );
        }
    }

    #[test]
    fn shared_string_patterns_and_bounds_fail_closed() {
        let long_binding = "x".repeat(65);
        let report = audit(
            &SERVER_SHAPED
                .replace("name = \"bind_addr\"", "name = \"Bind-Addr\"")
                .replace("key = \"ORES_CHAT_BIND\"", "key = \"ores-chat-bind\"")
                .replace(
                    "bind_addr_binding = \"bind_addr\"",
                    &format!("bind_addr_binding = \"{long_binding}\""),
                ),
        );
        for code in [
            "chat-env-name",
            "chat-env-key",
            "chat-optional-string-bounds",
        ] {
            assert!(
                report.findings.iter().any(|finding| finding.code == code),
                "missing {code}: {:#?}",
                report.findings
            );
        }
    }
}
