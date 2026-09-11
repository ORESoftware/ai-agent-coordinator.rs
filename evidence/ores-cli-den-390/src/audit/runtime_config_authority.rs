use std::collections::BTreeSet;
use std::fs;
use std::io;
use std::path::Path;

use serde_json::json;
use toml::Value;
use walkdir::WalkDir;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CLI_CONTRACT: &str = ".cli-flags.toml";
const GENERATED_RUNTIME: &str = "generated/rust/runtime.rs";
const MAX_RUST_SOURCE_BYTES: u64 = 1024 * 1024;

/// Enforce one runtime configuration authority when flags-2-env generated Rust
/// consumption exists, and require reproducible lockfiles for Rust applications.
///
/// The check is deliberately bounded and lexical. It does not try to replace a
/// Rust parser. Instead it catches high-signal fleet regressions observed in
/// production repositories:
///
/// - a `.cli-flags.toml` env key is read directly from authored Rust after a
///   generated runtime has been committed and no official `BundledFlags2Env`
///   binding exists to own argv-to-config normalization;
/// - sidecar startup retries a rejected generated bind through
///   `SidecarConfig::from_env`, creating a second ambient parser;
/// - a Rust application has an executable entrypoint but no committed
///   `Cargo.lock`.
pub(super) fn augment_runtime_config_authority_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    audit_rust_application_lockfile(&options.path, &mut report);

    let contract_path = options.path.join(CLI_CONTRACT);
    let generated_runtime_path = options.path.join(GENERATED_RUNTIME);
    if !contract_path.is_file() || !generated_runtime_path.is_file() {
        return report.finalize();
    }

    let contract = match fs::read_to_string(&contract_path) {
        Ok(contract) => contract,
        Err(error) => {
            report.push(
                Finding::warning(
                    "runtime-config-authority-contract-unreadable",
                    format!("could not read {CLI_CONTRACT} for runtime-authority analysis: {error}"),
                )
                .with_target(CLI_CONTRACT),
            );
            return report.finalize();
        }
    };
    let parsed = match contract.parse::<Value>() {
        Ok(parsed) => parsed,
        Err(error) => {
            report.push(
                Finding::warning(
                    "runtime-config-authority-contract-unparseable",
                    format!("could not parse {CLI_CONTRACT} for runtime-authority analysis: {error}"),
                )
                .with_target(CLI_CONTRACT),
            );
            return report.finalize();
        }
    };

    let mut declared_env = BTreeSet::new();
    collect_declared_env_names(&parsed, &mut declared_env);
    report.insert_metadata(
        "runtimeConfigAuthorityDeclaredEnvCount",
        json!(declared_env.len()),
    );

    let src_root = options.path.join("src");
    if !src_root.is_dir() {
        return report.finalize();
    }

    let walker = WalkDir::new(&src_root)
        .follow_links(false)
        .max_depth(8)
        .into_iter()
        .filter_entry(|entry| {
            let name = entry.file_name().to_string_lossy();
            name != "target" && name != "generated"
        });

    let mut authored_sources = Vec::new();
    for result in walker {
        let entry = match result {
            Ok(entry) => entry,
            Err(error) => {
                report.push(
                    Finding::warning(
                        "runtime-config-authority-walk-failed",
                        format!("could not inspect authored Rust source: {error}"),
                    )
                    .with_target("src"),
                );
                continue;
            }
        };
        if !entry.file_type().is_file()
            || entry.path().extension().and_then(|value| value.to_str()) != Some("rs")
        {
            continue;
        }
        let metadata = match fs::symlink_metadata(entry.path()) {
            Ok(metadata) => metadata,
            Err(error) => {
                report.push(
                    Finding::warning(
                        "runtime-config-authority-source-unreadable",
                        format!("could not inspect Rust source metadata: {error}"),
                    )
                    .with_target(relative_display(&options.path, entry.path())),
                );
                continue;
            }
        };
        if metadata.file_type().is_symlink() || metadata.len() > MAX_RUST_SOURCE_BYTES {
            continue;
        }
        let source = match fs::read_to_string(entry.path()) {
            Ok(source) => source,
            Err(error) => {
                report.push(
                    Finding::warning(
                        "runtime-config-authority-source-unreadable",
                        format!("could not read authored Rust source as UTF-8: {error}"),
                    )
                    .with_target(relative_display(&options.path, entry.path())),
                );
                continue;
            }
        };
        authored_sources.push((relative_display(&options.path, entry.path()), source));
    }

    let official_binding_present = authored_sources.iter().any(|(_, source)| {
        source.contains("flags2env::BundledFlags2Env")
            || source.contains("use flags2env::BundledFlags2Env")
    });
    report.insert_metadata(
        "runtimeConfigAuthorityOfficialBindingPresent",
        json!(official_binding_present),
    );

    for (target, source) in &authored_sources {
        if !official_binding_present {
            audit_direct_declared_env_reads(source, target, &declared_env, &mut report);
        }
        audit_generated_runtime_fallback_escape(source, target, &mut report);
    }

    if official_binding_present {
        report.push(
            Finding::info(
                "runtime-config-authority-official-binding",
                "official BundledFlags2Env Rust binding detected; typed application config may consume normalized environment values without being classified as a generated-runtime bypass",
            )
            .with_target("src"),
        );
    }

    report.insert_metadata(
        "runtimeConfigAuthorityRustFileCount",
        json!(authored_sources.len()),
    );
    report.push(
        Finding::info(
            "runtime-config-authority-inspected",
            format!(
                "inspected {} authored Rust source file{} against generated flags-2-env runtime authority",
                authored_sources.len(),
                if authored_sources.len() == 1 { "" } else { "s" }
            ),
        )
        .with_target("src"),
    );

    report.finalize()
}

fn audit_rust_application_lockfile(root: &Path, report: &mut CommandReport) {
    let manifest = root.join("Cargo.toml");
    if !manifest.is_file() {
        return;
    }

    let manifest_text = fs::read_to_string(&manifest).unwrap_or_default();
    let has_binary = root.join("src/main.rs").is_file() || manifest_text.contains("[[bin]]");
    if !has_binary {
        return;
    }

    let lock = root.join("Cargo.lock");
    match fs::symlink_metadata(&lock) {
        Ok(metadata) if metadata.file_type().is_symlink() => report.push(
            Finding::error(
                "rust-application-lockfile-symlink",
                "Rust application Cargo.lock must be repository-contained, not a symbolic link",
            )
            .with_target("Cargo.lock"),
        ),
        Ok(metadata) if metadata.is_file() => {}
        Ok(_) => report.push(
            Finding::error(
                "rust-application-lockfile-not-file",
                "Rust application Cargo.lock must be a regular file",
            )
            .with_target("Cargo.lock"),
        ),
        Err(error) if error.kind() == io::ErrorKind::NotFound => report.push(
            Finding::error(
                "rust-application-lockfile-missing",
                "Rust applications must commit Cargo.lock so CI and deployments resolve the reviewed dependency graph",
            )
            .with_target("Cargo.lock"),
        ),
        Err(error) => report.push(
            Finding::error(
                "rust-application-lockfile-unreadable",
                format!("Rust application Cargo.lock metadata could not be read: {error}"),
            )
            .with_target("Cargo.lock"),
        ),
    }
}

fn collect_declared_env_names(value: &Value, out: &mut BTreeSet<String>) {
    match value {
        Value::Table(table) => {
            if let Some(Value::String(name)) = table.get("env") {
                let name = name.trim();
                if !name.is_empty() {
                    out.insert(name.to_owned());
                }
            }
            for child in table.values() {
                collect_declared_env_names(child, out);
            }
        }
        Value::Array(values) => {
            for child in values {
                collect_declared_env_names(child, out);
            }
        }
        _ => {}
    }
}

fn audit_direct_declared_env_reads(
    source: &str,
    target: &str,
    declared_env: &BTreeSet<String>,
    report: &mut CommandReport,
) {
    let mut bypassed = Vec::new();
    for name in declared_env {
        let patterns = [
            format!("std::env::var(\"{name}\")"),
            format!("std::env::var_os(\"{name}\")"),
            format!("env::var(\"{name}\")"),
            format!("env::var_os(\"{name}\")"),
        ];
        if patterns.iter().any(|pattern| source.contains(pattern)) {
            bypassed.push(name.clone());
        }
    }
    if bypassed.is_empty() {
        return;
    }

    report.push(
        Finding::error(
            "generated-runtime-env-bypass",
            "authored Rust directly reads env keys owned by .cli-flags.toml even though generated/rust/runtime.rs exists and no official BundledFlags2Env binding owns argv-to-config normalization",
        )
        .with_target(target.to_owned())
        .with_detail("envKeys", json!(bypassed)),
    );
}

fn audit_generated_runtime_fallback_escape(
    source: &str,
    target: &str,
    report: &mut CommandReport,
) {
    let consumes_generated_runtime = source.contains("env_runtime::load_from_os()")
        || source.contains("generated/rust/runtime.rs");
    let retries_sidecar_from_env = source.contains("SidecarConfig::from_bind")
        && source.contains("SidecarConfig::from_env");
    if consumes_generated_runtime && retries_sidecar_from_env {
        report.push(
            Finding::error(
                "generated-runtime-fallback-parser",
                "generated runtime validation falls back to SidecarConfig::from_env, creating a second ambient parser after the generated value was rejected",
            )
            .with_target(target.to_owned()),
        );
    }
}

fn relative_display(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned()
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;

    use tempfile::tempdir;

    use super::*;

    fn options(path: &Path) -> RepositoryAuditOptions {
        RepositoryAuditOptions {
            path: path.to_path_buf(),
            profile: "baseline".to_owned(),
            additional_required_paths: Vec::new(),
        }
    }

    fn write_runtime_fixture(path: &Path, source: &str) {
        fs::create_dir_all(path.join("generated/rust")).expect("create generated runtime path");
        fs::create_dir_all(path.join("src")).expect("create src path");
        fs::write(
            path.join(CLI_CONTRACT),
            "[flags.bind]\nenv = \"SERVICE_BIND\"\nlong = \"bind\"\ntype = \"string\"\n",
        )
        .expect("write CLI contract");
        fs::write(path.join(GENERATED_RUNTIME), "// generated fixture\n")
            .expect("write generated runtime");
        fs::write(path.join("src/main.rs"), source).expect("write authored source");
        fs::write(
            path.join("Cargo.toml"),
            "[package]\nname = \"fixture\"\nversion = \"0.1.0\"\nedition = \"2024\"\n",
        )
        .expect("write manifest");
        fs::write(path.join("Cargo.lock"), "version = 4\n").expect("write lockfile");
    }

    #[test]
    fn detects_direct_reads_of_contract_owned_env_keys_without_official_binding() {
        let root = tempdir().expect("temporary directory");
        write_runtime_fixture(
            root.path(),
            "fn main() { let _ = std::env::var(\"SERVICE_BIND\"); }\n",
        );
        let report = augment_runtime_config_authority_audit(
            &options(root.path()),
            CommandReport::new("fixture"),
        );
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "generated-runtime-env-bypass"));
    }

    #[test]
    fn accepts_typed_env_reads_when_official_binding_exists() {
        let root = tempdir().expect("temporary directory");
        write_runtime_fixture(
            root.path(),
            "fn main() { let _ = std::env::var(\"SERVICE_BIND\"); }\n",
        );
        fs::write(
            root.path().join("src/flags.rs"),
            "use flags2env::BundledFlags2Env;\nfn parser() { let _ = BundledFlags2Env::new(); }\n",
        )
        .expect("write official binding fixture");
        let report = augment_runtime_config_authority_audit(
            &options(root.path()),
            CommandReport::new("fixture"),
        );
        assert!(!report
            .findings
            .iter()
            .any(|finding| finding.code == "generated-runtime-env-bypass"));
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "runtime-config-authority-official-binding"));
    }

    #[test]
    fn accepts_generated_runtime_consumption_without_parallel_env_reads() {
        let root = tempdir().expect("temporary directory");
        write_runtime_fixture(
            root.path(),
            "fn main() { let _values = env_runtime::load_from_os(); }\n",
        );
        let report = augment_runtime_config_authority_audit(
            &options(root.path()),
            CommandReport::new("fixture"),
        );
        assert!(!report.findings.iter().any(|finding| {
            matches!(
                finding.code.as_str(),
                "generated-runtime-env-bypass" | "generated-runtime-fallback-parser"
            )
        }));
    }

    #[test]
    fn detects_sidecar_fallback_to_second_ambient_parser() {
        let root = tempdir().expect("temporary directory");
        write_runtime_fixture(
            root.path(),
            "fn main() { let values = env_runtime::load_from_os(); let _ = match SidecarConfig::from_bind(id(), &values.bind, false) { Ok(cfg) => cfg, Err(_) => SidecarConfig::from_env(id()) }; }\n",
        );
        let report = augment_runtime_config_authority_audit(
            &options(root.path()),
            CommandReport::new("fixture"),
        );
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "generated-runtime-fallback-parser"));
    }

    #[test]
    fn rust_application_requires_committed_lockfile() {
        let root = tempdir().expect("temporary directory");
        fs::create_dir_all(root.path().join("src")).expect("create source directory");
        fs::write(
            root.path().join("Cargo.toml"),
            "[package]\nname = \"fixture\"\nversion = \"0.1.0\"\nedition = \"2024\"\n",
        )
        .expect("write manifest");
        fs::write(root.path().join("src/main.rs"), "fn main() {}\n").expect("write main");
        let report = augment_runtime_config_authority_audit(
            &options(root.path()),
            CommandReport::new("fixture"),
        );
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "rust-application-lockfile-missing"));
    }

    #[test]
    fn rust_library_does_not_require_application_lockfile() {
        let root = tempdir().expect("temporary directory");
        fs::create_dir_all(root.path().join("src")).expect("create source directory");
        fs::write(
            root.path().join("Cargo.toml"),
            "[package]\nname = \"fixture\"\nversion = \"0.1.0\"\nedition = \"2024\"\n",
        )
        .expect("write manifest");
        fs::write(root.path().join("src/lib.rs"), "pub fn value() -> u8 { 1 }\n")
            .expect("write library");
        let report = augment_runtime_config_authority_audit(
            &options(root.path()),
            CommandReport::new("fixture"),
        );
        assert!(!report
            .findings
            .iter()
            .any(|finding| finding.code.starts_with("rust-application-lockfile-")));
    }
}
