use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const MAX_POLICY_BYTES: u64 = 256 * 1024;
const MAX_MIGRATION_BYTES: u64 = 4 * 1024 * 1024;
const MAX_SQL_FILES_PER_LANE: usize = 512;
const PROVIDERS: [&str; 2] = ["supabase", "neon"];
const MIGRATION_LANES: [&str; 4] = [
    "supabase/auth/migrations",
    "supabase/admin/migrations",
    "neon/auth/migrations",
    "neon/admin/migrations",
];

/// Add bounded, secret-aware policy validation to the structural infra audit.
///
/// Empty provider lanes are valid only when the repository documents a real
/// migration authority and an intentionally deferred, separately reviewed
/// apply path. This prevents fleet tooling from encouraging fake timestamped
/// SQL or copies of migrations owned by an ORM/interfaces repository.
pub(super) fn augment_infra_policy_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    report.insert_metadata("infraPolicyHardeningVersion", json!(1));

    for provider in PROVIDERS {
        for policy_name in ["README.md", "GITOPS.md"] {
            let relative = format!("{provider}/{policy_name}");
            let path = options.path.join(&relative);
            if !path.exists() {
                continue;
            }
            if let Some(source) = read_bounded_text(
                &path,
                MAX_POLICY_BYTES,
                &relative,
                "provider-policy",
                &mut report,
            ) {
                reject_embedded_credential_material(&source, &relative, "provider-policy", &mut report);
            }
        }
    }

    for relative in MIGRATION_LANES {
        audit_migration_lane(&options.path, relative, &mut report);
    }

    report.finalize()
}

fn audit_migration_lane(root: &Path, relative: &str, report: &mut CommandReport) {
    let lane = root.join(relative);
    let metadata = match fs::symlink_metadata(&lane) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return,
        Err(error) => {
            report.push(
                Finding::error(
                    "infra-migration-lane-metadata-unreadable",
                    format!("migration lane metadata could not be read: {error}"),
                )
                .with_target(relative.to_owned()),
            );
            return;
        }
    };
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        // The base repository audit also rejects provider-root symlinks. Keep a
        // lane-specific finding because this is the boundary that owns DDL.
        report.push(
            Finding::error(
                "infra-migration-lane-not-directory",
                "migration lane must be a real repository directory, not a symlink or file",
            )
            .with_target(relative.to_owned()),
        );
        return;
    }

    let entries = match fs::read_dir(&lane) {
        Ok(entries) => entries,
        Err(error) => {
            report.push(
                Finding::error(
                    "infra-migration-lane-unreadable",
                    format!("migration lane could not be enumerated: {error}"),
                )
                .with_target(relative.to_owned()),
            );
            return;
        }
    };

    let mut sql_files = Vec::<PathBuf>::new();
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error) => {
                report.push(
                    Finding::warning(
                        "infra-migration-lane-entry-unreadable",
                        format!("migration lane entry could not be read: {error}"),
                    )
                    .with_target(relative.to_owned()),
                );
                continue;
            }
        };
        let path = entry.path();
        if path.extension().is_some_and(|extension| extension == "sql") {
            if sql_files.len() >= MAX_SQL_FILES_PER_LANE {
                report.push(
                    Finding::error(
                        "infra-migration-sql-file-limit",
                        format!(
                            "migration lane contains more than {MAX_SQL_FILES_PER_LANE} SQL files; split the review surface"
                        ),
                    )
                    .with_target(relative.to_owned()),
                );
                break;
            }
            sql_files.push(path);
        }
    }
    sql_files.sort();

    if sql_files.is_empty() {
        audit_deferred_lane_policy(root, &lane, relative, report);
        return;
    }

    for path in sql_files {
        let display = relative_path(root, &path);
        let Some(source) = read_bounded_text(
            &path,
            MAX_MIGRATION_BYTES,
            &display,
            "migration-sql",
            report,
        ) else {
            continue;
        };
        if source.trim().is_empty() {
            report.push(
                Finding::error(
                    "infra-migration-sql-empty",
                    "reviewed migration SQL must not be empty",
                )
                .with_target(display.clone()),
            );
            continue;
        }
        reject_embedded_credential_material(&source, &display, "migration-sql", report);
        if contains_exact_token(&source, "DATABASE_URL") {
            report.push(
                Finding::error(
                    "infra-migration-generic-database-url",
                    "migration SQL must not depend on generic DATABASE_URL; customer/admin provider lanes use explicit runtime keys",
                )
                .with_target(display),
            );
        }
    }
}

fn audit_deferred_lane_policy(
    root: &Path,
    lane: &Path,
    relative: &str,
    report: &mut CommandReport,
) {
    let readme = lane.join("README.md");
    let display = relative_path(root, &readme);
    let Some(source) = read_bounded_text(
        &readme,
        MAX_POLICY_BYTES,
        &display,
        "deferred-migration-policy",
        report,
    ) else {
        return;
    };
    let normalized = source.to_ascii_lowercase();

    let has_migration = normalized.contains("migration");
    let has_authority = ["canonical", "source", "owner", "ownership", "repository"]
        .iter()
        .filter(|needle| normalized.contains(**needle))
        .count()
        >= 2;
    let is_deferred = normalized.contains("defer")
        || normalized.contains("not generated")
        || normalized.contains("not approved")
        || normalized.contains("no production");
    let separate_apply = normalized.contains("apply") || normalized.contains("promotion");
    let no_startup_ddl = normalized.contains("startup")
        && ["never", "not", "forbid", "disabled"]
            .iter()
            .any(|needle| normalized.contains(needle));

    if !has_migration || !has_authority {
        report.push(
            Finding::error(
                "infra-deferred-migration-authority-missing",
                "deferred migration lane must identify a canonical migration source/owner or repository authority",
            )
            .with_target(relative.to_owned()),
        );
    }
    if !is_deferred || !separate_apply {
        report.push(
            Finding::error(
                "infra-deferred-migration-policy-incomplete",
                "deferred migration lane must explicitly state why SQL is deferred and that apply/promotion is separately reviewed",
            )
            .with_target(relative.to_owned()),
        );
    }
    if !no_startup_ddl {
        report.push(
            Finding::error(
                "infra-deferred-migration-startup-ddl-policy-missing",
                "deferred migration lane must explicitly prohibit application-startup DDL",
            )
            .with_target(relative.to_owned()),
        );
    }

    reject_embedded_credential_material(&source, &display, "deferred-migration-policy", report);
}

fn read_bounded_text(
    path: &Path,
    max_bytes: u64,
    target: &str,
    kind: &str,
    report: &mut CommandReport,
) -> Option<String> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return None,
        Err(error) => {
            report.push(
                Finding::error(
                    format!("infra-{kind}-metadata-unreadable"),
                    format!("infra policy file metadata could not be read: {error}"),
                )
                .with_target(target.to_owned()),
            );
            return None;
        }
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        report.push(
            Finding::error(
                format!("infra-{kind}-not-regular-file"),
                "infra policy and migration evidence must be regular repository files, not symlinks",
            )
            .with_target(target.to_owned()),
        );
        return None;
    }
    if metadata.len() > max_bytes {
        report.push(
            Finding::error(
                format!("infra-{kind}-too-large"),
                format!("infra policy file exceeds the bounded {max_bytes}-byte audit limit"),
            )
            .with_target(target.to_owned()),
        );
        return None;
    }
    match fs::read_to_string(path) {
        Ok(source) => Some(source),
        Err(error) => {
            report.push(
                Finding::error(
                    format!("infra-{kind}-unreadable"),
                    format!("infra policy file must be valid UTF-8 and readable: {error}"),
                )
                .with_target(target.to_owned()),
            );
            None
        }
    }
}

fn reject_embedded_credential_material(
    source: &str,
    target: &str,
    kind: &str,
    report: &mut CommandReport,
) {
    let lower = source.to_ascii_lowercase();
    if lower.contains("postgres://") || lower.contains("postgresql://") {
        report.push(
            Finding::error(
                format!("infra-{kind}-database-url"),
                "infra policy/migration content must not embed PostgreSQL connection URLs",
            )
            .with_target(target.to_owned()),
        );
    }
    if lower.contains("begin private key")
        || lower.contains("begin rsa private key")
        || lower.contains("begin ec private key")
        || lower.contains("begin openssh private key")
    {
        report.push(
            Finding::error(
                format!("infra-{kind}-private-key"),
                "infra policy/migration content must not embed private-key material",
            )
            .with_target(target.to_owned()),
        );
    }
}

fn contains_exact_token(source: &str, token: &str) -> bool {
    source
        .split(|character: char| !character.is_ascii_alphanumeric() && character != '_')
        .any(|candidate| candidate == token)
}

fn relative_path(root: &Path, path: &Path) -> String {
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

    use super::{RepositoryAuditOptions, augment_infra_policy_audit};
    use crate::model::CommandReport;

    fn audit(path: &Path) -> CommandReport {
        augment_infra_policy_audit(
            &RepositoryAuditOptions {
                path: path.to_path_buf(),
                profile: "infra".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("test infra policy"),
        )
    }

    fn write_provider_tree(path: &Path, sql: bool) {
        for provider in ["supabase", "neon"] {
            fs::create_dir_all(path.join(provider).join("auth/migrations")).unwrap();
            fs::create_dir_all(path.join(provider).join("admin/migrations")).unwrap();
            fs::write(
                path.join(provider).join("README.md"),
                "Migration ownership is reviewed; secrets remain outside Git.\n",
            )
            .unwrap();
            for lane in ["auth", "admin"] {
                let lane_path = path.join(provider).join(lane).join("migrations");
                if sql {
                    fs::write(lane_path.join("202609110001_policy.sql"), "select 1;\n").unwrap();
                } else {
                    fs::write(
                        lane_path.join("README.md"),
                        "Canonical migration source/owner is the ORM repository. Apply is deferred until a reviewed provider artifact exists; promotion is separately approved. Application startup never owns DDL.\n",
                    )
                    .unwrap();
                }
            }
        }
    }

    #[test]
    fn accepts_strict_deferred_migration_authority() {
        let root = tempdir().unwrap();
        write_provider_tree(root.path(), false);
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 0, "{:#?}", report.findings);
    }

    #[test]
    fn rejects_weak_deferred_policy_that_only_mentions_apply() {
        let root = tempdir().unwrap();
        write_provider_tree(root.path(), false);
        fs::write(
            root.path().join("neon/admin/migrations/README.md"),
            "Migration apply is deferred.\n",
        )
        .unwrap();
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 2);
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "infra-deferred-migration-authority-missing"));
        assert!(report.findings.iter().any(|finding| {
            finding.code == "infra-deferred-migration-startup-ddl-policy-missing"
        }));
    }

    #[test]
    fn rejects_database_url_in_deferred_policy() {
        let root = tempdir().unwrap();
        write_provider_tree(root.path(), false);
        let path = root.path().join("supabase/auth/migrations/README.md");
        let mut source = fs::read_to_string(&path).unwrap();
        source.push_str("Example: postgres://example.invalid/db\n");
        fs::write(path, source).unwrap();
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 2);
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "infra-deferred-migration-policy-database-url"));
    }

    #[test]
    fn rejects_database_url_in_sql() {
        let root = tempdir().unwrap();
        write_provider_tree(root.path(), true);
        fs::write(
            root.path().join("neon/auth/migrations/202609110001_policy.sql"),
            "-- postgres://example.invalid/db\nselect 1;\n",
        )
        .unwrap();
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 2);
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "infra-migration-sql-database-url"));
    }

    #[test]
    fn rejects_generic_database_url_token_in_sql() {
        let root = tempdir().unwrap();
        write_provider_tree(root.path(), true);
        fs::write(
            root.path().join("supabase/admin/migrations/202609110001_policy.sql"),
            "-- runtime must not read DATABASE_URL\nselect 1;\n",
        )
        .unwrap();
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 2);
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "infra-migration-generic-database-url"));
    }

    #[test]
    fn rejects_private_key_marker_in_provider_policy() {
        let root = tempdir().unwrap();
        write_provider_tree(root.path(), true);
        fs::write(
            root.path().join("neon/GITOPS.md"),
            "-----BEGIN PRIVATE KEY-----\n",
        )
        .unwrap();
        let report = audit(root.path());
        assert_eq!(report.exit_code(), 2);
        assert!(report
            .findings
            .iter()
            .any(|finding| finding.code == "infra-provider-policy-private-key"));
    }
}
