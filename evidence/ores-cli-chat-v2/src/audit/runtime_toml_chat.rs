use std::collections::BTreeSet;
use std::fs;
use std::io;

use toml::Value;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONFIG: &str = ".ores-chat.toml";
const MAX_CONFIG_BYTES: u64 = 256 * 1024;
const MAX_ENV_ENTRIES: usize = 128;
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

pub(super) fn augment_chat_runtime_toml_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    let path = options.path.join(CONFIG);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return report.finalize(),
        Err(error) => {
            error_finding(
                &mut report,
                "chat-config-metadata",
                format!("could not inspect {CONFIG}: {error}"),
                CONFIG,
            );
            return report.finalize();
        }
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        error_finding(
            &mut report,
            "chat-config-file-kind",
            "chat config must be a regular non-symlink file",
            CONFIG,
        );
        return report.finalize();
    }
    if metadata.len() > MAX_CONFIG_BYTES {
        error_finding(
            &mut report,
            "chat-config-size",
            format!("chat config exceeds {MAX_CONFIG_BYTES} bytes"),
            CONFIG,
        );
        return report.finalize();
    }

    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) => {
            error_finding(
                &mut report,
                "chat-config-utf8",
                format!("chat config must be readable UTF-8: {error}"),
                CONFIG,
            );
            return report.finalize();
        }
    };
    let document: Value = match toml::from_str(&text) {
        Ok(document) => document,
        Err(error) => {
            error_finding(
                &mut report,
                "chat-config-toml",
                format!("chat config is not valid TOML: {error}"),
                CONFIG,
            );
            return report.finalize();
        }
    };
    let Some(root) = document.as_table() else {
        error_finding(
            &mut report,
            "chat-root-shape",
            "chat config root must be a TOML table",
            CONFIG,
        );
        return report.finalize();
    };

    let issues_before = report.issue_count();
    closed_keys(root, ROOT_KEYS, "root", &mut report);
    require_integer(root, "version", 1, "chat-version", "root", &mut report);
    let mode = require_enum(root, "mode", MODES, "chat-mode", "root", &mut report);
    require_bool(root, "strict", "root", &mut report);
    audit_flags2env(root.get("flags2env"), &mut report);
    let env_names = audit_env(root.get("env"), &mut report);
    let client_enabled = audit_role(
        root.get("client"),
        CLIENT_KEYS,
        "client",
        &env_names,
        &mut report,
    );
    let server_enabled = audit_role(
        root.get("server"),
        SERVER_KEYS,
        "server",
        &env_names,
        &mut report,
    );
    audit_mode_surface(mode, client_enabled, server_enabled, &mut report);

    if report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "chat-domain-inspected",
                ".ores-chat.toml passed bounded shape, binding, and secret-default checks",
            )
            .with_target(CONFIG),
        );
    }
    report.finalize()
}

fn audit_flags2env(value: Option<&Value>, report: &mut CommandReport) {
    let Some(table) = value.and_then(Value::as_table) else {
        error_finding(
            report,
            "chat-flags2env-shape",
            "flags2env must be a TOML table",
            "flags2env",
        );
        return;
    };
    closed_keys(table, FLAGS2ENV_KEYS, "flags2env", report);
    require_string_eq(
        table,
        "contract",
        ".cli-flags.toml",
        "chat-flags2env-contract",
        "flags2env",
        report,
    );
    require_true(table, "require_audit", "chat-flags2env-audit", "flags2env", report);
    require_string_eq(
        table,
        "precedence",
        "argv-over-env",
        "chat-flags2env-precedence",
        "flags2env",
        report,
    );
}

fn audit_env(value: Option<&Value>, report: &mut CommandReport) -> BTreeSet<String> {
    let Some(entries) = value.and_then(Value::as_array) else {
        error_finding(
            report,
            "chat-env-shape",
            "env must be an array of TOML tables",
            "env",
        );
        return BTreeSet::new();
    };
    if entries.is_empty() || entries.len() > MAX_ENV_ENTRIES {
        error_finding(
            report,
            "chat-env-count",
            format!("env must contain 1..={MAX_ENV_ENTRIES} entries"),
            "env",
        );
    }

    let mut names = BTreeSet::new();
    let mut keys = BTreeSet::new();
    for (index, entry) in entries.iter().enumerate() {
        let target = format!("env[{index}]");
        let Some(table) = entry.as_table() else {
            error_finding(
                report,
                "chat-env-entry-shape",
                "env entries must be TOML tables",
                target,
            );
            continue;
        };
        closed_keys(table, ENV_KEYS, &target, report);
        let name = require_pattern(
            table,
            "name",
            PatternRule {
                min: 1,
                max: 64,
                predicate: is_logical_name,
                code: "chat-env-name",
            },
            &target,
            report,
        );
        let key = require_pattern(
            table,
            "key",
            PatternRule {
                min: 1,
                max: 128,
                predicate: is_env_key,
                code: "chat-env-key",
            },
            &target,
            report,
        );
        require_enum(table, "kind", ENV_KINDS, "chat-env-kind", &target, report);
        require_bool(table, "required", &target, report);
        let secret = require_bool(table, "secret", &target, report).unwrap_or(false);
        optional_string(table, "default", 0, 8192, &target, report);
        optional_string(table, "description", 0, 512, &target, report);
        if secret && table.contains_key("default") {
            error_finding(
                report,
                "chat-secret-default",
                "secret environment declarations must not contain defaults",
                format!("{target}.default"),
            );
        }
        if let Some(name) = name {
            if !names.insert(name.clone()) {
                error_finding(
                    report,
                    "chat-env-name-duplicate",
                    format!("duplicate environment declaration name: {name}"),
                    format!("{target}.name"),
                );
            }
        }
        if let Some(key) = key {
            if !keys.insert(key.clone()) {
                error_finding(
                    report,
                    "chat-env-key-duplicate",
                    format!("duplicate environment key: {key}"),
                    format!("{target}.key"),
                );
            }
        }
    }
    names
}

fn audit_role(
    value: Option<&Value>,
    allowed: &[&str],
    target: &str,
    env_names: &BTreeSet<String>,
    report: &mut CommandReport,
) -> bool {
    let Some(value) = value else {
        return false;
    };
    let Some(table) = value.as_table() else {
        error_finding(
            report,
            "chat-role-shape",
            "role configuration must be a TOML table",
            target,
        );
        return false;
    };
    closed_keys(table, allowed, target, report);
    let enabled = require_bool(table, "enabled", target, report).unwrap_or(false);
    for field in allowed.iter().copied().filter(|field| *field != "enabled") {
        let Some(binding) = optional_string(table, field, 1, 64, target, report) else {
            continue;
        };
        if !env_names.contains(&binding) {
            error_finding(
                report,
                "chat-binding-unknown",
                format!("binding references undeclared env name: {binding}"),
                format!("{target}.{field}"),
            );
        }
    }
    enabled
}

fn audit_mode_surface(
    mode: Option<&str>,
    client_enabled: bool,
    server_enabled: bool,
    report: &mut CommandReport,
) {
    let valid = match mode {
        Some("client") => client_enabled && !server_enabled,
        Some("server") => server_enabled && !client_enabled,
        Some("hybrid") => client_enabled && server_enabled,
        _ => true,
    };
    if !valid {
        error_finding(
            report,
            "chat-mode-surface",
            "mode must agree with the enabled client/server surfaces",
            "mode",
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
            error_finding(
                report,
                "chat-unknown-field",
                "field is not declared by the ORES Chat peer-authority shape",
                format!("{target}.{key}"),
            );
        }
    }
}

fn require_integer(
    table: &toml::map::Map<String, Value>,
    field: &str,
    expected: i64,
    code: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_integer) != Some(expected) {
        error_finding(
            report,
            code,
            format!("{field} must equal {expected}"),
            format!("{target}.{field}"),
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
        error_finding(
            report,
            code,
            format!("{field} must equal {expected}"),
            format!("{target}.{field}"),
        );
    }
}

fn require_true(
    table: &toml::map::Map<String, Value>,
    field: &str,
    code: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_bool) != Some(true) {
        error_finding(
            report,
            code,
            format!("{field} must be true"),
            format!("{target}.{field}"),
        );
    }
}

fn require_bool(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) -> Option<bool> {
    let value = table.get(field).and_then(Value::as_bool);
    if value.is_none() {
        error_finding(
            report,
            "chat-boolean-shape",
            format!("{field} must be boolean"),
            format!("{target}.{field}"),
        );
    }
    value
}

fn require_enum<'a>(
    table: &'a toml::map::Map<String, Value>,
    field: &str,
    allowed: &[&str],
    code: &str,
    target: &str,
    report: &mut CommandReport,
) -> Option<&'a str> {
    let value = table.get(field).and_then(Value::as_str);
    if !value.is_some_and(|value| allowed.contains(&value)) {
        error_finding(
            report,
            code,
            format!("{field} is outside the allowed enum"),
            format!("{target}.{field}"),
        );
    }
    value
}

#[derive(Clone, Copy)]
struct PatternRule {
    min: usize,
    max: usize,
    predicate: fn(&str) -> bool,
    code: &'static str,
}

fn require_pattern(
    table: &toml::map::Map<String, Value>,
    field: &str,
    rule: PatternRule,
    target: &str,
    report: &mut CommandReport,
) -> Option<String> {
    let value = table.get(field).and_then(Value::as_str);
    if !value.is_some_and(|value| {
        value.len() >= rule.min && value.len() <= rule.max && (rule.predicate)(value)
    }) {
        error_finding(
            report,
            rule.code,
            format!("{field} does not satisfy the required grammar or bounds"),
            format!("{target}.{field}"),
        );
        return None;
    }
    value.map(str::to_owned)
}

fn optional_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    min: usize,
    max: usize,
    target: &str,
    report: &mut CommandReport,
) -> Option<String> {
    let Some(value) = table.get(field) else {
        return None;
    };
    let Some(value) = value.as_str() else {
        error_finding(
            report,
            "chat-string-shape",
            format!("{field} must be a string"),
            format!("{target}.{field}"),
        );
        return None;
    };
    if value.len() < min || value.len() > max {
        error_finding(
            report,
            "chat-string-bounds",
            format!("{field} is outside the allowed length bounds"),
            format!("{target}.{field}"),
        );
        return None;
    }
    Some(value.to_owned())
}

fn is_logical_name(value: &str) -> bool {
    let mut bytes = value.bytes();
    matches!(bytes.next(), Some(b'a'..=b'z'))
        && bytes.all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_')
}

fn is_env_key(value: &str) -> bool {
    let mut bytes = value.bytes();
    matches!(bytes.next(), Some(b'A'..=b'Z') | Some(b'_'))
        && bytes.all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
}

fn error_finding(
    report: &mut CommandReport,
    code: &str,
    message: impl Into<String>,
    target: impl Into<String>,
) {
    report.push(Finding::error(code, message.into()).with_target(target.into()));
}
