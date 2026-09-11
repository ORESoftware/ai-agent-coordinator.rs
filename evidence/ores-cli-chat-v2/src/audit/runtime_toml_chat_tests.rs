use std::fs;

use tempfile::tempdir;

use super::runtime_toml_chat::augment_chat_runtime_toml_audit;
use super::RepositoryAuditOptions;
use crate::model::CommandReport;

const SERVER: &str = r#"
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

fn has(report: &CommandReport, code: &str) -> bool {
    report.findings.iter().any(|finding| finding.code == code)
}

#[test]
fn server_and_hybrid_surfaces_pass() {
    let report = audit(SERVER);
    assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
    assert!(has(&report, "chat-domain-inspected"));

    let hybrid = SERVER
        .replace("mode = \"server\"", "mode = \"hybrid\"")
        .replace(
            "[server]",
            "[client]\nenabled = true\napi_base_url_binding = \"shared_auth_issuer\"\nwebsocket_url_binding = \"shared_auth_issuer\"\n\n[server]",
        );
    let report = audit(&hybrid);
    assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
}

#[test]
fn version_mode_precedence_kind_and_unknown_fields_fail_closed() {
    let report = audit(
        &SERVER
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
        assert!(has(&report, code), "missing {code}: {:#?}", report.findings);
    }
}

#[test]
fn declaration_names_keys_and_counts_are_bounded() {
    let long_name = "x".repeat(65);
    let report = audit(
        &SERVER
            .replace("name = \"bind_addr\"", "name = \"Bad-Name\"")
            .replace("key = \"ORES_CHAT_BIND\"", "key = \"bad-key\"")
            .replace(
                "name = \"shared_auth_issuer\"",
                &format!("name = \"{long_name}\""),
            ),
    );
    assert!(has(&report, "chat-env-name"));
    assert!(has(&report, "chat-env-key"));

    let report = audit(&SERVER.replace("[[env]]", "[[not_env]]"));
    assert!(has(&report, "chat-env-shape"));
}

#[test]
fn duplicate_names_and_keys_fail_closed() {
    let report = audit(
        &SERVER
            .replace("name = \"shared_auth_issuer\"", "name = \"bind_addr\"")
            .replace("key = \"SHARED_AUTH_ISSUER\"", "key = \"ORES_CHAT_BIND\""),
    );
    assert!(has(&report, "chat-env-name-duplicate"));
    assert!(has(&report, "chat-env-key-duplicate"));
}

#[test]
fn secret_defaults_are_rejected() {
    let report = audit(
        &SERVER.replace(
            "required = true\nsecret = false",
            "required = true\nsecret = true\ndefault = \"plaintext\"",
        ),
    );
    assert!(has(&report, "chat-secret-default"));
}

#[test]
fn role_bindings_must_reference_declared_environment_names() {
    let report = audit(&SERVER.replace(
        "bind_addr_binding = \"bind_addr\"",
        "bind_addr_binding = \"missing_binding\"",
    ));
    assert!(has(&report, "chat-binding-unknown"));
}

#[test]
fn mode_and_enabled_surfaces_must_agree() {
    let report = audit(&SERVER.replace("enabled = true", "enabled = false"));
    assert!(has(&report, "chat-mode-surface"));

    let client = SERVER
        .replace("mode = \"server\"", "mode = \"client\"")
        .replace(
            "[server]\nenabled = true\nbind_addr_binding = \"bind_addr\"\nshared_auth_issuer_binding = \"shared_auth_issuer\"",
            "[client]\nenabled = true\napi_base_url_binding = \"shared_auth_issuer\"\nwebsocket_url_binding = \"shared_auth_issuer\"",
        );
    let report = audit(&client);
    assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
}

#[test]
fn malformed_tables_and_scalar_types_fail_closed() {
    let report = audit(
        &SERVER
            .replace("strict = true", "strict = \"yes\"")
            .replace("[flags2env]", "flags2env = \"bad\"")
            .replace("[server]", "server = \"bad\""),
    );
    assert!(has(&report, "chat-boolean-shape"));
    assert!(has(&report, "chat-flags2env-shape"));
    assert!(has(&report, "chat-role-shape"));
}

#[test]
fn missing_file_is_a_noop_and_malformed_toml_is_reported() {
    let root = tempdir().expect("temporary repository");
    let report = augment_chat_runtime_toml_audit(
        &RepositoryAuditOptions {
            path: root.path().to_path_buf(),
            profile: "baseline".to_owned(),
            additional_required_paths: Vec::new(),
        },
        CommandReport::new("audit repo"),
    );
    assert_eq!(report.issue_count(), 0);

    let report = audit("not = [valid");
    assert!(has(&report, "chat-config-toml"));
}
