mod runtime_toml_sidecar_policy;

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

/// Run the exact Sidecar runtime-policy audit.
#[must_use]
pub fn audit_repository(options: &RepositoryAuditOptions) -> CommandReport {
    runtime_toml_sidecar_policy::augment_sidecar_runtime_policy_audit(
        options,
        CommandReport::new("audit repo"),
    )
}
