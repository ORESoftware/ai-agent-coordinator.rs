use std::collections::BTreeSet;
use std::fs;

use serde_json::json;

use super::RepositoryAuditOptions;
use crate::model::{CommandReport, Finding};

const CANONICAL_SHARED_AUTH: &str = ".shared-auth.toml";
const COMPAT_SHARED_AUTH: &str = ".auth-shared.toml";

const REGISTERED_RUNTIME_CONFIGS: &[&str] = &[
    ".ores-otel.toml",
    ".ores-chat.toml",
    ".ores-forms.toml",
    ".opto-sync.toml",
    ".fanwaave-cfg.toml",
    ".ores-mw.toml",
    ".ores-rl.toml",
    ".ores-lru.toml",
    CANONICAL_SHARED_AUTH,
    COMPAT_SHARED_AUTH,
    ".ores-rpc.toml",
    ".ores-legal.toml",
    ".ores-wasm.toml",
    ".ores-sidecar.toml",
    ".indiebuild.toml",
];

/// Reject repository-root `.ores-*.toml` spellings that are not in the
/// reviewed concern registry. A syntactically valid typo must not silently
/// bypass concern-specific policy checks.
pub(super) fn augment_runtime_toml_registry_audit(
    options: &RepositoryAuditOptions,
    mut report: CommandReport,
) -> CommandReport {
    let Ok(entries) = fs::read_dir(&options.path) else {
        return report.finalize();
    };

    let mut recognized = BTreeSet::new();
    let mut unregistered = 0_u64;

    for entry in entries.flatten() {
        let path = entry.path();
        let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        if !regular_file(&path) {
            continue;
        }
        if REGISTERED_RUNTIME_CONFIGS.contains(&name) {
            recognized.insert(name.to_owned());
            continue;
        }
        if name.starts_with(".ores-") && name.ends_with(".toml") {
            unregistered += 1;
            report.push(
                Finding::error(
                    "runtime-toml-unregistered-name",
                    "repository-root .ores-*.toml files must use a registered concern filename",
                )
                .with_target(name)
                .with_detail("registered", json!(REGISTERED_RUNTIME_CONFIGS)),
            );
        }
    }

    if recognized.contains(CANONICAL_SHARED_AUTH) && recognized.contains(COMPAT_SHARED_AUTH) {
        report.push(
            Finding::error(
                "runtime-toml-shared-auth-ambiguous",
                "canonical .shared-auth.toml and compatibility .auth-shared.toml may not coexist",
            )
            .with_target(CANONICAL_SHARED_AUTH)
            .with_detail("compatibilityAlias", json!(COMPAT_SHARED_AUTH)),
        );
    }

    let names = recognized.into_iter().collect::<Vec<_>>();
    report.insert_metadata("registeredRuntimeTomlCount", json!(names.len()));
    report.insert_metadata("registeredRuntimeTomlNames", json!(names));
    report.insert_metadata("unregisteredOresTomlCount", json!(unregistered));

    if unregistered == 0 {
        report.push(Finding::info(
            "runtime-toml-registry-clean",
            "repository contains no unregistered .ores-*.toml root contracts",
        ));
    }

    report.finalize()
}

fn regular_file(path: &std::path::Path) -> bool {
    fs::symlink_metadata(path)
        .is_ok_and(|metadata| metadata.is_file() && !metadata.file_type().is_symlink())
}
