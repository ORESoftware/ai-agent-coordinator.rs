use std::collections::BTreeSet;
use std::fs;
use std::io;

use serde_json::json;
use toml::Value;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONFIG: &str = ".ores-rl.toml";
const SCHEMA_VERSION: &str = "ores.rate-limit.config.v1";
const ROOT_KEYS: &[&str] = &[
    "schemaVersion",
    "layout",
    "defaultPolicyId",
    "client",
    "server",
    "env",
    "policies",
];
const POLICY_KEYS: &[&str] = &[
    "policyId",
    "clientVisible",
    "algorithm",
    "identityScope",
    "capacity",
    "windowMs",
    "refillTokens",
    "refillIntervalMs",
    "requestCost",
    "enforcementMode",
    "consistencyMode",
    "backendFailureMode",
    "denyCacheMode",
    "denyCacheCapacity",
    "maxOvershoot",
    "maxBlockTtlMs",
    "keyVersion",
    "policyVersion",
];
const ENV_KEYS: &[&str] = &["key", "kind", "required", "secret", "purpose"];
const LAYOUTS: &[&str] = &["client-only", "server-only", "combined"];
const ALGORITHMS: &[&str] = &["token-bucket", "fixed-window", "sliding-window", "gcra"];
const IDENTITIES: &[&str] = &[
    "anonymous-ip",
    "authenticated-subject",
    "authenticated-tenant-subject",
];
const ENFORCEMENT: &[&str] = &["enforce", "observe-only", "disabled"];
const CONSISTENCY: &[&str] = &["strict", "bounded", "advisory"];
const FAILURE: &[&str] = &["fail-open", "fail-closed", "local-fallback"];
const DENY_CACHE: &[&str] = &[
    "local-denials",
    "redis-denial-fanout",
    "redis-strict-blocks",
];
const ENV_KINDS: &[&str] = &["string", "boolean", "integer", "double", "url", "path"];

/// Apply only invariants shared by the independently authored rate-limit v1
/// TypeSpec and JSON Schema authorities. Full admission remains owner/TJSV work.
pub(super) fn augment_rate_limit_runtime_toml_audit(
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
    require_string_eq(root, "schemaVersion", SCHEMA_VERSION, "rate-limit-schema-version", &mut report);
    require_enum(root, "layout", LAYOUTS, "root", &mut report);
    match root.get("defaultPolicyId").and_then(Value::as_str) {
        Some(value) if valid_policy_id(value) => {}
        _ => push_error(
            &mut report,
            "rate-limit-default-policy-id",
            "defaultPolicyId does not match the peer-authority identifier grammar",
            "defaultPolicyId",
        ),
    }

    audit_client(root.get("client"), &mut report);
    audit_server(root.get("server"), &mut report);
    audit_env(root.get("env"), &mut report);
    audit_policies(root.get("policies"), &mut report);

    if report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "rate-limit-domain-inspected",
                ".ores-rl.toml passed the bounded peer-authority invariant audit",
            )
            .with_target(CONFIG),
        );
    }
    report.finalize()
}

fn audit_client(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        push_error(report, "rate-limit-client-shape", "client must be a table", "client");
        return;
    };
    audit_closed_keys(table, &["root", "exposePolicyMetadata"], "client", report);
    require_bounded_string(table, "root", 255, "client", report);
    if table.get("exposePolicyMetadata").and_then(Value::as_bool).is_none() {
        push_error(
            report,
            "rate-limit-client-boolean",
            "client.exposePolicyMetadata must be boolean",
            "client.exposePolicyMetadata",
        );
    }
}

fn audit_server(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(table) = value.as_table() else {
        push_error(report, "rate-limit-server-shape", "server must be a table", "server");
        return;
    };
    audit_closed_keys(
        table,
        &["root", "backend", "enforcementLayer", "redisUrlEnv", "keyHmacEnv"],
        "server",
        report,
    );
    require_bounded_string(table, "root", 255, "server", report);
    require_enum(table, "backend", &["local", "redis"], "server", report);
    require_enum(
        table,
        "enforcementLayer",
        &["edge", "load-balancer", "service", "authentication", "data-store"],
        "server",
        report,
    );
    require_bounded_string(table, "keyHmacEnv", 128, "server", report);
    if table.contains_key("redisUrlEnv") {
        require_bounded_string(table, "redisUrlEnv", 128, "server", report);
    }
}

fn audit_env(value: Option<&Value>, report: &mut CommandReport) {
    let Some(value) = value else { return };
    let Some(entries) = value.as_array() else {
        push_error(report, "rate-limit-env-shape", "env must be an array", "env");
        return;
    };
    if entries.is_empty() || entries.len() > 128 {
        push_error(report, "rate-limit-env-count", "env must contain 1..=128 declarations", "env");
    }
    let mut seen = BTreeSet::new();
    for (index, entry) in entries.iter().enumerate() {
        let target = format!("env[{index}]");
        let Some(table) = entry.as_table() else {
            push_error(report, "rate-limit-env-entry-shape", "environment declaration must be a table", &target);
            continue;
        };
        audit_closed_keys(table, ENV_KEYS, &target, report);
        match table.get("key").and_then(Value::as_str) {
            Some(key) if valid_env_key(key) => {
                if !seen.insert(key.to_owned()) {
                    push_error(
                        report,
                        "rate-limit-env-key-duplicate",
                        "environment keys must be unique for deterministic resolution",
                        &target,
                    );
                }
            }
            _ => push_error(
                report,
                "rate-limit-env-key",
                "environment key must match ^[A-Z_][A-Z0-9_]*$",
                &target,
            ),
        }
        require_enum(table, "kind", ENV_KINDS, &target, report);
        for field in ["required", "secret"] {
            if table.get(field).and_then(Value::as_bool).is_none() {
                push_error(
                    report,
                    "rate-limit-env-boolean",
                    "environment required/secret fields must be boolean",
                    &format!("{target}.{field}"),
                );
            }
        }
        require_bounded_string(table, "purpose", 255, &target, report);
    }
}

fn audit_policies(value: Option<&Value>, report: &mut CommandReport) {
    let Some(entries) = value.and_then(Value::as_array) else {
        push_error(report, "rate-limit-policies-shape", "policies must be an array", "policies");
        return;
    };
    if entries.is_empty() || entries.len() > 256 {
        push_error(report, "rate-limit-policy-count", "policies must contain 1..=256 entries", "policies");
    }
    let mut seen = BTreeSet::new();
    for (index, entry) in entries.iter().enumerate() {
        let target = format!("policies[{index}]");
        let Some(table) = entry.as_table() else {
            push_error(report, "rate-limit-policy-shape", "policy entry must be a table", &target);
            continue;
        };
        audit_closed_keys(table, POLICY_KEYS, &target, report);
        match table.get("policyId").and_then(Value::as_str) {
            Some(id) if valid_policy_id(id) => {
                if !seen.insert(id.to_owned()) {
                    push_error(
                        report,
                        "rate-limit-policy-id-duplicate",
                        "policyId values must be unique for deterministic lookup",
                        &target,
                    );
                }
            }
            _ => push_error(
                report,
                "rate-limit-policy-id",
                "policyId does not match the peer-authority grammar",
                &target,
            ),
        }
        if table.get("clientVisible").and_then(Value::as_bool).is_none() {
            push_error(report, "rate-limit-policy-client-visible", "clientVisible must be boolean", &target);
        }
        for (field, allowed) in [
            ("algorithm", ALGORITHMS),
            ("identityScope", IDENTITIES),
            ("enforcementMode", ENFORCEMENT),
            ("consistencyMode", CONSISTENCY),
            ("backendFailureMode", FAILURE),
            ("denyCacheMode", DENY_CACHE),
        ] {
            require_enum(table, field, allowed, &target, report);
        }
        for (field, min, max) in [
            ("capacity", 1, 1_000_000_000),
            ("windowMs", 0, 2_678_400_000),
            ("refillTokens", 0, 1_000_000_000),
            ("refillIntervalMs", 0, 2_678_400_000),
            ("requestCost", 1, 1_000_000_000),
            ("denyCacheCapacity", 0, 10_000),
            ("maxOvershoot", 0, 1_000_000_000),
            ("maxBlockTtlMs", 1, 3_600_000),
            ("policyVersion", 1, 9_007_199_254_740_991),
        ] {
            require_integer_range(table, field, min, max, &target, report);
        }
        match table.get("keyVersion").and_then(Value::as_str) {
            Some(value) if valid_key_version(value) => {}
            _ => push_error(
                report,
                "rate-limit-key-version",
                "keyVersion must match ^v[1-9][0-9]*$ and be 2..=32 characters",
                &format!("{target}.keyVersion"),
            ),
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
                "rate-limit-unknown-field",
                "field is not declared by the peer-authority rate-limit v1 shape",
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
    report: &mut CommandReport,
) {
    if table.get(field).and_then(Value::as_str) != Some(expected) {
        push_error(report, code, "string value does not match the peer-authority constant", field);
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
        push_error(
            report,
            "rate-limit-enum",
            "value is outside the peer-authority enum",
            &format!("{target}.{field}"),
        );
    }
}

fn require_integer_range(
    table: &toml::map::Map<String, Value>,
    field: &str,
    min: i64,
    max: i64,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_integer)
        .is_some_and(|value| (min..=max).contains(&value))
    {
        report.push(
            Finding::error(
                "rate-limit-integer-range",
                "integer is outside the peer-authority range",
            )
            .with_target(format!("{target}.{field}"))
            .with_detail("minimum", json!(min))
            .with_detail("maximum", json!(max)),
        );
    }
}

fn require_bounded_string(
    table: &toml::map::Map<String, Value>,
    field: &str,
    max: usize,
    target: &str,
    report: &mut CommandReport,
) {
    if !table
        .get(field)
        .and_then(Value::as_str)
        .is_some_and(|value| !value.is_empty() && value.len() <= max)
    {
        push_error(
            report,
            "rate-limit-string-bound",
            "string is empty or exceeds the peer-authority bound",
            &format!("{target}.{field}"),
        );
    }
}

fn valid_env_key(value: &str) -> bool {
    if value.is_empty() || value.len() > 128 {
        return false;
    }
    let mut bytes = value.bytes();
    let Some(first) = bytes.next() else { return false };
    (first.is_ascii_uppercase() || first == b'_')
        && bytes.all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_')
}

fn valid_policy_id(value: &str) -> bool {
    if value.is_empty() || value.len() > 128 {
        return false;
    }
    let bytes = value.as_bytes();
    let valid_edge = |byte: u8| byte.is_ascii_lowercase() || byte.is_ascii_digit();
    valid_edge(bytes[0])
        && valid_edge(*bytes.last().expect("nonempty policy id"))
        && bytes.iter().all(|byte| {
            let byte = *byte;
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || matches!(byte, b'.' | b'_' | b'-')
        })
}

fn valid_key_version(value: &str) -> bool {
    if value.len() < 2 || value.len() > 32 || !value.starts_with('v') {
        return false;
    }
    let digits = &value[1..];
    !digits.starts_with('0') && digits.bytes().all(|byte| byte.is_ascii_digit())
}

fn push_error(report: &mut CommandReport, code: &str, message: &str, target: &str) {
    report.push(Finding::error(code, message).with_target(target.to_owned()));
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::augment_rate_limit_runtime_toml_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    const OWNER_SHAPED: &str = r#"
schemaVersion = "ores.rate-limit.config.v1"
layout = "client-only"
defaultPolicyId = "interfaces-observe"
[client]
root = "."
exposePolicyMetadata = true
[[policies]]
policyId = "interfaces-observe"
clientVisible = true
algorithm = "fixed-window"
identityScope = "anonymous-ip"
capacity = 100
windowMs = 60000
refillTokens = 0
refillIntervalMs = 0
requestCost = 1
enforcementMode = "observe-only"
consistencyMode = "advisory"
backendFailureMode = "fail-open"
denyCacheMode = "local-denials"
denyCacheCapacity = 0
maxOvershoot = 10
maxBlockTtlMs = 60000
keyVersion = "v1"
policyVersion = 1
"#;

    const ZED_SERVER_SHAPED: &str = r#"
schemaVersion = "ores.rate-limit.config.v1"
layout = "server-only"
defaultPolicyId = "api-default"
[server]
root = "."
backend = "redis"
enforcementLayer = "service"
redisUrlEnv = "REDIS_URL"
keyHmacEnv = "ORES_RL_HMAC_KEY"
[[policies]]
policyId = "api-default"
clientVisible = false
algorithm = "token-bucket"
identityScope = "anonymous-ip"
capacity = 120
windowMs = 0
refillTokens = 120
refillIntervalMs = 60000
requestCost = 1
enforcementMode = "enforce"
consistencyMode = "strict"
backendFailureMode = "fail-closed"
denyCacheMode = "redis-strict-blocks"
denyCacheCapacity = 2048
maxOvershoot = 0
maxBlockTtlMs = 60000
keyVersion = "v1"
policyVersion = 1
"#;

    fn audit(contents: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".ores-rl.toml"), contents).expect("write config");
        augment_rate_limit_runtime_toml_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn owner_and_real_server_shapes_pass_bounded_domain_audit() {
        for fixture in [OWNER_SHAPED, ZED_SERVER_SHAPED] {
            let report = audit(fixture);
            assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
            assert!(
                report
                    .findings
                    .iter()
                    .any(|finding| finding.code == "rate-limit-domain-inspected")
            );
        }
    }

    #[test]
    fn unknown_fields_and_bad_ranges_fail_closed() {
        let report = audit(
            &OWNER_SHAPED
                .replace("capacity = 100", "capacity = 0\nplaintextSecret = \"nope\"")
                .replace("keyVersion = \"v1\"", "keyVersion = \"v0\""),
        );
        assert!(report.findings.iter().any(|finding| finding.code == "rate-limit-unknown-field"));
        assert!(report.findings.iter().any(|finding| finding.code == "rate-limit-integer-range"));
        assert!(report.findings.iter().any(|finding| finding.code == "rate-limit-key-version"));
    }

    #[test]
    fn duplicate_env_keys_fail_closed() {
        let report = audit(&format!(
            "{OWNER_SHAPED}\n[[env]]\nkey = \"DATABASE_URL\"\nkind = \"url\"\nrequired = true\nsecret = true\npurpose = \"db\"\n[[env]]\nkey = \"DATABASE_URL\"\nkind = \"url\"\nrequired = true\nsecret = true\npurpose = \"db again\"\n"
        ));
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "rate-limit-env-key-duplicate")
        );
    }
}
