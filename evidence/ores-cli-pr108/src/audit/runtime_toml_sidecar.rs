use std::fs;
use std::io;

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

/// Apply only invariants shared by the independently authored Sidecar TypeSpec
/// and Draft 2020-12 JSON Schema. Full admission remains owner/TJSV work.
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

    audit_runtime_updates(root.get("runtimeUpdates"), &mut report);
    audit_sidecars(root.get("sidecars"), &mut report);

    if report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "sidecar-domain-inspected",
                ".ores-sidecar.toml passed the bounded peer-authority invariant audit",
            )
            .with_target(CONFIG),
        );
    }

    report.finalize()
}

fn audit_runtime_updates(value: Option<&Value>, report: &mut CommandReport) {
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
    require_string(table, "lruConfigPath", "runtimeUpdates", report);
    require_string(table, "cache", "runtimeUpdates", report);
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
        require_string(table, "name", &target, report);
        require_bool(table, "enabled", &target, report);
        require_string(table, "bindIp", &target, report);
        require_u16(table, "bindPort", &target, report);
        require_bool(table, "loopbackOnly", &target, report);
        require_string(table, "runtimeNamespace", &target, report);
        require_string_array(table, "runtimeKeys", &target, report);
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
            "sidecar-string-shape",
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
            "sidecar-boolean-shape",
            "required field must be boolean",
            &format!("{target}.{field}"),
        );
    }
}

fn require_u16(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_integer)
        .is_some_and(|value| (0..=65_535).contains(&value))
    {
        push_error(
            report,
            "sidecar-u16-shape",
            "bindPort must be an integer in the uint16 range",
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
            "sidecar-string-array-shape",
            "runtimeKeys must be an array of strings",
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
bindIp = "127.0.0.1"
bindPort = 7420
loopbackOnly = true
runtimeNamespace = "example-worker"
runtimeKeys = ["WORKER_BATCH_SIZE"]
"#;

    fn audit(contents: &str) -> CommandReport {
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
    fn owner_example_passes_bounded_peer_authority_audit() {
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
    fn protocol_unknown_field_and_port_range_fail_closed() {
        let report = audit(
            &OWNER_EXAMPLE
                .replace("ores.sidecar-config.v1", "ores.sidecar-config.v2")
                .replace(
                    "bindPort = 7410",
                    "bindPort = 65536\nplaintextToken = \"nope\"",
                ),
        );
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-protocol")
        );
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-unknown-field")
        );
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-u16-shape")
        );
    }

    #[test]
    fn missing_required_fields_fail_closed() {
        let report = audit(&OWNER_EXAMPLE.replace("cache = \"runtime-env\"\n", ""));
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "sidecar-string-shape")
        );
    }
}
