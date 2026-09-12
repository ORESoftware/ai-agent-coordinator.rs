use std::fs;
use std::path::{Path, PathBuf};

use serde_json::json;
use toml::Value;
use walkdir::{DirEntry, WalkDir};

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CARGO_MANIFEST: &str = "Cargo.toml";
const MAX_CARGO_MANIFESTS: usize = 256;
const MAX_CARGO_MANIFEST_BYTES: u64 = 2 * 1024 * 1024;
const MAX_WALK_DEPTH: usize = 8;

/// Require every Cargo git dependency to bind to an immutable full commit rev.
///
/// Registry/version dependencies are outside this gate. Git dependency specs in
/// normal/dev/build/workspace/target/patch/replace contexts must declare a full
/// lowercase 40- or 64-character hexadecimal `rev` and must not also select a
/// mutable `branch` or `tag`.
pub(super) fn augment_cargo_git_pin_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    let mut manifests = Vec::<PathBuf>::new();
    let walker = WalkDir::new(&options.path)
        .follow_links(false)
        .max_depth(MAX_WALK_DEPTH)
        .into_iter()
        .filter_entry(should_descend);

    for result in walker {
        let entry = match result {
            Ok(entry) => entry,
            Err(error) => {
                report.push(
                    Finding::warning(
                        "cargo-git-pin-walk-failed",
                        format!("could not inspect part of the Cargo manifest tree: {error}"),
                    )
                    .with_target(
                        error
                            .path()
                            .map_or_else(|| options.path.clone(), Path::to_path_buf)
                            .to_string_lossy(),
                    ),
                );
                continue;
            }
        };
        if entry.file_name() != CARGO_MANIFEST {
            continue;
        }
        if manifests.len() >= MAX_CARGO_MANIFESTS {
            report.push(
                Finding::error(
                    "cargo-git-pin-manifest-limit",
                    format!("more than {MAX_CARGO_MANIFESTS} Cargo manifests were discovered"),
                )
                .with_target(CARGO_MANIFEST),
            );
            break;
        }
        manifests.push(entry.path().to_path_buf());
    }
    manifests.sort();

    let issues_before = report.issue_count();
    let mut git_dependency_count = 0usize;
    for path in &manifests {
        git_dependency_count += audit_manifest(&options.path, path, &mut report);
    }

    report.insert_metadata("cargoGitPinManifestCount", json!(manifests.len()));
    report.insert_metadata("cargoGitPinDependencyCount", json!(git_dependency_count));
    if !manifests.is_empty() && report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "cargo-git-pins-ready",
                "all Cargo git dependencies are bound to immutable full commit revs",
            )
            .with_target(CARGO_MANIFEST),
        );
    }
    report.finalize()
}

fn audit_manifest(root: &Path, path: &Path, report: &mut CommandReport) -> usize {
    let target = relative_display(root, path);
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) => {
            report.push(
                Finding::error(
                    "cargo-git-pin-manifest-unreadable",
                    format!("Cargo manifest metadata could not be read: {error}"),
                )
                .with_target(target),
            );
            return 0;
        }
    };
    if metadata.file_type().is_symlink() {
        report.push(
            Finding::error(
                "cargo-git-pin-manifest-symlink",
                "Cargo manifests must not be symbolic links",
            )
            .with_target(target),
        );
        return 0;
    }
    if !metadata.is_file() {
        report.push(
            Finding::error(
                "cargo-git-pin-manifest-not-regular",
                "Cargo manifest path must be a regular file",
            )
            .with_target(target),
        );
        return 0;
    }
    if metadata.len() > MAX_CARGO_MANIFEST_BYTES {
        report.push(
            Finding::error(
                "cargo-git-pin-manifest-too-large",
                format!("Cargo manifest exceeds the {MAX_CARGO_MANIFEST_BYTES}-byte audit bound"),
            )
            .with_target(target),
        );
        return 0;
    }
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) => {
            report.push(
                Finding::error(
                    "cargo-git-pin-manifest-not-utf8",
                    format!("Cargo manifest could not be read as UTF-8: {error}"),
                )
                .with_target(target),
            );
            return 0;
        }
    };
    let document = match toml::from_str::<Value>(&text) {
        Ok(document) => document,
        Err(error) => {
            report.push(
                Finding::error(
                    "cargo-git-pin-manifest-invalid",
                    format!("Cargo manifest is not valid TOML: {error}"),
                )
                .with_target(target),
            );
            return 0;
        }
    };

    let mut count = 0usize;
    walk_dependency_tables(&document, "", false, &target, &mut count, report);
    count
}

fn walk_dependency_tables(
    value: &Value,
    path: &str,
    dependency_context: bool,
    manifest_target: &str,
    count: &mut usize,
    report: &mut CommandReport,
) {
    let Value::Table(table) = value else {
        return;
    };

    if dependency_context && table.contains_key("git") {
        *count += 1;
        audit_git_dependency(table, path, manifest_target, report);
    }

    for (key, child) in table {
        let child_path = if path.is_empty() {
            key.to_owned()
        } else {
            format!("{path}.{key}")
        };
        let child_context = dependency_context || dependency_context_key(key);
        walk_dependency_tables(
            child,
            &child_path,
            child_context,
            manifest_target,
            count,
            report,
        );
    }
}

fn audit_git_dependency(
    table: &toml::map::Map<String, Value>,
    path: &str,
    manifest_target: &str,
    report: &mut CommandReport,
) {
    let target = if path.is_empty() {
        manifest_target.to_owned()
    } else {
        format!("{manifest_target}:{path}")
    };

    if table
        .get("git")
        .and_then(Value::as_str)
        .is_none_or(|value| value.trim().is_empty())
    {
        report.push(
            Finding::error(
                "cargo-git-pin-url-shape",
                "git dependency source must be a non-empty string",
            )
            .with_target(target.clone()),
        );
    }

    if table.contains_key("branch") || table.contains_key("tag") {
        report.push(
            Finding::error(
                "cargo-git-pin-mutable-selector",
                "git dependency must not carry branch/tag selectors; bind review intent only to rev",
            )
            .with_target(target.clone()),
        );
    }

    if !table
        .get("rev")
        .and_then(Value::as_str)
        .is_some_and(valid_commit_rev)
    {
        report.push(
            Finding::error(
                "cargo-git-pin-rev",
                "git dependency rev must be a full 40- or 64-character lowercase hexadecimal commit id",
            )
            .with_target(target),
        );
    }
}

fn dependency_context_key(key: &str) -> bool {
    matches!(
        key,
        "dependencies" | "dev-dependencies" | "build-dependencies" | "patch" | "replace"
    )
}

fn valid_commit_rev(value: &str) -> bool {
    matches!(value.len(), 40 | 64)
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn should_descend(entry: &DirEntry) -> bool {
    if entry.depth() == 0 {
        return true;
    }
    !matches!(
        entry.file_name().to_string_lossy().as_ref(),
        ".git" | "target" | "node_modules" | ".dart_tool" | "vendor" | "generated"
    )
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

    use tempfile::tempdir;

    use super::augment_cargo_git_pin_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    const REV: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    fn audit(files: &[(&str, String)]) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        for (name, contents) in files {
            let path = root.path().join(name);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).expect("create manifest parent");
            }
            fs::write(path, contents).expect("write Cargo manifest");
        }
        augment_cargo_git_pin_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn full_rev_git_dependency_passes() {
        let report = audit(&[(
            "Cargo.toml",
            format!(
                "[dependencies]\nfoo = {{ git = \"https://github.com/o/foo\", rev = \"{REV}\" }}\n"
            ),
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert_eq!(report.metadata["cargoGitPinDependencyCount"], 1);
    }

    #[test]
    fn git_dependency_without_rev_fails_closed() {
        let report = audit(&[(
            "Cargo.toml",
            "[dependencies]\nfoo = { git = \"https://github.com/o/foo\" }\n".to_owned(),
        )]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "cargo-git-pin-rev")
        );
    }

    #[test]
    fn branch_and_tag_selectors_are_rejected() {
        for selector in ["branch = \"main\"", "tag = \"v1\""] {
            let report = audit(&[(
                "Cargo.toml",
                format!(
                    "[dependencies]\nfoo = {{ git = \"https://github.com/o/foo\", rev = \"{REV}\", {selector} }}\n"
                ),
            )]);
            assert!(
                report
                    .findings
                    .iter()
                    .any(|finding| { finding.code == "cargo-git-pin-mutable-selector" })
            );
        }
    }

    #[test]
    fn short_or_uppercase_rev_is_rejected() {
        for rev in ["abc", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"] {
            let report = audit(&[(
                "Cargo.toml",
                format!(
                    "[dependencies]\nfoo = {{ git = \"https://github.com/o/foo\", rev = \"{rev}\" }}\n"
                ),
            )]);
            assert!(
                report
                    .findings
                    .iter()
                    .any(|finding| finding.code == "cargo-git-pin-rev")
            );
        }
    }

    #[test]
    fn workspace_and_target_git_dependencies_are_scanned() {
        let report = audit(&[(
            "Cargo.toml",
            format!(
                "[workspace.dependencies]\nfoo = {{ git = \"https://github.com/o/foo\", rev = \"{REV}\" }}\n[target.'cfg(unix)'.dependencies]\nbar = {{ git = \"https://github.com/o/bar\" }}\n"
            ),
        )]);
        assert_eq!(report.metadata["cargoGitPinDependencyCount"], 2);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "cargo-git-pin-rev")
        );
    }

    #[test]
    fn patch_git_dependencies_are_scanned() {
        let report = audit(&[(
            "Cargo.toml",
            format!(
                "[patch.crates-io]\nfoo = {{ git = \"https://github.com/o/foo\", rev = \"{REV}\" }}\n"
            ),
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert_eq!(report.metadata["cargoGitPinDependencyCount"], 1);
    }

    #[test]
    fn registry_dependencies_are_ignored() {
        let report = audit(&[(
            "Cargo.toml",
            "[dependencies]\nserde = \"1\"\ntokio = { version = \"1\", features = [\"rt\"] }\n"
                .to_owned(),
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert_eq!(report.metadata["cargoGitPinDependencyCount"], 0);
    }

    #[test]
    fn nested_member_manifests_are_scanned() {
        let report = audit(&[
            (
                "Cargo.toml",
                "[workspace]\nmembers = [\"member\"]\n".to_owned(),
            ),
            (
                "member/Cargo.toml",
                "[dependencies]\nfoo = { git = \"https://github.com/o/foo\" }\n".to_owned(),
            ),
        ]);
        assert_eq!(report.metadata["cargoGitPinManifestCount"], 2);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "cargo-git-pin-rev")
        );
    }
}
