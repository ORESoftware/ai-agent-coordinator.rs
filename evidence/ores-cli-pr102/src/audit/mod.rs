mod runtime_toml_registry;
#[cfg(test)]
mod runtime_toml_registry_tests;

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

/// Run the exact runtime-config namespace registry adapter.
#[must_use]
pub fn audit_repository(options: &RepositoryAuditOptions) -> CommandReport {
    runtime_toml_registry::augment_runtime_toml_registry_audit(
        options,
        CommandReport::new("audit repo"),
    )
}
