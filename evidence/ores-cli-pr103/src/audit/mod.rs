mod workflow_permissions;

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

/// Run the exact workflow-permissions adapter.
#[must_use]
pub fn audit_repository(options: &RepositoryAuditOptions) -> CommandReport {
    workflow_permissions::augment_workflow_permissions_audit(
        options,
        CommandReport::new("audit repo"),
    )
}
