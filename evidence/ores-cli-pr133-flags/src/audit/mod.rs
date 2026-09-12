mod cli_secret_flag_hygiene;
mod flags2env_source_hygiene;
mod flags2env_submodule_source_hygiene;

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

/// Run the exact public-argv and flags2env source-hygiene gates.
#[must_use]
pub fn audit_repository(options: &RepositoryAuditOptions) -> CommandReport {
    let mut report = CommandReport::new("audit repo");
    cli_secret_flag_hygiene::audit_cli_secret_flag_hygiene(&options.path, &mut report);
    flags2env_source_hygiene::audit_flags2env_source_hygiene(&options.path, &mut report);
    flags2env_submodule_source_hygiene::audit_flags2env_submodule_source_hygiene(
        &options.path,
        &mut report,
    );
    report.finalize()
}
