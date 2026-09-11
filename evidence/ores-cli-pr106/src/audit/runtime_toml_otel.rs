use std::fs;
use std::io;

use toml::Value;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONFIG: &str = ".ores-otel.toml";
const ROOT_KEYS: &[&str] = &["version", "common", "client", "server"];
const ROLE_KEYS: &[&str] = &[
    "enabled",
    "service_name",
    "environment",
    "logging",
    "tracing",
    "metrics",
    "exporter",
];
const LOGGING_KEYS: &[&str] = &["enabled", "level", "console", "auto_send"];
const TRACING_KEYS: &[&str] = &["enabled", "sample_ratio", "propagators"];
const METRICS_KEYS: &[&str] = &["enabled"];
const EXPORTER_KEYS: &[&str] = &["protocol", "endpoint_env"];

/// Apply only `.ores-otel.toml` invariants shared by the owner's independently
/// authored TypeSpec and Draft 2020-12 JSON Schema authorities. Runtime
/// environment resolution, protocol semantics, and exporter reachability remain
/// owner responsibilities and are intentionally not re-specified here.
pub(super) fn augment_otel_runtime_toml_audit(
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
    closed_keys(root, ROOT_KEYS, "root", &mut report);
    if root.get("version").and_then(Value::as_integer) != Some(1) {
        error(
            &mut report,
            "otel-version",
            "version must equal the peer-authority constant 1",
            "version",
        );
    }
    for section in ["common", "client", "server"] {
        audit_role(root.get(section), section, &mut report);
    }

    if report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "otel-domain-inspected",
                ".ores-otel.toml passed the bounded peer-authority invariant audit",
            )
            .with_target(CONFIG),
        );
    }
    report.finalize()
}

fn audit_role(value: Option<&Value>, target: &str, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        error(
            report,
            "otel-section-shape",
            "OTel role section must be a table",
            target,
        );
        return;
    };
    closed_keys(table, ROLE_KEYS, target, report);
    optional_bool(table, "enabled", target, report);
    optional_bounded_string(table, "service_name", 1, 256, target, report);
    optional_bounded_string(table, "environment", 1, 128, target, report);
    audit_logging(table.get("logging"), &format!("{target}.logging"), report);
    audit_tracing(table.get("tracing"), &format!("{target}.tracing"), report);
    audit_metrics(table.get("metrics"), &format!("{target}.metrics"), report);
    audit_exporter(table.get("exporter"), &format!("{target}.exporter"), report);
}

fn audit_logging(value: Option<&Value>, target: &str, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        error(report, "otel-logging-shape", "logging must be a table", target);
        return;
    };
    closed_keys(table, LOGGING_KEYS, target, report);
    for field in ["enabled", "console", "auto_send"] {
        optional_bool(table, field, target, report);
    }
    optional_bounded_string(table, "level", 1, 16, target, report);
}

fn audit_tracing(value: Option<&Value>, target: &str, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        error(report, "otel-tracing-shape", "tracing must be a table", target);
        return;
    };
    closed_keys(table, TRACING_KEYS, target, report);
    optional_bool(table, "enabled", target, report);
    if let Some(value) = table.get("sample_ratio") {
        let ratio = value
            .as_float()
            .or_else(|| value.as_integer().map(|value| value as f64));
        if !ratio.is_some_and(|ratio| (0.0..=1.0).contains(&ratio)) {
            error(
                report,
                "otel-sample-ratio",
                "sample_ratio must be a number in the inclusive range 0..=1",
                &format!("{target}.sample_ratio"),
            );
        }
    }
    if let Some(value) = table.get("propagators") {
        if !value
            .as_array()
            .is_some_and(|items| items.iter().all(|item| item.as_str().is_some()))
        {
            error(
                report,
                "otel-propagators-shape",
                "propagators must be an array of strings",
                &format!("{target}.propagators"),
            );
        }
    }
}

fn audit_metrics(value: Option<&Value>, target: &str, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        error(report, "otel-metrics-shape", "metrics must be a table", target);
        return;
    };
    closed_keys(table, METRICS_KEYS, target, report);
    optional_bool(table, "enabled", target, report);
}

fn audit_exporter(value: Option<&Value>, target: &str, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        error(report, "otel-exporter-shape", "exporter must be a table", target);
        return;
    };
    closed_keys(table, EXPORTER_KEYS, target, report);
    optional_bounded_string(table, "protocol", 1, 32, target, report);
    optional_bounded_string(table, "endpoint_env", 1, 128, target, report);
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
                "otel-unknown-field",
                "field is not declared by the peer-authority OTel config shape",
                &format!("{target}.{key}"),
            );
        }
    }
}

fn optional_bool(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if table.contains_key(field) && table.get(field).and_then(Value::as_bool).is_none() {
        error(
            report,
            "otel-boolean-shape",
            "field must be boolean when present",
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
    let Some(value) = table.get(field) else { return };
    let Some(value) = value.as_str() else {
        error(
            report,
            "otel-string-shape",
            "field must be a string when present",
            &format!("{target}.{field}"),
        );
        return;
    };
    let length = value.chars().count();
    if length < min || length > max {
        error(
            report,
            "otel-string-bound",
            "string length is outside the peer-authority bound",
            &format!("{target}.{field}"),
        );
    }
}

fn error(report: &mut CommandReport, code: &str, message: &str, target: &str) {
    report.push(Finding::error(code, message).with_target(target.to_owned()));
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::augment_otel_runtime_toml_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    fn audit(contents: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".ores-otel.toml"), contents).expect("write OTel config");
        augment_otel_runtime_toml_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn common_and_mixed_role_shapes_pass() {
        let report = audit(
            r#"
version = 1

[common]
enabled = true
service_name = "shared-observability"
environment = "test"

[common.logging]
enabled = true
level = "info"
console = true
auto_send = false

[common.tracing]
enabled = true
sample_ratio = 0.5
propagators = ["tracecontext", "baggage"]

[common.metrics]
enabled = true

[common.exporter]
protocol = "otlp-grpc"
endpoint_env = "OTEL_EXPORTER_OTLP_ENDPOINT"

[client]
enabled = true
service_name = "browser"

[server]
enabled = true
service_name = "api"

[server.tracing]
sample_ratio = 1
"#,
        );
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "otel-domain-inspected")
        );
    }

    #[test]
    fn version_unknown_fields_and_ratio_drift_fail_closed() {
        let report = audit(
            r#"
version = 2
mystery = true

[common]
service_name = "svc"
unknown = "field"

[common.tracing]
sample_ratio = 1.01
"#,
        );
        for code in ["otel-version", "otel-unknown-field", "otel-sample-ratio"] {
            assert!(
                report.findings.iter().any(|finding| finding.code == code),
                "missing {code}: {:#?}",
                report.findings
            );
        }
    }

    #[test]
    fn nested_types_and_bounds_fail_closed() {
        let report = audit(
            r#"
version = 1

[client]
enabled = "yes"
service_name = ""

[client.logging]
level = "this-level-is-way-too-long"

[client.tracing]
propagators = ["tracecontext", 7]

[client.metrics]
enabled = "yes"

[client.exporter]
protocol = ""
endpoint_env = ""
"#,
        );
        for code in [
            "otel-boolean-shape",
            "otel-string-bound",
            "otel-propagators-shape",
        ] {
            assert!(
                report.findings.iter().any(|finding| finding.code == code),
                "missing {code}: {:#?}",
                report.findings
            );
        }
    }
}
