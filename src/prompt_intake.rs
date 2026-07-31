use std::{
    collections::{BTreeMap, BTreeSet},
    sync::OnceLock,
};

use chrono::{DateTime, Duration, Utc};
use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

const SCHEMA_VERSION: u32 = 1;
const MAX_SUMMARY_CHARS: usize = 180;
const MIN_REFINEMENT_TOKENS: usize = 4;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptExport {
    pub account_id: String,
    pub prompts: Vec<PromptRecord>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PromptRecord {
    pub thread_id: String,
    pub message_id: String,
    pub created_at: DateTime<Utc>,
    #[serde(default)]
    pub thread_title: String,
    pub text: String,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectCatalog {
    #[serde(default)]
    pub repositories: Vec<RepositoryProject>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RepositoryProject {
    pub repository: String,
    pub linear_project: String,
    #[serde(default)]
    pub aliases: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PromptClassification {
    RepositoryWork,
    RecurringAutomation,
    OperationalProgram,
    ProductWork,
    Informational,
    Ambiguous,
}

impl PromptClassification {
    fn is_actionable(self) -> bool {
        matches!(
            self,
            Self::RepositoryWork
                | Self::RecurringAutomation
                | Self::OperationalProgram
                | Self::ProductWork
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ExclusionReason {
    EmptyPrompt,
    InformationalQuestion,
    TranslationOrRewrite,
    NoDurableDeliverable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ProjectResolutionState {
    Resolved,
    CrossProject,
    Unmapped,
    NotRequired,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProjectResolution {
    pub state: ProjectResolutionState,
    pub repositories: Vec<String>,
    pub linear_projects: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct GithubEvidenceQuery {
    pub repository: String,
    pub checks: Vec<&'static str>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PromptDecision {
    pub source_identity: String,
    pub content_fingerprint: String,
    pub mutation_key: String,
    pub created_at: DateTime<Utc>,
    pub title_summary: String,
    pub prompt_summary: String,
    pub classification: PromptClassification,
    pub actionable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub exclusion_reason: Option<ExclusionReason>,
    pub project_resolution: ProjectResolution,
    pub github_evidence_queries: Vec<GithubEvidenceQuery>,
    pub linear_search_terms: Vec<String>,
    pub needs_review: bool,
    pub scope_signature: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct DuplicateGroup {
    pub content_fingerprint: String,
    pub source_identities: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RefinementGroup {
    pub thread_fingerprint: String,
    pub scope_signature: String,
    pub source_identities: Vec<String>,
    pub content_fingerprints: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PromptIntakeCounts {
    pub input_records: usize,
    pub within_window: usize,
    pub outside_window: usize,
    pub actionable: usize,
    pub excluded: usize,
    pub needs_review: usize,
    pub duplicate_groups: usize,
    pub refinement_groups: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct PromptIntakeWindow {
    pub start: DateTime<Utc>,
    pub end: DateTime<Utc>,
    pub hours: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct PromptIntakeReport {
    pub schema_version: u32,
    pub mode: &'static str,
    pub generated_at: DateTime<Utc>,
    pub account_fingerprint: String,
    pub window: PromptIntakeWindow,
    pub counts: PromptIntakeCounts,
    pub decisions: Vec<PromptDecision>,
    pub duplicate_groups: Vec<DuplicateGroup>,
    pub refinement_groups: Vec<RefinementGroup>,
}

#[derive(Debug, Error)]
pub enum PromptIntakeError {
    #[error("account_id must not be empty")]
    EmptyAccountId,
    #[error("window_hours must be between 1 and 8760")]
    InvalidWindow,
    #[error("prompt source identifiers must not be empty")]
    EmptySourceIdentifier,
    #[error("duplicate prompt source identity in the same export: {0}")]
    DuplicateSourceIdentity(String),
}

pub fn build_dry_run_report(
    export: &PromptExport,
    catalog: &ProjectCatalog,
    now: DateTime<Utc>,
    window_hours: i64,
) -> Result<PromptIntakeReport, PromptIntakeError> {
    if export.account_id.trim().is_empty() {
        return Err(PromptIntakeError::EmptyAccountId);
    }
    if !(1..=8_760).contains(&window_hours) {
        return Err(PromptIntakeError::InvalidWindow);
    }

    let window_start = now - Duration::hours(window_hours);
    let catalog_index = CatalogIndex::new(catalog);
    let mut seen_sources = BTreeSet::new();
    let mut decisions = Vec::new();
    let mut outside_window = 0usize;

    for prompt in &export.prompts {
        if prompt.thread_id.trim().is_empty() || prompt.message_id.trim().is_empty() {
            return Err(PromptIntakeError::EmptySourceIdentifier);
        }
        if prompt.created_at < window_start || prompt.created_at > now {
            outside_window += 1;
            continue;
        }

        let source_identity = source_identity(export, prompt);
        if !seen_sources.insert(source_identity.clone()) {
            return Err(PromptIntakeError::DuplicateSourceIdentity(source_identity));
        }
        decisions.push(decide_prompt(prompt, source_identity, &catalog_index));
    }

    decisions.sort_by(|left, right| {
        left.created_at
            .cmp(&right.created_at)
            .then_with(|| left.source_identity.cmp(&right.source_identity))
    });

    let duplicate_groups = duplicate_groups(&decisions);
    let refinement_groups = refinement_groups(export, &decisions);
    let actionable = decisions.iter().filter(|item| item.actionable).count();
    let excluded = decisions.len().saturating_sub(actionable);
    let needs_review = decisions.iter().filter(|item| item.needs_review).count();

    Ok(PromptIntakeReport {
        schema_version: SCHEMA_VERSION,
        mode: "dry_run",
        generated_at: now,
        account_fingerprint: sha256_hex(export.account_id.trim().as_bytes()),
        window: PromptIntakeWindow {
            start: window_start,
            end: now,
            hours: window_hours,
        },
        counts: PromptIntakeCounts {
            input_records: export.prompts.len(),
            within_window: decisions.len(),
            outside_window,
            actionable,
            excluded,
            needs_review,
            duplicate_groups: duplicate_groups.len(),
            refinement_groups: refinement_groups.len(),
        },
        decisions,
        duplicate_groups,
        refinement_groups,
    })
}

fn decide_prompt(
    prompt: &PromptRecord,
    source_identity: String,
    catalog: &CatalogIndex,
) -> PromptDecision {
    let normalized = normalize_prompt(&prompt.text);
    let content_fingerprint = sha256_hex(normalized.as_bytes());
    let repositories = catalog.repositories_for(&normalized);
    let classification = classify_prompt(&normalized, !repositories.is_empty());
    let actionable = classification.is_actionable();
    let exclusion_reason = exclusion_reason(classification, &normalized);
    let project_resolution = catalog.resolve(&repositories, actionable);
    let needs_review = actionable
        && matches!(
            project_resolution.state,
            ProjectResolutionState::CrossProject | ProjectResolutionState::Unmapped
        );
    let github_evidence_queries = repositories
        .iter()
        .map(|repository| GithubEvidenceQuery {
            repository: repository.clone(),
            checks: vec![
                "default_branch",
                "remote_feature_branches",
                "open_closed_merged_pull_requests",
                "resolvable_commits_and_ancestry",
                "release_or_issue_evidence_when_relevant",
            ],
        })
        .collect();
    let linear_search_terms = linear_search_terms(&normalized, &repositories);
    let scope_signature = scope_signature(&normalized, &repositories);
    let mutation_key = sha256_hex(
        format!("prompt-intake:v{SCHEMA_VERSION}:{source_identity}:{content_fingerprint}")
            .as_bytes(),
    );

    PromptDecision {
        source_identity,
        content_fingerprint,
        mutation_key,
        created_at: prompt.created_at,
        title_summary: bounded_summary(&prompt.thread_title),
        prompt_summary: bounded_summary(&prompt.text),
        classification,
        actionable,
        exclusion_reason,
        project_resolution,
        github_evidence_queries,
        linear_search_terms,
        needs_review,
        scope_signature,
    }
}

fn source_identity(export: &PromptExport, prompt: &PromptRecord) -> String {
    sha256_hex(
        format!(
            "{}\u{1f}{}\u{1f}{}\u{1f}{}",
            export.account_id.trim(),
            prompt.thread_id.trim(),
            prompt.message_id.trim(),
            prompt.created_at.to_rfc3339()
        )
        .as_bytes(),
    )
}

pub fn normalize_prompt(text: &str) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .trim()
        .to_lowercase()
}

fn bounded_summary(text: &str) -> String {
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let redacted = redact_summary(&collapsed);
    let char_count = redacted.chars().count();
    if char_count <= MAX_SUMMARY_CHARS {
        return redacted;
    }
    let mut summary = redacted
        .chars()
        .take(MAX_SUMMARY_CHARS.saturating_sub(1))
        .collect::<String>();
    summary.push('…');
    summary
}

fn redact_summary(text: &str) -> String {
    let redacted = secret_assignment_regex().replace_all(text, "$1=[REDACTED]");
    let redacted = bearer_regex().replace_all(&redacted, "Bearer [REDACTED]");
    token_regex()
        .replace_all(&redacted, "[REDACTED_TOKEN]")
        .into_owned()
}

fn classify_prompt(normalized: &str, has_repository: bool) -> PromptClassification {
    if normalized.is_empty() {
        return PromptClassification::Informational;
    }
    if translation_regex().is_match(normalized) {
        return PromptClassification::Informational;
    }
    if has_repository && action_regex().is_match(normalized) {
        return PromptClassification::RepositoryWork;
    }
    if recurring_regex().is_match(normalized) && action_regex().is_match(normalized) {
        return PromptClassification::RecurringAutomation;
    }
    if operational_regex().is_match(normalized) && action_regex().is_match(normalized) {
        return PromptClassification::OperationalProgram;
    }
    if action_regex().is_match(normalized) {
        return PromptClassification::ProductWork;
    }
    if informational_regex().is_match(normalized) {
        return PromptClassification::Informational;
    }
    PromptClassification::Ambiguous
}

fn exclusion_reason(
    classification: PromptClassification,
    normalized: &str,
) -> Option<ExclusionReason> {
    if classification.is_actionable() {
        return None;
    }
    if normalized.is_empty() {
        return Some(ExclusionReason::EmptyPrompt);
    }
    if translation_regex().is_match(normalized) {
        return Some(ExclusionReason::TranslationOrRewrite);
    }
    if informational_regex().is_match(normalized) {
        return Some(ExclusionReason::InformationalQuestion);
    }
    Some(ExclusionReason::NoDurableDeliverable)
}

fn linear_search_terms(normalized: &str, repositories: &[String]) -> Vec<String> {
    let mut terms = BTreeSet::new();
    terms.extend(repositories.iter().cloned());
    for token in scope_tokens(normalized).into_iter().take(12) {
        terms.insert(token);
    }
    terms.into_iter().collect()
}

fn scope_signature(normalized: &str, repositories: &[String]) -> String {
    let mut tokens = scope_tokens(normalized);
    tokens.extend(repositories.iter().map(|item| item.to_lowercase()));
    sha256_hex(
        tokens
            .into_iter()
            .collect::<Vec<_>>()
            .join("\u{1f}")
            .as_bytes(),
    )
}

fn scope_tokens(normalized: &str) -> BTreeSet<String> {
    token_word_regex()
        .find_iter(normalized)
        .filter_map(|capture| {
            let token = capture.as_str();
            if token.chars().all(|character| character.is_ascii_digit()) {
                return Some("<number>".to_owned());
            }
            if token.len() < 4 || stop_words().contains(token) {
                None
            } else {
                Some(token.to_owned())
            }
        })
        .collect()
}

fn duplicate_groups(decisions: &[PromptDecision]) -> Vec<DuplicateGroup> {
    let mut grouped: BTreeMap<&str, Vec<String>> = BTreeMap::new();
    for decision in decisions {
        grouped
            .entry(&decision.content_fingerprint)
            .or_default()
            .push(decision.source_identity.clone());
    }
    grouped
        .into_iter()
        .filter_map(|(fingerprint, source_identities)| {
            (source_identities.len() > 1).then_some(DuplicateGroup {
                content_fingerprint: fingerprint.to_owned(),
                source_identities,
            })
        })
        .collect()
}

fn refinement_groups(export: &PromptExport, decisions: &[PromptDecision]) -> Vec<RefinementGroup> {
    let mut thread_by_source = BTreeMap::new();
    for prompt in &export.prompts {
        thread_by_source.insert(source_identity(export, prompt), prompt.thread_id.trim());
    }

    let mut grouped: BTreeMap<(String, String), Vec<&PromptDecision>> = BTreeMap::new();
    for decision in decisions {
        let token_count = decision.linear_search_terms.len();
        if token_count < MIN_REFINEMENT_TOKENS {
            continue;
        }
        let Some(thread_id) = thread_by_source.get(&decision.source_identity) else {
            continue;
        };
        grouped
            .entry((
                sha256_hex(thread_id.as_bytes()),
                decision.scope_signature.clone(),
            ))
            .or_default()
            .push(decision);
    }

    grouped
        .into_iter()
        .filter_map(|((thread_fingerprint, scope_signature), items)| {
            let content_fingerprints = items
                .iter()
                .map(|item| item.content_fingerprint.clone())
                .collect::<BTreeSet<_>>();
            if content_fingerprints.len() < 2 {
                return None;
            }
            Some(RefinementGroup {
                thread_fingerprint,
                scope_signature,
                source_identities: items
                    .iter()
                    .map(|item| item.source_identity.clone())
                    .collect(),
                content_fingerprints: content_fingerprints.into_iter().collect(),
            })
        })
        .collect()
}

struct CatalogIndex {
    entries: Vec<CatalogEntry>,
}

struct CatalogEntry {
    repository: String,
    repository_lower: String,
    linear_project: String,
    needles: Vec<String>,
}

impl CatalogIndex {
    fn new(catalog: &ProjectCatalog) -> Self {
        let entries = catalog
            .repositories
            .iter()
            .map(|mapping| {
                let repository = canonical_repository(&mapping.repository);
                let mut needles = vec![
                    repository.to_lowercase(),
                    format!("github.com/{}", repository.to_lowercase()),
                ];
                needles.extend(
                    mapping
                        .aliases
                        .iter()
                        .map(|alias| alias.trim().to_lowercase())
                        .filter(|alias| !alias.is_empty()),
                );
                needles.sort();
                needles.dedup();
                CatalogEntry {
                    repository_lower: repository.to_lowercase(),
                    repository,
                    linear_project: mapping.linear_project.trim().to_owned(),
                    needles,
                }
            })
            .collect();
        Self { entries }
    }

    fn repositories_for(&self, normalized: &str) -> Vec<String> {
        let mut repositories = BTreeSet::new();
        for capture in github_repository_regex().captures_iter(normalized) {
            let repository = canonical_repository(&format!("{}/{}", &capture[1], &capture[2]));
            repositories.insert(repository);
        }
        for entry in &self.entries {
            if entry
                .needles
                .iter()
                .any(|needle| !needle.is_empty() && normalized.contains(needle))
            {
                repositories.insert(entry.repository.clone());
            }
        }
        repositories.into_iter().collect()
    }

    fn resolve(&self, repositories: &[String], actionable: bool) -> ProjectResolution {
        if !actionable {
            return ProjectResolution {
                state: ProjectResolutionState::NotRequired,
                repositories: repositories.to_vec(),
                linear_projects: Vec::new(),
            };
        }

        let mut projects = BTreeSet::new();
        for repository in repositories {
            let repository_lower = repository.to_lowercase();
            if let Some(entry) = self
                .entries
                .iter()
                .find(|entry| entry.repository_lower == repository_lower)
            {
                if !entry.linear_project.is_empty() {
                    projects.insert(entry.linear_project.clone());
                }
            }
        }
        let state = match projects.len() {
            0 => ProjectResolutionState::Unmapped,
            1 => ProjectResolutionState::Resolved,
            _ => ProjectResolutionState::CrossProject,
        };
        ProjectResolution {
            state,
            repositories: repositories.to_vec(),
            linear_projects: projects.into_iter().collect(),
        }
    }
}

fn canonical_repository(repository: &str) -> String {
    repository
        .trim()
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_start_matches("github.com/")
        .trim_end_matches('/')
        .trim_end_matches(".git")
        .trim_matches(|character: char| {
            matches!(
                character,
                '`' | '"' | '\'' | ')' | ']' | '}' | ',' | ';' | ':'
            )
        })
        .to_owned()
}

fn sha256_hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn github_repository_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?i)github\.com/([a-z0-9_.-]+)/([a-z0-9_.-]+)")
            .expect("GitHub repository regex must compile")
    })
}

fn token_word_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"[a-z0-9_.-]+").expect("token regex must compile"))
}

fn action_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(
            r"\b(add|amend|audit|build|create|deploy|document|enrich|fix|harden|implement|integrate|merge|migrate|open|publish|reconcile|release|roll out|rollout|schedule|ship|submit|test|track|update|verify|write)\b",
        )
        .expect("action regex must compile")
    })
}

fn recurring_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"\b(daily|every day|every week|hourly|monthly|recurring|scheduled|weekly)\b")
            .expect("recurring regex must compile")
    })
}

fn operational_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(
            r"\b(application|campaign|compliance|incident|migration|monitoring|operations|outreach|release|rollout|security|triage)\b",
        )
        .expect("operational regex must compile")
    })
}

fn informational_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"^(are|can|could|did|do|does|explain|how|is|what|when|where|which|who|why)\b")
            .expect("informational regex must compile")
    })
}

fn translation_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"\b(rephrase|rewrite|translate|translation)\b")
            .expect("translation regex must compile")
    })
}

fn secret_assignment_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?i)\b(api[_ -]?key|password|private[_ -]?key|secret|token)\b\s*[:=]\s*\S+")
            .expect("secret assignment regex must compile")
    })
}

fn bearer_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}").expect("bearer regex must compile")
    })
}

fn token_regex() -> &'static Regex {
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"\b(gh[pousr]_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b")
            .expect("token regex must compile")
    })
}

fn stop_words() -> &'static BTreeSet<&'static str> {
    static WORDS: OnceLock<BTreeSet<&'static str>> = OnceLock::new();
    WORDS.get_or_init(|| {
        [
            "about",
            "after",
            "again",
            "also",
            "been",
            "before",
            "being",
            "branch",
            "chat",
            "check",
            "could",
            "day",
            "days",
            "from",
            "have",
            "hour",
            "hours",
            "into",
            "issue",
            "linear",
            "make",
            "more",
            "other",
            "please",
            "prompt",
            "pull",
            "repo",
            "repository",
            "should",
            "that",
            "their",
            "them",
            "then",
            "there",
            "these",
            "they",
            "this",
            "thread",
            "ticket",
            "tickets",
            "update",
            "using",
            "want",
            "with",
            "work",
        ]
        .into_iter()
        .collect()
    })
}

#[cfg(test)]
mod tests {
    use chrono::TimeZone;

    use super::*;

    fn catalog() -> ProjectCatalog {
        ProjectCatalog {
            repositories: vec![
                RepositoryProject {
                    repository: "ORESoftware/ai-agent-coordinator.rs".to_owned(),
                    linear_project: "github.com/ORESoftware/ai-agent-coordinator.rs".to_owned(),
                    aliases: vec!["ai agent coordinator".to_owned()],
                },
                RepositoryProject {
                    repository: "fiducia-cloud/fiducia-node.rs".to_owned(),
                    linear_project: "github.com/fiducia-cloud".to_owned(),
                    aliases: vec!["fiducia node".to_owned()],
                },
            ],
        }
    }

    fn prompt(
        thread_id: &str,
        message_id: &str,
        created_at: DateTime<Utc>,
        text: &str,
    ) -> PromptRecord {
        PromptRecord {
            thread_id: thread_id.to_owned(),
            message_id: message_id.to_owned(),
            created_at,
            thread_title: "Prompt audit".to_owned(),
            text: text.to_owned(),
        }
    }

    #[test]
    fn normalizes_whitespace_and_redacts_bounded_summaries() {
        assert_eq!(
            normalize_prompt("  Build\n  the   thing  "),
            "build the thing"
        );
        let summary = bounded_summary(
            "Deploy with token=super-secret-value and Bearer abcdefghijklmnop plus ghp_abcdefghijklmnopqrstuvwxyz",
        );
        assert!(!summary.contains("super-secret-value"));
        assert!(!summary.contains("abcdefghijklmnop"));
        assert!(!summary.contains("ghp_"));
        assert!(summary.contains("[REDACTED]"));
    }

    #[test]
    fn filters_window_and_groups_exact_duplicates() {
        let now = Utc.with_ymd_and_hms(2026, 7, 31, 12, 0, 0).unwrap();
        let text = "Implement prompt intake in github.com/ORESoftware/ai-agent-coordinator.rs";
        let export = PromptExport {
            account_id: "account-1".to_owned(),
            prompts: vec![
                prompt("thread-a", "message-a", now - Duration::hours(1), text),
                prompt("thread-b", "message-b", now - Duration::hours(2), text),
                prompt(
                    "thread-c",
                    "message-c",
                    now - Duration::hours(300),
                    "Old prompt",
                ),
            ],
        };
        let report = build_dry_run_report(&export, &catalog(), now, 240).unwrap();
        assert_eq!(report.counts.within_window, 2);
        assert_eq!(report.counts.outside_window, 1);
        assert_eq!(report.counts.actionable, 2);
        assert_eq!(report.duplicate_groups.len(), 1);
        assert!(report.decisions.iter().all(|decision| {
            decision.project_resolution.state == ProjectResolutionState::Resolved
        }));
    }

    #[test]
    fn marks_multiple_project_owners_for_review() {
        let now = Utc.with_ymd_and_hms(2026, 7, 31, 12, 0, 0).unwrap();
        let export = PromptExport {
            account_id: "account-1".to_owned(),
            prompts: vec![prompt(
                "thread-a",
                "message-a",
                now,
                "Integrate github.com/ORESoftware/ai-agent-coordinator.rs with github.com/fiducia-cloud/fiducia-node.rs",
            )],
        };
        let report = build_dry_run_report(&export, &catalog(), now, 24).unwrap();
        let decision = &report.decisions[0];
        assert_eq!(
            decision.project_resolution.state,
            ProjectResolutionState::CrossProject
        );
        assert!(decision.needs_review);
    }

    #[test]
    fn excludes_informational_and_translation_prompts() {
        let now = Utc.with_ymd_and_hms(2026, 7, 31, 12, 0, 0).unwrap();
        let export = PromptExport {
            account_id: "account-1".to_owned(),
            prompts: vec![
                prompt("thread-a", "message-a", now, "What is a Raft quorum?"),
                prompt(
                    "thread-b",
                    "message-b",
                    now,
                    "Translate this paragraph into Spanish",
                ),
            ],
        };
        let report = build_dry_run_report(&export, &catalog(), now, 24).unwrap();
        assert_eq!(report.counts.actionable, 0);
        assert_eq!(report.counts.excluded, 2);
        assert_eq!(
            report.decisions[0].exclusion_reason,
            Some(ExclusionReason::InformationalQuestion)
        );
        assert_eq!(
            report.decisions[1].exclusion_reason,
            Some(ExclusionReason::TranslationOrRewrite)
        );
    }

    #[test]
    fn groups_material_refinements_inside_one_thread() {
        let now = Utc.with_ymd_and_hms(2026, 7, 31, 12, 0, 0).unwrap();
        let export = PromptExport {
            account_id: "account-1".to_owned(),
            prompts: vec![
                prompt(
                    "thread-a",
                    "message-a",
                    now - Duration::minutes(5),
                    "Build weekly prompt intake automation for github.com/ORESoftware/ai-agent-coordinator.rs with a 10 day window",
                ),
                prompt(
                    "thread-a",
                    "message-b",
                    now,
                    "Build weekly prompt intake automation for github.com/ORESoftware/ai-agent-coordinator.rs with a 300 hour window",
                ),
            ],
        };
        let report = build_dry_run_report(&export, &catalog(), now, 24).unwrap();
        assert_eq!(report.refinement_groups.len(), 1);
        assert_eq!(report.refinement_groups[0].content_fingerprints.len(), 2);
    }
}
