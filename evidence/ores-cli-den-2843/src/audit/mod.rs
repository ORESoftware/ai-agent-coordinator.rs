mod infra_policy;

use std::path::PathBuf;

use crate::model::CommandReport;

/// Minimal repository-audit options consumed by the exact infra-policy module.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepositoryAuditOptions {
    /// Repository root.
    pub path: PathBuf,
    /// Structural profile.
    pub profile: String,
    /// Additional required paths retained for source-shape parity.
    pub additional_required_paths: Vec<String>,
}

/// Execute only the exact DEN-2843 infra-policy augmentation.
#[must_use]
pub fn audit_infra_policy(options: &RepositoryAuditOptions) -> CommandReport {
    infra_policy::augment_infra_policy_audit(options, CommandReport::new("audit repo"))
}
