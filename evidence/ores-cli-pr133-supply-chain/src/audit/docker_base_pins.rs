use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::json;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const MAX_DOCKERFILES: usize = 64;
const MAX_DOCKERFILE_BYTES: u64 = 2 * 1024 * 1024;
const SHA256_HEX_LENGTH: usize = 64;

/// Require immutable external base-image digests in repository-root Dockerfiles.
///
/// `scratch` and references to a previously declared build stage are local
/// identities and remain valid without a registry digest. External image names
/// must carry one literal lowercase `sha256` digest; ARG/expression-selected
/// base images fail closed because their resolved identity is not reviewable in
/// repository source.
pub(super) fn augment_docker_base_pin_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    let entries = match fs::read_dir(&options.path) {
        Ok(entries) => entries,
        Err(error) => {
            report.push(
                Finding::error(
                    "docker-base-root-unreadable",
                    format!("repository root could not be enumerated for Dockerfiles: {error}"),
                )
                .with_target(options.path.to_string_lossy()),
            );
            return report.finalize();
        }
    };

    let mut files = Vec::<PathBuf>::new();
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error) => {
                report.push(
                    Finding::warning(
                        "docker-base-root-entry-unreadable",
                        format!("repository root entry could not be inspected: {error}"),
                    )
                    .with_target(options.path.to_string_lossy()),
                );
                continue;
            }
        };
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        if !is_dockerfile_name(name) {
            continue;
        }
        if files.len() >= MAX_DOCKERFILES {
            report.push(
                Finding::error(
                    "docker-base-file-limit",
                    format!("more than {MAX_DOCKERFILES} root Dockerfiles were discovered"),
                )
                .with_target(options.path.to_string_lossy()),
            );
            break;
        }
        files.push(path);
    }
    files.sort();

    let issues_before = report.issue_count();
    let mut from_count = 0usize;
    let mut external_count = 0usize;
    for path in &files {
        let result = audit_dockerfile(&options.path, path, &mut report);
        from_count += result.from_count;
        external_count += result.external_count;
    }

    report.insert_metadata("dockerBasePinFileCount", json!(files.len()));
    report.insert_metadata("dockerBasePinFromCount", json!(from_count));
    report.insert_metadata("dockerBasePinExternalCount", json!(external_count));
    if !files.is_empty() && report.issue_count() == issues_before {
        report.push(
            Finding::info(
                "docker-base-pins-ready",
                "all external Docker base images are pinned by literal lowercase sha256 digests",
            )
            .with_target("Dockerfile"),
        );
    }
    report.finalize()
}

#[derive(Debug, Default)]
struct DockerfileAudit {
    from_count: usize,
    external_count: usize,
}

fn audit_dockerfile(root: &Path, path: &Path, report: &mut CommandReport) -> DockerfileAudit {
    let target = relative_display(root, path);
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) => {
            report.push(
                Finding::error(
                    "docker-base-file-unreadable",
                    format!("Dockerfile metadata could not be read: {error}"),
                )
                .with_target(target),
            );
            return DockerfileAudit::default();
        }
    };
    if metadata.file_type().is_symlink() {
        report.push(
            Finding::error(
                "docker-base-file-symlink",
                "Dockerfiles must not be symbolic links",
            )
            .with_target(target),
        );
        return DockerfileAudit::default();
    }
    if !metadata.is_file() {
        report.push(
            Finding::error(
                "docker-base-file-not-regular",
                "Dockerfile path must be a regular file",
            )
            .with_target(target),
        );
        return DockerfileAudit::default();
    }
    if metadata.len() > MAX_DOCKERFILE_BYTES {
        report.push(
            Finding::error(
                "docker-base-file-too-large",
                format!("Dockerfile exceeds the {MAX_DOCKERFILE_BYTES}-byte audit bound"),
            )
            .with_target(target),
        );
        return DockerfileAudit::default();
    }
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) => {
            report.push(
                Finding::error(
                    "docker-base-file-not-utf8",
                    format!("Dockerfile could not be read as UTF-8: {error}"),
                )
                .with_target(target),
            );
            return DockerfileAudit::default();
        }
    };

    let mut stages = BTreeSet::<String>::new();
    let mut audit = DockerfileAudit::default();
    for (index, raw_line) in text.lines().enumerate() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some(spec) = parse_from_instruction(line) else {
            continue;
        };
        audit.from_count += 1;
        let line_target = format!("{target}:{}", index + 1);
        let local = spec.image.eq_ignore_ascii_case("scratch") || stages.contains(spec.image);
        if !local {
            audit.external_count += 1;
            if spec.image.contains("${") || spec.image.contains("$[") {
                report.push(
                    Finding::error(
                        "docker-base-image-dynamic",
                        "external Docker base image must be a literal reviewable identity, not an ARG/expression",
                    )
                    .with_target(line_target.clone()),
                );
            } else if !valid_external_image_pin(spec.image) {
                report.push(
                    Finding::error(
                        "docker-base-image-unpinned",
                        "external Docker base image must end in @sha256:<64 lowercase hex characters>",
                    )
                    .with_target(line_target.clone())
                    .with_detail("image", json!(spec.image)),
                );
            }
        }

        if let Some(alias) = spec.alias {
            if !valid_stage_alias(alias) {
                report.push(
                    Finding::error(
                        "docker-base-stage-alias-invalid",
                        "Docker build stage alias must be a non-empty whitespace-free token",
                    )
                    .with_target(line_target),
                );
            } else {
                stages.insert(alias.to_owned());
            }
        }
    }

    if audit.from_count == 0 {
        report.push(
            Finding::error(
                "docker-base-from-missing",
                "Dockerfile does not contain a reviewable FROM instruction",
            )
            .with_target(target),
        );
    }
    audit
}

#[derive(Debug, Clone, Copy)]
struct FromInstruction<'a> {
    image: &'a str,
    alias: Option<&'a str>,
}

fn parse_from_instruction(line: &str) -> Option<FromInstruction<'_>> {
    let mut fields = line.split_whitespace();
    if !fields.next()?.eq_ignore_ascii_case("FROM") {
        return None;
    }
    let mut image = fields.next()?;
    if image.starts_with("--platform=") {
        image = fields.next()?;
    }
    let remaining = fields.collect::<Vec<_>>();
    let alias = match remaining.as_slice() {
        [] => None,
        [keyword, alias] if keyword.eq_ignore_ascii_case("AS") => Some(*alias),
        _ => None,
    };
    Some(FromInstruction { image, alias })
}

fn valid_external_image_pin(image: &str) -> bool {
    let Some((name, digest)) = image.rsplit_once("@sha256:") else {
        return false;
    };
    !name.is_empty()
        && !name.chars().any(char::is_whitespace)
        && digest.len() == SHA256_HEX_LENGTH
        && digest
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn valid_stage_alias(alias: &str) -> bool {
    !alias.is_empty() && !alias.chars().any(char::is_whitespace)
}

fn is_dockerfile_name(name: &str) -> bool {
    matches!(name, "Dockerfile" | "Containerfile")
        || name.starts_with("Dockerfile.")
        || name.starts_with("Containerfile.")
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

    use super::augment_docker_base_pin_audit;
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    const DIGEST: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

    fn audit(files: &[(&str, String)]) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        for (name, contents) in files {
            fs::write(root.path().join(name), contents).expect("write Dockerfile");
        }
        augment_docker_base_pin_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    #[test]
    fn pinned_external_image_and_stage_reuse_pass() {
        let report = audit(&[(
            "Dockerfile",
            format!("FROM rust:1.95@sha256:{DIGEST} AS builder\nRUN true\nFROM builder AS final\n"),
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "docker-base-pins-ready")
        );
    }

    #[test]
    fn scratch_and_platform_pins_pass() {
        let report = audit(&[(
            "Containerfile",
            format!("FROM --platform=linux/amd64 alpine@sha256:{DIGEST} AS build\nFROM scratch\n"),
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
    }

    #[test]
    fn tag_only_external_image_fails_closed() {
        let report = audit(&[("Dockerfile", "FROM ubuntu:24.04\n".to_owned())]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| { finding.code == "docker-base-image-unpinned" })
        );
    }

    #[test]
    fn dynamic_base_image_fails_closed() {
        let report = audit(&[(
            "Dockerfile",
            "ARG BASE_IMAGE\nFROM ${BASE_IMAGE} AS build\n".to_owned(),
        )]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| { finding.code == "docker-base-image-dynamic" })
        );
    }

    #[test]
    fn short_or_uppercase_digest_fails_closed() {
        for digest in [
            "abc",
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        ] {
            let report = audit(&[("Dockerfile", format!("FROM alpine@sha256:{digest}\n"))]);
            assert!(
                report
                    .findings
                    .iter()
                    .any(|finding| { finding.code == "docker-base-image-unpinned" })
            );
        }
    }

    #[test]
    fn dockerfile_variants_are_scanned() {
        let report = audit(&[(
            "Dockerfile.release",
            "FROM ghcr.io/example/app:latest\n".to_owned(),
        )]);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| { finding.code == "docker-base-image-unpinned" })
        );
    }

    #[test]
    fn comments_do_not_create_fake_from_instructions() {
        let report = audit(&[(
            "Dockerfile",
            format!("# FROM mutable:latest\nFROM alpine@sha256:{DIGEST}\n"),
        )]);
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
    }
}
