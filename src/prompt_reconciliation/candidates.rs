use std::collections::BTreeSet;

use crate::prompt_intake::PromptDecision;

use super::model::*;

pub(super) struct CandidateAssessment<'a> {
    pub(super) selected: Option<SelectedCandidate<'a>>,
    pub(super) ambiguous: bool,
    pub(super) terminal_exact: bool,
}

pub(super) struct SelectedCandidate<'a> {
    pub(super) candidate: &'a LinearIssueCandidate,
    pub(super) reason: PlanReason,
}

pub(super) fn select_candidate<'a>(
    decision: &PromptDecision,
    evidence: &'a PromptEvidence,
    project: &str,
) -> CandidateAssessment<'a> {
    let expected_repositories = decision
        .project_resolution
        .repositories
        .iter()
        .map(|item| item.to_lowercase())
        .collect::<BTreeSet<_>>();
    let mut scored = Vec::new();
    let mut terminal_exact = false;

    for candidate in &evidence.linear_candidates {
        let exact_mutation = candidate
            .mutation_keys
            .iter()
            .any(|key| key == &decision.mutation_key);
        let exact_scope = candidate.scope_signature.as_deref()
            == Some(decision.scope_signature.as_str());
        let candidate_repositories = candidate
            .repositories
            .iter()
            .map(|item| item.to_lowercase())
            .collect::<BTreeSet<_>>();
        let repository_project_match = candidate.project == project
            && !expected_repositories.is_empty()
            && expected_repositories
                .iter()
                .all(|repository| candidate_repositories.contains(repository));

        let (score, reason) = if exact_mutation {
            (3u8, PlanReason::ExactMutationCandidate)
        } else if exact_scope && candidate.project == project {
            (2u8, PlanReason::ExactScopeCandidate)
        } else if repository_project_match {
            (1u8, PlanReason::RepositoryProjectCandidate)
        } else {
            continue;
        };

        if !candidate.status.can_be_canonical() {
            if exact_mutation {
                terminal_exact = true;
            }
            continue;
        }
        scored.push((score, candidate.issue_id.as_str(), candidate, reason));
    }

    scored.sort_by(|left, right| {
        right
            .0
            .cmp(&left.0)
            .then_with(|| left.1.cmp(right.1))
    });
    let Some((top_score, _, top_candidate, top_reason)) = scored.first().copied() else {
        return CandidateAssessment {
            selected: None,
            ambiguous: false,
            terminal_exact,
        };
    };
    let ambiguous = scored.iter().skip(1).any(|item| item.0 == top_score);
    CandidateAssessment {
        selected: (!ambiguous).then_some(SelectedCandidate {
            candidate: top_candidate,
            reason: top_reason,
        }),
        ambiguous,
        terminal_exact,
    }
}
