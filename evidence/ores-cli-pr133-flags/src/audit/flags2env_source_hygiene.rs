use std::collections::BTreeSet;
use std::fs;
use std::path::Path;

use serde_json::json;
use walkdir::{DirEntry, WalkDir};

use crate::model::{CommandReport, Finding};

const RETIRED_SOURCE: &str = "github.com/ORESoftware/flags-2-env";
const CANONICAL_SOURCE: &str = "github.com/flags-2-env/flags-2-env";
const MAX_MANIFEST_BYTES: u64 = 1024 * 1024;

/// Audit manifests for the canonical flags2env source transition and immutable
/// Git dependency pins.
///
/// Registry dependencies are outside this source-URL rule. When a manifest
/// chooses a Git source, however, the historical `ORESoftware/flags-2-env`
/// repository is no longer an admitted source and the canonical repository
/// must be pinned to an exact 40-hex commit rather than a branch/tag/floating
/// HEAD.
pub(crate) fn audit_flags2env_source_hygiene(root: &Path, report: &mut CommandReport) {
    let mut scanned = 0_u64;
    let mut canonical_git_refs = 0_u64;
    let mut immutable_git_refs = 0_u64;
    let mut retired_hits = 0_u64;

    let walker = WalkDir::new(root)
        .follow_links(false)
        .max_depth(10)
        .into_iter()
        .filter_entry(|entry| !ignored_entry(entry));

    for item in walker {
        let entry = match item {
            Ok(entry) => entry,
            Err(error) => {
                report.push(Finding::warning(
                    "flags2env-source-inventory-failed",
                    format!("manifest inventory entry could not be inspected: {error}"),
                ));
                continue;
            }
        };
        if !entry.file_type().is_file()
            || entry.file_type().is_symlink()
            || !is_manifest(entry.path())
        {
            continue;
        }
        scanned += 1;

        let target = relative_display(root, entry.path());
        let metadata = match entry.metadata() {
            Ok(metadata) => metadata,
            Err(error) => {
                report.push(
                    Finding::error(
                        "flags2env-source-manifest-unreadable",
                        format!("dependency manifest metadata could not be inspected: {error}"),
                    )
                    .with_target(target),
                );
                continue;
            }
        };
        if metadata.len() > MAX_MANIFEST_BYTES {
            report.push(
                Finding::error(
                    "flags2env-source-manifest-oversized",
                    "dependency manifest exceeds the bounded source-hygiene audit size",
                )
                .with_target(target)
                .with_detail("bytes", json!(metadata.len())),
            );
            continue;
        }
        let text = match fs::read_to_string(entry.path()) {
            Ok(text) => text,
            Err(error) => {
                report.push(
                    Finding::error(
                        "flags2env-source-manifest-unreadable",
                        format!("dependency manifest could not be read as UTF-8: {error}"),
                    )
                    .with_target(target),
                );
                continue;
            }
        };

        let lines = text.lines().collect::<Vec<_>>();
        let mut reported_retired = BTreeSet::new();
        let mut reported_floating = BTreeSet::new();
        for (line_index, line) in lines.iter().enumerate() {
            if line.contains(RETIRED_SOURCE) && reported_retired.insert(line_index + 1) {
                retired_hits += 1;
                report.push(
                    Finding::error(
                        "flags2env-source-retired-owner",
                        "dependency manifest still references the retired ORESoftware/flags-2-env source; use the canonical flags-2-env/flags-2-env repository",
                    )
                    .with_target(target.clone())
                    .with_detail("line", json!(line_index + 1)),
                );
            }

            if !line.contains(CANONICAL_SOURCE) {
                continue;
            }
            canonical_git_refs += 1;
            if has_immutable_pin_near(&lines, line_index) {
                immutable_git_refs += 1;
                continue;
            }
            if reported_floating.insert(line_index + 1) {
                report.push(
                    Finding::error(
                        "flags2env-source-git-unpinned",
                        "canonical flags2env Git dependency must be pinned to an exact 40-hex commit instead of a branch, tag, or floating HEAD",
                    )
                    .with_target(target.clone())
                    .with_detail("line", json!(line_index + 1)),
                );
            }
        }
    }

    report.insert_metadata("flags2envSourceManifestCount", json!(scanned));
    report.insert_metadata(
        "flags2envCanonicalGitReferenceCount",
        json!(canonical_git_refs),
    );
    report.insert_metadata(
        "flags2envImmutableGitReferenceCount",
        json!(immutable_git_refs),
    );
    report.insert_metadata("flags2envRetiredSourceReferenceCount", json!(retired_hits));
}

fn has_immutable_pin_near(lines: &[&str], source_line: usize) -> bool {
    let start = source_line.saturating_sub(2);
    let end = usize::min(lines.len(), source_line + 5);
    lines[start..end].iter().any(|line| line_has_pin(line))
}

fn line_has_pin(line: &str) -> bool {
    // URL fragment forms used by npm/Bun/Deno/git specs.
    if let Some((_, fragment)) = line.rsplit_once('#') {
        let candidate = fragment
            .trim()
            .trim_matches(|ch: char| matches!(ch, '"' | '\'' | ',' | '}' | ']' | ' '));
        if is_sha40(candidate) {
            return true;
        }
    }

    // Structured manifest fields: Cargo `rev =`, pubspec `ref:`, or analogous
    // JSON/TOML keys. We deliberately do not treat `branch` or `tag` as pins.
    for marker in ["rev", "ref", "commit"] {
        let Some(position) = line.find(marker) else {
            continue;
        };
        let tail = &line[position + marker.len()..];
        for token in tail.split(|ch: char| {
            ch.is_ascii_whitespace()
                || matches!(ch, '=' | ':' | '"' | '\'' | ',' | '{' | '}' | '[' | ']')
        }) {
            if is_sha40(token) {
                return true;
            }
        }
    }
    false
}

fn is_sha40(value: &str) -> bool {
    value.len() == 40 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn is_manifest(path: &Path) -> bool {
    matches!(
        path.file_name().and_then(|name| name.to_str()),
        Some(
            "Cargo.toml"
                | "package.json"
                | "deno.json"
                | "deno.jsonc"
                | "pubspec.yaml"
                | "pubspec.yml"
                | "gleam.toml"
                | "go.mod"
        )
    )
}

fn ignored_entry(entry: &DirEntry) -> bool {
    if entry.depth() == 0 {
        return false;
    }
    matches!(
        entry.file_name().to_str(),
        Some(
            ".git"
                | "target"
                | "node_modules"
                | "vendor"
                | "build"
                | "dist"
                | ".dart_tool"
                | ".pub-cache"
                | "generated"
                | "tmp"
                | "temp"
        )
    )
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

    use tempfile::tempdir;

    use super::audit_flags2env_source_hygiene;
    use crate::model::CommandReport;

    fn run(name: &str, manifest: &str) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        fs::write(root.path().join(name), manifest).expect("manifest");
        let mut report = CommandReport::new("flags2env source hygiene test");
        audit_flags2env_source_hygiene(root.path(), &mut report);
        report.finalize()
    }

    fn has(report: &CommandReport, code: &str) -> bool {
        report.findings.iter().any(|finding| finding.code == code)
    }

    #[test]
    fn retired_owner_is_rejected() {
        let report = run(
            "Cargo.toml",
            "[dependencies]\nflags2env = { git = \"https://github.com/ORESoftware/flags-2-env.git\", rev = \"0123456789abcdef0123456789abcdef01234567\" }\n",
        );
        assert!(has(&report, "flags2env-source-retired-owner"));
    }

    #[test]
    fn cargo_canonical_exact_rev_is_admitted() {
        let report = run(
            "Cargo.toml",
            "[dependencies]\nflags2env = { git = \"https://github.com/flags-2-env/flags-2-env.git\", rev = \"0123456789abcdef0123456789abcdef01234567\" }\n",
        );
        assert!(!has(&report, "flags2env-source-git-unpinned"));
    }

    #[test]
    fn cargo_branch_is_not_an_immutable_pin() {
        let report = run(
            "Cargo.toml",
            "[dependencies]\nflags2env = { git = \"https://github.com/flags-2-env/flags-2-env.git\", branch = \"main\" }\n",
        );
        assert!(has(&report, "flags2env-source-git-unpinned"));
    }

    #[test]
    fn npm_fragment_sha_is_admitted() {
        let report = run(
            "package.json",
            "{\"dependencies\":{\"flags2env\":\"git+https://github.com/flags-2-env/flags-2-env.git#0123456789abcdef0123456789abcdef01234567\"}}\n",
        );
        assert!(!has(&report, "flags2env-source-git-unpinned"));
    }

    #[test]
    fn flutter_pubspec_ref_near_url_is_admitted() {
        let report = run(
            "pubspec.yaml",
            "dependencies:\n  flags2env:\n    git:\n      url: https://github.com/flags-2-env/flags-2-env.git\n      ref: 0123456789abcdef0123456789abcdef01234567\n",
        );
        assert!(!has(&report, "flags2env-source-git-unpinned"));
    }

    #[test]
    fn ordinary_registry_dependency_is_outside_git_source_policy() {
        let report = run("Cargo.toml", "[dependencies]\nflags2env = \"0.1\"\n");
        assert!(!has(&report, "flags2env-source-git-unpinned"));
        assert!(!has(&report, "flags2env-source-retired-owner"));
    }
}
