use std::fs;
use std::io;

use toml::Value;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONFIG: &str = ".ores-mw.toml";
const ROOT_KEYS: &[&str] = &[
    "schema_version",
    "repository_mode",
    "allow_overlapping_roots",
    "default_target",
    "flags2env",
    "env",
    "targets",
];
const FLAGS2ENV_KEYS: &[&str] = &["contract", "require_audit", "precedence"];
const ENV_KEYS: &[&str] = &[
    "name",
    "key",
    "kind",
    "required",
    "secret",
    "default_value",
    "description",
];
const TARGET_KEYS: &[&str] = &[
    "name",
    "role",
    "roots",
    "enabled",
    "middleware",
    "stack_config",
    "propagate_headers",
];
const REPOSITORY_MODES: &[&str] = &["server-only", "client-only", "hybrid"];
const ENV_KINDS: &[&str] = &["string", "bool", "integer", "double", "json", "url"];
const TARGET_ROLES: &[&str] = &["server", "client"];
const MIDDLEWARE_MODES: &[&str] = &["stack", "propagation-only", "disabled"];

/// Apply only raw-TOML shape invariants that survive normalization into both
/// independently authored middleware peer authorities. The owner compiler owns
/// defaults, path/reference semantics, overlap checks, and cross-field policy;
/// full admission remains owner/TJSV work.
pub(super) fn augment_middleware_runtime_toml_audit(
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
    require_integer_eq(root, "schema_version", 1, "middleware-schema-version", "root", &mut report);
    require_enum(
        root,
        "repository_mode",
        REPOSITORY_MODES,
        "middleware-repository-mode",
        "root",
        &mut report,
    );
    optional_bool(root, "allow_overlapping_roots", "root", &mut report);
    optional_string(root, "default_target", "root", &mut report);
    audit_flags2env(root.get("flags2env"), &mut report);
    audit_env(root.get("env"), &mut report);
    audit_targets(root.get("targets"), &mut report);

    if report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "middleware-domain-inspected",
                ".ores-mw.toml passed the bounded peer-authority invariant audit",
            )
            .with_target(CONFIG),
        );
    }

    report.finalize()
}

fn audit_flags2env(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else {
        return;
    };
    let Some(table) = value.as_table() else {
        push_error(
            report,
            "middleware-flags2env-shape",
            "flags2env must be a TOML table",
            "flags2env",
        );
        return;
    };

    audit_closed_keys(table, FLAGS2ENV_KEYS, "flags2env", report);
    require_string(table, "contract", "flags2env", report);
    require_bool(table, "require_audit", "flags2env", report);
    require_string_eq(
        table,
        "precedence",
        "argv-over-env",
        "middleware-flags2env-precedence",
        "flags2env",
        report,
    );
}

fn audit_env(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else {
        return;
    };
    let Some(entries) = value.as_array() else {
        push_error(
            report,
            "middleware-env-list-shape",
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
                "middleware-env-entry-shape",
                "env entries must be TOML tables",
                &target,
            );
            continue;
        };

        audit_closed_keys(table, ENV_KEYS, &target, report);
        require_string(table, "name", &target, report);
        require_string(table, "key", &target, report);
        require_enum(
            table,
            "kind",
            ENV_KINDS,
            "middleware-env-kind",
            &target,
            report,
        );
        require_bool(table, "required", &target, report);
        require_bool(table, "secret", &target, report);
        optional_string(table, "default_value", &target, report);
        optional_string(table, "description", &target, report);
    }
}

fn audit_targets(value: Option<&Value>, report: &mut CommandReport) {
    let Some(targets) = value.and_then(Value::as_array) else {
        push_error(
            report,
            "middleware-target-list-shape",
            "targets must be an array of TOML tables",
            "targets",
        );
        return;
    };

    for (index, entry) in targets.iter().enumerate() {
        let target = format!("targets[{index}]");
        let Some(table) = entry.as_table() else {
            push_error(
                report,
                "middleware-target-entry-shape",
                "target entries must be TOML tables",
                &target,
            );
            continue;
        };

        audit_closed_keys(table, TARGET_KEYS, &target, report);
        require_string(table, "name", &target, report);
        require_enum(
            table,
            "role",
            TARGET_ROLES,
            "middleware-target-role",
            &target,
            report,
        );
        require_string_array(table, "roots", &target, report);
        optional_bool(table, "enabled", &target, report);
        require_enum(
            table,
            "middleware",
            MIDDLEWARE_MODES,
            "middleware-target-mode",
            &target,
            report,
        );
        optional_string(table, "stack_config", &target, report);
        optional_string_array(table, "propagate_headers", &target, report);
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
                "middleware-unknown-field",
                "field is not declared by the raw middleware orchestration shape",
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

fn require_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_str).is_none() {
        push_error(
            report,
            "middleware-string-shape",
            "required field must be a string",
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
            "middleware-boolean-shape",
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

fn require_string_array(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_array)
        .is_some_and(|values| values.iter().all(|value| value.as_str().is_some()))
    {
        push_error(
            report,
            "middleware-string-array-shape",
            "required field must be an array of strings",
            &format!("{target}.{field}"),
        );
    }
}

fn optional_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.contains_key(field) && table.get(field).and_then(Value::as_str).is_none() {
        push_error(
            report,
            "middleware-optional-string-shape",
            "optional field must be a string when present",
            &format!("{target}.{field}"),
        );
    }
}

fn optional_bool(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.contains_key(field) && table.get(field).and_then(Value::as_bool).is_none() {
        push_error(
            report,
            "middleware-optional-boolean-shape",
            "optional field must be boolean when present",
            &format!("{target}.{field}"),
        );
    }
}

fn optional_string_array(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.contains_key(field)
        && !table
            .get(field)
            .and_then(Value::as_array)
            .is_some_and(|values| values.iter().all(|value| value.as_str().is_some()))
    {
        push_error(
            report,
            "middleware-optional-string-array-shape",
            "optional field must be an array of strings when present",
            &format!("{target}.{field}"),
        );
    }
}

fn push_error(report: &mut CommandReport, code: &str, message: &str, target: &str) {
    report.push(Finding::error(code, message).with_target(target.to_owned()));
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::augment_middleware_runtime_toml_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    const OWNER_SHAPED: &str = r#"
schema_version = 1
repository_mode = "server-only"
default_target = "portable-adapters"

[[targets]]
name = "portable-adapters"
role = "server"
roots = ["src"]
middleware = "stack"
stack_config = "contracts/fixtures/stack.minimal.json"
"#;

    const FULL_SHAPED: &str = r#"
schema_version = 1
repository_mode = "hybrid"
allow_overlapping_roots = false
default_target = "api"

[flags2env]
contract = ".cli-flags.toml"
require_audit = true
precedence = "argv-over-env"

[[env]]
name = "request-timeout"
key = "REQUEST_TIMEOUT_MS"
kind = "integer"
required = true
secret = false
default_value = "5000"
description = "request timeout"

[[targets]]
name = "api"
role = "server"
roots = ["server"]
enabled = true
middleware = "stack"
stack_config = "config/middleware.server.json"

[[targets]]
name = "browser"
role = "client"
roots = ["web"]
middleware = "propagation-only"
propagate_headers = ["traceparent", "x-request-id"]
"#;

    fn audit(contents: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".ores-mw.toml"), contents).expect("write config");
        augment_middleware_runtime_toml_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn owner_shaped_config_passes_without_requiring_normalizer_defaults_in_raw_toml() {
        let report = audit(OWNER_SHAPED);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "middleware-domain-inspected")
        );
    }

    #[test]
    fn complete_peer_authority_surface_passes() {
        let report = audit(FULL_SHAPED);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
    }

    #[test]
    fn version_repository_mode_unknown_field_and_target_enum_fail_closed() {
        let report = audit(
            &OWNER_SHAPED
                .replace("schema_version = 1", "schema_version = 2\nplaintext_token = \"nope\"")
                .replace("server-only", "peer-to-peer")
                .replace("role = \"server\"", "role = \"admin\"")
                .replace("middleware = \"stack\"", "middleware = \"magic\""),
        );
        for code in [
            "middleware-schema-version",
            "middleware-repository-mode",
            "middleware-unknown-field",
            "middleware-target-role",
            "middleware-target-mode",
        ] {
            assert!(
                report.findings.iter().any(|finding| finding.code == code),
                "missing {code}: {:#?}",
                report.findings
            );
        }
    }

    #[test]
    fn flags_env_and_collection_shapes_fail_closed() {
        let report = audit(
            r#"
schema_version = 1
repository_mode = "hybrid"

[flags2env]
contract = ".cli-flags.toml"
require_audit = "yes"
precedence = "env-over-argv"

[[env]]
name = "key"
key = "API_KEY"
kind = "bytes"
required = true
secret = false

[[targets]]
name = "api"
role = "server"
roots = "src"
middleware = "stack"
propagate_headers = "traceparent"
"#,
        );
        for code in [
            "middleware-boolean-shape",
            "middleware-flags2env-precedence",
            "middleware-env-kind",
            "middleware-string-array-shape",
            "middleware-optional-string-array-shape",
        ] {
            assert!(
                report.findings.iter().any(|finding| finding.code == code),
                "missing {code}: {:#?}",
                report.findings
            );
        }
    }
}
