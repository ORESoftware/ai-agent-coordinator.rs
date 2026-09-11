use std::collections::BTreeMap;

use serde_json::Value;

/// Finding severity used by the exact infra-policy audit.
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum Severity {
    /// Informational evidence.
    Info,
    /// Policy/completeness concern.
    Warning,
    /// Required invariant violation.
    Error,
}

impl Severity {
    const fn is_issue(self) -> bool {
        !matches!(self, Self::Info)
    }
}

/// One actionable audit finding.
#[derive(Debug, Clone, PartialEq)]
pub struct Finding {
    /// Stable machine-readable code.
    pub code: String,
    /// Severity used for exit status.
    pub severity: Severity,
    /// Human-readable message.
    pub message: String,
    /// Optional target path.
    pub target: Option<String>,
}

impl Finding {
    /// Create informational evidence.
    #[must_use]
    pub fn info(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::new(Severity::Info, code, message)
    }

    /// Create a warning.
    #[must_use]
    pub fn warning(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::new(Severity::Warning, code, message)
    }

    /// Create an error.
    #[must_use]
    pub fn error(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::new(Severity::Error, code, message)
    }

    fn new(severity: Severity, code: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            severity,
            message: message.into(),
            target: None,
        }
    }

    /// Attach a target path.
    #[must_use]
    pub fn with_target(mut self, target: impl Into<String>) -> Self {
        self.target = Some(target.into());
        self
    }
}

/// Minimal command report required by the production policy module.
#[derive(Debug, Clone, PartialEq)]
pub struct CommandReport {
    /// Command identifier.
    pub command: String,
    /// Findings emitted by the audit.
    pub findings: Vec<Finding>,
    /// Structured metadata emitted by the audit.
    pub metadata: BTreeMap<String, Value>,
}

impl CommandReport {
    /// Create an empty report.
    #[must_use]
    pub fn new(command: impl Into<String>) -> Self {
        Self {
            command: command.into(),
            findings: Vec::new(),
            metadata: BTreeMap::new(),
        }
    }

    /// Add a finding.
    pub fn push(&mut self, finding: Finding) {
        self.findings.push(finding);
    }

    /// Add structured metadata.
    pub fn insert_metadata(&mut self, key: impl Into<String>, value: Value) {
        self.metadata.insert(key.into(), value);
    }

    /// Stable process exit code: 0 for pass, 2 for policy findings.
    #[must_use]
    pub fn exit_code(&self) -> u8 {
        if self
            .findings
            .iter()
            .any(|finding| finding.severity.is_issue())
        {
            2
        } else {
            0
        }
    }

    /// Complete report construction.
    #[must_use]
    pub fn finalize(self) -> Self {
        self
    }
}
