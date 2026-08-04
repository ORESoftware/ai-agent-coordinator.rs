use std::collections::{BTreeMap, BTreeSet};

use crate::prompt_intake::PromptDecision;

use super::model::*;

pub(super) fn validate_limits(
    evidence: &ReconciliationEvidence,
) -> Result<(), ReconciliationError> {
    if evidence.prompts.len() > MAX_PROMPT_EVIDENCE {
        return Err(ReconciliationError::TooManyPromptEvidence(
            evidence.prompts.len(),
        ));
    }
    if evidence.receipts.len() > MAX_MUTATION_RECEIPTS {
        return Err(ReconciliationError::TooManyReceipts(
            evidence.receipts.len(),
        ));
    }
    Ok(())
}

pub(super) fn index_prompt_evidence<'a>(
    evidence: &'a ReconciliationEvidence,
    known_mutation_keys: &BTreeSet<&str>,
) -> Result<BTreeMap<&'a str, &'a PromptEvidence>, ReconciliationError> {
    let mut indexed = BTreeMap::new();
    for prompt in &evidence.prompts {
        validate_identifier("prompt_evidence.mutation_key", &prompt.mutation_key)?;
        if !known_mutation_keys.contains(prompt.mutation_key.as_str()) {
            return Err(ReconciliationError::UnknownMutationKey(
                prompt.mutation_key.clone(),
            ));
        }
        if prompt.repositories.len() > MAX_REPOSITORIES_PER_PROMPT {
            return Err(ReconciliationError::TooManyRepositories {
                mutation_key: prompt.mutation_key.clone(),
                count: prompt.repositories.len(),
            });
        }
        if prompt.linear_candidates.len() > MAX_CANDIDATES_PER_PROMPT {
            return Err(ReconciliationError::TooManyCandidates {
                mutation_key: prompt.mutation_key.clone(),
                count: prompt.linear_candidates.len(),
            });
        }
        validate_prompt_evidence(prompt)?;
        if indexed.insert(prompt.mutation_key.as_str(), prompt).is_some() {
            return Err(ReconciliationError::DuplicatePromptEvidence(
                prompt.mutation_key.clone(),
            ));
        }
    }
    Ok(indexed)
}

pub(super) fn index_receipts<'a>(
    evidence: &'a ReconciliationEvidence,
    known_mutation_keys: &BTreeSet<&str>,
) -> Result<BTreeMap<&'a str, &'a MutationReceipt>, ReconciliationError> {
    let mut indexed = BTreeMap::new();
    for receipt in &evidence.receipts {
        validate_identifier("receipt.mutation_key", &receipt.mutation_key)?;
        validate_identifier("receipt.operation_id", &receipt.operation_id)?;
        if !known_mutation_keys.contains(receipt.mutation_key.as_str()) {
            return Err(ReconciliationError::UnknownReceiptMutationKey(
                receipt.mutation_key.clone(),
            ));
        }
        if receipt.outcome == ReceiptOutcome::Applied {
            let Some(issue_id) = receipt.canonical_issue_id.as_deref() else {
                return Err(ReconciliationError::InvalidAppliedReceipt(
                    receipt.mutation_key.clone(),
                ));
            };
            validate_identifier("receipt.canonical_issue_id", issue_id)?;
        }
        if indexed
            .insert(receipt.mutation_key.as_str(), receipt)
            .is_some()
        {
            return Err(ReconciliationError::DuplicateReceipt(
                receipt.mutation_key.clone(),
            ));
        }
    }
    Ok(indexed)
}

pub(super) fn validate_prompt_evidence(prompt: &PromptEvidence) -> Result<(), ReconciliationError> {
    let mut repositories = BTreeSet::new();
    for repository in &prompt.repositories {
        validate_identifier("repository_evidence.repository", &repository.repository)?;
        if !repositories.insert(repository.repository.as_str()) {
            return Err(ReconciliationError::DuplicateRepositoryEvidence {
                mutation_key: prompt.mutation_key.clone(),
                repository: repository.repository.clone(),
            });
        }
        if matches!(
            repository.state,
            LandingState::NonDefaultOnly | LandingState::DefaultBranch
        ) && repository.links.is_empty()
        {
            return Err(ReconciliationError::MissingLandingLink(
                repository.repository.clone(),
            ));
        }
        for link in &repository.links {
            validate_github_url(link)?;
        }
    }

    let mut candidates = BTreeSet::new();
    for candidate in &prompt.linear_candidates {
        validate_identifier("linear_candidate.issue_id", &candidate.issue_id)?;
        validate_identifier("linear_candidate.project", &candidate.project)?;
        validate_linear_url(&candidate.url)?;
        if !candidates.insert(candidate.issue_id.as_str()) {
            return Err(ReconciliationError::DuplicateLinearCandidate {
                mutation_key: prompt.mutation_key.clone(),
                issue_id: candidate.issue_id.clone(),
            });
        }
        if let Some(scope_signature) = candidate.scope_signature.as_deref() {
            validate_identifier("linear_candidate.scope_signature", scope_signature)?;
        }
        for mutation_key in &candidate.mutation_keys {
            validate_identifier("linear_candidate.mutation_key", mutation_key)?;
        }
        for repository in &candidate.repositories {
            validate_identifier("linear_candidate.repository", repository)?;
        }
    }
    Ok(())
}

pub(super) fn validate_expected_repositories(
    decision: &PromptDecision,
    evidence: &PromptEvidence,
) -> Result<(), ReconciliationError> {
    let expected = decision
        .project_resolution
        .repositories
        .iter()
        .map(String::as_str)
        .collect::<BTreeSet<_>>();
    for repository in &evidence.repositories {
        if !expected.contains(repository.repository.as_str()) {
            return Err(ReconciliationError::UnexpectedRepositoryEvidence {
                mutation_key: decision.mutation_key.clone(),
                repository: repository.repository.clone(),
            });
        }
    }
    Ok(())
}

fn validate_identifier(
    field: &'static str,
    value: &str,
) -> Result<(), ReconciliationError> {
    let value = value.trim();
    if value.is_empty()
        || value.chars().count() > MAX_IDENTIFIER_LEN
        || value.chars().any(char::is_control)
    {
        return Err(ReconciliationError::InvalidIdentifier { field });
    }
    Ok(())
}

fn validate_github_url(value: &str) -> Result<(), ReconciliationError> {
    validate_evidence_url(value, "https://github.com/")
}

fn validate_linear_url(value: &str) -> Result<(), ReconciliationError> {
    validate_evidence_url(value, "https://linear.app/")
}

fn validate_evidence_url(value: &str, required_prefix: &str) -> Result<(), ReconciliationError> {
    let trimmed = value.trim();
    let safe = trimmed == value
        && trimmed.len() <= MAX_LINK_LEN
        && trimmed.starts_with(required_prefix)
        && !trimmed.chars().any(char::is_whitespace)
        && !trimmed.contains('@')
        && !trimmed.contains('?')
        && !trimmed.contains('#');
    if !safe {
        return Err(ReconciliationError::UnsafeEvidenceUrl(value.to_owned()));
    }
    Ok(())
}
