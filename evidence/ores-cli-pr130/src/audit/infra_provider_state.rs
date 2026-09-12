use std::fs;
use std::io;
use std::path::Path;

use serde_json::json;
use walkdir::WalkDir;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const FORBIDDEN_LOCAL_STATE_PATHS: [&str; 4] = [
    "supabase/.temp",
    "supabase/.branches",
    "neon/.neon",
    ".neon",
];
const PROVIDERS: [&str; 2] = ["supabase", "neon"];

/// Reject mutable provider-local state and plaintext dotenv artifacts from an
/// infrastructure repository.
///
/// Provider CLIs may materialize these paths locally, but reviewed GitOps state
/// must remain reproducible from committed non-secret policy plus external
/// secret/runtime delivery. Presence in a checkout is therefore a source-policy
/// violation rather than deployment evidence.
pub(super) fn augment_infra_provider_state_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    let mut local_state_count = 0usize;
    let mut dotenv_count = 0usize;

    for relative in FORBIDDEN_LOCAL_STATE_PATHS {
        let path = options.path.join(relative);
        match fs::symlink_metadata(&path) {
            Ok(_) => {
                local_state_count += 1;
                report.push(
                    Finding::error(
                        "infra-provider-local-state-committed",
                        "provider-local mutable state must not be committed as GitOps authority",
                    )
                    .with_target(relative.to_owned()),
                );
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => report.push(
                Finding::error(
                    "infra-provider-local-state-unreadable",
                    format!("provider-local state path metadata could not be read: {error}"),
                )
                .with_target(relative.to_owned()),
            ),
        }
    }

    for provider in PROVIDERS {
        let root = options.path.join(provider);
        if !root.is_dir() {
            continue;
        }
        let walker = WalkDir::new(&root)
            .follow_links(false)
            .max_depth(8)
            .into_iter();
        for result in walker {
            let entry = match result {
                Ok(entry) => entry,
                Err(error) => {
                    report.push(
                        Finding::warning(
                            "infra-provider-dotenv-walk-failed",
                            format!("provider tree could not be fully inspected for dotenv state: {error}"),
                        )
                        .with_target(provider),
                    );
                    continue;
                }
            };
            let Some(name) = entry.file_name().to_str() else {
                continue;
            };
            if entry.depth() == 0 || !is_plaintext_dotenv_name(name) {
                continue;
            }
            dotenv_count += 1;
            report.push(
                Finding::error(
                    "infra-provider-plaintext-dotenv",
                    "provider roots must not commit plaintext dotenv state; use encrypted env/enc material or CI/runtime secret delivery",
                )
                .with_target(relative_display(&options.path, entry.path())),
            );
        }
    }

    report.insert_metadata("infraProviderLocalStateCount", json!(local_state_count));
    report.insert_metadata("infraProviderPlaintextDotenvCount", json!(dotenv_count));
    report.finalize()
}

fn is_plaintext_dotenv_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    if !lower.starts_with(".env") {
        return false;
    }
    if [".env.example", ".env.sample", ".env.template"].contains(&lower.as_str()) {
        return false;
    }
    if lower.ends_with(".enc") || lower.ends_with(".sops") {
        return false;
    }
    lower == ".env" || lower.starts_with(".env.")
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

    use super::{RepositoryAuditOptions, augment_infra_provider_state_audit};
    use crate::model::CommandReport;

    fn audit(path: &Path) -> CommandReport {
        augment_infra_provider_state_audit(
            &RepositoryAuditOptions {
                path: path.to_path_buf(),
                profile: "infra".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("test infra provider state"),
        )
    }

    fn write_provider_roots(path: &Path) {
        fs::create_dir_all(path.join("supabase/auth/migrations")).unwrap();
        fs::create_dir_all(path.join("neon/auth/migrations")).unwrap();
    }

    #[test]
    fn clean_provider_roots_pass() {
        let root = tempdir().unwrap();
        write_provider_roots(root.path());
        fs::write(root.path().join("supabase/.env.example"), "SAFE=example\n").unwrap();
        fs::write(root.path().join("neon/.env.production.enc"), "ciphertext\n").unwrap();
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 0, "{:#?}", report.findings);
    }

    #[test]
    fn rejects_supabase_temp_and_branch_state() {
        let root = tempdir().unwrap();
        write_provider_roots(root.path());
        fs::create_dir_all(root.path().join("supabase/.temp")).unwrap();
        fs::create_dir_all(root.path().join("supabase/.branches")).unwrap();
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 2);
        assert_eq!(
            report
                .findings
                .iter()
                .filter(|finding| finding.code == "infra-provider-local-state-committed")
                .count(),
            2
        );
    }

    #[test]
    fn rejects_root_and_provider_neon_state() {
        let root = tempdir().unwrap();
        write_provider_roots(root.path());
        fs::create_dir_all(root.path().join(".neon")).unwrap();
        fs::create_dir_all(root.path().join("neon/.neon")).unwrap();
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 2);
        assert_eq!(
            report
                .findings
                .iter()
                .filter(|finding| finding.code == "infra-provider-local-state-committed")
                .count(),
            2
        );
    }

    #[test]
    fn rejects_plaintext_dotenv_variants_but_allows_templates_and_encrypted_files() {
        let root = tempdir().unwrap();
        write_provider_roots(root.path());
        fs::create_dir_all(root.path().join("supabase/local")).unwrap();
        fs::write(root.path().join("supabase/.env.local"), "TOKEN=secret\n").unwrap();
        fs::write(
            root.path().join("supabase/local/.env.production"),
            "DATABASE_URL=secret\n",
        )
        .unwrap();
        fs::write(root.path().join("supabase/.env.sample"), "TOKEN=example\n").unwrap();
        fs::write(root.path().join("neon/.env.staging.sops"), "ciphertext\n").unwrap();

        let report = audit(root.path());
        assert_eq!(report.exit_code(), 2);
        assert_eq!(
            report
                .findings
                .iter()
                .filter(|finding| finding.code == "infra-provider-plaintext-dotenv")
                .count(),
            2
        );
    }
}
