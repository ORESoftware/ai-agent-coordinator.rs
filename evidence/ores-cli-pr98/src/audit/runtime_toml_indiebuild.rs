use std::fs;
use std::io;

use toml::Value;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONFIG: &str = ".indiebuild.toml";
const SCHEMA_VERSION: &str = "gha-indie-worker.indiebuild/v1";
const ROOT_KEYS: &[&str] = &[
    "schema_version",
    "repository_role",
    "default_target",
    "targets",
];
const TARGET_KEYS: &[&str] = &[
    "name",
    "role",
    "path",
    "profile",
    "platform",
    "artifacts",
    "cache_paths",
    "env",
    "secret_env",
    "allow_network",
    "allow_push",
    "allow_deploy",
    "timeout_seconds",
];
const REPOSITORY_ROLES: &[&str] = &["client", "server", "mixed"];
const TARGET_ROLES: &[&str] = &["client", "server"];
const PLATFORMS: &[&str] = &["linux", "macos", "windows"];

/// Apply only raw `.indiebuild.toml` shape invariants represented by both
/// independently authored IndieBuild TypeSpec and Draft 2020-12 JSON Schema
/// authorities. Cross-target role policy, profile resolution, filesystem
/// containment, runner capabilities, artifact publication, environment
/// resolution, and execution remain owner/consumer responsibilities.
pub(super) fn augment_indiebuild_runtime_toml_audit(
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
    require_string_eq(
        root,
        "schema_version",
        SCHEMA_VERSION,
        "indiebuild-schema-version",
        "root",
        &mut report,
    );
    require_enum(
        root,
        "repository_role",
        REPOSITORY_ROLES,
        "indiebuild-repository-role",
        "root",
        &mut report,
    );
    require_string(root, "default_target", "root", &mut report);
    audit_targets(root.get("targets"), &mut report);

    if report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "indiebuild-domain-inspected",
                ".indiebuild.toml passed the bounded peer-authority invariant audit",
            )
            .with_target(CONFIG),
        );
    }

    report.finalize()
}

fn audit_targets(value: Option<&Value>, report: &mut CommandReport) {
    let Some(targets) = value.and_then(Value::as_array) else {
        push_error(
            report,
            "indiebuild-targets-shape",
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
                "indiebuild-target-shape",
                "target entries must be TOML tables",
                &target,
            );
            continue;
        };

        audit_closed_keys(table, TARGET_KEYS, &target, report);
        for field in ["name", "path", "profile"] {
            require_string(table, field, &target, report);
        }
        require_enum(
            table,
            "role",
            TARGET_ROLES,
            "indiebuild-target-role",
            &target,
            report,
        );
        require_enum(
            table,
            "platform",
            PLATFORMS,
            "indiebuild-platform",
            &target,
            report,
        );
        for field in ["artifacts", "cache_paths", "env", "secret_env"] {
            require_string_array(table, field, &target, report);
        }
        for field in ["allow_network", "allow_push", "allow_deploy"] {
            require_bool(table, field, &target, report);
        }
        require_u32(table, "timeout_seconds", &target, report);
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
                "indiebuild-unknown-field",
                "field is not declared by the peer-authority IndieBuild config shape",
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
            "indiebuild-string-shape",
            "required field must be a string",
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
        .is_some_and(|items| items.iter().all(|item| item.as_str().is_some()))
    {
        push_error(
            report,
            "indiebuild-string-array-shape",
            "required field must be an array of strings",
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
            "indiebuild-boolean-shape",
            "required field must be boolean",
            &format!("{target}.{field}"),
        );
    }
}

fn require_u32(
    table: &toml::map::Map<String, Value>,
    field: &str,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_integer)
        .is_some_and(|value| (0..=i64::from(u32::MAX)).contains(&value))
    {
        push_error(
            report,
            "indiebuild-u32-shape",
            "required field must be an integer in 0..=4294967295",
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

    use super::augment_indiebuild_runtime_toml_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    const VALID: &str = r#"
schema_version = "gha-indie-worker.indiebuild/v1"
repository_role = "mixed"
default_target = "api"

[[targets]]
name = "api"
role = "server"
path = "."
profile = "rust-server"
platform = "linux"
artifacts = ["target/release/api"]
cache_paths = ["target"]
env = ["RUST_LOG"]
secret_env = ["DATABASE_URL"]
allow_network = true
allow_push = false
allow_deploy = true
timeout_seconds = 900

[[targets]]
name = "web"
role = "client"
path = "web"
profile = "flutter-web"
platform = "macos"
artifacts = []
cache_paths = [".dart_tool"]
env = []
secret_env = []
allow_network = false
allow_push = false
allow_deploy = false
timeout_seconds = 0
"#;

    fn audit(contents: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".indiebuild.toml"), contents).expect("write config");
        augment_indiebuild_runtime_toml_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn peer_authority_shape_passes() {
        let report = audit(VALID);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "indiebuild-domain-inspected")
        );
    }

    #[test]
    fn version_roles_platform_and_unknown_fields_fail_closed() {
        let report = audit(
            &VALID
                .replace(
                    "schema_version = \"gha-indie-worker.indiebuild/v1\"",
                    "schema_version = \"gha-indie-worker.indiebuild/v2\"\nmystery = true",
                )
                .replace("repository_role = \"mixed\"", "repository_role = \"admin\"")
                .replacen("role = \"server\"", "role = \"admin\"", 1)
                .replacen("platform = \"linux\"", "platform = \"solaris\"", 1),
        );
        for code in [
            "indiebuild-schema-version",
            "indiebuild-repository-role",
            "indiebuild-target-role",
            "indiebuild-platform",
            "indiebuild-unknown-field",
        ] {
            assert!(
                report.findings.iter().any(|finding| finding.code == code),
                "missing {code}: {:#?}",
                report.findings
            );
        }
    }

    #[test]
    fn required_collection_boolean_and_timeout_shapes_fail_closed() {
        let report = audit(
            r#"
schema_version = "gha-indie-worker.indiebuild/v1"
repository_role = "server"
default_target = "api"

[[targets]]
name = "api"
role = "server"
path = "."
profile = "rust-server"
platform = "windows"
artifacts = "target/release/api"
cache_paths = []
env = ["RUST_LOG", 7]
secret_env = []
allow_network = "yes"
allow_push = false
allow_deploy = false
timeout_seconds = -1
"#,
        );
        for code in [
            "indiebuild-string-array-shape",
            "indiebuild-boolean-shape",
            "indiebuild-u32-shape",
        ] {
            assert!(
                report.findings.iter().any(|finding| finding.code == code),
                "missing {code}: {:#?}",
                report.findings
            );
        }
    }

    #[test]
    fn missing_required_target_field_fails_closed() {
        let report = audit(&VALID.replace("profile = \"rust-server\"\n", ""));
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "indiebuild-string-shape")
        );
    }

    #[test]
    fn u32_upper_bound_is_enforced() {
        let report = audit(&VALID.replace("timeout_seconds = 900", "timeout_seconds = 4294967296"));
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "indiebuild-u32-shape")
        );
    }
}
