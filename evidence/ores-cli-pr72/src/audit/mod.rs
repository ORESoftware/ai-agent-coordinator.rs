use std::path::PathBuf;

mod runtime_toml_rate_limit;

use crate::model::CommandReport;

/// Minimal options shape kept byte-compatible at the call boundary used by the
/// exact private rate-limit audit module.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepositoryAuditOptions {
    /// Local repository root.
    pub path: PathBuf,
    /// Structural profile.
    pub profile: String,
    /// Additional required paths.
    pub additional_required_paths: Vec<String>,
}

/// Run the exact private `.ores-rl.toml` module in this non-merge harness.
#[must_use]
pub fn audit_rate_limit(path: PathBuf) -> CommandReport {
    runtime_toml_rate_limit::augment_rate_limit_runtime_toml_audit(
        &RepositoryAuditOptions {
            path,
            profile: "baseline".to_owned(),
            additional_required_paths: Vec::new(),
        },
        CommandReport::new("audit repo"),
    )
}
