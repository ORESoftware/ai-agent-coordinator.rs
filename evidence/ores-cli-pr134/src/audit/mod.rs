use std::path::PathBuf;

mod runtime_toml_lru;
mod runtime_toml_middleware;

/// Minimal local-repository options required by the exact product adapters.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepositoryAuditOptions {
    /// Local repository root.
    pub path: PathBuf,
    /// Structural profile.
    pub profile: String,
    /// Additional required paths.
    pub additional_required_paths: Vec<String>,
}
