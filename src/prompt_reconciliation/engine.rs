use std::collections::{BTreeMap, BTreeSet};

use crate::prompt_intake::{ProjectResolutionState, PromptDecision, PromptIntakeReport};

use super::{
    candidates::select_candidate,
    model::*,
    mutation::{build_mutation, count_action, plan_without_mutation, sort_dedup},
    validation::{
        index_prompt_evidence, index_receipts, validate_expected_repositories, validate_limits,
    },
};

pub fn build_reconciliation_plan(
    report: &PromptIntakeReport,
    evidence: &ReconciliationEvidence,
) -> Result<ReconciliationPlan, ReconciliationError> {
    validate_limits(evidence)?;

    let known_mutation_keys = report
        .decisions
        .iter()
        .map(|decision| decision.mutation_key.as_str())
        .collect::<BTreeSet<_>>();
    let evidence_by_key = index_prompt_evidence(evidence, &known_mutation_keys)?;
    let receipts_by_key = index_receipts(evidence, &known_mutation_keys)?;

    let mut prompts = Vec::with_capacity(report.decisions.len());
    for decision in &report.decisions {
        let prompt_evidence = evidence_by_key.get(decision.mutation_key.as_str()).copied();
        let receipt = receipts_by_key.get(decision.mutation_key.as_str()).copied();
        prompts.push(plan_prompt(decision, prompt_evidence, receipt)?);
    }
    prompts.sort_by(|left, right| {
        left.source_identity
            .cmp(&right.source_identity)
            .then_with(|| left.mutation_key.cmp(&right.mutation_key))
    });

    let counts = ReconciliationPlanCounts {
        input_decisions: prompts.len(),
        ignored: count_action(&prompts, PlanAction::Ignore),
        review: count_action(&prompts, PlanAction::Review),
        already_applied: count_action(&prompts, PlanAction::AlreadyApplied),
        already_landed: count_action(&prompts, PlanAction::AlreadyLanded),
        amend: count_action(&prompts, PlanAction::AmendIssue),
        create: count_action(&prompts, PlanAction::CreateIssue),
    };

    Ok(ReconciliationPlan {
        schema_version: PLAN_SCHEMA_VERSION,
        source_report_schema_version: report.schema_version,
        generated_at: report.generated_at.to_owned(),
        account_fingerprint: report.account_fingerprint.clone(),
        counts,
        prompts,
    })
}

fn plan_prompt(
    decision: &PromptDecision,
    evidence: Option<&PromptEvidence>,
    receipt: Option<&MutationReceipt>,
) -> Result<PromptReconciliationPlan, ReconciliationError> {
    if !decision.actionable {
        return Ok(plan_without_mutation(
            decision,
            PlanAction::Ignore,
            vec![PlanReason::NonActionable],
            Vec::new(),
        ));
    }

    if let Some(receipt) = receipt.filter(|item| item.outcome == ReceiptOutcome::Applied) {
        debug_assert!(receipt.canonical_issue_id.is_some());
        return Ok(plan_without_mutation(
            decision,
            PlanAction::AlreadyApplied,
            vec![PlanReason::PriorAppliedReceipt],
            Vec::new(),
        ));
    }

    if decision.needs_review
        || decision.project_resolution.state != ProjectResolutionState::Resolved
    {
        return Ok(plan_without_mutation(
            decision,
            PlanAction::Review,
            vec![PlanReason::OwnershipNeedsReview],
            Vec::new(),
        ));
    }

    let project = resolved_project(decision)?;
    let Some(evidence) = evidence else {
        return Ok(plan_without_mutation(
            decision,
            PlanAction::Review,
            vec![PlanReason::MissingPromptEvidence],
            Vec::new(),
        ));
    };

    validate_expected_repositories(decision, evidence)?;
    let github_assessment = assess_github(decision, evidence);
    if github_assessment.review_required {
        return Ok(plan_without_mutation(
            decision,
            PlanAction::Review,
            github_assessment.reasons,
            github_assessment.links,
        ));
    }

    let candidate_assessment = select_candidate(decision, evidence, project);
    if candidate_assessment.ambiguous {
        let mut reasons = github_assessment.reasons;
        reasons.push(PlanReason::AmbiguousLinearCandidates);
        sort_dedup(&mut reasons);
        return Ok(plan_without_mutation(
            decision,
            PlanAction::Review,
            reasons,
            github_assessment.links,
        ));
    }
    if candidate_assessment.terminal_exact {
        let mut reasons = github_assessment.reasons;
        reasons.push(PlanReason::TerminalExactCandidate);
        sort_dedup(&mut reasons);
        return Ok(plan_without_mutation(
            decision,
            PlanAction::Review,
            reasons,
            github_assessment.links,
        ));
    }

    let mut reasons = github_assessment.reasons;
    if evidence.residual_operational_work {
        reasons.push(PlanReason::ResidualOperationalWork);
    }

    if let Some(selected) = candidate_assessment.selected {
        reasons.push(selected.reason);
        sort_dedup(&mut reasons);
        let mutation = build_mutation(
            decision,
            project,
            Some(selected.candidate.issue_id.as_str()),
            LinearMutationKind::Amend,
            &github_assessment.links,
            evidence.residual_operational_work,
        );
        return Ok(PromptReconciliationPlan {
            source_identity: decision.source_identity.clone(),
            mutation_key: decision.mutation_key.clone(),
            scope_signature: decision.scope_signature.clone(),
            action: PlanAction::AmendIssue,
            reasons,
            mutation: Some(mutation),
            evidence_links: github_assessment.links,
        });
    }

    reasons.push(PlanReason::NoLinearCandidate);
    sort_dedup(&mut reasons);
    if github_assessment.default_branch_landed && !evidence.residual_operational_work {
        return Ok(plan_without_mutation(
            decision,
            PlanAction::AlreadyLanded,
            reasons,
            github_assessment.links,
        ));
    }

    let mutation = build_mutation(
        decision,
        project,
        None,
        LinearMutationKind::Create,
        &github_assessment.links,
        evidence.residual_operational_work,
    );
    Ok(PromptReconciliationPlan {
        source_identity: decision.source_identity.clone(),
        mutation_key: decision.mutation_key.clone(),
        scope_signature: decision.scope_signature.clone(),
        action: PlanAction::CreateIssue,
        reasons,
        mutation: Some(mutation),
        evidence_links: github_assessment.links,
    })
}

fn resolved_project(decision: &PromptDecision) -> Result<&str, ReconciliationError> {
    match decision.project_resolution.linear_projects.as_slice() {
        [project] if !project.trim().is_empty() => Ok(project),
        _ => Err(ReconciliationError::InvalidResolvedProject(
            decision.mutation_key.clone(),
        )),
    }
}

struct GithubAssessment {
    review_required: bool,
    default_branch_landed: bool,
    reasons: Vec<PlanReason>,
    links: Vec<String>,
}

fn assess_github(decision: &PromptDecision, evidence: &PromptEvidence) -> GithubAssessment {
    let expected = decision
        .project_resolution
        .repositories
        .iter()
        .map(String::as_str)
        .collect::<BTreeSet<_>>();
    if expected.is_empty() {
        return GithubAssessment {
            review_required: false,
            default_branch_landed: false,
            reasons: Vec::new(),
            links: Vec::new(),
        };
    }

    let by_repository = evidence
        .repositories
        .iter()
        .map(|item| (item.repository.as_str(), item))
        .collect::<BTreeMap<_, _>>();
    let mut reasons = Vec::new();
    let mut links = BTreeSet::new();
    let mut review_required = false;
    let mut all_default_branch = true;

    for repository in expected {
        let Some(item) = by_repository.get(repository).copied() else {
            reasons.push(PlanReason::MissingRepositoryEvidence);
            review_required = true;
            all_default_branch = false;
            continue;
        };
        links.extend(item.links.iter().cloned());
        if !item.complete {
            reasons.push(PlanReason::IncompleteRepositoryEvidence);
            review_required = true;
            all_default_branch = false;
            continue;
        }
        match item.state {
            LandingState::DefaultBranch => {
                reasons.push(PlanReason::DefaultBranchLanded);
            }
            LandingState::NonDefaultOnly => {
                reasons.push(PlanReason::NonDefaultEvidenceOnly);
                all_default_branch = false;
            }
            LandingState::NoMatch => {
                reasons.push(PlanReason::NoGithubMatch);
                all_default_branch = false;
            }
            LandingState::Conflicting => {
                reasons.push(PlanReason::ConflictingRepositoryEvidence);
                review_required = true;
                all_default_branch = false;
            }
        }
    }

    sort_dedup(&mut reasons);
    GithubAssessment {
        review_required,
        default_branch_landed: all_default_branch,
        reasons,
        links: links.into_iter().collect(),
    }
}
