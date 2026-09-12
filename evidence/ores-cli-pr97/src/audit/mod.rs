mod nested_peer_contracts;
mod workflow_action_pins;

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

/// Run the exact recursive peer-contract and immutable-action adapters.
#[must_use]
pub fn audit_repository(options: &RepositoryAuditOptions) -> CommandReport {
    let report = nested_peer_contracts::augment_nested_peer_contract_audit(
        options,
        CommandReport::new("audit repo"),
    );
    workflow_action_pins::augment_workflow_action_pin_audit(options, report)
}
