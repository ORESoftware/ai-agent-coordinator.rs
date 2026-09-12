mod runtime_toml_sidecar;

use std::path::PathBuf;

use crate::model::CommandReport;

/// Minimal options surface copied from `ores-cli` for exact module execution.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepositoryAuditOptions {
    /// Local repository root.
    pub path: PathBuf,
    /// Structural profile.
    pub profile: String,
    /// Additional required paths.
    pub additional_required_paths: Vec<String>,
}

/// Run the exact ORES Sidecar peer-authority adapter.
#[must_use]
pub fn audit_repository(options: &RepositoryAuditOptions) -> CommandReport {
    runtime_toml_sidecar::augment_sidecar_runtime_toml_audit(
        options,
        CommandReport::new("audit repo"),
    )
}
