use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const WORKFLOW_DIRECTORY: &str = ".github/workflows";
const MAX_WORKFLOW_FILES: usize = 256;
const MAX_WORKFLOW_BYTES: u64 = 1024 * 1024;

/// Require every GitHub Actions workflow to declare an explicit top-level
/// permissions posture and reject unbounded or expression-selected permissions.
///
/// GitHub remains the workflow-schema authority. This is a narrow ORES policy
/// gate over the `permissions:` key and intentionally permits scoped write
/// grants such as `contents: write` when a workflow genuinely needs them.
pub(super) fn augment_workflow_permissions_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    let root = options.path.join(WORKFLOW_DIRECTORY);
    let metadata = match fs::symlink_metadata(&root) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return report.finalize(),
        Err(error) => {
            report.push(
                Finding::error(
                    "workflow-permissions-directory-unreadable",
                    format!("workflow directory metadata could not be read: {error}"),
                )
                .with_target(WORKFLOW_DIRECTORY),
            );
            return report.finalize();
        }
    };

    if metadata.file_type().is_symlink() {
        report.push(
            Finding::error(
                "workflow-permissions-directory-symlink",
                "workflow directory must not be a symbolic link",
            )
            .with_target(WORKFLOW_DIRECTORY),
        );
        return report.finalize();
    }
    if !metadata.is_dir() {
        report.push(
            Finding::error(
                "workflow-permissions-directory-not-directory",
                "workflow path must be a directory when present",
            )
            .with_target(WORKFLOW_DIRECTORY),
        );
        return report.finalize();
    }

    let entries = match fs::read_dir(&root) {
        Ok(entries) => entries,
        Err(error) => {
            report.push(
                Finding::error(
                    "workflow-permissions-directory-unreadable",
                    format!("workflow directory could not be enumerated: {error}"),
                )
                .with_target(WORKFLOW_DIRECTORY),
            );
            return report.finalize();
        }
    };

    let mut workflows = Vec::<PathBuf>::new();
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error) => {
                report.push(
                    Finding::warning(
                        "workflow-permissions-entry-unreadable",
                        format!("workflow directory entry could not be read: {error}"),
                    )
                    .with_target(WORKFLOW_DIRECTORY),
                );
                continue;
            }
        };
        let path = entry.path();
        if !is_workflow_path(&path) {
            continue;
        }
        if workflows.len() >= MAX_WORKFLOW_FILES {
            report.push(
                Finding::error(
                    "workflow-permissions-file-limit",
                    format!(
                        "more than {MAX_WORKFLOW_FILES} workflow files were discovered; split or narrow the workflow surface"
                    ),
                )
                .with_target(WORKFLOW_DIRECTORY),
            );
            break;
        }
        workflows.push(path);
    }
    workflows.sort();

    let issues_before = report.issue_count();
    let mut permission_declaration_count = 0usize;
    for path in &workflows {
        permission_declaration_count += audit_workflow_file(&options.path, path, &mut report);
    }

    report.insert_metadata("workflowPermissionsFileCount", json!(workflows.len()));
    report.insert_metadata(
        "workflowPermissionsDeclarationCount",
        json!(permission_declaration_count),
    );
    if !workflows.is_empty() && report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "workflow-permissions-ready",
                "all workflows declare an explicit top-level permissions posture without write-all or dynamic permissions",
            )
            .with_target(WORKFLOW_DIRECTORY),
        );
    }
    report.finalize()
}

fn audit_workflow_file(root: &Path, path: &Path, report: &mut CommandReport) -> usize {
    let target = relative_display(root, path);
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) => {
            report.push(
                Finding::error(
                    "workflow-permissions-file-unreadable",
                    format!("workflow metadata could not be read: {error}"),
                )
                .with_target(target),
            );
            return 0;
        }
    };
    if metadata.file_type().is_symlink() {
        report.push(
            Finding::error(
                "workflow-permissions-file-symlink",
                "workflow files must not be symbolic links",
            )
            .with_target(target),
        );
        return 0;
    }
    if !metadata.is_file() {
        report.push(
            Finding::error(
                "workflow-permissions-file-not-regular",
                "workflow path must be a regular file",
            )
            .with_target(target),
        );
        return 0;
    }
    if metadata.len() > MAX_WORKFLOW_BYTES {
        report.push(
            Finding::error(
                "workflow-permissions-file-too-large",
                format!("workflow file exceeds the {MAX_WORKFLOW_BYTES}-byte audit bound"),
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
                    "workflow-permissions-file-not-utf8",
                    format!("workflow file could not be read as UTF-8: {error}"),
                )
                .with_target(target),
            );
            return 0;
        }
    };

    let mut top_level_count = 0usize;
    let mut total_count = 0usize;
    for (index, line) in text.lines().enumerate() {
        let trimmed = line.trim_start();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let Some(rest) = trimmed.strip_prefix("permissions:") else {
            continue;
        };
        total_count += 1;
        let indent = line.len().saturating_sub(trimmed.len());
        if indent == 0 {
            top_level_count += 1;
        }

        let value = permissions_scalar(rest);
        let line_target = format!("{target}:{}", index + 1);
        if value.contains("${{") || value.contains("}}") {
            report.push(
                Finding::error(
                    "workflow-permissions-dynamic",
                    "permissions declarations must not be selected by an expression",
                )
                .with_target(line_target.clone()),
            );
        }
        if strip_balanced_quotes(value) == "write-all" {
            report.push(
                Finding::error(
                    "workflow-permissions-write-all",
                    "permissions: write-all is forbidden; grant only the scopes the job requires",
                )
                .with_target(line_target),
            );
        }
    }

    match top_level_count {
        0 => report.push(
            Finding::error(
                "workflow-permissions-top-level-missing",
                "workflow must declare a top-level permissions posture such as `permissions: read-all`, `permissions: {}`, or an explicit scope map",
            )
            .with_target(target),
        ),
        1 => {}
        count => report.push(
            Finding::error(
                "workflow-permissions-top-level-duplicate",
                format!("workflow declares {count} top-level permissions keys"),
            )
            .with_target(target),
        ),
    }

    total_count
}

fn permissions_scalar(rest: &str) -> &str {
    let value = rest.trim();
    value
        .split_once(" #")
        .map_or(value, |(head, _)| head.trim_end())
}

fn strip_balanced_quotes(value: &str) -> &str {
    let bytes = value.as_bytes();
    if bytes.len() >= 2
        && matches!(bytes[0], b'\'' | b'"')
        && bytes.last().is_some_and(|last| *last == bytes[0])
    {
        &value[1..value.len() - 1]
    } else {
        value
    }
}

fn is_workflow_path(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|extension| extension.to_str()),
        Some("yml" | "yaml")
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

    use super::augment_workflow_permissions_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    fn audit(workflows: &[(&str, &str)]) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::create_dir_all(root.path().join(".github/workflows")).expect("create workflows");
        for (name, contents) in workflows {
            fs::write(root.path().join(".github/workflows").join(name), contents)
                .expect("write workflow");
        }
        augment_workflow_permissions_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn explicit_scope_map_passes() {
        let report = audit(&[(
            "ci.yml",
            "name: CI\non: push\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps: []\n",
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "workflow-permissions-ready")
        );
    }

    #[test]
    fn read_all_and_empty_permissions_pass() {
        for declaration in ["permissions: read-all", "permissions: {}"] {
            let report = audit(&[(
                "ci.yml",
                &format!("name: CI\non: push\n{declaration}\njobs: {{}}\n"),
            )]);
            assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        }
    }

    #[test]
    fn missing_top_level_permissions_fails_closed() {
        let report = audit(&[(
            "ci.yml",
            "name: CI\non: push\njobs:\n  test:\n    permissions:\n      contents: read\n",
        )]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| { finding.code == "workflow-permissions-top-level-missing" })
        );
    }

    #[test]
    fn write_all_is_forbidden_at_any_scope() {
        let report = audit(&[(
            "ci.yml",
            "name: CI\non: push\npermissions: read-all\njobs:\n  release:\n    permissions: \"write-all\"\n",
        )]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| { finding.code == "workflow-permissions-write-all" })
        );
    }

    #[test]
    fn dynamic_permissions_are_forbidden() {
        let report = audit(&[(
            "ci.yml",
            "name: CI\non: push\npermissions: ${{ matrix.permissions }}\njobs: {}\n",
        )]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| { finding.code == "workflow-permissions-dynamic" })
        );
    }

    #[test]
    fn comments_do_not_satisfy_permissions_policy() {
        let report = audit(&[(
            "ci.yml",
            "name: CI\non: push\n# permissions: read-all\njobs: {}\n",
        )]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| { finding.code == "workflow-permissions-top-level-missing" })
        );
    }

    #[test]
    fn duplicate_top_level_permissions_fail_closed() {
        let report = audit(&[(
            "ci.yml",
            "permissions: read-all\nname: CI\npermissions: {}\njobs: {}\n",
        )]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| { finding.code == "workflow-permissions-top-level-duplicate" })
        );
    }
}
