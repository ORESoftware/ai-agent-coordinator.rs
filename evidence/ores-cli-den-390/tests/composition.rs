const AUDIT_MOD: &str = include_str!("../fixtures/audit-mod.rs");

#[test]
fn runtime_authority_is_declared_and_applied_exactly_once() {
    assert_eq!(AUDIT_MOD.matches("mod runtime_config_authority;").count(), 1);
    assert_eq!(
        AUDIT_MOD
            .matches("runtime_config_authority::augment_runtime_config_authority_audit")
            .count(),
        1
    );
}

#[test]
fn semantic_merge_preserves_newer_mainline_audits() {
    for marker in [
        "mod workflow_permissions;",
        "mod docker_base_pins;",
        "mod cargo_git_pins;",
        "mod cli_secret_flag_hygiene;",
        "mod flags2env_source_hygiene;",
        "mod flags2env_submodule_source_hygiene;",
        "mod infra_provider_state;",
        "workflow_permissions::augment_workflow_permissions_audit",
        "docker_base_pins::augment_docker_base_pin_audit",
        "cargo_git_pins::augment_cargo_git_pin_audit",
        "cli_secret_flag_hygiene::audit_cli_secret_flag_hygiene",
        "flags2env_source_hygiene::audit_flags2env_source_hygiene",
        "flags2env_submodule_source_hygiene::audit_flags2env_submodule_source_hygiene",
        "infra_provider_state::augment_infra_provider_state_audit",
    ] {
        assert!(AUDIT_MOD.contains(marker), "missing current-main marker: {marker}");
    }
}

#[test]
fn runtime_authority_runs_after_runtime_toml_checks_before_secret_and_source_hygiene() {
    let sidecar = AUDIT_MOD
        .find("runtime_toml_sidecar::augment_sidecar_runtime_toml_audit")
        .expect("sidecar runtime audit");
    let authority = AUDIT_MOD
        .find("runtime_config_authority::augment_runtime_config_authority_audit")
        .expect("runtime authority audit");
    let secret_hygiene = AUDIT_MOD
        .find("cli_secret_flag_hygiene::audit_cli_secret_flag_hygiene")
        .expect("secret hygiene audit");
    let source_hygiene = AUDIT_MOD
        .find("flags2env_source_hygiene::audit_flags2env_source_hygiene")
        .expect("source hygiene audit");

    assert!(sidecar < authority);
    assert!(authority < secret_hygiene);
    assert!(secret_hygiene < source_hygiene);
}
