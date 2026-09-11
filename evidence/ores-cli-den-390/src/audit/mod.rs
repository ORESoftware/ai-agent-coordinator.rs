use std::path::PathBuf;

use crate::model::CommandReport;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepositoryAuditOptions {
    pub path: PathBuf,
    pub profile: String,
    pub additional_required_paths: Vec<String>,
}

mod runtime_config_authority;

/// Compile-time harness entrypoint that keeps the exact production audit module
/// reachable in non-test targets so Clippy exercises the same call boundary as
/// the real `ores-cli` repository.
pub fn certify_runtime_config_authority(options: &RepositoryAuditOptions) -> CommandReport {
    runtime_config_authority::augment_runtime_config_authority_audit(
        options,
        CommandReport::new("runtime-config-authority-probe"),
    )
}
