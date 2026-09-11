use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const WORKFLOW_DIRECTORY: &str = ".github/workflows";
const MAX_WORKFLOW_FILES: usize = 256;
const MAX_WORKFLOW_BYTES: u64 = 1024 * 1024;
const SHA1_HEX_LENGTH: usize = 40;
const SHA256_HEX_LENGTH: usize = 64;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum UsesKind {
    Local,
    RemoteCommit,
    DockerDigest,
}

/// Require immutable references for every action and reusable workflow invoked
/// from repository GitHub Actions workflows.
///
/// This is intentionally a bounded lexical gate over the `uses:` key rather
/// than a general YAML interpreter. GitHub remains the workflow-schema
/// authority; this audit only rejects mutable or ambiguous supply-chain refs.
pub(super) fn augment_workflow_action_pin_audit(
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
                    "workflow-directory-unreadable",
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
                "workflow-directory-symlink",
                "workflow directory must not be a symbolic link",
            )
            .with_target(WORKFLOW_DIRECTORY),
        );
        return report.finalize();
    }
    if !metadata.is_dir() {
        report.push(
            Finding::error(
                "workflow-directory-not-directory",
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
                    "workflow-directory-unreadable",
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
                        "workflow-directory-entry-unreadable",
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
                    "workflow-file-limit",
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
    report.insert_metadata("workflowActionPinFileCount", json!(workflows.len()));

    let mut uses_count = 0usize;
    for path in workflows {
        uses_count += audit_workflow_file(&options.path, &path, &mut report);
    }
    report.insert_metadata("workflowActionPinReferenceCount", json!(uses_count));
    if uses_count > 0 {
        report.push(
            Finding::info(
                "workflow-action-pins-inspected",
                format!(
                    "inspected {uses_count} GitHub Actions action/reusable-workflow reference{}",
                    if uses_count == 1 { "" } else { "s" }
                ),
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
                    "workflow-file-unreadable",
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
                "workflow-file-symlink",
                "workflow files must not be symbolic links",
            )
            .with_target(target),
        );
        return 0;
    }
    if !metadata.is_file() {
        report.push(
            Finding::error(
                "workflow-file-not-regular",
                "workflow path must be a regular file",
            )
            .with_target(target),
        );
        return 0;
    }
    if metadata.len() > MAX_WORKFLOW_BYTES {
        report.push(
            Finding::error(
                "workflow-file-too-large",
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
                    "workflow-file-not-utf8",
                    format!("workflow file could not be read as UTF-8: {error}"),
                )
                .with_target(target),
            );
            return 0;
        }
    };

    let mut count = 0usize;
    for (index, line) in text.lines().enumerate() {
        let Some(reference) = parse_uses_reference(line) else {
            continue;
        };
        count += 1;
        let line_target = format!("{target}:{}", index + 1);
        match classify_uses_reference(reference) {
            Ok(UsesKind::Local | UsesKind::RemoteCommit | UsesKind::DockerDigest) => {}
            Err(message) => report.push(
                Finding::error("workflow-action-ref-mutable", message)
                    .with_target(line_target)
                    .with_detail("reference", json!(reference)),
            ),
        }
    }
    count
}

fn parse_uses_reference(line: &str) -> Option<&str> {
    let trimmed = line.trim_start();
    if trimmed.starts_with('#') {
        return None;
    }
    let trimmed = trimmed.strip_prefix('-').map_or(trimmed, str::trim_start);
    let value = trimmed.strip_prefix("uses:")?.trim();
    if value.is_empty() {
        return Some(value);
    }

    if let Some(quoted) = strip_balanced_quotes(value) {
        return Some(quoted);
    }
    Some(
        value
            .split_once(" #")
            .map_or(value, |(head, _)| head.trim_end()),
    )
}

fn strip_balanced_quotes(value: &str) -> Option<&str> {
    let bytes = value.as_bytes();
    if bytes.len() < 2 {
        return None;
    }
    let quote = bytes[0];
    if !matches!(quote, b'\'' | b'"') || *bytes.last()? != quote {
        return None;
    }
    Some(&value[1..value.len() - 1])
}

fn classify_uses_reference(reference: &str) -> Result<UsesKind, String> {
    if reference.is_empty() {
        return Err("uses reference must not be empty".to_owned());
    }
    if reference.contains("${{") || reference.contains("}}") {
        return Err("uses reference must not be computed from an expression".to_owned());
    }
    if reference.starts_with("./") {
        if reference.contains('@') {
            return Err("local action paths must not contain an @ ref suffix".to_owned());
        }
        return Ok(UsesKind::Local);
    }
    if let Some(image) = reference.strip_prefix("docker://") {
        let Some((name, digest)) = image.rsplit_once("@sha256:") else {
            return Err("Docker actions must be pinned by a sha256 image digest".to_owned());
        };
        if name.is_empty() || name.chars().any(char::is_whitespace) {
            return Err(
                "Docker action image name must be non-empty and whitespace-free".to_owned(),
            );
        }
        if !is_lower_hex(digest, SHA256_HEX_LENGTH) {
            return Err(
                "Docker action digest must be 64 lowercase hexadecimal characters".to_owned(),
            );
        }
        return Ok(UsesKind::DockerDigest);
    }

    let Some((source, revision)) = reference.rsplit_once('@') else {
        return Err(
            "remote actions and reusable workflows must include an exact commit ref".to_owned(),
        );
    };
    if source.is_empty()
        || source.starts_with('/')
        || source.ends_with('/')
        || source.chars().any(char::is_whitespace)
        || source.split('/').count() < 2
    {
        return Err("remote action source must be an owner/repository path".to_owned());
    }
    if !is_lower_hex(revision, SHA1_HEX_LENGTH) && !is_lower_hex(revision, SHA256_HEX_LENGTH) {
        return Err(
            "remote actions and reusable workflows must be pinned to a 40- or 64-character lowercase commit SHA"
                .to_owned(),
        );
    }
    Ok(UsesKind::RemoteCommit)
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn is_workflow_path(path: &Path) -> bool {
    path.extension()
        .and_then(|extension| extension.to_str())
        .is_some_and(|extension| matches!(extension, "yml" | "yaml"))
}

fn relative_display(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use std::fs;

    use serde_json::Value as JsonValue;
    use tempfile::tempdir;

    use super::{MAX_WORKFLOW_BYTES, augment_workflow_action_pin_audit};
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    fn audit(files: &[(&str, &str)]) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        let workflows = root.path().join(".github/workflows");
        fs::create_dir_all(&workflows).expect("workflows");
        for (name, content) in files {
            fs::write(workflows.join(name), content).expect("workflow fixture");
        }
        augment_workflow_action_pin_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn accepts_exact_commits_local_actions_and_docker_digests() {
        let report = audit(&[(
            "ci.yml",
            r#"
steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
  - uses: "owner/repo/.github/workflows/check.yml@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  - uses: ./actions/local
  - uses: docker://ghcr.io/example/tool@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
"#,
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert_eq!(
            report.metadata.get("workflowActionPinReferenceCount"),
            Some(&JsonValue::from(4))
        );
    }

    #[test]
    fn rejects_tags_branches_missing_refs_and_expressions() {
        let report = audit(&[(
            "ci.yaml",
            r#"
steps:
  - uses: actions/checkout@v4
  - uses: owner/action@main
  - uses: owner/action
  - uses: owner/action@${{ inputs.ref }}
"#,
        )]);
        assert_eq!(
            report
                .findings
                .iter()
                .filter(|finding| finding.code == "workflow-action-ref-mutable")
                .count(),
            4
        );
    }

    #[test]
    fn rejects_uppercase_short_and_malformed_commit_refs() {
        let report = audit(&[(
            "ci.yml",
            r#"
steps:
  - uses: owner/action@AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
  - uses: owner/action@aaaaaaaa
  - uses: /owner/action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  - uses: owner /action@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
"#,
        )]);
        assert_eq!(
            report
                .findings
                .iter()
                .filter(|finding| finding.code == "workflow-action-ref-mutable")
                .count(),
            4
        );
    }

    #[test]
    fn rejects_mutable_docker_tags_and_invalid_digests() {
        let report = audit(&[(
            "ci.yml",
            r#"
steps:
  - uses: docker://alpine:latest
  - uses: docker://ghcr.io/example/tool@sha256:abc
  - uses: docker://ghcr.io/example/tool@sha256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
"#,
        )]);
        assert_eq!(
            report
                .findings
                .iter()
                .filter(|finding| finding.code == "workflow-action-ref-mutable")
                .count(),
            3
        );
    }

    #[test]
    fn ignores_comments_and_non_uses_scalar_text() {
        let report = audit(&[(
            "ci.yml",
            r#"
# - uses: owner/action@main
name: "uses: owner/action@main"
run: echo "uses: owner/action@main"
steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
"#,
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert_eq!(
            report.metadata.get("workflowActionPinReferenceCount"),
            Some(&JsonValue::from(1))
        );
    }

    #[test]
    fn rejects_empty_uses_and_local_paths_with_refs() {
        let report = audit(&[(
            "ci.yml",
            "steps:\n  - uses:\n  - uses: ./actions/local@main\n",
        )]);
        assert_eq!(
            report
                .findings
                .iter()
                .filter(|finding| finding.code == "workflow-action-ref-mutable")
                .count(),
            2
        );
    }

    #[test]
    fn rejects_oversized_and_symlinked_workflows() {
        let oversized_content = "a".repeat(MAX_WORKFLOW_BYTES as usize + 1);
        let oversized = audit(&[("oversized.yml", &oversized_content)]);
        assert!(
            oversized
                .findings
                .iter()
                .any(|finding| finding.code == "workflow-file-too-large")
        );

        #[cfg(unix)]
        {
            use std::os::unix::fs::symlink;

            let root = tempdir().expect("temporary repository");
            let workflows = root.path().join(".github/workflows");
            fs::create_dir_all(&workflows).expect("workflows");
            let outside = root.path().join("outside.yml");
            fs::write(
                &outside,
                "steps:\n  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n",
            )
            .expect("outside");
            symlink(&outside, workflows.join("ci.yml")).expect("workflow symlink");
            let report = augment_workflow_action_pin_audit(
                &RepositoryAuditOptions {
                    path: root.path().to_path_buf(),
                    profile: "baseline".to_owned(),
                    additional_required_paths: Vec::new(),
                },
                CommandReport::new("audit repo"),
            );
            assert!(
                report
                    .findings
                    .iter()
                    .any(|finding| finding.code == "workflow-file-symlink")
            );
        }
    }

    #[test]
    fn absent_workflow_directory_is_not_an_error() {
        let root = tempdir().expect("temporary repository");
        let report = augment_workflow_action_pin_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        );
        assert_eq!(report.issue_count(), 0);
        assert!(!report.metadata.contains_key("workflowActionPinFileCount"));
    }
}
