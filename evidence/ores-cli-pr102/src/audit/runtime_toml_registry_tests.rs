use std::fs;
use std::path::Path;

use tempfile::tempdir;

use super::{audit_repository, RepositoryAuditOptions};

fn write_baseline(root: &Path) {
    fs::create_dir_all(root.join(".github")).expect("create .github");
    fs::write(root.join(".github/README.md"), "fixture").expect("write .github fixture");
    fs::write(root.join("README.md"), "fixture").expect("write README");
    fs::write(root.join("LICENSE"), "fixture").expect("write LICENSE");
    fs::write(root.join("AGENTS.md"), "fixture").expect("write AGENTS");
}

fn audit(root: &Path) -> crate::model::CommandReport {
    audit_repository(&RepositoryAuditOptions {
        path: root.to_path_buf(),
        profile: "baseline".to_owned(),
        additional_required_paths: Vec::new(),
    })
}

#[test]
fn rejects_invented_ores_root_config_name() {
    let root = tempdir().expect("tempdir");
    write_baseline(root.path());
    fs::write(root.path().join(".ores-ratelimit.toml"), "strict = true\n")
        .expect("write typo config");

    let report = audit(root.path());
    assert!(report.findings.iter().any(|finding| {
        finding.code == "runtime-toml-unregistered-name"
            && finding.target.as_deref() == Some(".ores-ratelimit.toml")
    }));
    assert_eq!(
        report
            .metadata
            .get("unregisteredOresTomlCount")
            .and_then(serde_json::Value::as_u64),
        Some(1)
    );
}

#[test]
fn accepts_registered_sidecar_and_canonical_shared_auth_names() {
    let root = tempdir().expect("tempdir");
    write_baseline(root.path());
    fs::write(root.path().join(".ores-sidecar.toml"), "protocol = \"v1\"\n")
        .expect("write sidecar config");
    fs::write(root.path().join(".shared-auth.toml"), "schema_version = 1\n")
        .expect("write shared auth config");

    let report = audit(root.path());
    assert!(!report
        .findings
        .iter()
        .any(|finding| finding.code == "runtime-toml-unregistered-name"));
    assert_eq!(
        report
            .metadata
            .get("registeredRuntimeTomlCount")
            .and_then(serde_json::Value::as_u64),
        Some(2)
    );
}

#[test]
fn rejects_canonical_and_compat_shared_auth_coexistence() {
    let root = tempdir().expect("tempdir");
    write_baseline(root.path());
    fs::write(root.path().join(".shared-auth.toml"), "schema_version = 1\n")
        .expect("write canonical shared auth");
    fs::write(root.path().join(".auth-shared.toml"), "schema_version = 1\n")
        .expect("write compatibility shared auth");

    let report = audit(root.path());
    assert!(report.findings.iter().any(|finding| {
        finding.code == "runtime-toml-shared-auth-ambiguous"
            && finding.target.as_deref() == Some(".shared-auth.toml")
    }));
}

#[test]
fn reports_unregistered_names_deterministically() {
    let root = tempdir().expect("tempdir");
    write_baseline(root.path());
    fs::write(root.path().join(".ores-alpha.toml"), "x = 1\n").expect("write alpha");
    fs::write(root.path().join(".ores-zeta.toml"), "x = 1\n").expect("write zeta");

    let report = audit(root.path());
    let mut targets = report
        .findings
        .iter()
        .filter(|finding| finding.code == "runtime-toml-unregistered-name")
        .filter_map(|finding| finding.target.clone())
        .collect::<Vec<_>>();
    targets.sort();
    assert_eq!(targets, vec![".ores-alpha.toml", ".ores-zeta.toml"]);
    assert_eq!(
        report
            .metadata
            .get("unregisteredOresTomlCount")
            .and_then(serde_json::Value::as_u64),
        Some(2)
    );
}
