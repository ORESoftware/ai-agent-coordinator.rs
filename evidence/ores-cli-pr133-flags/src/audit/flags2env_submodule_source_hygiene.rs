use std::fs;
use std::path::Path;

use serde_json::json;

use crate::model::{CommandReport, Finding};

const GITMODULES: &str = ".gitmodules";
const RETIRED_SOURCE: &str = "github.com/ORESoftware/flags-2-env";
const CANONICAL_SOURCE: &str = "github.com/flags-2-env/flags-2-env";
const MAX_INPUT_BYTES: u64 = 1024 * 1024;

/// Enforce the post-cutoff canonical flags2env source for Git submodules.
///
/// `.gitmodules` owns the clone URL only; the immutable revision for a
/// submodule is the repository's gitlink entry, not a field in this file. This
/// audit therefore validates source identity without inventing or duplicating
/// a commit authority.
pub(crate) fn audit_flags2env_submodule_source_hygiene(
    root: &Path,
    report: &mut CommandReport,
) {
    let path = root.join(GITMODULES);
    let metadata = match fs::symlink_metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
        Err(error) => {
            report.push(
                Finding::error(
                    "flags2env-submodule-config-unreadable",
                    format!(".gitmodules metadata could not be inspected: {error}"),
                )
                .with_target(GITMODULES),
            );
            return;
        }
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() || metadata.len() > MAX_INPUT_BYTES {
        report.push(
            Finding::error(
                "flags2env-submodule-config-unsafe",
                ".gitmodules must be a bounded regular file before submodule-source hygiene can be trusted",
            )
            .with_target(GITMODULES),
        );
        return;
    }

    let text = match fs::read_to_string(&path) {
        Ok(text) => text,
        Err(error) => {
            report.push(
                Finding::error(
                    "flags2env-submodule-config-unreadable",
                    format!(".gitmodules could not be read as UTF-8: {error}"),
                )
                .with_target(GITMODULES),
            );
            return;
        }
    };

    let mut current_section = String::new();
    let mut current_path: Option<String> = None;
    let mut current_url: Option<String> = None;
    let mut flags2env_sections = 0_u64;
    let mut canonical_sections = 0_u64;

    for (line_number, raw) in text.lines().enumerate() {
        let line = raw.trim();
        if line.starts_with('[') && line.ends_with(']') {
            flush_section(
                &current_section,
                current_path.take(),
                current_url.take(),
                report,
                &mut flags2env_sections,
                &mut canonical_sections,
            );
            current_section = line.to_owned();
            continue;
        }

        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim();
        let value = value.trim();
        match key {
            "path" => current_path = Some(value.to_owned()),
            "url" => {
                current_url = Some(value.to_owned());
                if value.contains(RETIRED_SOURCE) {
                    report.push(
                        Finding::error(
                            "flags2env-submodule-retired-source",
                            "flags2env submodule still references the retired ORESoftware/flags-2-env source; preserve the gitlink revision and migrate the clone URL to flags-2-env/flags-2-env",
                        )
                        .with_target(GITMODULES)
                        .with_detail("line", json!(line_number + 1)),
                    );
                }
            }
            _ => {}
        }
    }

    flush_section(
        &current_section,
        current_path,
        current_url,
        report,
        &mut flags2env_sections,
        &mut canonical_sections,
    );

    report.insert_metadata("flags2envSubmoduleSectionCount", json!(flags2env_sections));
    report.insert_metadata("flags2envCanonicalSubmoduleSectionCount", json!(canonical_sections));
}

fn flush_section(
    section: &str,
    path: Option<String>,
    url: Option<String>,
    report: &mut CommandReport,
    flags2env_sections: &mut u64,
    canonical_sections: &mut u64,
) {
    if section.is_empty() {
        return;
    }
    let path_is_flags2env = path
        .as_deref()
        .is_some_and(|path| path.ends_with("flags-2-env") || path.ends_with("flags2env"));
    let section_is_flags2env = section.to_ascii_lowercase().contains("flags-2-env")
        || section.to_ascii_lowercase().contains("flags2env");
    let url_is_flags2env = url.as_deref().is_some_and(|url| {
        url.contains("flags-2-env") || url.to_ascii_lowercase().contains("flags2env")
    });

    if !(path_is_flags2env || section_is_flags2env || url_is_flags2env) {
        return;
    }
    *flags2env_sections += 1;

    let Some(url) = url else {
        report.push(
            Finding::error(
                "flags2env-submodule-url-missing",
                "flags2env submodule section has no clone URL",
            )
            .with_target(GITMODULES)
            .with_detail("section", json!(section)),
        );
        return;
    };

    if url.contains(CANONICAL_SOURCE) {
        *canonical_sections += 1;
        return;
    }
    if url.contains(RETIRED_SOURCE) {
        // The line-level finding above carries the most actionable location.
        return;
    }

    report.push(
        Finding::error(
            "flags2env-submodule-noncanonical-source",
            "flags2env submodule must use the canonical flags-2-env/flags-2-env repository URL",
        )
        .with_target(GITMODULES)
        .with_detail("section", json!(section)),
    );
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::audit_flags2env_submodule_source_hygiene;
    use crate::model::CommandReport;

    fn run(gitmodules: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(".gitmodules"), gitmodules).expect("gitmodules");
        let mut report = CommandReport::new("flags2env submodule source hygiene test");
        audit_flags2env_submodule_source_hygiene(root.path(), &mut report);
        report.finalize()
    }

    fn has(report: &CommandReport, code: &str) -> bool {
        report.findings.iter().any(|finding| finding.code == code)
    }

    #[test]
    fn canonical_flags2env_submodule_is_admitted() {
        let report = run(
            "[submodule \"vendor/flags-2-env\"]\n\tpath = vendor/flags-2-env\n\turl = https://github.com/flags-2-env/flags-2-env.git\n",
        );
        assert!(!has(&report, "flags2env-submodule-retired-source"));
        assert!(!has(&report, "flags2env-submodule-noncanonical-source"));
    }

    #[test]
    fn retired_flags2env_submodule_source_is_rejected() {
        let report = run(
            "[submodule \"vendor/flags-2-env\"]\n\tpath = vendor/flags-2-env\n\turl = https://github.com/ORESoftware/flags-2-env.git\n",
        );
        assert!(has(&report, "flags2env-submodule-retired-source"));
    }

    #[test]
    fn alternate_flags2env_fork_is_not_silently_admitted() {
        let report = run(
            "[submodule \"vendor/flags2env\"]\n\tpath = vendor/flags2env\n\turl = https://github.com/example/flags2env.git\n",
        );
        assert!(has(&report, "flags2env-submodule-noncanonical-source"));
    }

    #[test]
    fn unrelated_submodules_are_ignored() {
        let report = run(
            "[submodule \"vendor/other\"]\n\tpath = vendor/other\n\turl = https://github.com/ORESoftware/k8s-libs-and-shared-defs.git\n",
        );
        assert!(!has(&report, "flags2env-submodule-retired-source"));
        assert!(!has(&report, "flags2env-submodule-noncanonical-source"));
    }
}
