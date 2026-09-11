mod cargo_git_pins;
mod docker_base_pins;

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

/// Run the exact Docker then Cargo immutable-source gates.
#[must_use]
pub fn audit_repository(options: &RepositoryAuditOptions) -> CommandReport {
    let report = docker_base_pins::augment_docker_base_pin_audit(
        options,
        CommandReport::new("audit repo"),
    );
    cargo_git_pins::augment_cargo_git_pin_audit(options, report)
}
