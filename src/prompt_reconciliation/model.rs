use serde::{Deserialize, Serialize};
use thiserror::Error;

pub(super) const PLAN_SCHEMA_VERSION: u32 = 1;
pub(super) const MAX_PROMPT_EVIDENCE: usize = 10_000;
pub(super) const MAX_REPOSITORIES_PER_PROMPT: usize = 64;
pub(super) const MAX_CANDIDATES_PER_PROMPT: usize = 100;
pub(super) const MAX_MUTATION_RECEIPTS: usize = 100_000;
pub(super) const MAX_IDENTIFIER_LEN: usize = 256;
pub(super) const MAX_LINK_LEN: usize = 2_048;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReconciliationEvidence {
    #[serde(default)]
    pub prompts: Vec<PromptEvidence>,
    #[serde(default)]
    pub receipts: Vec<MutationReceipt>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptEvidence {
    pub mutation_key: String,
    #[serde(default)]
    pub repositories: Vec<RepositoryLandingEvidence>,
    #[serde(default)]
    pub linear_candidates: Vec<LinearIssueCandidate>,
    #[serde(default)]
    pub residual_operational_work: bool,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepositoryLandingEvidence {
    pub repository: String,
    pub complete: bool,
    pub state: LandingState,
    #[serde(default)]
    pub links: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LandingState {
    NoMatch,
    NonDefaultOnly,
    DefaultBranch,
    Conflicting,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LinearIssueCandidate {
    pub issue_id: String,
    pub url: String,
    pub project: String,
    pub status: LinearIssueStatus,
    #[serde(default)]
    pub scope_signature: Option<String>,
    #[serde(default)]
    pub mutation_keys: Vec<String>,
    #[serde(default)]
    pub repositories: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LinearIssueStatus {
    Backlog,
    Todo,
    InProgress,
    Done,
    Canceled,
    Duplicate,
}

impl LinearIssueStatus {
    pub(super) fn can_be_canonical(self) -> bool {
        !matches!(self, Self::Canceled | Self::Duplicate)
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MutationReceipt {
    pub mutation_key: String,
    pub operation_id: String,
    pub outcome: ReceiptOutcome,
    #[serde(default)]
    pub canonical_issue_id: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReceiptOutcome {
    Applied,
    Failed,
    Superseded,
}

#[derive(Debug, Clone, Serialize)]
pub struct ReconciliationPlan {
    pub schema_version: u32,
    pub source_report_schema_version: u32,
    pub generated_at: chrono::DateTime<chrono::Utc>,
    pub account_fingerprint: String,
    pub counts: ReconciliationPlanCounts,
    pub prompts: Vec<PromptReconciliationPlan>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ReconciliationPlanCounts {
    pub input_decisions: usize,
    pub ignored: usize,
    pub review: usize,
    pub already_applied: usize,
    pub already_landed: usize,
    pub amend: usize,
    pub create: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct PromptReconciliationPlan {
    pub source_identity: String,
    pub mutation_key: String,
    pub scope_signature: String,
    pub action: PlanAction,
    pub reasons: Vec<PlanReason>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mutation: Option<LinearMutationPlan>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub evidence_links: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PlanAction {
    Ignore,
    Review,
    AlreadyApplied,
    AlreadyLanded,
    AmendIssue,
    CreateIssue,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PlanReason {
    NonActionable,
    PriorAppliedReceipt,
    OwnershipNeedsReview,
    MissingPromptEvidence,
    MissingRepositoryEvidence,
    IncompleteRepositoryEvidence,
    ConflictingRepositoryEvidence,
    DefaultBranchLanded,
    NonDefaultEvidenceOnly,
    NoGithubMatch,
    ResidualOperationalWork,
    ExactMutationCandidate,
    ExactScopeCandidate,
    RepositoryProjectCandidate,
    AmbiguousLinearCandidates,
    TerminalExactCandidate,
    NoLinearCandidate,
}

#[derive(Debug, Clone, Serialize)]
pub struct LinearMutationPlan {
    pub operation_id: String,
    pub idempotency_key: String,
    pub kind: LinearMutationKind,
    pub project: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub issue_id: Option<String>,
    pub title: String,
    pub body: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum LinearMutationKind {
    Amend,
    Create,
}

#[derive(Debug, Error)]
pub enum ReconciliationError {
    #[error("too many prompt evidence records: {0}")]
    TooManyPromptEvidence(usize),
    #[error("too many mutation receipts: {0}")]
    TooManyReceipts(usize),
    #[error("duplicate prompt evidence for mutation key: {0}")]
    DuplicatePromptEvidence(String),
    #[error("duplicate receipt for mutation key: {0}")]
    DuplicateReceipt(String),
    #[error("evidence references an unknown mutation key: {0}")]
    UnknownMutationKey(String),
    #[error("receipt references an unknown mutation key: {0}")]
    UnknownReceiptMutationKey(String),
    #[error("identifier is empty or exceeds {MAX_IDENTIFIER_LEN} characters: {field}")]
    InvalidIdentifier { field: &'static str },
    #[error("too many repositories for mutation key {mutation_key}: {count}")]
    TooManyRepositories { mutation_key: String, count: usize },
    #[error("too many Linear candidates for mutation key {mutation_key}: {count}")]
    TooManyCandidates { mutation_key: String, count: usize },
    #[error("duplicate repository evidence for {repository} under mutation key {mutation_key}")]
    DuplicateRepositoryEvidence {
        mutation_key: String,
        repository: String,
    },
    #[error("duplicate Linear candidate {issue_id} under mutation key {mutation_key}")]
    DuplicateLinearCandidate {
        mutation_key: String,
        issue_id: String,
    },
    #[error("repository evidence contains an unexpected repository {repository} under mutation key {mutation_key}")]
    UnexpectedRepositoryEvidence {
        mutation_key: String,
        repository: String,
    },
    #[error("unsafe or invalid evidence URL: {0}")]
    UnsafeEvidenceUrl(String),
    #[error("default-branch or non-default evidence requires at least one resolvable link for repository {0}")]
    MissingLandingLink(String),
    #[error("applied receipt must include a canonical issue ID when the operation amended or created an issue: {0}")]
    InvalidAppliedReceipt(String),
    #[error("actionable prompt has no single resolved Linear project: {0}")]
    InvalidResolvedProject(String),
}
