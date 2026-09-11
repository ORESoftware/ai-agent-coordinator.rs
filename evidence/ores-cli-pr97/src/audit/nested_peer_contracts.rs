use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use serde_json::{Value as JsonValue, json};
use walkdir::{DirEntry, WalkDir};

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CONTRACTS_DIRECTORY: &str = "contracts";
const TYPESPEC_FILE: &str = "main.tsp";
const JSON_SCHEMA_FILE: &str = "authored.schema.json";
const JSON_SCHEMA_DRAFT: &str = "https://json-schema.org/draft/2020-12/schema";
const MAX_CONTRACT_DIRECTORIES: usize = 256;
const MAX_CONTRACT_FILE_BYTES: u64 = 2 * 1024 * 1024;
const MAX_WALK_DEPTH: usize = 16;
const CONFLICT_MARKERS: [&str; 3] = ["<<<<<<<", "=======", ">>>>>>>"];

#[derive(Debug, Default)]
struct CandidateDirectory {
    typespec: Option<PathBuf>,
    schema: Option<PathBuf>,
}

/// Extend the repository audit with bounded recursive peer-authority checks.
///
/// The linter does not compare semantic parity or generate either authority.
/// It only proves that nested contract homes expose regular, bounded,
/// independently editable TypeSpec and Draft 2020-12 JSON Schema source files
/// before compiler-backed TJSV admission is attempted.
pub(super) fn augment_nested_peer_contract_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    let root = options.path.join(CONTRACTS_DIRECTORY);
    let metadata = match fs::symlink_metadata(&root) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return report.finalize(),
        Err(error) => {
            report.push(
                Finding::error(
                    "nested-contract-root-unreadable",
                    format!("contracts root metadata could not be read: {error}"),
                )
                .with_target(CONTRACTS_DIRECTORY),
            );
            return report.finalize();
        }
    };

    if metadata.file_type().is_symlink() {
        report.push(
            Finding::error(
                "nested-contract-root-symlink",
                "contracts root must not be a symbolic link",
            )
            .with_target(CONTRACTS_DIRECTORY),
        );
        return report.finalize();
    }
    if !metadata.is_dir() {
        report.push(
            Finding::error(
                "nested-contract-root-not-directory",
                "contracts root must be a directory when present",
            )
            .with_target(CONTRACTS_DIRECTORY),
        );
        return report.finalize();
    }

    let mut candidates = BTreeMap::<PathBuf, CandidateDirectory>::new();
    let mut overflow_reported = false;
    let walker = WalkDir::new(&root)
        .follow_links(false)
        .max_depth(MAX_WALK_DEPTH)
        .into_iter()
        .filter_entry(should_descend);

    for result in walker {
        let entry = match result {
            Ok(entry) => entry,
            Err(error) => {
                let target = error.path().map_or_else(
                    || CONTRACTS_DIRECTORY.to_owned(),
                    |path| relative_display(&options.path, path),
                );
                report.push(
                    Finding::warning(
                        "nested-contract-walk-failed",
                        format!("could not inspect part of the contracts tree: {error}"),
                    )
                    .with_target(target),
                );
                continue;
            }
        };

        let name = entry.file_name().to_string_lossy();
        if name != TYPESPEC_FILE && name != JSON_SCHEMA_FILE {
            continue;
        }

        let Some(parent) = entry.path().parent() else {
            continue;
        };
        let relative_parent = parent
            .strip_prefix(&options.path)
            .unwrap_or(parent)
            .to_path_buf();
        if !candidates.contains_key(&relative_parent)
            && candidates.len() >= MAX_CONTRACT_DIRECTORIES
        {
            if !overflow_reported {
                report.push(
                    Finding::error(
                        "nested-contract-pair-limit",
                        format!(
                            "more than {MAX_CONTRACT_DIRECTORIES} contract directories were discovered; narrow or split the contract tree"
                        ),
                    )
                    .with_target(CONTRACTS_DIRECTORY),
                );
                overflow_reported = true;
            }
            continue;
        }

        let candidate = candidates.entry(relative_parent).or_default();
        let destination = if name == TYPESPEC_FILE {
            &mut candidate.typespec
        } else {
            &mut candidate.schema
        };
        if destination.replace(entry.path().to_path_buf()).is_some() {
            report.push(
                Finding::error(
                    "nested-contract-duplicate-authority-path",
                    "a contract directory resolved the same authority filename more than once",
                )
                .with_target(relative_display(&options.path, entry.path())),
            );
        }
    }

    report.insert_metadata("nestedContractCandidateCount", json!(candidates.len()));

    let mut valid_pairs = 0usize;
    for (directory, candidate) in candidates {
        let target = relative_display(&options.path, &options.path.join(&directory));
        match (candidate.typespec, candidate.schema) {
            (Some(typespec), Some(schema)) => {
                let before = report.issue_count();
                audit_typespec(&options.path, &typespec, &mut report);
                audit_json_schema(&options.path, &schema, &mut report);
                if report.issue_count() == before {
                    valid_pairs += 1;
                }
            }
            (Some(_), None) => report.push(
                Finding::error(
                    "nested-authored-json-schema-missing",
                    format!(
                        "{TYPESPEC_FILE} exists without the independently authored {JSON_SCHEMA_FILE} peer"
                    ),
                )
                .with_target(target),
            ),
            (None, Some(_)) => report.push(
                Finding::error(
                    "nested-typespec-source-missing",
                    format!(
                        "{JSON_SCHEMA_FILE} exists without the independently authored {TYPESPEC_FILE} peer"
                    ),
                )
                .with_target(target),
            ),
            (None, None) => {}
        }
    }

    report.insert_metadata("nestedContractValidPairCount", json!(valid_pairs));
    if valid_pairs > 0 {
        report.push(
            Finding::info(
                "nested-peer-contracts-inspected",
                format!(
                    "inspected {valid_pairs} nested TypeSpec/JSON Schema peer-authority pair{}",
                    if valid_pairs == 1 { "" } else { "s" }
                ),
            )
            .with_target(CONTRACTS_DIRECTORY),
        );
    }

    report.finalize()
}

fn should_descend(entry: &DirEntry) -> bool {
    if entry.depth() == 0 {
        return true;
    }
    let name = entry.file_name().to_string_lossy();
    !matches!(
        name.as_ref(),
        ".git" | "target" | "node_modules" | ".dart_tool" | ".typespec-json-schema-validator"
    )
}

fn audit_typespec(root: &Path, path: &Path, report: &mut CommandReport) {
    let target = relative_display(root, path);
    let Some(text) = read_regular_bounded_file(root, path, "typespec", report) else {
        return;
    };
    if text.trim().is_empty() {
        report.push(
            Finding::error(
                "nested-typespec-empty",
                "TypeSpec peer authority must not be empty",
            )
            .with_target(target),
        );
        return;
    }
    audit_conflict_markers(&text, "nested-typespec-conflict-marker", &target, report);
}

fn audit_json_schema(root: &Path, path: &Path, report: &mut CommandReport) {
    let target = relative_display(root, path);
    let Some(text) = read_regular_bounded_file(root, path, "json-schema", report) else {
        return;
    };
    audit_conflict_markers(&text, "nested-json-schema-conflict-marker", &target, report);

    let document = match serde_json::from_str::<JsonValue>(&text) {
        Ok(document) => document,
        Err(error) => {
            report.push(
                Finding::error(
                    "nested-json-schema-invalid",
                    format!("authored JSON Schema is not valid JSON: {error}"),
                )
                .with_target(target),
            );
            return;
        }
    };
    let Some(object) = document.as_object() else {
        report.push(
            Finding::error(
                "nested-json-schema-root-shape",
                "authored JSON Schema root must be an object",
            )
            .with_target(target),
        );
        return;
    };

    if object.get("$schema").and_then(JsonValue::as_str) != Some(JSON_SCHEMA_DRAFT) {
        report.push(
            Finding::error(
                "nested-json-schema-draft",
                format!("authored JSON Schema must declare {JSON_SCHEMA_DRAFT}"),
            )
            .with_target(target.clone()),
        );
    }
    if object
        .get("$id")
        .is_some_and(|value| value.as_str().is_none_or(str::is_empty))
    {
        report.push(
            Finding::error(
                "nested-json-schema-id-shape",
                "$id must be a non-empty string when present",
            )
            .with_target(target.clone()),
        );
    }
    if object.get("$defs").is_some_and(|value| !value.is_object()) {
        report.push(
            Finding::error(
                "nested-json-schema-defs-shape",
                "$defs must be an object when present",
            )
            .with_target(target),
        );
    }
}

fn read_regular_bounded_file(
    root: &Path,
    path: &Path,
    authority: &str,
    report: &mut CommandReport,
) -> Option<String> {
    let target = relative_display(root, path);
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) => {
            report.push(
                Finding::error(
                    "nested-contract-file-unreadable",
                    format!("{authority} authority metadata could not be read: {error}"),
                )
                .with_target(target),
            );
            return None;
        }
    };
    if metadata.file_type().is_symlink() {
        report.push(
            Finding::error(
                "nested-contract-file-symlink",
                format!("{authority} authority must not be a symbolic link"),
            )
            .with_target(target),
        );
        return None;
    }
    if !metadata.is_file() {
        report.push(
            Finding::error(
                "nested-contract-file-not-regular",
                format!("{authority} authority must be a regular file"),
            )
            .with_target(target),
        );
        return None;
    }
    if metadata.len() > MAX_CONTRACT_FILE_BYTES {
        report.push(
            Finding::error(
                "nested-contract-file-too-large",
                format!(
                    "{authority} authority exceeds the {MAX_CONTRACT_FILE_BYTES}-byte audit bound"
                ),
            )
            .with_target(target),
        );
        return None;
    }
    match fs::read_to_string(path) {
        Ok(text) => Some(text),
        Err(error) => {
            report.push(
                Finding::error(
                    "nested-contract-file-not-utf8",
                    format!("{authority} authority could not be read as UTF-8: {error}"),
                )
                .with_target(target),
            );
            None
        }
    }
}

fn audit_conflict_markers(text: &str, code: &str, target: &str, report: &mut CommandReport) {
    let markers = text
        .lines()
        .filter_map(|line| {
            let line = line.trim_start();
            CONFLICT_MARKERS
                .iter()
                .find(|marker| line.starts_with(*marker))
                .copied()
        })
        .collect::<BTreeSet<_>>();
    if !markers.is_empty() {
        report.push(
            Finding::error(
                code,
                format!(
                    "authored authority contains unresolved conflict marker{}: {}",
                    if markers.len() == 1 { "" } else { "s" },
                    markers.into_iter().collect::<Vec<_>>().join(", ")
                ),
            )
            .with_target(target.to_owned()),
        );
    }
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

    use super::{MAX_CONTRACT_FILE_BYTES, augment_nested_peer_contract_audit};
    use crate::audit::RepositoryAuditOptions;
    use crate::model::CommandReport;

    fn audit(setup: impl FnOnce(&std::path::Path)) -> CommandReport {
        let root = tempdir().expect("temporary repository");
        setup(root.path());
        augment_nested_peer_contract_audit(
            &RepositoryAuditOptions {
                path: root.path().to_path_buf(),
                profile: "baseline".to_owned(),
                additional_required_paths: Vec::new(),
            },
            CommandReport::new("audit repo"),
        )
    }

    fn write_pair(root: &std::path::Path, relative: &str) {
        let contract = root.join(relative);
        fs::create_dir_all(&contract).expect("contract directory");
        fs::write(
            contract.join("main.tsp"),
            "namespace Example;\nmodel Packet { value: string; }\n",
        )
        .expect("TypeSpec authority");
        fs::write(
            contract.join("authored.schema.json"),
            r#"{"$schema":"https://json-schema.org/draft/2020-12/schema","$id":"https://example.test/packet.schema.json","type":"object","properties":{"value":{"type":"string"}},"required":["value"],"unevaluatedProperties":false}"#,
        )
        .expect("JSON Schema authority");
    }

    #[test]
    fn accepts_multiple_nested_peer_authority_pairs() {
        let report = audit(|root| {
            write_pair(root, "contracts/customer");
            write_pair(root, "contracts/coordination/v1");
        });
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "nested-peer-contracts-inspected")
        );
        assert_eq!(
            report.metadata.get("nestedContractValidPairCount"),
            Some(&JsonValue::from(2))
        );
    }

    #[test]
    fn rejects_one_sided_nested_contract_homes() {
        let report = audit(|root| {
            let typespec = root.join("contracts/only-typespec");
            fs::create_dir_all(&typespec).expect("directory");
            fs::write(typespec.join("main.tsp"), "model Packet {}\n").expect("TypeSpec");

            let schema = root.join("contracts/only-schema");
            fs::create_dir_all(&schema).expect("directory");
            fs::write(
                schema.join("authored.schema.json"),
                r#"{"$schema":"https://json-schema.org/draft/2020-12/schema"}"#,
            )
            .expect("schema");
        });
        for code in [
            "nested-authored-json-schema-missing",
            "nested-typespec-source-missing",
        ] {
            assert!(report.findings.iter().any(|finding| finding.code == code));
        }
    }

    #[test]
    fn rejects_invalid_json_wrong_draft_and_malformed_metadata_keywords() {
        let report = audit(|root| {
            let invalid = root.join("contracts/invalid-json");
            fs::create_dir_all(&invalid).expect("directory");
            fs::write(invalid.join("main.tsp"), "model Packet {}\n").expect("TypeSpec");
            fs::write(invalid.join("authored.schema.json"), "{not-json").expect("schema");

            let wrong = root.join("contracts/wrong-draft");
            fs::create_dir_all(&wrong).expect("directory");
            fs::write(wrong.join("main.tsp"), "model Packet {}\n").expect("TypeSpec");
            fs::write(
                wrong.join("authored.schema.json"),
                r#"{"$schema":"http://json-schema.org/draft-07/schema#","$id":42,"$defs":[]}"#,
            )
            .expect("schema");
        });
        for code in [
            "nested-json-schema-invalid",
            "nested-json-schema-draft",
            "nested-json-schema-id-shape",
            "nested-json-schema-defs-shape",
        ] {
            assert!(report.findings.iter().any(|finding| finding.code == code));
        }
    }

    #[test]
    fn rejects_empty_typespec_and_unresolved_conflict_markers() {
        let report = audit(|root| {
            let empty = root.join("contracts/empty");
            fs::create_dir_all(&empty).expect("directory");
            fs::write(empty.join("main.tsp"), " \n").expect("TypeSpec");
            fs::write(
                empty.join("authored.schema.json"),
                r#"{"$schema":"https://json-schema.org/draft/2020-12/schema"}"#,
            )
            .expect("schema");

            let conflicted = root.join("contracts/conflicted");
            fs::create_dir_all(&conflicted).expect("directory");
            fs::write(
                conflicted.join("main.tsp"),
                "<<<<<<< ours\nmodel Packet {}\n=======\nmodel PacketV2 {}\n>>>>>>> theirs\n",
            )
            .expect("TypeSpec");
            fs::write(
                conflicted.join("authored.schema.json"),
                "<<<<<<< ours\n{}\n=======\n{}\n>>>>>>> theirs\n",
            )
            .expect("schema");
        });
        for code in [
            "nested-typespec-empty",
            "nested-typespec-conflict-marker",
            "nested-json-schema-conflict-marker",
        ] {
            assert!(report.findings.iter().any(|finding| finding.code == code));
        }
    }

    #[test]
    fn rejects_contract_files_over_the_audit_bound() {
        let report = audit(|root| {
            let contract = root.join("contracts/oversized");
            fs::create_dir_all(&contract).expect("directory");
            fs::write(
                contract.join("main.tsp"),
                vec![b'a'; MAX_CONTRACT_FILE_BYTES as usize + 1],
            )
            .expect("TypeSpec");
            fs::write(
                contract.join("authored.schema.json"),
                r#"{"$schema":"https://json-schema.org/draft/2020-12/schema"}"#,
            )
            .expect("schema");
        });
        assert!(
            report
                .findings
                .iter()
                .any(|finding| finding.code == "nested-contract-file-too-large")
        );
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlinked_contract_roots_and_authority_files() {
        use std::os::unix::fs::symlink;

        let root_symlink = audit(|root| {
            let outside = root.join("outside");
            fs::create_dir_all(&outside).expect("outside");
            symlink(&outside, root.join("contracts")).expect("contracts symlink");
        });
        assert!(
            root_symlink
                .findings
                .iter()
                .any(|finding| finding.code == "nested-contract-root-symlink")
        );

        let file_symlink = audit(|root| {
            let contract = root.join("contracts/symlinked");
            fs::create_dir_all(&contract).expect("directory");
            let outside = root.join("outside.tsp");
            fs::write(&outside, "model Packet {}\n").expect("outside");
            symlink(&outside, contract.join("main.tsp")).expect("TypeSpec symlink");
            fs::write(
                contract.join("authored.schema.json"),
                r#"{"$schema":"https://json-schema.org/draft/2020-12/schema"}"#,
            )
            .expect("schema");
        });
        assert!(
            file_symlink
                .findings
                .iter()
                .any(|finding| finding.code == "nested-contract-file-symlink")
        );
    }

    #[test]
    fn absent_contracts_directory_is_not_an_error() {
        let report = audit(|_| {});
        assert_eq!(report.issue_count(), 0);
        assert!(!report.metadata.contains_key("nestedContractCandidateCount"));
    }

    #[test]
    fn ignores_generated_and_tool_output_contract_witnesses() {
        let report = audit(|root| {
            write_pair(root, "contracts/source");
            let generated = root.join("contracts/source/.typespec-json-schema-validator/generated");
            fs::create_dir_all(&generated).expect("generated");
            fs::write(generated.join("main.tsp"), "model Generated {}\n").expect("generated");
        });
        assert_eq!(report.issue_count(), 0, "{:#?}", report.findings);
        assert_eq!(
            report.metadata.get("nestedContractCandidateCount"),
            Some(&JsonValue::from(1))
        );
    }
}
